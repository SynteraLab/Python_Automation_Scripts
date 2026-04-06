"""
Workflow scheduler — cron-like system for timed workflow execution.

Features:
- Schedule workflows to run at intervals
- Cron-like expressions (simplified)
- One-shot delayed execution
- Job management (add, remove, list, pause)
- Persistent job queue

Usage:
    scheduler = WorkflowScheduler()

    # Run every 30 minutes
    scheduler.add_job("backup", graph, interval_minutes=30)

    # Run once after 5 minutes
    scheduler.add_job("report", graph, delay_seconds=300, repeat=False)

    # Start scheduler loop
    await scheduler.start()
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional

from ..engine.events import event_bus

logger = logging.getLogger(__name__)


class JobStatus(Enum):
    """Scheduled job status."""
    PENDING = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    PAUSED = auto()
    CANCELLED = auto()


@dataclass
class ScheduledJob:
    """A scheduled workflow execution."""
    id: str
    name: str
    graph_id: str                      # Reference to saved workflow
    graph_data: Optional[Dict] = None  # Inline graph data (alternative)

    # Timing
    interval_seconds: float = 0        # 0 = one-shot
    delay_seconds: float = 0           # Initial delay before first run
    repeat: bool = True                # Whether to repeat after completion
    max_runs: int = 0                  # 0 = unlimited

    # State
    status: JobStatus = JobStatus.PENDING
    run_count: int = 0
    last_run: float = 0.0
    next_run: float = 0.0
    last_error: str = ""
    created_at: float = field(default_factory=time.time)

    # Execution config
    context: Dict[str, Any] = field(default_factory=dict)
    execution_mode: str = "sequential"

    @property
    def is_active(self) -> bool:
        return self.status in (JobStatus.PENDING, JobStatus.RUNNING)

    @property
    def is_due(self) -> bool:
        return time.time() >= self.next_run and self.status == JobStatus.PENDING

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "graph_id": self.graph_id,
            "graph_data": self.graph_data,
            "interval_seconds": self.interval_seconds,
            "delay_seconds": self.delay_seconds,
            "repeat": self.repeat,
            "max_runs": self.max_runs,
            "run_count": self.run_count,
            "last_run": self.last_run,
            "next_run": self.next_run,
            "status": self.status.name,
            "created_at": self.created_at,
            "execution_mode": self.execution_mode,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScheduledJob":
        job = cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            graph_id=data.get("graph_id", ""),
            graph_data=data.get("graph_data"),
            interval_seconds=data.get("interval_seconds", 0),
            delay_seconds=data.get("delay_seconds", 0),
            repeat=data.get("repeat", True),
            max_runs=data.get("max_runs", 0),
            run_count=data.get("run_count", 0),
            last_run=data.get("last_run", 0),
            next_run=data.get("next_run", 0),
            execution_mode=data.get("execution_mode", "sequential"),
        )
        status_str = data.get("status", "PENDING")
        try:
            job.status = JobStatus[status_str]
        except KeyError:
            job.status = JobStatus.PENDING
        return job


class WorkflowScheduler:
    """
    Manages scheduled workflow executions.

    Runs as an async background task, checking for due jobs
    and executing them via WorkflowExecutor.
    """

    def __init__(self, check_interval: float = 5.0):
        self._jobs: Dict[str, ScheduledJob] = {}
        self._check_interval = check_interval
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._executor_factory: Optional[Callable] = None
        self._graph_loader: Optional[Callable] = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def job_count(self) -> int:
        return len(self._jobs)

    @property
    def active_jobs(self) -> List[ScheduledJob]:
        return [j for j in self._jobs.values() if j.is_active]

    def set_executor_factory(self, factory: Callable) -> None:
        """Set factory function that creates WorkflowExecutor instances."""
        self._executor_factory = factory

    def set_graph_loader(self, loader: Callable) -> None:
        """Set function that loads a graph by ID from storage."""
        self._graph_loader = loader

    # ── Job Management ─────────────────────────────────────

    def add_job(
        self,
        name: str,
        graph_id: str = "",
        graph_data: Optional[Dict] = None,
        interval_minutes: float = 0,
        interval_seconds: float = 0,
        delay_seconds: float = 0,
        repeat: bool = True,
        max_runs: int = 0,
        context: Optional[Dict] = None,
        execution_mode: str = "sequential",
    ) -> ScheduledJob:
        """
        Schedule a workflow for execution.

        Args:
            name: Human-readable job name
            graph_id: ID of saved workflow (loaded via graph_loader)
            graph_data: Inline graph dict (alternative to graph_id)
            interval_minutes: Run every N minutes (0 = one-shot)
            interval_seconds: Run every N seconds (overrides minutes)
            delay_seconds: Wait before first run
            repeat: Whether to repeat
            max_runs: Max executions (0 = unlimited)
            context: Execution context dict
            execution_mode: "sequential" or "parallel"
        """
        import uuid

        interval = interval_seconds or (interval_minutes * 60)
        now = time.time()

        job = ScheduledJob(
            id=f"job_{uuid.uuid4().hex[:8]}",
            name=name,
            graph_id=graph_id,
            graph_data=graph_data,
            interval_seconds=interval,
            delay_seconds=delay_seconds,
            repeat=repeat and interval > 0,
            max_runs=max_runs,
            next_run=now + delay_seconds,
            context=context or {},
            execution_mode=execution_mode,
        )

        self._jobs[job.id] = job

        event_bus.emit(
            "scheduler.job.added",
            source="WorkflowScheduler",
            job_id=job.id,
            job_name=name,
            interval=interval,
        )
        logger.info(f"Scheduled job: {name} (interval={interval}s, delay={delay_seconds}s)")

        return job

    def remove_job(self, job_id: str) -> bool:
        """Remove a scheduled job."""
        job = self._jobs.pop(job_id, None)
        if job:
            job.status = JobStatus.CANCELLED
            event_bus.emit("scheduler.job.removed", source="WorkflowScheduler", job_id=job_id)
            return True
        return False

    def pause_job(self, job_id: str) -> bool:
        """Pause a scheduled job."""
        job = self._jobs.get(job_id)
        if job and job.status == JobStatus.PENDING:
            job.status = JobStatus.PAUSED
            event_bus.emit("scheduler.job.paused", source="WorkflowScheduler", job_id=job_id)
            return True
        return False

    def resume_job(self, job_id: str) -> bool:
        """Resume a paused job."""
        job = self._jobs.get(job_id)
        if job and job.status == JobStatus.PAUSED:
            job.status = JobStatus.PENDING
            event_bus.emit("scheduler.job.resumed", source="WorkflowScheduler", job_id=job_id)
            return True
        return False

    def get_job(self, job_id: str) -> Optional[ScheduledJob]:
        return self._jobs.get(job_id)

    def list_jobs(self) -> List[ScheduledJob]:
        return list(self._jobs.values())

    # ── Scheduler Loop ─────────────────────────────────────

    async def start(self) -> None:
        """Start the scheduler background loop."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._scheduler_loop())

        event_bus.emit("scheduler.started", source="WorkflowScheduler")
        logger.info("Workflow scheduler started")

    async def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        event_bus.emit("scheduler.stopped", source="WorkflowScheduler")
        logger.info("Workflow scheduler stopped")

    async def _scheduler_loop(self) -> None:
        """Main scheduler loop — checks for due jobs."""
        while self._running:
            try:
                await self._check_and_run_jobs()
                await asyncio.sleep(self._check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scheduler loop error: {e}")
                await asyncio.sleep(self._check_interval)

    async def _check_and_run_jobs(self) -> None:
        """Check all jobs and execute those that are due."""
        now = time.time()

        for job in list(self._jobs.values()):
            if not job.is_due:
                continue

            # Check max runs
            if job.max_runs > 0 and job.run_count >= job.max_runs:
                job.status = JobStatus.COMPLETED
                event_bus.emit(
                    "scheduler.job.completed",
                    source="WorkflowScheduler",
                    job_id=job.id,
                    run_count=job.run_count,
                )
                continue

            # Execute the job
            await self._execute_job(job)

    async def _execute_job(self, job: ScheduledJob) -> None:
        """Execute a single scheduled job."""
        job.status = JobStatus.RUNNING
        job.last_run = time.time()

        event_bus.emit(
            "scheduler.job.running",
            source="WorkflowScheduler",
            job_id=job.id,
            job_name=job.name,
            run_count=job.run_count + 1,
        )

        try:
            # Load graph
            graph = await self._load_graph(job)
            if not graph:
                raise RuntimeError(f"Could not load graph for job: {job.name}")

            # Create executor
            if self._executor_factory:
                executor = self._executor_factory()
            else:
                from .executor import WorkflowExecutor
                executor = WorkflowExecutor()

            # Determine execution mode
            from .executor import ExecutionMode
            mode = ExecutionMode(job.execution_mode)

            # Execute
            result = await executor.execute(graph, context=job.context, mode=mode)

            job.run_count += 1

            if result.success:
                logger.info(f"Job '{job.name}' completed (run #{job.run_count})")
            else:
                job.last_error = result.error or "Unknown error"
                logger.warning(f"Job '{job.name}' had failures: {job.last_error}")

        except Exception as e:
            job.last_error = str(e)
            job.status = JobStatus.FAILED
            logger.error(f"Job '{job.name}' execution failed: {e}")
            event_bus.emit(
                "scheduler.job.failed",
                source="WorkflowScheduler",
                job_id=job.id,
                error=str(e),
            )
            return

        # Schedule next run
        if job.repeat and job.interval_seconds > 0:
            job.next_run = time.time() + job.interval_seconds
            job.status = JobStatus.PENDING
        else:
            job.status = JobStatus.COMPLETED
            event_bus.emit(
                "scheduler.job.completed",
                source="WorkflowScheduler",
                job_id=job.id,
                run_count=job.run_count,
            )

    async def _load_graph(self, job: ScheduledJob):
        """Load graph from job data or storage."""
        # Inline graph data
        if job.graph_data:
            from ..nodes.base import NodeGraph
            from ..nodes.registry import node_registry
            return NodeGraph.from_dict(
                job.graph_data,
                node_factory=lambda t, d: node_registry.create_from_dict(d),
            )

        # Load from storage via loader
        if self._graph_loader and job.graph_id:
            return self._graph_loader(job.graph_id)

        return None

    # ── Serialization ──────────────────────────────────────

    def export_jobs(self) -> List[Dict]:
        """Export all jobs as dicts."""
        return [job.to_dict() for job in self._jobs.values()]

    def import_jobs(self, jobs_data: List[Dict]) -> int:
        """Import jobs from dicts. Returns count imported."""
        count = 0
        for data in jobs_data:
            job = ScheduledJob.from_dict(data)
            if job.id:
                self._jobs[job.id] = job
                count += 1
        return count