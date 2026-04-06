"""ASS subtitle style presets."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class AssStyle:
    """Maps to an ASS V4+ Style line."""
    name: str = "Default"
    fontname: str = "Arial"
    fontsize: int = 20
    primary_color: str = "&H00FFFFFF"   # white  (AABBGGRR)
    secondary_color: str = "&H000000FF"
    outline_color: str = "&H00000000"   # black
    back_color: str = "&H80000000"      # semi-transparent
    bold: bool = False
    italic: bool = False
    border_style: int = 1               # 1=outline+shadow, 3=opaque box
    outline: float = 2.0
    shadow: float = 1.0
    alignment: int = 2                  # bottom-centre
    margin_l: int = 10
    margin_r: int = 10
    margin_v: int = 20
    scale_x: int = 100
    scale_y: int = 100
    spacing: float = 0.0
    encoding: int = 1


# ── Pre-built presets ────────────────────────────────────────────

NETFLIX_STYLE = AssStyle(
    name="Netflix",
    fontname="Netflix Sans",
    fontsize=22,
    primary_color="&H00FFFFFF",
    outline_color="&H00000000",
    back_color="&H80000000",
    bold=False,
    border_style=1,
    outline=2.0,
    shadow=0.75,
    alignment=2,
    margin_v=30,
)

MINIMAL_STYLE = AssStyle(
    name="Minimal",
    fontname="Helvetica Neue",
    fontsize=18,
    primary_color="&H00FFFFFF",
    outline_color="&H40000000",
    back_color="&H00000000",
    bold=False,
    border_style=1,
    outline=1.0,
    shadow=0.0,
    alignment=2,
    margin_v=25,
)

CUSTOM_STYLE = AssStyle(
    name="Custom",
    fontname="Arial",
    fontsize=24,
    primary_color="&H0000FFFF",  # yellow
    outline_color="&H00000000",
    back_color="&H80000000",
    bold=True,
    border_style=3,
    outline=0.0,
    shadow=0.0,
    alignment=2,
    margin_v=20,
)

STYLE_PRESETS: Dict[str, AssStyle] = {
    "netflix": NETFLIX_STYLE,
    "minimal": MINIMAL_STYLE,
    "custom": CUSTOM_STYLE,
}