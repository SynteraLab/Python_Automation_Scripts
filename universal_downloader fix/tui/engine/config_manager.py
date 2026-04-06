"""
Runtime configuration manager with hot-reload support.

Features:
- Wraps existing Config dataclass (no changes to config.py)
- Watch config file for changes
- Emit events on config changes
- Thread-safe mutations
- Snapshot/restore for undo

Usage:
    from tui.engine import ConfigManager
    from config import Config

    cfg = Config.load()
    manager = ConfigManager(cfg)

    # Mutate safely
    manager.set("download.output_dir", "/new/path")

    # Listen for changes
    event_bus.on("config.changed", my_handler)

    # Hot-reload
    manager.reload()
"""

import json
import logging
import os
import threading
import time
from copy import deepcopy
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .events import event_bus

logger = logging.getLogger(__name__)


class ConfigManager:
    """
    Thread-safe wrapper around Config with change tracking and hot-reload.
    Does NOT modify the Config class itself — wraps it.
    """

    def __init__(self, config: Any, config_path: Optional[str] = None):
        """
        Args:
            config: Config instance from config.py
            config_path: Path to config file for hot-reload
        """
        self._config = config
        self._config_path = config_path
        self._lock = threading.RLock()
        self._snapshots: List[Dict[str, Any]] = []
        self._max_snapshots = 10
        self._watchers: List[Callable] = []
        self._last_modified: float = 0.0

        # Detect config path if not provided
        if not self._config_path:
            self._config_path = self._detect_config_path()

        # Take initial snapshot
        self._take_snapshot()

    @property
    def config(self) -> Any:
        """Get current Config object."""
        return self._config

    # ── Getters ────────────────────────────────────────────

    def get(self, dotted_key: str, default: Any = None) -> Any:
        """
        Get config value by dotted path.
        Example: get("download.output_dir")
        """
        with self._lock:
            parts = dotted_key.split(".")
            obj = self._config

            for part in parts:
                if hasattr(obj, part):
                    obj = getattr(obj, part)
                elif isinstance(obj, dict):
                    obj = obj.get(part, default)
                    if obj is default:
                        return default
                else:
                    return default

            return obj

    # ── Setters ────────────────────────────────────────────

    def set(self, dotted_key: str, value: Any) -> None:
        """
        Set config value by dotted path.
        Emits "config.changed" event.

        Example: set("download.output_dir", "/new/path")
        """
        with self._lock:
            parts = dotted_key.split(".")
            obj = self._config

            # Navigate to parent
            for part in parts[:-1]:
                if hasattr(obj, part):
                    obj = getattr(obj, part)
                else:
                    logger.warning(f"Config path not found: {dotted_key}")
                    return

            attr = parts[-1]
            if not hasattr(obj, attr):
                logger.warning(f"Config attribute not found: {dotted_key}")
                return

            old_value = getattr(obj, attr)
            if old_value == value:
                return  # No change

            setattr(obj, attr, value)

        # Emit change event (outside lock)
        event_bus.emit(
            "config.changed",
            source="ConfigManager",
            key=dotted_key,
            old_value=old_value,
            new_value=value,
        )
        logger.debug(f"Config changed: {dotted_key} = {value}")

    def set_many(self, changes: Dict[str, Any]) -> None:
        """Apply multiple config changes at once."""
        for key, value in changes.items():
            self.set(key, value)

    # ── Snapshots ──────────────────────────────────────────

    def _take_snapshot(self) -> None:
        """Save a snapshot of current config state."""
        with self._lock:
            try:
                snapshot = self._serialize_config()
                self._snapshots.append(snapshot)
                if len(self._snapshots) > self._max_snapshots:
                    self._snapshots = self._snapshots[-self._max_snapshots:]
            except Exception as e:
                logger.debug(f"Failed to take config snapshot: {e}")

    def snapshot(self) -> None:
        """Manually take a config snapshot (for undo)."""
        self._take_snapshot()

    def restore(self) -> bool:
        """Restore last snapshot. Returns True if restored."""
        with self._lock:
            if len(self._snapshots) < 2:
                logger.warning("No previous snapshot to restore")
                return False

            # Pop current, restore previous
            self._snapshots.pop()
            snapshot = self._snapshots[-1]

        self._apply_snapshot(snapshot)
        event_bus.emit("config.restored", source="ConfigManager")
        logger.info("Config restored from snapshot")
        return True

    def _serialize_config(self) -> Dict[str, Any]:
        """Serialize config to dict."""
        if hasattr(self._config, "to_dict"):
            return self._config.to_dict()
        return {}

    def _apply_snapshot(self, snapshot: Dict[str, Any]) -> None:
        """Apply a snapshot dict back to config object."""
        for section_key, section_val in snapshot.items():
            if isinstance(section_val, dict):
                section_obj = getattr(self._config, section_key, None)
                if section_obj and is_dataclass(section_obj):
                    for attr, val in section_val.items():
                        if hasattr(section_obj, attr):
                            setattr(section_obj, attr, val)
            else:
                if hasattr(self._config, section_key):
                    setattr(self._config, section_key, section_val)

    # ── Hot Reload ─────────────────────────────────────────

    def reload(self) -> bool:
        """
        Reload config from file.
        Returns True if config was reloaded successfully.
        """
        if not self._config_path:
            logger.warning("No config path set, cannot reload")
            return False

        path = Path(self._config_path)
        if not path.exists():
            logger.warning(f"Config file not found: {self._config_path}")
            return False

        try:
            self._take_snapshot()  # Backup before reload

            from config import Config
            new_config = Config.load(str(path))

            # Apply new values to existing config object
            with self._lock:
                self._merge_config(new_config)

            event_bus.emit(
                "config.reloaded",
                source="ConfigManager",
                path=str(path),
            )
            logger.info(f"Config reloaded from {path}")
            return True
        except Exception as e:
            logger.error(f"Config reload failed: {e}")
            return False

    def _merge_config(self, new_config: Any) -> None:
        """Merge new config values into existing config object."""
        if not is_dataclass(self._config) or not is_dataclass(new_config):
            return

        for f in fields(self._config):
            new_val = getattr(new_config, f.name, None)
            old_val = getattr(self._config, f.name, None)

            if new_val is None:
                continue

            if is_dataclass(old_val) and is_dataclass(new_val):
                # Recursively merge sub-configs
                for sub_f in fields(old_val):
                    sub_new = getattr(new_val, sub_f.name, None)
                    if sub_new is not None:
                        setattr(old_val, sub_f.name, sub_new)
            else:
                setattr(self._config, f.name, new_val)

    def check_file_changed(self) -> bool:
        """Check if config file was modified since last load."""
        if not self._config_path:
            return False

        try:
            mtime = os.path.getmtime(self._config_path)
            if mtime > self._last_modified:
                self._last_modified = mtime
                return True
        except OSError:
            pass
        return False

    # ── File Detection ─────────────────────────────────────

    @staticmethod
    def _detect_config_path() -> Optional[str]:
        """Try to find existing config file."""
        candidates = [
            "./config.yaml",
            "./config.yml",
            "./config.json",
        ]
        for path in candidates:
            if os.path.exists(path):
                return path
        return None

    # ── Export ──────────────────────────────────────────────

    def export_current(self) -> Dict[str, Any]:
        """Export current config as dict."""
        with self._lock:
            return self._serialize_config()

    def save(self, path: Optional[str] = None) -> bool:
        """Save current config to file."""
        target = path or self._config_path
        if not target:
            logger.warning("No path specified for config save")
            return False

        try:
            if hasattr(self._config, "save"):
                self._config.save(target)
            else:
                data = self.export_current()
                with open(target, "w") as f:
                    json.dump(data, f, indent=2)

            event_bus.emit(
                "config.saved",
                source="ConfigManager",
                path=target,
            )
            logger.info(f"Config saved to {target}")
            return True
        except Exception as e:
            logger.error(f"Config save failed: {e}")
            return False