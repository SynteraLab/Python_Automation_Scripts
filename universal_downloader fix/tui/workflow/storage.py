"""
Workflow persistence — save/load workflows as JSON/YAML files.

Features:
- Save workflows to files
- Load workflows from files
- List saved workflows
- Delete workflows
- Auto-save directory management

Usage:
    storage = WorkflowStorage()
    storage.save(graph, "my_workflow")
    loaded = storage.load("my_workflow")
    workflows = storage.list_workflows()
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..engine.events import event_bus
from ..nodes.base import NodeGraph
from ..nodes.registry import node_registry

logger = logging.getLogger(__name__)


class WorkflowStorage:
    """
    File-based workflow persistence.

    Workflows are saved as JSON files in a configurable directory.
    """

    def __init__(self, storage_dir: Optional[str] = None):
        self._storage_dir = Path(
            storage_dir or os.path.expanduser("~/.universal_downloader/workflows")
        )
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        """Create storage directory if it doesn't exist."""
        self._storage_dir.mkdir(parents=True, exist_ok=True)

    @property
    def storage_dir(self) -> Path:
        return self._storage_dir

    # ── Save ───────────────────────────────────────────────

    def save(self, graph: NodeGraph, name: Optional[str] = None) -> str:
        """
        Save a workflow to file.

        Args:
            graph: NodeGraph to save
            name: Filename (without extension). Defaults to graph.name

        Returns:
            Filepath where workflow was saved
        """
        filename = self._sanitize_name(name or graph.name)
        filepath = self._storage_dir / f"{filename}.json"

        data = {
            "version": "1.0",
            "saved_at": time.time(),
            "graph": graph.to_dict(),
        }

        try:
            with open(filepath, "w") as f:
                json.dump(data, f, indent=2, default=str)

            event_bus.emit(
                "workflow.saved",
                source="WorkflowStorage",
                name=filename,
                path=str(filepath),
                node_count=len(graph.nodes),
            )
            logger.info(f"Workflow saved: {filepath}")
            return str(filepath)

        except Exception as e:
            logger.error(f"Failed to save workflow: {e}")
            raise

    # ── Load ───────────────────────────────────────────────

    def load(self, name: str) -> Optional[NodeGraph]:
        """
        Load a workflow from file.

        Args:
            name: Workflow name (with or without .json)

        Returns:
            NodeGraph or None if not found
        """
        filename = self._sanitize_name(name)
        filepath = self._storage_dir / f"{filename}.json"

        if not filepath.exists():
            # Try with original name
            filepath = self._storage_dir / name
            if not filepath.exists():
                logger.warning(f"Workflow not found: {name}")
                return None

        try:
            with open(filepath) as f:
                data = json.load(f)

            graph_data = data.get("graph", data)

            graph = NodeGraph.from_dict(
                graph_data,
                node_factory=lambda t, d: node_registry.create_from_dict(d),
            )

            event_bus.emit(
                "workflow.loaded",
                source="WorkflowStorage",
                name=filename,
                path=str(filepath),
            )
            logger.info(f"Workflow loaded: {filepath}")
            return graph

        except Exception as e:
            logger.error(f"Failed to load workflow '{name}': {e}")
            return None

    def load_by_id(self, graph_id: str) -> Optional[NodeGraph]:
        """Load a workflow by its graph ID (searches all files)."""
        for wf in self.list_workflows():
            if wf.get("graph_id") == graph_id:
                return self.load(wf["name"])
        return None

    # ── List ───────────────────────────────────────────────

    def list_workflows(self) -> List[Dict[str, Any]]:
        """List all saved workflows with metadata."""
        workflows = []

        for filepath in sorted(self._storage_dir.glob("*.json")):
            try:
                with open(filepath) as f:
                    data = json.load(f)

                graph_data = data.get("graph", {})
                workflows.append({
                    "name": filepath.stem,
                    "filename": filepath.name,
                    "path": str(filepath),
                    "graph_id": graph_data.get("id", ""),
                    "graph_name": graph_data.get("name", ""),
                    "node_count": len(graph_data.get("nodes", [])),
                    "edge_count": len(graph_data.get("edges", [])),
                    "saved_at": data.get("saved_at", 0),
                    "version": data.get("version", ""),
                    "size_bytes": filepath.stat().st_size,
                })
            except Exception as e:
                logger.debug(f"Skipping invalid workflow file {filepath}: {e}")
                workflows.append({
                    "name": filepath.stem,
                    "filename": filepath.name,
                    "path": str(filepath),
                    "error": str(e),
                })

        return workflows

    # ── Delete ─────────────────────────────────────────────

    def delete(self, name: str) -> bool:
        """Delete a saved workflow."""
        filename = self._sanitize_name(name)
        filepath = self._storage_dir / f"{filename}.json"

        if not filepath.exists():
            return False

        try:
            filepath.unlink()
            event_bus.emit(
                "workflow.deleted",
                source="WorkflowStorage",
                name=filename,
            )
            logger.info(f"Workflow deleted: {filepath}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete workflow: {e}")
            return False

    # ── Exists ─────────────────────────────────────────────

    def exists(self, name: str) -> bool:
        """Check if a workflow exists."""
        filename = self._sanitize_name(name)
        return (self._storage_dir / f"{filename}.json").exists()

    # ── Duplicate ──────────────────────────────────────────

    def duplicate(self, name: str, new_name: str) -> Optional[str]:
        """Duplicate a workflow with a new name."""
        graph = self.load(name)
        if not graph:
            return None

        import uuid
        graph.id = f"graph_{uuid.uuid4().hex[:8]}"
        graph.name = new_name

        return self.save(graph, new_name)

    # ── Export / Import ────────────────────────────────────

    def export_all(self) -> Dict[str, Any]:
        """Export all workflows as a single dict (for backup)."""
        result = {
            "version": "1.0",
            "exported_at": time.time(),
            "workflows": [],
        }

        for filepath in self._storage_dir.glob("*.json"):
            try:
                with open(filepath) as f:
                    data = json.load(f)
                data["_filename"] = filepath.stem
                result["workflows"].append(data)
            except Exception:
                pass

        return result

    def import_all(self, export_data: Dict[str, Any]) -> int:
        """Import workflows from exported backup. Returns count imported."""
        count = 0
        for wf_data in export_data.get("workflows", []):
            try:
                name = wf_data.get("_filename", f"imported_{count}")
                graph_data = wf_data.get("graph", wf_data)
                graph = NodeGraph.from_dict(
                    graph_data,
                    node_factory=lambda t, d: node_registry.create_from_dict(d),
                )
                self.save(graph, name)
                count += 1
            except Exception as e:
                logger.warning(f"Failed to import workflow: {e}")

        return count

    # ── Helpers ─────────────────────────────────────────────

    @staticmethod
    def _sanitize_name(name: str) -> str:
        """Sanitize workflow name for use as filename."""
        # Remove extension if present
        if name.endswith(".json"):
            name = name[:-5]

        # Replace unsafe characters
        safe = ""
        for c in name:
            if c.isalnum() or c in "-_ ":
                safe += c
            else:
                safe += "_"

        return safe.strip() or "untitled"