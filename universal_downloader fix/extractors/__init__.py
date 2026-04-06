"""Extractor bootstrap, registry access, and plugin loading helpers."""

from pathlib import Path
from typing import Any, List, Optional
import logging

from .base import registry, ExtractorBase, ExtractionError
from .plugins.loader import PluginManager
from .upgraded_loader import load_upgraded_extractors

logger = logging.getLogger(__name__)
plugin_manager = PluginManager()
_loaded_plugin_dirs: set[str] = set()
_loaded_upgraded_dirs: set[str] = set()
_entrypoints_loaded = False

# Import built-in extractors (order matters: specific first, generic last)
from . import hls
from . import jwplayer
from . import dooplay
from . import avtub
from . import nontondrama
from . import kuronime
from . import erome
from . import pubjav
from . import supjav
from . import vstream
from . import doodstream
from . import social
from . import generic

# yt-dlp wrapper (not registered in registry - called explicitly as fallback)
from . import ytdlp

# Try to import advanced extractor (optional)
try:
    from . import advanced
except ImportError:
    logger.debug("Advanced extractor not available (Playwright not installed)")


def _extractor_config(config: Any) -> Any:
    if config is None:
        return None
    if isinstance(config, dict):
        return config.get('extractor', config)
    return getattr(config, 'extractor', config)


def _config_list(config: Any, key: str, defaults: list[str]) -> list[str]:
    section = _extractor_config(config)
    value = None
    if isinstance(section, dict):
        value = section.get(key)
    elif section is not None:
        value = getattr(section, key, None)

    if value is None:
        value = defaults
    if isinstance(value, str):
        value = [value]
    return [str(item) for item in value if str(item).strip()]


def _default_plugin_dirs() -> list[str]:
    return [
        str(Path(__file__).parent / 'plugins'),
        str(Path.home() / '.universal_downloader' / 'plugins'),
    ]


def _default_upgraded_dirs() -> list[str]:
    return [str(Path(__file__).parent / 'upgraded')]


def bootstrap_extractors(
    config: Any = None,
    *,
    log_summary: bool = False,
) -> dict[str, Any]:
    """Load upgraded extractors and plugins into the shared registry."""
    global _entrypoints_loaded

    warnings: list[str] = []
    upgraded_loaded = 0
    plugin_loaded = 0

    for upgraded_dir in _config_list(config, 'upgraded_dirs', _default_upgraded_dirs()):
        resolved = str(Path(upgraded_dir).expanduser().resolve())
        if resolved in _loaded_upgraded_dirs:
            continue

        load_result = load_upgraded_extractors(base_dir=Path(resolved))
        for metadata in load_result.metadata_list:
            registry.register_metadata(metadata)
        upgraded_loaded += load_result.success_count
        _loaded_upgraded_dirs.add(resolved)

        for discovered, error_message in load_result.failed_modules:
            warnings.append(f"upgraded:{discovered.import_path}: {error_message}")

    new_plugin_dirs: list[str] = []
    for plugin_dir in _config_list(config, 'plugin_dirs', _default_plugin_dirs()):
        resolved = str(Path(plugin_dir).expanduser().resolve())
        if resolved in _loaded_plugin_dirs:
            continue
        _loaded_plugin_dirs.add(resolved)
        new_plugin_dirs.append(resolved)

    if new_plugin_dirs:
        plugin_loaded += plugin_manager.load_paths(new_plugin_dirs)

    if not _entrypoints_loaded:
        plugin_loaded += plugin_manager.load_entrypoints()
        _entrypoints_loaded = True

    result = {
        'registry_size': registry.size,
        'enabled_count': registry.enabled_count,
        'upgraded_loaded': upgraded_loaded,
        'plugin_loaded': plugin_loaded,
        'warnings': warnings,
        'conflict_count': len(registry.conflicts),
    }

    if log_summary:
        logger.info(registry.summary())
        if warnings:
            logger.warning("Bootstrap warnings:\n%s", "\n".join(warnings))

    return result


def load_plugins(plugin_dirs: Optional[List[str]] = None) -> int:
    """Load extractor plugins from plugin directories."""
    result = bootstrap_extractors({'extractor': {'plugin_dirs': plugin_dirs or _default_plugin_dirs()}})
    return int(result['plugin_loaded'])


def get_extractor(url: str, html: Optional[str] = None) -> Optional[type]:
    """Find the best extractor for a given URL."""
    return registry.find_extractor(url, html=html)


def list_extractors() -> List[type]:
    """List all available extractors."""
    return registry.get_all()


def list_extractor_metadata() -> list[Any]:
    """List rich extractor metadata records."""
    return registry.list_all()


def debug_extractor_resolution(url: str, html: Optional[str] = None) -> str:
    """Return a human-readable resolution report for a URL."""
    return registry.debug_resolution(url, html=html)


bootstrap_extractors()
