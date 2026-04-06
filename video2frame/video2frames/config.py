"""
JSON-based configuration and named presets.

Default config lives at ``~/.video2frames/config.json``.
Presets are stored in ``~/.video2frames/presets/<name>.json``.
"""

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_CONFIG_DIR = Path.home() / ".video2frames"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.json"
PRESETS_DIR = DEFAULT_CONFIG_DIR / "presets"


@dataclass
class Config:
    """All tuneable settings for the extraction pipeline."""

    output_dir: str = "./output"
    format: str = "png"
    threads: int = 4
    compression_level: int = 3        # PNG 0-9 (lower → faster)
    batch_parallel: int = 2           # simultaneous videos in batch mode
    overwrite: bool = False
    validate_after: bool = True
    log_dir: str = str(DEFAULT_CONFIG_DIR / "logs")
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"

    # ── serialisation ─────────────────────────────────────────────────────
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Config":
        valid = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in valid})


class ConfigManager:
    """Load / save the global config and named presets."""

    def __init__(self, config_dir: Path = DEFAULT_CONFIG_DIR):
        self.config_dir = config_dir
        self.config_file = config_dir / "config.json"
        self.presets_dir = config_dir / "presets"
        self._ensure_dirs()

    # ── internals ─────────────────────────────────────────────────────────
    def _ensure_dirs(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.presets_dir.mkdir(parents=True, exist_ok=True)

    # ── global config ─────────────────────────────────────────────────────
    def load_config(self, path: Optional[Path] = None) -> Config:
        p = Path(path) if path else self.config_file
        if p.exists():
            try:
                return Config.from_dict(json.loads(p.read_text()))
            except Exception:
                return Config()
        return Config()

    def save_config(self, cfg: Config, path: Optional[Path] = None) -> None:
        p = Path(path) if path else self.config_file
        p.write_text(json.dumps(cfg.to_dict(), indent=2))

    # ── presets ───────────────────────────────────────────────────────────
    def save_preset(self, name: str, cfg: Config) -> None:
        (self.presets_dir / f"{name}.json").write_text(
            json.dumps(cfg.to_dict(), indent=2)
        )

    def load_preset(self, name: str) -> Optional[Config]:
        p = self.presets_dir / f"{name}.json"
        if p.exists():
            return Config.from_dict(json.loads(p.read_text()))
        return None

    def list_presets(self) -> List[str]:
        return sorted(f.stem for f in self.presets_dir.glob("*.json"))

    def delete_preset(self, name: str) -> bool:
        p = self.presets_dir / f"{name}.json"
        if p.exists():
            p.unlink()
            return True
        return False