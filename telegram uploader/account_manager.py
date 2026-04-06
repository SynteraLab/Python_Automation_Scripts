"""Multi-account Telegram session manager."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable
from pathlib import Path
from typing import Any, cast

from telethon import TelegramClient
from telethon.errors import AuthKeyDuplicatedError, SessionPasswordNeededError

logger = logging.getLogger("tg_uploader")


class AccountManager:
    def __init__(self, api_id: int, api_hash: str, sessions_dir: Path, phones: list[str]) -> None:
        self.api_id = api_id
        self.api_hash = api_hash
        self.sessions_dir = sessions_dir
        self.phones = phones
        self.clients: list[TelegramClient] = []
        self._labels: list[str] = []

    async def initialize(self, max_clients: int | None = None) -> int:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        limit = max_clients or len(self.phones)
        for phone in self.phones:
            if len(self.clients) >= limit:
                break
            safe = self._safe_phone(phone)
            session_path = self.session_file(self.sessions_dir, phone)
            session_name = str(self.sessions_dir / safe)
            if session_path.exists():
                client, label = await self._load_session(session_name, phone)
            else:
                client, label = await self._first_login(session_name, phone)
            if client is not None:
                self.clients.append(client)
                self._labels.append(label)
        n = len(self.clients)
        if n == 0:
            logger.error("Tidak ada akun yang terhubung.")
        else:
            logger.info(f"✓ {n} akun siap")
        return n

    async def disconnect_all(self) -> None:
        for c in self.clients:
            try:
                await self._disconnect_client(c)
            except Exception:
                pass
        self.clients.clear()
        self._labels.clear()

    async def _disconnect_client(self, client: TelegramClient) -> None:
        result: Any = client.disconnect()
        if inspect.isawaitable(result):
            await cast(Awaitable[Any], result)

    async def _logout_client(self, client: TelegramClient) -> None:
        result: Any = client.log_out()
        if inspect.isawaitable(result):
            await cast(Awaitable[Any], result)

    @staticmethod
    def _safe_phone(phone: str) -> str:
        return (
            phone.replace("+", "")
            .replace(" ", "")
            .replace("-", "")
            .replace("(", "")
            .replace(")", "")
        )

    @classmethod
    def session_file(cls, sessions_dir: Path, phone: str) -> Path:
        return sessions_dir / f"{cls._safe_phone(phone)}.session"

    @classmethod
    def has_session(cls, sessions_dir: Path, phone: str) -> bool:
        return cls.session_file(sessions_dir, phone).exists()

    @classmethod
    def _session_related_files(cls, sessions_dir: Path, phone: str) -> list[Path]:
        base = cls.session_file(sessions_dir, phone)
        return [
            base,
            Path(f"{base}-journal"),
            Path(f"{base}-shm"),
            Path(f"{base}-wal"),
        ]

    @classmethod
    async def logout_account(
        cls, api_id: int, api_hash: str, sessions_dir: Path, phone: str,
    ) -> tuple[bool, str]:
        sessions_dir.mkdir(parents=True, exist_ok=True)
        safe = cls._safe_phone(phone)
        session_name = str(sessions_dir / safe)
        session_exists = cls.has_session(sessions_dir, phone)
        logged_out = False

        if session_exists:
            manager = cls(api_id, api_hash, sessions_dir, [phone])
            client = manager._create_client(session_name)
            try:
                await client.connect()
                await manager._logout_client(client)
                logged_out = True
            except Exception as exc:
                logger.warning(f"  Logout Telegram gagal {phone}: {exc}")
            finally:
                try:
                    await manager._disconnect_client(client)
                except Exception:
                    pass

        removed_files = cls.clear_session_files(sessions_dir, phone)

        if logged_out:
            return True, "Session Telegram ditutup dan file session dihapus."
        if removed_files > 0:
            return True, f"File session lokal dihapus ({removed_files} file)."
        return True, "Session lokal tidak ditemukan."

    @classmethod
    def clear_session_files(cls, sessions_dir: Path, phone: str) -> int:
        sessions_dir.mkdir(parents=True, exist_ok=True)
        removed_files = 0
        for path in cls._session_related_files(sessions_dir, phone):
            if path.exists():
                try:
                    path.unlink()
                    removed_files += 1
                except OSError:
                    pass
        return removed_files

    @classmethod
    def reset_account_session(cls, sessions_dir: Path, phone: str) -> tuple[bool, str]:
        removed_files = cls.clear_session_files(sessions_dir, phone)
        if removed_files > 0:
            return True, f"Session lokal dihapus ({removed_files} file). Login akan diminta lagi saat akun dipakai."
        return True, "Session lokal tidak ditemukan."

    @classmethod
    async def logout_all_accounts(
        cls, api_id: int, api_hash: str, sessions_dir: Path, phones: list[str],
    ) -> list[tuple[str, bool, str]]:
        results: list[tuple[str, bool, str]] = []
        seen: set[str] = set()
        for phone in phones:
            safe = cls._safe_phone(phone)
            if safe in seen:
                continue
            seen.add(safe)
            ok, message = await cls.logout_account(api_id, api_hash, sessions_dir, phone)
            results.append((phone, ok, message))
        return results

    def _create_client(self, session_name: str) -> TelegramClient:
        return TelegramClient(
            session_name,
            self.api_id,
            self.api_hash,
            connection_retries=10,
            retry_delay=2,
            auto_reconnect=True,
            request_retries=5,
            flood_sleep_threshold=60,
        )

    @staticmethod
    def _label_from_me(me: object) -> str:
        for attr in ("username", "phone", "id"):
            value = getattr(me, attr, None)
            if value:
                return str(value)
        return "unknown"

    def get_client(self, index: int) -> TelegramClient:
        return self.clients[index % len(self.clients)]

    def get_label(self, index: int) -> str:
        return self._labels[index % len(self._labels)]

    @property
    def count(self) -> int:
        return len(self.clients)

    def list_accounts(self) -> list[str]:
        return list(self._labels)

    async def ensure_connected(self, index: int) -> TelegramClient:
        client = self.get_client(index)
        if not client.is_connected():
            try:
                await client.connect()
            except Exception as exc:
                logger.error(f"  Reconnect gagal: {exc}")
        return client

    async def _load_session(self, session_name: str, phone: str) -> tuple[TelegramClient | None, str]:
        client = self._create_client(session_name)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                return await self._do_login(client, phone)
            me = await client.get_me()
            label = self._label_from_me(me)
            logger.info(f"  ✓ {label}")
            return client, label
        except AuthKeyDuplicatedError:
            logger.warning(f"  ✗ Duplicate key: {phone}")
        except Exception as exc:
            logger.warning(f"  ✗ Gagal load {phone}: {exc}")
        try:
            await self._disconnect_client(client)
        except Exception:
            pass
        return None, ""

    async def _prompt(self, prompt: str) -> str:
        return (await asyncio.to_thread(input, prompt)).strip()

    async def _first_login(self, session_name: str, phone: str) -> tuple[TelegramClient | None, str]:
        client = self._create_client(session_name)
        try:
            await client.connect()
            return await self._do_login(client, phone)
        except Exception as exc:
            logger.error(f"  ✗ Login gagal {phone}: {exc}")
            try:
                await self._disconnect_client(client)
            except Exception:
                pass
            return None, ""

    async def _do_login(self, client: TelegramClient, phone: str) -> tuple[TelegramClient | None, str]:
        try:
            await client.send_code_request(phone)
            print(f"\n  ┌─ Verifikasi {phone} ─────────────────────┐")
            code = await self._prompt("  │ Masukkan kode Telegram: ")
            print("  └─────────────────────────────────────────────┘")
            try:
                await client.sign_in(phone, code)
            except SessionPasswordNeededError:
                pw = await self._prompt(f"  Password 2FA untuk {phone}: ")
                await client.sign_in(password=pw)
            me = await client.get_me()
            label = self._label_from_me(me)
            logger.info(f"  ✓ Login berhasil: {label}")
            return client, label
        except Exception as exc:
            logger.error(f"  ✗ Verifikasi gagal: {exc}")
            try:
                await self._disconnect_client(client)
            except Exception:
                pass
            return None, ""
