"""Configuration and CLI parsing."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] == value[-1:] and value[:1] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _parse_env_line(raw_line: str) -> tuple[str, str] | None:
    stripped = raw_line.strip()
    if not stripped or stripped.startswith("#") or "=" not in raw_line:
        return None
    key, value = raw_line.split("=", 1)
    return key.strip(), value.strip()


def _env_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int = 0) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_accounts(name: str) -> list[str]:
    raw = os.getenv(name, "")
    if not raw:
        return []
    normalized = raw.replace("\n", ",").replace(";", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


_load_env_file(ENV_FILE)


def _refresh_runtime_config() -> None:
    global API_ID, API_HASH, ACCOUNTS, DEFAULT_SESSIONS_DIR
    API_ID = _env_int("TG_API_ID")
    API_HASH = _env_str("TG_API_HASH")
    ACCOUNTS = _env_accounts("TG_ACCOUNTS")
    DEFAULT_SESSIONS_DIR = _env_str("TG_SESSIONS_DIR", "./sessions")


def _set_env_values(updates: dict[str, str]) -> None:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    output: list[str] = []
    updated_keys: set[str] = set()

    for raw_line in lines:
        parsed = _parse_env_line(raw_line)
        if not parsed:
            output.append(raw_line)
            continue

        key, _ = parsed
        if key in updates:
            output.append(f"{key}={updates[key]}")
            updated_keys.add(key)
        else:
            output.append(raw_line)

    for key, value in updates.items():
        if key not in updated_keys:
            output.append(f"{key}={value}")
        os.environ[key] = value

    ENV_FILE.write_text("\n".join(output) + "\n", encoding="utf-8")
    _refresh_runtime_config()


def normalize_account_phone(phone: str) -> str:
    value = (
        phone.strip()
        .replace(" ", "")
        .replace("-", "")
        .replace("(", "")
        .replace(")", "")
    )
    if value.startswith("00"):
        value = "+" + value[2:]
    return value


def _validate_account_phone(phone: str) -> None:
    if not phone:
        raise ValueError("Nomor akun tidak boleh kosong.")
    body = phone[1:] if phone.startswith("+") else phone
    if not body.isdigit():
        raise ValueError("Nomor akun tidak valid. Gunakan format seperti +628123456789.")


def get_configured_accounts() -> list[str]:
    return list(ACCOUNTS)


def save_configured_accounts(accounts: list[str]) -> list[str]:
    normalized_accounts: list[str] = []
    seen: set[str] = set()

    for account in accounts:
        normalized = normalize_account_phone(account)
        _validate_account_phone(normalized)
        if normalized not in seen:
            normalized_accounts.append(normalized)
            seen.add(normalized)

    _set_env_values({"TG_ACCOUNTS": ",".join(normalized_accounts)})
    return list(ACCOUNTS)


def add_configured_account(phone: str) -> list[str]:
    normalized = normalize_account_phone(phone)
    _validate_account_phone(normalized)
    accounts = get_configured_accounts()
    if normalized in accounts:
        raise ValueError("Nomor akun sudah ada di daftar.")
    return save_configured_accounts(accounts + [normalized])


def remove_configured_account(phone: str) -> list[str]:
    normalized = normalize_account_phone(phone)
    accounts = get_configured_accounts()
    if normalized not in accounts:
        raise ValueError("Nomor akun tidak ditemukan di daftar.")
    return save_configured_accounts([account for account in accounts if account != normalized])


API_ID = 0
API_HASH = ""
ACCOUNTS: list[str] = []
DEFAULT_SESSIONS_DIR = "./sessions"
_refresh_runtime_config()

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  PENGATURAN DEFAULT                                                 ║
# ╚══════════════════════════════════════════════════════════════════════╝

DEFAULT_TARGET = "me"
DEFAULT_MAX_SIZE_MB = 1900
DEFAULT_WORKERS = 3
DEFAULT_RETRIES = 5
DEFAULT_THUMB_SIZE = 720
DEFAULT_SPEED_LIMIT_MB = 0
DEFAULT_SORT = "name"
DEFAULT_PHOTO_MODE = "foto"


@dataclass
class AppConfig:
    folder: Path = field(default_factory=lambda: Path("."))
    workers: int = DEFAULT_WORKERS
    max_size_mb: int = DEFAULT_MAX_SIZE_MB
    retries: int = DEFAULT_RETRIES
    target: str = DEFAULT_TARGET
    sessions_dir: Path = field(default_factory=lambda: Path(DEFAULT_SESSIONS_DIR))
    cleanup: bool = False
    skip_uploaded: bool = True
    thumb_size: int = DEFAULT_THUMB_SIZE
    recursive: bool = False
    compress: bool = False
    speed_limit_mb: float = DEFAULT_SPEED_LIMIT_MB
    sort: str = DEFAULT_SORT
    caption: str = ""
    photo_mode: str = DEFAULT_PHOTO_MODE
    upload_mode: str = "besar"
    caption_per_subfolder: bool = False
    api_id: int = API_ID
    api_hash: str = API_HASH
    accounts: list[str] = field(default_factory=lambda: list(ACCOUNTS))

    @property
    def max_size_bytes(self) -> int:
        return self.max_size_mb * 1024 * 1024

    @property
    def speed_limit_bytes(self) -> int:
        if self.speed_limit_mb <= 0:
            return 0
        return int(self.speed_limit_mb * 1024 * 1024)

    @property
    def photo_as_document(self) -> bool:
        return self.photo_mode == "dokumen"

    def validate(self) -> None:
        if not self.folder.exists():
            raise SystemExit(f"Error: '{self.folder}' tidak ditemukan.")
        if not self.folder.is_file() and not self.folder.is_dir():
            raise SystemExit(f"Error: '{self.folder}' bukan file atau folder.")
        if not self.api_id or not self.api_hash:
            raise SystemExit("Error: TG_API_ID dan TG_API_HASH belum diisi di environment atau .env.")
        if not self.accounts:
            raise SystemExit("Error: TG_ACCOUNTS belum diisi di environment atau .env.")
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    @property
    def history_file(self) -> Path:
        return self.sessions_dir / ".upload_history.json"


def parse_args() -> AppConfig:
    p = argparse.ArgumentParser(
        description="Telegram Video Uploader v5",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Contoh:\n"
            "  python main.py --folder ./videos --upload-mode besar\n"
            "  python main.py --folder ./mixed --upload-mode kecil\n"
            "  python main.py --folder ./videos --sort smallest --cleanup\n\n"
            "Mode interaktif: python app.py"
        ),
    )
    p.add_argument("--folder", type=Path, required=True, help="Folder/file")
    p.add_argument("--api-id", type=int, default=API_ID)
    p.add_argument("--api-hash", type=str, default=API_HASH)
    p.add_argument("--account", dest="accounts", action="append",
                   help="Nomor akun Telegram. Ulangi flag ini untuk banyak akun.")
    p.add_argument("--target", type=str, default=DEFAULT_TARGET)
    p.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    p.add_argument("--max-size", type=int, default=DEFAULT_MAX_SIZE_MB, dest="max_size_mb")
    p.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    p.add_argument("--sessions-dir", type=Path, default=Path(DEFAULT_SESSIONS_DIR))
    p.add_argument("--cleanup", action="store_true")
    p.add_argument("--no-skip", action="store_true")
    p.add_argument("--recursive", action="store_true")
    p.add_argument("--compress", action="store_true")
    p.add_argument("--speed-limit", type=float, default=DEFAULT_SPEED_LIMIT_MB, dest="speed_limit_mb")
    p.add_argument("--thumb-size", type=int, default=DEFAULT_THUMB_SIZE)
    p.add_argument("--sort", type=str, default=DEFAULT_SORT,
                    choices=["name", "smallest", "largest", "newest", "oldest"])
    p.add_argument("--caption", type=str, default="")
    p.add_argument("--photo-mode", type=str, default=DEFAULT_PHOTO_MODE,
                    choices=["foto", "dokumen"])
    p.add_argument("--upload-mode", type=str, default="besar",
                    choices=["besar", "kecil"])
    p.add_argument(
        "--caption-per-subfolder",
        action="store_true",
        help="Mode kecil: proses tiap subfolder terpisah, caption akhir = nama subfolder.",
    )
    a = p.parse_args()
    return AppConfig(
        folder=a.folder, workers=a.workers, max_size_mb=a.max_size_mb,
        retries=a.retries, target=a.target, sessions_dir=a.sessions_dir,
        cleanup=a.cleanup, skip_uploaded=not a.no_skip, thumb_size=a.thumb_size,
        recursive=a.recursive, compress=a.compress, speed_limit_mb=a.speed_limit_mb,
        sort=a.sort, caption=a.caption, photo_mode=a.photo_mode,
        caption_per_subfolder=a.caption_per_subfolder,
        upload_mode=a.upload_mode, api_id=a.api_id, api_hash=a.api_hash,
        accounts=a.accounts or list(ACCOUNTS),
    )
