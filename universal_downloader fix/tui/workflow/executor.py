"""
Workflow execution engine.

Features:
- Sequential execution (topological order)
- Parallel execution (independent nodes run concurrently)
- Step-by-step execution (manual advance)
- Pause/resume/cancel
- Per-node error handling with retry
- Data propagation between nodes
- Event emission for live UI updates

Usage:
    from tui.workflow import WorkflowExecutor

    executor = WorkflowExecutor()
    result = await executor.execute(graph, context={"config": config})

    # Or step-by-step:
    executor.prepare(graph)
    while executor.has_next():
        await executor.step()
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Set

from ..engine.events import event_bus
from ..engine.errors import ErrorBoundary, ErrorSeverity
from ..nodes.base import Node, NodeGraph, NodeStatus

logger = logging.getLogger(__name__)


class ExecutionStatus(Enum):
    """Overall workflow execution status."""
    IDLE = auto()
    PREPARING = auto()
    RUNNING = auto()
    PAUSED = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()


class ExecutionMode(Enum):
    """How to execute the workflow."""
    SEQUENTIAL = "sequential"     # One node at a time, topo order
    PARALLEL = "parallel"         # Independent nodes run concurrently
    STEP_BY_STEP = "step"         # Manual advance


@dataclass
class NodeResult:
    """Result from a single node execution."""
    node_id: str
    node_name: str
    status: NodeStatus
    output: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    execution_time: float = 0.0
    retry_count: int = 0


@dataclass
class ExecutionResult:
    """Complete workflow execution result."""
    status: ExecutionStatus = ExecutionStatus.IDLE
    node_results: List[NodeResult] = field(default_factory=list)
    total_time: float = 0.0
    nodes_completed: int = 0
    nodes_failed: int = 0
    nodes_skipped: int = 0
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.status == ExecutionStatus.COMPLETED and self.nodes_failed == 0

    def summary(self) -> str:
        return (
            f"Status: {self.status.name} | "
            f"Completed: {self.nodes_completed} | "
            f"Failed: {self.nodes_failed} | "
            f"Skipped: {self.nodes_skipped} | "
            f"Time: {self.total_time:.2f}s"
        )


class WorkflowExecutor:
    """
    Executes a NodeGraph workflow.

    Handles:
    - Topological sorting for execution order
    - Data propagation between connected nodes
    - Error handling and retry per node
    - Pause/resume/cancel control
    - Event emission for live status updates
    """

    def __init__(
        self,
        max_retries: int = 2,
        retry_delay: float = 1.0,
        node_timeout: float = 300.0,
        on_node_start: Optional[Callable] = None,
        on_node_complete: Optional[Callable] = None,
        on_node_error: Optional[Callable] = None,
    ):
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._node_timeout = node_timeout
        self._on_node_start = on_node_start
        self._on_node_complete = on_node_complete
        self._on_node_error = on_node_error

        # Runtime state
        self._status = ExecutionStatus.IDLE
        self._graph: Optional[NodeGraph] = None
        self._context: Dict[str, Any] = {}
        self._execution_order: List[Node] = []
        self._current_index: int = 0
        self._result = ExecutionResult()
        self._cancel_event = asyncio.Event()
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # Not paused by default

    @property
    def status(self) -> ExecutionStatus:
        return self._status

    @property
    def result(self) -> ExecutionResult:
        return self._result

    @property
    def progress(self) -> float:
        """Execution progress 0.0 - 1.0."""
        if not self._execution_order:
            return 0.0
        return self._current_index / len(self._execution_order)

    @property
    def current_node(self) -> Optional[Node]:
        """Currently executing node."""
        if 0 <= self._current_index < len(self._execution_order):
            return self._execution_order[self._current_index]
        return None

    def has_next(self) -> bool:
        """Check if there are more nodes to execute."""
        return (
            self._current_index < len(self._execution_order)
            and self._status not in (
                ExecutionStatus.CANCELLED,
                ExecutionStatus.FAILED,
                ExecutionStatus.COMPLETED,
            )
        )

    # ── Control ────────────────────────────────────────────

    def pause(self) -> None:
        """Pause execution."""
        if self._status == ExecutionStatus.RUNNING:
            self._pause_event.clear()
            self._status = ExecutionStatus.PAUSED
            event_bus.emit("workflow.paused", source="WorkflowExecutor")
            logger.info("Workflow paused")

    def resume(self) -> None:
        """Resume paused execution."""
        if self._status == ExecutionStatus.PAUSED:
            self._pause_event.set()
            self._status = ExecutionStatus.RUNNING
            event_bus.emit("workflow.resumed", source="WorkflowExecutor")
            logger.info("Workflow resumed")

    def cancel(self) -> None:
        """Cancel execution."""
        self._cancel_event.set()
        self._status = ExecutionStatus.CANCELLED
        event_bus.emit("workflow.cancelled", source="WorkflowExecutor")
        logger.info("Workflow cancelled")

    def reset(self) -> None:
        """Reset executor for new run."""
        self._status = ExecutionStatus.IDLE
        self._current_index = 0
        self._result = ExecutionResult()
        self._cancel_event.clear()
        self._pause_event.set()
        if self._graph:
            self._graph.reset_all()

    # ── Preparation ────────────────────────────────────────

    def prepare(self, graph: NodeGraph, context: Optional[Dict[str, Any]] = None) -> bool:
        """
        Prepare workflow for execution.
        Performs topological sort and validation.
        Returns True if ready.
        """
        self._graph = graph
        self._context = context or {}
        self._status = ExecutionStatus.PREPARING

        try:
            self._execution_order = graph.topological_sort()
        except ValueError as e:
            self._status = ExecutionStatus.FAILED
            self._result.error = f"Graph error: {e}"
            logger.error(f"Workflow preparation failed: {e}")
            return False

        # Validate all nodes
        validation_errors = []
        for node in self._execution_order:
            errors = node.validate_inputs()
            if errors:
                # Only flag errors for nodes without incoming edges
                incoming = graph.get_incoming_edges(node.id)
                unconnected_errors = []
                for err in errors:
                    port_name = err.split(": ")[-1] if ": " in err else ""
                    port = node.get_input(port_name)
                    if port and not port.connected:
                        unconnected_errors.append(err)
                if unconnected_errors:
                    validation_errors.extend(
                        f"[{node.name}] {e}" for e in unconnected_errors
                    )

        if validation_errors:
            logger.warning(f"Validation warnings: {validation_errors}")

        self._current_index = 0
        self._result = ExecutionResult()
        graph.reset_all()

        # Mark all as pending
        for node in self._execution_order:
            node.status = NodeStatus.PENDING

        self._status = ExecutionStatus.IDLE
        event_bus.emit(
            "workflow.prepared",
            source="WorkflowExecutor",
            node_count=len(self._execution_order),
            graph_name=graph.name,
        )
        return True

    # ── Execution ──────────────────────────────────────────

    async def execute(
        self,
        graph: NodeGraph,
        context: Optional[Dict[str, Any]] = None,
        mode: ExecutionMode = ExecutionMode.SEQUENTIAL,
    ) -> ExecutionResult:
        """
        Execute the entire workflow.

        Args:
            graph: NodeGraph to execute
            context: Shared context dict (config, services)
            mode: Execution mode
        """
        if not self.prepare(graph, context):
            return self._result

        self._status = ExecutionStatus.RUNNING
        start_time = time.time()

        event_bus.emit(
            "workflow.started",
            source="WorkflowExecutor",
            graph_name=graph.name,
            mode=mode.value,
            node_count=len(self._execution_order),
        )

        try:
            if mode == ExecutionMode.PARALLEL:
                await self._execute_parallel()
            else:
                await self._execute_sequential()

            if self._status == ExecutionStatus.RUNNING:
                self._status = ExecutionStatus.COMPLETED
        except Exception as e:
            self._status = ExecutionStatus.FAILED
            self._result.error = str(e)
            logger.error(f"Workflow execution failed: {e}")

        self._result.total_time = time.time() - start_time
        self._result.status = self._status

        event_bus.emit(
            "workflow.completed",
            source="WorkflowExecutor",
            status=self._status.name,
            total_time=self._result.total_time,
            completed=self._result.nodes_completed,
            failed=self._result.nodes_failed,
        )

        return self._result

    async def _execute_sequential(self) -> None:
        """Execute nodes one at a time in topological order."""
        while self.has_next():
            # Check cancel
            if self._cancel_event.is_set():
                self._mark_remaining_skipped()
                return

            # Wait if paused
            await self._pause_event.wait()

            node = self._execution_order[self._current_index]
            await self._execute_node(node)
            self._current_index += 1

    async def _execute_parallel(self) -> None:
        """Execute independent nodes concurrently."""
        if not self._graph:
            return

        executed: Set[str] = set()
        order = list(self._execution_order)

        while len(executed) < len(order):
            if self._cancel_event.is_set():
                break

            await self._pause_event.wait()

            # Find nodes whose dependencies are all completed
            ready = []
            for node in order:
                if node.id in executed:
                    continue

                predecessors = self._graph.get_predecessors(node.id)
                all_done = all(
                    p.id in executed and p.status == NodeStatus.COMPLETED
                    for p in predecessors
                )

                if all_done:
                    ready.append(node)

            if not ready:
                # Check for deadlock (failed dependencies)
                remaining = [n for n in order if n.id not in executed]
                all_blocked = True
                for node in remaining:
                    preds = self._graph.get_predecessors(node.id)
                    if any(p.status == NodeStatus.FAILED for p in preds):
                        node.status = NodeStatus.SKIPPED
                        executed.add(node.id)
                        self._result.nodes_skipped += 1
                        all_blocked = False

                if all_blocked and remaining:
                    logger.error("Workflow deadlocked — no nodes ready to execute")
                    self._status = ExecutionStatus.FAILED
                    self._result.error = "Execution deadlock"
                    return
                continue

            # Execute ready nodes concurrently
            tasks = [self._execute_node(node) for node in ready]
            await asyncio.gather(*tasks, return_exceptions=True)

            for node in ready:
                executed.add(node.id)
                self._current_index = len(executed)

    async def _execute_node(self, node: Node) -> NodeResult:
        """Execute a single node with retry logic."""
        node_result = NodeResult(
            node_id=node.id,
            node_name=node.name,
            status=NodeStatus.RUNNING,
        )

        # Propagate input data from predecessors
        if self._graph:
            for pred in self._graph.get_predecessors(node.id):
                self._graph.propagate_data(pred.id)

        # Notify start
        if self._on_node_start:
            self._on_node_start(node)

        event_bus.emit(
            "workflow.node.executing",
            source="WorkflowExecutor",
            node_id=node.id,
            node_name=node.name,
            node_type=node.node_type,
        )

        # Execute with retry
        last_error = None
        for attempt in range(self._max_retries + 1):
            if self._cancel_event.is_set():
                node.status = NodeStatus.CANCELLED
                node_result.status = NodeStatus.CANCELLED
                break

            try:
                output = await asyncio.wait_for(
                    node.execute(self._context),
                    timeout=self._node_timeout,
                )
                node_result.output = output or {}
                node_result.status = NodeStatus.COMPLETED
                node_result.execution_time = node.execution_time
                node_result.retry_count = attempt
                self._result.nodes_completed += 1

                # Propagate data to successors
                if self._graph:
                    self._graph.propagate_data(node.id)

                # Activate outgoing edges
                if self._graph:
                    for edge in self._graph.get_outgoing_edges(node.id):
                        edge.active = True

                if self._on_node_complete:
                    self._on_node_complete(node, node_result)

                break

            except asyncio.TimeoutError:
                last_error = f"Timeout after {self._node_timeout}s"
                logger.warning(f"Node '{node.name}' timed out (attempt {attempt + 1})")

            except asyncio.CancelledError:
                node.status = NodeStatus.CANCELLED
                node_result.status = NodeStatus.CANCELLED
                break

            except Exception as e:
                last_error = str(e)
                logger.warning(
                    f"Node '{node.name}' failed (attempt {attempt + 1}/{self._max_retries + 1}): {e}"
                )

                if attempt < self._max_retries:
                    await asyncio.sleep(self._retry_delay * (attempt + 1))

        else:
            # All retries exhausted
            node.status = NodeStatus.FAILED
            node.error_message = last_error or "Unknown error"
            node_result.status = NodeStatus.FAILED
            node_result.error = last_error
            self._result.nodes_failed += 1

            if self._on_node_error:
                self._on_node_error(node, last_error)

            # Skip dependent nodes
            if self._graph:
                self._skip_dependents(node.id)

        self._result.node_results.append(node_result)
        return node_result

    def _skip_dependents(self, failed_node_id: str) -> None:
        """Mark all downstream nodes as skipped."""
        if not self._graph:
            return

        to_skip = set()
        stack = [failed_node_id]

        while stack:
            nid = stack.pop()
            for successor in self._graph.get_successors(nid):
                if successor.id not in to_skip:
                    to_skip.add(successor.id)
                    successor.status = NodeStatus.SKIPPED
                    self._result.nodes_skipped += 1
                    stack.append(successor.id)

    def _mark_remaining_skipped(self) -> None:
        """Mark all unexecuted nodes as skipped."""
        for i in range(self._current_index, len(self._execution_order)):
            node = self._execution_order[i]
            if node.status in (NodeStatus.IDLE, NodeStatus.PENDING):
                node.status = NodeStatus.SKIPPED
                self._result.nodes_skipped += 1

    # ── Step-by-step ───────────────────────────────────────

    async def step(self) -> Optional[NodeResult]:
        """Execute the next node in sequence. Returns result or None if done."""
        if not self.has_next():
            return None

        if self._status == ExecutionStatus.IDLE:
            self._status = ExecutionStatus.RUNNING

        node = self._execution_order[self._current_index]
        result = await self._execute_node(node)
        self._current_index += 1

        if not self.has_next():
            self._status = ExecutionStatus.COMPLETED
            self._result.status = self._status

        return result