"""Improved plugin loader for extractor extensions."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Dict, List, Optional

from extractors.base import registry
from extractors.registry_models import ExtractorSource

logger = logging.getLogger(__name__)


@dataclass
class PluginInfo:
    name: str
    module_name: str
    source: str
    version: str = "0"
    extractors: List[str] = field(default_factory=list)
    strategies: List[str] = field(default_factory=list)


class PluginManager:
    """Loads extractor plugins from directories and Python entry points."""

    def __init__(self) -> None:
        self.loaded: Dict[str, PluginInfo] = {}

    def load_paths(self, plugin_dirs: List[str]) -> int:
        loaded = 0
        for plugin_dir in map(Path, plugin_dirs):
            if not plugin_dir.exists():
                continue

            logger.debug("Scanning plugin directory: %s", plugin_dir)
            for file in plugin_dir.glob('*.py'):
                if file.name.startswith('_'):
                    continue
                module_name = f"unidown_plugin_{file.stem}"
                spec = importlib.util.spec_from_file_location(module_name, file)
                if spec is None or spec.loader is None:
                    logger.warning("Failed to load plugin %s: invalid module spec", file.name)
                    continue

                try:
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[module_name] = module
                    spec.loader.exec_module(module)
                    self._register_module(module, source=str(file), name=file.stem)
                    loaded += 1
                except Exception as exc:
                    logger.warning("Failed to load plugin %s: %s", file.name, exc)
        return loaded

    def load_entrypoints(self, group: str = 'universal_downloader.extractors') -> int:
        loaded = 0
        try:
            entry_points = importlib.metadata.entry_points().select(group=group)
        except Exception:
            return 0

        for entry_point in entry_points:
            try:
                module = entry_point.load()
                self._register_module(module, source=f"entrypoint:{entry_point.name}", name=entry_point.name)
                loaded += 1
            except Exception as exc:
                logger.warning("Failed to load plugin entrypoint %s: %s", entry_point.name, exc)
        return loaded

    def reload_plugin(self, source: str) -> bool:
        for info in self.loaded.values():
            if info.source != source:
                continue
            module = sys.modules.get(info.module_name)
            if module is None:
                return False
            try:
                spec = getattr(module, '__spec__', None)
                if spec is None or spec.loader is None:
                    return False
                spec.loader.exec_module(module)
                logger.info("Reloaded plugin: %s", info.name)
                return True
            except Exception as exc:
                logger.warning("Failed to reload plugin %s: %s", info.name, exc)
                return False
        return False

    def _register_module(self, module: ModuleType, *, source: str, name: str) -> None:
        if hasattr(module, 'register'):
            module.register(self)

        extractor_names: List[str] = []
        strategy_names: List[str] = []

        for extractor_cls in getattr(module, 'EXTRACTORS', []):
            registry.register(extractor_cls, source=ExtractorSource.PLUGIN)
            extractor_names.append(getattr(extractor_cls, 'EXTRACTOR_NAME', extractor_cls.__name__))

        for strategy_cls in getattr(module, 'STRATEGIES', []):
            strategy_names.append(getattr(strategy_cls, 'NAME', strategy_cls.__name__))

        self.loaded[name] = PluginInfo(
            name=name,
            module_name=getattr(module, '__name__', name),
            source=source,
            extractors=extractor_names,
            strategies=strategy_names,
        )
        logger.info("Loaded plugin: %s", name)

    def register_extractor(self, extractor_cls: type, *, generic: bool = False) -> None:
        registry.register(extractor_cls, generic=generic, source=ExtractorSource.PLUGIN)

    def register_strategy(self, extractor_cls: type, strategy_cls: type, *, prepend: bool = False, fallback: bool = False) -> None:
        if hasattr(extractor_cls, 'register_strategy'):
            extractor_cls.register_strategy(strategy_cls, prepend=prepend, fallback=fallback)
