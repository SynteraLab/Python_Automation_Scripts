"""
Built-in node types for the downloader workflow system.

Nodes:
- url_input: Single URL input
- url_list_input: Multiple URL input
- file_input: Read URLs from file
- smart_download: Download using fallback chain
- audio_download: Download audio only
- extract_info: Extract media info without downloading
- format_select: Select format from available formats
- conditional: Branch based on condition
- filter: Filter items by criteria
- log_output: Log/display results
- file_output: Save results to file
- delay: Wait between operations
- counter: Count processed items
"""

import asyncio
import time
from pathlib import Path
from typing import Any, Dict

from .base import Node, Port, PortType, NodeCategory
from .registry import node_registry


def _make_node(
    node_type: str,
    name: str,
    icon: str,
    category: NodeCategory,
    inputs: list,
    outputs: list,
    execute_fn=None,
    config: Dict[str, Any] = None,
    **kwargs,
) -> Node:
    """Helper to create a node with ports and execute function."""
    input_ports = [
        Port(
            name=p["name"],
            port_type=PortType(p.get("type", "any")),
            is_input=True,
            required=p.get("required", True),
            default_value=p.get("default"),
            description=p.get("description", ""),
        )
        for p in inputs
    ]

    output_ports = [
        Port(
            name=p["name"],
            port_type=PortType(p.get("type", "any")),
            is_input=False,
            description=p.get("description", ""),
        )
        for p in outputs
    ]

    node = Node(
        node_type=node_type,
        name=name,
        icon=icon,
        category=category,
        inputs=input_ports,
        outputs=output_ports,
        config=config or {},
        _execute_fn=execute_fn,
    )
    return node


# ── Execute Functions ──────────────────────────────────────

def _exec_url_input(inputs: dict, config: dict, context: dict) -> dict:
    """URL input node: provides a URL from config."""
    url = config.get("url", "") or inputs.get("url", "")
    return {"url": url}


def _exec_url_list_input(inputs: dict, config: dict, context: dict) -> dict:
    """URL list node: provides multiple URLs."""
    urls = config.get("urls", [])
    if isinstance(urls, str):
        urls = [u.strip() for u in urls.split("\n") if u.strip()]
    return {"urls": urls}


def _exec_file_input(inputs: dict, config: dict, context: dict) -> dict:
    """File input: read URLs from file."""
    filepath = config.get("filepath", "") or inputs.get("filepath", "")
    if not filepath or not Path(filepath).exists():
        return {"urls": []}

    with open(filepath) as f:
        urls = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    return {"urls": urls}


def _exec_smart_download(inputs: dict, config: dict, context: dict) -> dict:
    """Smart download: uses the existing fallback chain."""
    url = inputs.get("url", "")
    if not url:
        return {"success": False, "filepath": "", "error": "No URL provided"}

    app_config = context.get("config")
    if not app_config:
        return {"success": False, "filepath": "", "error": "No config in context"}

    # Import here to avoid circular imports
    from ..controllers.downloader import smart_download_headless

    result = smart_download_headless(
        url=url,
        config=app_config,
        quality=config.get("quality", "best"),
        audio_only=config.get("audio_only", False),
    )

    return {
        "success": result.get("success", False),
        "filepath": result.get("filepath", ""),
        "error": result.get("error", ""),
    }


def _exec_audio_download(inputs: dict, config: dict, context: dict) -> dict:
    """Audio-only download."""
    config["audio_only"] = True
    return _exec_smart_download(inputs, config, context)


def _exec_extract_info(inputs: dict, config: dict, context: dict) -> dict:
    """Extract media info without downloading."""
    url = inputs.get("url", "")
    if not url:
        return {"media_info": None, "title": "", "format_count": 0}

    app_config = context.get("config")
    if not app_config:
        return {"media_info": None, "title": "", "format_count": 0}

    from utils.network import SessionManager
    from extractors.base import registry

    session = SessionManager(
        user_agent=app_config.extractor.user_agent,
        proxy=app_config.proxy.to_dict(),
        cookies_file=app_config.cookies_file,
        cookies_from_browser=app_config.cookies_from_browser,
    )

    try:
        extractor_class = registry.find_extractor(url)
        if extractor_class:
            ext = extractor_class(session, config=vars(app_config))
            info = ext.extract(url)
            return {
                "media_info": info,
                "title": info.title if info else "",
                "format_count": len(info.formats) if info else 0,
            }
    except Exception as e:
        return {"media_info": None, "title": "", "format_count": 0, "error": str(e)}
    finally:
        session.close()

    return {"media_info": None, "title": "", "format_count": 0}


def _exec_conditional(inputs: dict, config: dict, context: dict) -> dict:
    """Conditional node: route based on boolean input."""
    condition = inputs.get("condition", False)
    value = inputs.get("value")

    if condition:
        return {"true_out": value, "false_out": None}
    else:
        return {"true_out": None, "false_out": value}


def _exec_filter(inputs: dict, config: dict, context: dict) -> dict:
    """Filter node: filter list items."""
    items = inputs.get("items", [])
    if not isinstance(items, list):
        items = [items] if items else []

    field_name = config.get("field", "")
    operator = config.get("operator", "equals")
    match_value = config.get("value", "")

    def _matches(item):
        if isinstance(item, dict) and field_name:
            val = str(item.get(field_name, ""))
        else:
            val = str(item)

        if operator == "equals":
            return val == match_value
        elif operator == "contains":
            return match_value in val
        elif operator == "not_equals":
            return val != match_value
        elif operator == "not_empty":
            return bool(val)
        return True

    filtered = [item for item in items if _matches(item)]
    rejected = [item for item in items if not _matches(item)]

    return {"filtered": filtered, "rejected": rejected, "count": len(filtered)}


def _exec_log_output(inputs: dict, config: dict, context: dict) -> dict:
    """Log output: log message to TUI log panel."""
    message = inputs.get("message", "")
    level = config.get("level", "info")

    from ..logging_ import log_manager

    log_fn = getattr(log_manager, level, log_manager.info)
    log_fn(str(message), source="workflow")

    return {"logged": True}


def _exec_delay(inputs: dict, config: dict, context: dict) -> dict:
    """Delay node: wait for specified seconds."""
    seconds = config.get("seconds", 1.0)
    time.sleep(float(seconds))
    passthrough = inputs.get("input")
    return {"output": passthrough}


def _exec_counter(inputs: dict, config: dict, context: dict) -> dict:
    """Counter node: count items passing through."""
    item = inputs.get("item")
    current = config.get("_count", 0) + 1
    config["_count"] = current
    return {"item": item, "count": current}


# ── Registration ───────────────────────────────────────────

def register_builtin_nodes() -> None:
    """Register all built-in node types."""

    # Input nodes
    node_registry.register(
        type_name="url_input",
        factory=lambda **kw: _make_node(
            "url_input", "URL Input", "🔗", NodeCategory.INPUT,
            inputs=[],
            outputs=[{"name": "url", "type": "url", "description": "Output URL"}],
            execute_fn=_exec_url_input,
            config={"url": ""},
        ),
        display_name="URL Input",
        description="Single URL source",
        category=NodeCategory.INPUT,
        icon="🔗",
        tags=["input", "url", "source"],
    )

    node_registry.register(
        type_name="url_list_input",
        factory=lambda **kw: _make_node(
            "url_list_input", "URL List", "📋", NodeCategory.INPUT,
            inputs=[],
            outputs=[{"name": "urls", "type": "url_list", "description": "URL list"}],
            execute_fn=_exec_url_list_input,
            config={"urls": []},
        ),
        display_name="URL List Input",
        description="Multiple URLs source",
        category=NodeCategory.INPUT,
        icon="📋",
        tags=["input", "url", "batch", "list"],
    )

    node_registry.register(
        type_name="file_input",
        factory=lambda **kw: _make_node(
            "file_input", "File Input", "📄", NodeCategory.INPUT,
            inputs=[{"name": "filepath", "type": "file_path", "required": False}],
            outputs=[{"name": "urls", "type": "url_list"}],
            execute_fn=_exec_file_input,
            config={"filepath": ""},
        ),
        display_name="File Input",
        description="Read URLs from text file",
        category=NodeCategory.INPUT,
        icon="📄",
        tags=["input", "file", "batch"],
    )

    # Download nodes
    node_registry.register(
        type_name="smart_download",
        factory=lambda **kw: _make_node(
            "smart_download", "Smart Download", "🎯", NodeCategory.DOWNLOAD,
            inputs=[{"name": "url", "type": "url", "description": "URL to download"}],
            outputs=[
                {"name": "success", "type": "boolean"},
                {"name": "filepath", "type": "file_path"},
                {"name": "error", "type": "text"},
            ],
            execute_fn=_exec_smart_download,
            config={"quality": "best", "audio_only": False},
        ),
        display_name="Smart Download",
        description="Download with auto-detect (custom → yt-dlp → generic)",
        category=NodeCategory.DOWNLOAD,
        icon="🎯",
        tags=["download", "smart", "auto"],
    )

    node_registry.register(
        type_name="audio_download",
        factory=lambda **kw: _make_node(
            "audio_download", "Audio Download", "🎵", NodeCategory.DOWNLOAD,
            inputs=[{"name": "url", "type": "url"}],
            outputs=[
                {"name": "success", "type": "boolean"},
                {"name": "filepath", "type": "file_path"},
                {"name": "error", "type": "text"},
            ],
            execute_fn=_exec_audio_download,
            config={"quality": "best"},
        ),
        display_name="Audio Download",
        description="Download audio only (MP3)",
        category=NodeCategory.DOWNLOAD,
        icon="🎵",
        tags=["download", "audio", "mp3"],
    )

    # Processing nodes
    node_registry.register(
        type_name="extract_info",
        factory=lambda **kw: _make_node(
            "extract_info", "Extract Info", "ℹ️", NodeCategory.PROCESSING,
            inputs=[{"name": "url", "type": "url"}],
            outputs=[
                {"name": "media_info", "type": "media_info"},
                {"name": "title", "type": "text"},
                {"name": "format_count", "type": "number"},
            ],
            execute_fn=_exec_extract_info,
        ),
        display_name="Extract Info",
        description="Extract media metadata without downloading",
        category=NodeCategory.PROCESSING,
        icon="ℹ️",
        tags=["extract", "info", "metadata"],
    )

    node_registry.register(
        type_name="conditional",
        factory=lambda **kw: _make_node(
            "conditional", "Conditional", "🔀", NodeCategory.CONDITIONAL,
            inputs=[
                {"name": "condition", "type": "boolean"},
                {"name": "value", "type": "any", "required": False},
            ],
            outputs=[
                {"name": "true_out", "type": "any"},
                {"name": "false_out", "type": "any"},
            ],
            execute_fn=_exec_conditional,
        ),
        display_name="Conditional",
        description="Route data based on boolean condition",
        category=NodeCategory.CONDITIONAL,
        icon="🔀",
        tags=["conditional", "branch", "if"],
    )

    node_registry.register(
        type_name="filter",
        factory=lambda **kw: _make_node(
            "filter", "Filter", "🔍", NodeCategory.PROCESSING,
            inputs=[{"name": "items", "type": "list"}],
            outputs=[
                {"name": "filtered", "type": "list"},
                {"name": "rejected", "type": "list"},
                {"name": "count", "type": "number"},
            ],
            execute_fn=_exec_filter,
            config={"field": "", "operator": "equals", "value": ""},
        ),
        display_name="Filter",
        description="Filter list items by criteria",
        category=NodeCategory.PROCESSING,
        icon="🔍",
        tags=["filter", "process", "list"],
    )

    # Utility nodes
    node_registry.register(
        type_name="delay",
        factory=lambda **kw: _make_node(
            "delay", "Delay", "⏱️", NodeCategory.UTILITY,
            inputs=[{"name": "input", "type": "any", "required": False}],
            outputs=[{"name": "output", "type": "any"}],
            execute_fn=_exec_delay,
            config={"seconds": 1.0},
        ),
        display_name="Delay",
        description="Wait for specified duration",
        category=NodeCategory.UTILITY,
        icon="⏱️",
        tags=["utility", "delay", "wait", "timer"],
    )

    node_registry.register(
        type_name="counter",
        factory=lambda **kw: _make_node(
            "counter", "Counter", "🔢", NodeCategory.UTILITY,
            inputs=[{"name": "item", "type": "any"}],
            outputs=[
                {"name": "item", "type": "any"},
                {"name": "count", "type": "number"},
            ],
            execute_fn=_exec_counter,
            config={"_count": 0},
        ),
        display_name="Counter",
        description="Count items passing through",
        category=NodeCategory.UTILITY,
        icon="🔢",
        tags=["utility", "counter", "count"],
    )

    # Output nodes
    node_registry.register(
        type_name="log_output",
        factory=lambda **kw: _make_node(
            "log_output", "Log Output", "📝", NodeCategory.OUTPUT,
            inputs=[{"name": "message", "type": "any"}],
            outputs=[{"name": "logged", "type": "boolean"}],
            execute_fn=_exec_log_output,
            config={"level": "info"},
        ),
        display_name="Log Output",
        description="Log message to TUI log panel",
        category=NodeCategory.OUTPUT,
        icon="📝",
        tags=["output", "log", "display"],
    )