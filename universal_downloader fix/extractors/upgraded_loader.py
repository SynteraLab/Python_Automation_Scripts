# extractors/upgraded_loader.py
"""
Upgraded extractor discovery and loading.

Scans the extractors/upgraded/ directory tree, discovers extractor
modules, imports them, finds ExtractorBase subclasses, and produces
ExtractorMetadata objects ready for registry registration.

Supported discovery patterns:
  Pattern 1: extractors/upgraded/foo.py
             → importlib import of 'extractors.upgraded.foo'
  Pattern 2: extractors/upgraded/bar/__init__.py
             → importlib import of 'extractors.upgraded.bar'
  Pattern 3: extractors/upgraded/baz/extractor.py
             → importlib import of 'extractors.upgraded.baz.extractor'

Design principles:
  - Never crash the application if an upgraded extractor fails to load
  - Log all discovery steps for debug traceability
  - Produce clean ExtractorMetadata with source=UPGRADED
  - Support gradual migration: upgraded extractors can coexist with builtins
"""

from __future__ import annotations

import importlib
import inspect
import logging
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Type

from extractors.registry_models import (
    ExtractorMetadata,
    ExtractorSource,
    default_priority_for_source,
)

from extractors.base import ExtractorBase

logger = logging.getLogger(__name__)


# ================================================================
# Discovery Data Structures
# ================================================================

class DiscoveredModule:
    """
    Represents a discovered upgraded extractor module before import.
    """

    __slots__ = ("file_path", "import_path", "pattern", "package_name")

    def __init__(
        self,
        file_path: Path,
        import_path: str,
        pattern: str,
        package_name: str,
    ) -> None:
        self.file_path = file_path
        self.import_path = import_path
        self.pattern = pattern
        self.package_name = package_name

    def __repr__(self) -> str:
        return (
            f"<DiscoveredModule "
            f"import={self.import_path!r} "
            f"pattern={self.pattern!r} "
            f"file={self.file_path}>"
        )


class LoadResult:
    """
    Result of loading upgraded extractors. Contains both successes
    and failures for diagnostic purposes.
    """

    def __init__(self) -> None:
        self.metadata_list: List[ExtractorMetadata] = []
        self.discovered_modules: List[DiscoveredModule] = []
        self.failed_modules: List[Tuple[DiscoveredModule, str]] = []
        self.skipped_files: List[Tuple[Path, str]] = []
        self.extractor_count: int = 0

    @property
    def success_count(self) -> int:
        return len(self.metadata_list)

    @property
    def failure_count(self) -> int:
        return len(self.failed_modules)

    @property
    def module_count(self) -> int:
        return len(self.discovered_modules)

    def summary(self) -> str:
        """Human-readable summary of the load operation."""
        lines = [
            "--- Upgraded Loader Summary ---",
            f"Modules discovered : {self.module_count}",
            f"Modules failed     : {self.failure_count}",
            f"Extractors found   : {self.extractor_count}",
            f"Metadata produced  : {self.success_count}",
        ]

        if self.failed_modules:
            lines.append("")
            lines.append("Failed modules:")
            for dm, error_msg in self.failed_modules:
                lines.append(f"  ✗ {dm.import_path}: {error_msg}")

        if self.skipped_files:
            lines.append("")
            lines.append("Skipped files:")
            for path, reason in self.skipped_files:
                lines.append(f"  - {path}: {reason}")

        lines.append("-------------------------------")
        return "\n".join(lines)


# ================================================================
# Default Base Directory Resolution
# ================================================================

def _default_upgraded_dir() -> Path:
    """
    Determine the default path for the upgraded extractors directory.

    Resolves relative to this module's location:
      this file: extractors/upgraded_loader.py
      target:    extractors/upgraded/

    # ADAPTATION POINT
    # If the real repository stores upgraded extractors elsewhere,
    # override this by passing base_dir explicitly to
    # load_upgraded_extractors().
    """
    this_dir = Path(__file__).resolve().parent
    upgraded_dir = this_dir / "upgraded"
    return upgraded_dir


def _ensure_package_search_path(base_import_path: str, base_dir: Path) -> None:
    """Allow upgraded modules to be discovered from external directories."""

    try:
        package = importlib.import_module(base_import_path)
    except ImportError:
        return

    package_path = getattr(package, "__path__", None)
    if package_path is None:
        return

    base_dir_str = str(base_dir)
    if base_dir_str not in package_path:
        package_path.append(base_dir_str)
        logger.debug("Added upgraded search path %s to package %s", base_dir, base_import_path)


# ================================================================
# Module Discovery
# ================================================================

# Files and directories to always skip during discovery.
_SKIP_NAMES: Set[str] = {
    "__pycache__",
    ".git",
    ".svn",
    ".hg",
    ".DS_Store",
    "thumbs.db",
}


def discover_upgraded_modules(
    base_dir: Optional[Path] = None,
    *,
    base_import_path: str = "extractors.upgraded",
) -> List[DiscoveredModule]:
    """
    Scan the upgraded extractors directory and find importable modules.

    Supports three discovery patterns:
      Pattern 1 — Single-file module:
                  upgraded/foo.py → extractors.upgraded.foo
      Pattern 2 — Package with __init__.py:
                  upgraded/bar/__init__.py → extractors.upgraded.bar
      Pattern 3 — Package with extractor.py:
                  upgraded/baz/extractor.py → extractors.upgraded.baz.extractor

    Args:
        base_dir: Path to the upgraded/ directory.
                  Defaults to extractors/upgraded/ relative to this module.
        base_import_path: Dotted import path prefix for the upgraded package.

    Returns:
        List of DiscoveredModule objects, sorted by import path
        for deterministic ordering.
    """
    if base_dir is None:
        base_dir = _default_upgraded_dir()

    base_dir = Path(base_dir).resolve()

    if not base_dir.exists():
        logger.debug(
            "Upgraded extractors directory does not exist: %s — skipping",
            base_dir,
        )
        return []

    if not base_dir.is_dir():
        logger.warning(
            "Upgraded extractors path is not a directory: %s — skipping",
            base_dir,
        )
        return []

    discovered: List[DiscoveredModule] = []
    seen_import_paths: Set[str] = set()

    logger.debug("Scanning for upgraded extractors in: %s", base_dir)

    for entry in sorted(base_dir.iterdir()):
        # Skip hidden files and known non-module entries
        if entry.name.startswith((".", "_")) and entry.name != "__init__.py":
            continue
        if entry.name.lower() in _SKIP_NAMES:
            continue

        # ----- Pattern 1: single .py file -----
        if entry.is_file() and entry.suffix == ".py":
            if entry.name == "__init__.py":
                # Skip the package __init__.py itself
                continue

            module_name = entry.stem
            import_path = f"{base_import_path}.{module_name}"

            if import_path not in seen_import_paths:
                seen_import_paths.add(import_path)
                discovered.append(DiscoveredModule(
                    file_path=entry,
                    import_path=import_path,
                    pattern="single_file",
                    package_name=module_name,
                ))
                logger.debug(
                    "Discovered upgraded module (pattern 1 — single file): %s → %s",
                    entry.name,
                    import_path,
                )

        # ----- Pattern 2 & 3: subdirectory -----
        elif entry.is_dir():
            if entry.name.lower() in _SKIP_NAMES:
                continue

            subdir_name = entry.name
            init_path = entry / "__init__.py"
            extractor_path = entry / "extractor.py"

            # Pattern 2: __init__.py in the subdirectory
            if init_path.is_file():
                import_path = f"{base_import_path}.{subdir_name}"
                if import_path not in seen_import_paths:
                    seen_import_paths.add(import_path)
                    discovered.append(DiscoveredModule(
                        file_path=init_path,
                        import_path=import_path,
                        pattern="package_init",
                        package_name=subdir_name,
                    ))
                    logger.debug(
                        "Discovered upgraded module (pattern 2 — package __init__): "
                        "%s/ → %s",
                        subdir_name,
                        import_path,
                    )

            # Pattern 3: extractor.py in the subdirectory
            # (loaded IN ADDITION to __init__.py if both exist,
            #  but only if __init__.py didn't already define extractors)
            if extractor_path.is_file():
                import_path = f"{base_import_path}.{subdir_name}.extractor"
                if import_path not in seen_import_paths:
                    seen_import_paths.add(import_path)
                    discovered.append(DiscoveredModule(
                        file_path=extractor_path,
                        import_path=import_path,
                        pattern="package_extractor",
                        package_name=subdir_name,
                    ))
                    logger.debug(
                        "Discovered upgraded module (pattern 3 — extractor.py): "
                        "%s/extractor.py → %s",
                        subdir_name,
                        import_path,
                    )

    # Sort for deterministic ordering
    discovered.sort(key=lambda dm: dm.import_path)

    logger.debug(
        "Discovery complete: found %d importable module(s) in %s",
        len(discovered),
        base_dir,
    )

    return discovered


# ================================================================
# Module Import + Extractor Class Extraction
# ================================================================

def _is_extractor_class(
    obj: Any,
    module: Any,
    *,
    base_class: Type[Any] = ExtractorBase,
) -> bool:
    """
    Determine if an object is a valid extractor class defined in the
    given module.

    Criteria:
      1. Is a class (not instance, function, etc.)
      2. Is a subclass of base_class
      3. Is NOT base_class itself
      4. Is defined in this module (not imported from elsewhere)
      5. Does not have a name starting with underscore (private)
      6. Is not marked as abstract (no __abstract__ = True)

    Args:
        obj: The object to check.
        module: The module the object was found in.
        base_class: The base extractor class to check against.

    Returns:
        True if the object is a valid extractor class.
    """
    if not isinstance(obj, type):
        return False

    if not issubclass(obj, base_class):
        return False

    if obj is base_class:
        return False

    # Must be defined in this module (not imported)
    obj_module = getattr(obj, "__module__", None)
    if obj_module != module.__name__:
        return False

    # Skip private classes
    if obj.__name__.startswith("_"):
        return False

    # Skip explicitly abstract classes
    if getattr(obj, "__abstract__", False):
        return False

    # Skip classes that declare themselves as base/mixin only
    if getattr(obj, "_IS_BASE_CLASS", False):
        return False

    return True


def load_extractor_classes_from_module(
    import_path: str,
    *,
    base_class: Type[Any] = ExtractorBase,
) -> List[Type[Any]]:
    """
    Import a module by dotted path and extract all extractor classes
    defined in it.

    Args:
        import_path: Dotted import path (e.g. 'extractors.upgraded.youtube').
        base_class: The base class to filter against.

    Returns:
        List of extractor classes found in the module.

    Raises:
        ImportError: If the module cannot be imported.
        Exception: Any exception raised during module import.
    """
    logger.debug("Importing module: %s", import_path)
    module = importlib.import_module(import_path)

    classes: List[Type[Any]] = []
    for attr_name in dir(module):
        obj = getattr(module, attr_name)
        if _is_extractor_class(obj, module, base_class=base_class):
            classes.append(obj)
            logger.debug(
                "Found extractor class: %s.%s",
                import_path,
                obj.__name__,
            )

    return classes


# ================================================================
# Metadata Builder
# ================================================================

def _build_metadata_for_class(
    cls: Type[Any],
    module_import_path: str,
    package_name: str,
) -> ExtractorMetadata:
    """
    Build an ExtractorMetadata for an upgraded extractor class.

    Reads class attributes, applies upgraded defaults.

    Args:
        cls: The extractor class.
        module_import_path: Dotted import path of the module.
        package_name: The upgraded package name (directory/file name).

    Returns:
        ExtractorMetadata with source=UPGRADED.
    """
    # --- Name ---
    name = getattr(cls, "EXTRACTOR_NAME", "") or ""
    name = name.strip()
    if not name:
        # Derive from class name
        raw = cls.__name__
        for suffix in ("Extractor", "IE", "Provider", "Handler"):
            if raw.endswith(suffix) and len(raw) > len(suffix):
                raw = raw[: -len(suffix)]
                break
        name = raw.lower().strip("_-")

    # --- Priority ---
    cls_priority = getattr(cls, "PRIORITY", 0)
    if cls_priority and cls_priority > 0:
        priority = cls_priority
    else:
        priority = default_priority_for_source(ExtractorSource.UPGRADED)

    # --- Generic ---
    is_generic = bool(getattr(cls, "IS_GENERIC", False))

    # --- Replaces ---
    cls_replaces = getattr(cls, "REPLACES", [])
    replaces = [r.lower().strip() for r in cls_replaces if r.strip()]

    # --- Version ---
    version = getattr(cls, "EXTRACTOR_VERSION", None)

    # --- Enabled ---
    # Upgraded extractors can opt-out by setting ENABLED = False
    enabled = bool(getattr(cls, "ENABLED", True))

    return ExtractorMetadata(
        name=name.lower(),
        cls=cls,
        source=ExtractorSource.UPGRADED,
        priority=priority,
        is_generic=is_generic,
        replaces=replaces,
        enabled=enabled,
        module_path=module_import_path,
        version=version,
    )


# ================================================================
# Top-Level Load Function
# ================================================================

def load_upgraded_extractors(
    base_dir: Optional[Path] = None,
    *,
    base_import_path: str = "extractors.upgraded",
    base_class: Type[Any] = ExtractorBase,
) -> LoadResult:
    """
    Discover, import, and build metadata for all upgraded extractors.

    This is the main entry point for the upgraded loader.
    Called during application bootstrap.

    Flow:
      1. Discover importable modules in the upgraded directory.
      2. Import each module safely (catching exceptions).
      3. Find extractor classes in each module.
      4. Build ExtractorMetadata for each class.
      5. Return LoadResult with all metadata and diagnostics.

    Args:
        base_dir: Path to the upgraded/ directory.
                  Defaults to extractors/upgraded/ relative to this module.
        base_import_path: Dotted import path prefix.
        base_class: Base class to filter extractor classes against.

    Returns:
        LoadResult containing metadata_list and diagnostic information.
    """
    result = LoadResult()

    # --- Step 1: Discover ---
    if base_dir is not None:
        base_dir = Path(base_dir).resolve()
        _ensure_package_search_path(base_import_path, base_dir)

    discovered = discover_upgraded_modules(
        base_dir=base_dir,
        base_import_path=base_import_path,
    )
    result.discovered_modules = discovered

    if not discovered:
        logger.debug("No upgraded extractor modules discovered")
        return result

    logger.info(
        "Discovered %d upgraded extractor module(s), loading...",
        len(discovered),
    )

    # Track seen extractor classes to avoid duplicates
    # (can happen if Pattern 2 and Pattern 3 both find the same class)
    seen_classes: Set[Type[Any]] = set()

    # --- Step 2–4: Import, find, build ---
    for dm in discovered:
        try:
            classes = load_extractor_classes_from_module(
                dm.import_path,
                base_class=base_class,
            )
        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            result.failed_modules.append((dm, error_msg))
            logger.error(
                "Failed to import upgraded module '%s': %s\n%s",
                dm.import_path,
                error_msg,
                traceback.format_exc(),
            )
            continue

        if not classes:
            logger.debug(
                "Module '%s' imported but no extractor classes found",
                dm.import_path,
            )
            continue

        for cls in classes:
            if cls in seen_classes:
                logger.debug(
                    "Skipping duplicate extractor class %s.%s "
                    "(already found via another pattern)",
                    dm.import_path,
                    cls.__name__,
                )
                continue

            seen_classes.add(cls)
            result.extractor_count += 1

            try:
                metadata = _build_metadata_for_class(
                    cls,
                    module_import_path=dm.import_path,
                    package_name=dm.package_name,
                )
                result.metadata_list.append(metadata)
                logger.debug(
                    "Built metadata for upgraded extractor: %s (%s) from %s",
                    metadata.name,
                    cls.__name__,
                    dm.import_path,
                )
            except Exception as exc:
                error_msg = (
                    f"Failed to build metadata for {cls.__name__}: "
                    f"{type(exc).__name__}: {exc}"
                )
                result.failed_modules.append((dm, error_msg))
                logger.error(
                    "Failed to build metadata for %s.%s: %s",
                    dm.import_path,
                    cls.__name__,
                    exc,
                )

    # --- Summary logging ---
    logger.info(
        "Upgraded loader complete: %d module(s), %d extractor(s), %d failure(s)",
        result.module_count,
        result.success_count,
        result.failure_count,
    )

    if result.failed_modules:
        logger.warning(
            "Some upgraded modules failed to load:\n%s",
            "\n".join(
                f"  ✗ {dm.import_path}: {err}"
                for dm, err in result.failed_modules
            ),
        )

    return result


# ================================================================
# Utility: Reload upgraded extractors
# ================================================================

def reload_upgraded_extractors(
    base_dir: Optional[Path] = None,
    *,
    base_import_path: str = "extractors.upgraded",
    base_class: Type[Any] = ExtractorBase,
) -> LoadResult:
    """
    Reload upgraded extractors by first removing their modules
    from sys.modules, then running discovery and import again.

    Useful for development and hot-reload scenarios.

    # ADAPTATION POINT
    # This function modifies sys.modules. Use with caution in
    # production. Primarily intended for development and testing.

    Args:
        base_dir: Path to the upgraded/ directory.
        base_import_path: Dotted import path prefix.
        base_class: Base class filter.

    Returns:
        Fresh LoadResult.
    """
    # Remove all previously imported upgraded modules from sys.modules
    to_remove = [
        key for key in sys.modules
        if key.startswith(base_import_path)
    ]
    for key in to_remove:
        del sys.modules[key]
        logger.debug("Removed '%s' from sys.modules for reload", key)

    logger.info("Reloading upgraded extractors...")
    return load_upgraded_extractors(
        base_dir=base_dir,
        base_import_path=base_import_path,
        base_class=base_class,
    )
