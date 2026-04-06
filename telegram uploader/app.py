#!/usr/bin/env python3
"""Telegram Video Uploader v5 — Interactive Mode."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from config import (
    AppConfig, API_ID, API_HASH,
    DEFAULT_TARGET, DEFAULT_MAX_SIZE_MB, DEFAULT_WORKERS,
    DEFAULT_RETRIES, DEFAULT_SESSIONS_DIR, DEFAULT_THUMB_SIZE,
    DEFAULT_SPEED_LIMIT_MB, DEFAULT_SORT, DEFAULT_PHOTO_MODE,
    add_configured_account, get_configured_accounts, remove_configured_account,
)
from account_manager import AccountManager
from queue_manager import QueueManager, run_small_per_subfolder
from utils import setup_logging, has_ffmpeg, has_ffprobe, logger


# ═══════════════════════════════════════════════════════════════════════
# Session settings
# ═══════════════════════════════════════════════════════════════════════

class Settings:
    def __init__(self) -> None:
        self.api_id: int = API_ID
        self.api_hash: str = API_HASH
        self.accounts: list[str] = get_configured_accounts()
        self.target: str = DEFAULT_TARGET
        self.workers: int = DEFAULT_WORKERS
        self.max_size_mb: int = DEFAULT_MAX_SIZE_MB
        self.retries: int = DEFAULT_RETRIES
        self.sessions_dir: str = DEFAULT_SESSIONS_DIR
        self.thumb_size: int = DEFAULT_THUMB_SIZE
        self.speed_limit_mb: float = float(DEFAULT_SPEED_LIMIT_MB)
        self.sort: str = DEFAULT_SORT
        self.cleanup: bool = False
        self.skip_uploaded: bool = True
        self.compress: bool = False
        self.photo_mode: str = DEFAULT_PHOTO_MODE
        self.caption_per_subfolder: bool = False

    def to_config(
        self, folder: Path, caption: str, upload_mode: str, recursive: bool,
    ) -> AppConfig:
        return AppConfig(
            folder=folder,
            workers=self.workers,
            max_size_mb=self.max_size_mb,
            retries=self.retries,
            target=self.target,
            sessions_dir=Path(self.sessions_dir),
            cleanup=self.cleanup,
            skip_uploaded=self.skip_uploaded,
            thumb_size=self.thumb_size,
            recursive=recursive,
            compress=self.compress,
            speed_limit_mb=self.speed_limit_mb,
            sort=self.sort,
            caption=caption,
            photo_mode=self.photo_mode,
            upload_mode=upload_mode,
            caption_per_subfolder=self.caption_per_subfolder,
            api_id=self.api_id,
            api_hash=self.api_hash,
            accounts=list(self.accounts),
        )


# ═══════════════════════════════════════════════════════════════════════
# UI helpers
# ═══════════════════════════════════════════════════════════════════════

def ask(prompt: str, default: str = "") -> str:
    if default:
        result = input(f"  {prompt} [{default}]: ").strip()
        return result if result else default
    return input(f"  {prompt}: ").strip()


def ask_yn(prompt: str, default: bool = False) -> bool:
    d = "Y/n" if default else "y/N"
    result = input(f"  {prompt} ({d}): ").strip().lower()
    if not result:
        return default
    return result in ("y", "yes", "ya")


def ask_choice(prompt: str, choices: list[str], default: str = "") -> str:
    while True:
        result = input(f"  {prompt} ({'/'.join(choices)}) [{default}]: ").strip().lower()
        if not result and default:
            return default
        if result in choices:
            return result
        print(f"  ⚠ Pilih: {'/'.join(choices)}")


def pause() -> None:
    input("\n  Tekan Enter untuk kembali...")


def account_status(s: Settings, phone: str) -> str:
    sessions_dir = Path(s.sessions_dir)
    return "session ada" if AccountManager.has_session(sessions_dir, phone) else "belum login"


def print_account_rows(s: Settings) -> None:
    if not s.accounts:
        print("  │  (belum ada akun)")
        return
    for i, phone in enumerate(s.accounts, 1):
        print(f"  │  {i}. {phone}  [{account_status(s, phone)}]")


def clear() -> None:
    print("\033[2J\033[H", end="")


def header() -> None:
    print()
    print("  ╔══════════════════════════════════════════════════╗")
    print("  ║       Telegram Video Uploader v5                ║")
    print("  ╚══════════════════════════════════════════════════╝")
    print()


def show_menu(s: Settings) -> None:
    print(f"  Target: {s.target}  │  Workers: {s.workers}  │  Akun: {len(s.accounts)}")
    print()
    print("  1. 📤  Upload")
    print("  2. 🎯  Ganti target grup")
    print("  3. 👥  Kelola akun")
    print("  4. ⚙️   Setting")
    print("  5. 🚪  Keluar")
    print()


# ═══════════════════════════════════════════════════════════════════════
# Upload action
# ═══════════════════════════════════════════════════════════════════════

async def action_upload(s: Settings) -> None:
    print()
    print("  ┌─ Upload ────────────────────────────────────────┐")

    # Step 1: Upload mode
    print("  │")
    print("  │  Tipe upload:")
    print("  │    1. 📦 File besar (video single/split)")
    print("  │    2. 📁 File kecil (album grup)")
    print("  │")
    mode_choice = ask("Pilih tipe (1/2)", "1")
    upload_mode = "kecil" if mode_choice == "2" else "besar"

    # Step 2: Path
    folder_str = ask("Path folder/file (drag dari Finder)").strip("'\"")
    if not folder_str:
        print("  ⚠ Path kosong.")
        return
    folder = Path(folder_str)
    if not folder.exists():
        print(f"  ⚠ '{folder}' tidak ditemukan.")
        return

    # Step 3: Caption
    caption = ask("Caption (kosong = nama asli)", "")

    # Step 4: Mode-specific options
    recursive = False

    if upload_mode == "besar":
        s.caption_per_subfolder = False
        sort = ask_choice(
            "Urutan", ["name", "smallest", "largest", "newest", "oldest"], s.sort,
        )
        compress = ask_yn("Kompres video sebelum upload?", s.compress)
        cleanup = ask_yn("Hapus file split setelah upload?", s.cleanup)
        s.sort = sort
        s.compress = compress
        s.cleanup = cleanup
    else:
        # Mode kecil
        photo_mode = ask_choice("Gambar sebagai", ["foto", "dokumen"], s.photo_mode)
        s.photo_mode = photo_mode
        if folder.is_dir():
            s.caption_per_subfolder = ask_yn(
                "Caption akhir per subfolder = nama folder?",
                s.caption_per_subfolder,
            )
        else:
            s.caption_per_subfolder = False

    # Step 5: Subfolder
    if folder.is_dir():
        recursive = ask_yn("Scan subfolder?", False)

    print("  └────────────────────────────────────────────────┘")
    print()

    # Build config & run
    cfg = s.to_config(folder, caption, upload_mode, recursive)
    cfg.validate()

    accounts = AccountManager(cfg.api_id, cfg.api_hash, cfg.sessions_dir, cfg.accounts)
    if await accounts.initialize(max_clients=cfg.workers) == 0:
        pause()
        return

    try:
        if cfg.upload_mode == "kecil" and cfg.caption_per_subfolder:
            await run_small_per_subfolder(cfg, accounts)
        else:
            pipeline = QueueManager(cfg, accounts)
            await pipeline.run()
    except KeyboardInterrupt:
        logger.info("Dihentikan.")
    except Exception as exc:
        logger.error(f"Error: {exc}", exc_info=True)
    finally:
        await accounts.disconnect_all()
    pause()


# ═══════════════════════════════════════════════════════════════════════
# Other actions
# ═══════════════════════════════════════════════════════════════════════

def action_target(s: Settings) -> None:
    print()
    print("  ┌─ Ganti Target ──────────────────────────────────┐")
    print(f"  │ Saat ini: {s.target}")
    print('  │ Contoh: "me", "@channel", "-1001234567890"')
    s.target = ask("Target baru", s.target)
    print(f"  │ ✓ Target: {s.target}")
    print("  └────────────────────────────────────────────────┘")
    pause()


async def action_check_accounts(s: Settings) -> None:
    print()
    print("  ┌─ Cek Akun ──────────────────────────────────────┐")
    print(f"  │ Di konfigurasi: {len(s.accounts)} akun")
    print_account_rows(s)
    print("  │")
    if not s.accounts:
        print("  │ Tambahkan akun dulu.")
        print("  └────────────────────────────────────────────────┘")
        pause()
        return
    print("  │ Mengecek koneksi...")
    acc = AccountManager(s.api_id, s.api_hash, Path(s.sessions_dir), list(s.accounts))
    try:
        n = await acc.initialize()
        if n > 0:
            print(f"  │ ✓ {n} terhubung:")
            for label in acc.list_accounts():
                print(f"  │   • {label}")
    finally:
        await acc.disconnect_all()
    print("  └────────────────────────────────────────────────┘")
    pause()


async def action_add_account(s: Settings) -> None:
    print()
    print("  ┌─ Tambah Akun ───────────────────────────────────┐")
    phone = ask("Nomor akun Telegram (+62...)", "")
    if not phone:
        print("  │ ⚠ Nomor akun kosong.")
        print("  └────────────────────────────────────────────────┘")
        pause()
        return

    try:
        s.accounts = add_configured_account(phone)
    except ValueError as exc:
        print(f"  │ ⚠ {exc}")
        print("  └────────────────────────────────────────────────┘")
        pause()
        return

    new_phone = s.accounts[-1]
    print(f"  │ ✓ Disimpan ke .env: {new_phone}")
    print("  │ Memulai login akun baru...")

    acc = AccountManager(s.api_id, s.api_hash, Path(s.sessions_dir), [new_phone])
    try:
        n = await acc.initialize(max_clients=1)
        if n > 0:
            label = acc.list_accounts()[0]
            print(f"  │ ✓ Login berhasil: {label}")
        else:
            print("  │ ⚠ Akun disimpan, tapi login belum selesai.")
    except Exception as exc:
        print(f"  │ ⚠ Akun disimpan, tetapi login gagal: {exc}")
    finally:
        await acc.disconnect_all()

    print("  └────────────────────────────────────────────────┘")
    pause()


def choose_account_index(s: Settings, prompt: str) -> int | None:
    choice = ask(prompt, "")
    try:
        index = int(choice)
    except ValueError:
        print("  │ ⚠ Pilihan harus angka.")
        return None

    if index < 1 or index > len(s.accounts):
        print("  │ ⚠ Nomor akun tidak ada.")
        return None

    return index - 1


def action_reset_account_session(s: Settings) -> None:
    print()
    print("  ┌─ Reset Session Akun ────────────────────────────┐")
    if not s.accounts:
        print("  │ Belum ada akun di daftar.")
        print("  └────────────────────────────────────────────────┘")
        pause()
        return

    print_account_rows(s)
    print("  │")
    index = choose_account_index(s, "Nomor akun yang session-nya ingin dihapus")
    if index is None:
        print("  └────────────────────────────────────────────────┘")
        pause()
        return

    phone = s.accounts[index]
    if not ask_yn(f"Hapus session lokal untuk {phone}?", False):
        print("  │ Batal.")
        print("  └────────────────────────────────────────────────┘")
        pause()
        return

    ok, message = AccountManager.reset_account_session(Path(s.sessions_dir), phone)
    status_mark = "✓" if ok else "⚠"
    print(f"  │ {status_mark} {message}")
    print(f"  │ ✓ Akun tetap ada di daftar: {phone}")
    print("  └────────────────────────────────────────────────┘")
    pause()


async def action_logout_account(s: Settings) -> None:
    print()
    print("  ┌─ Logout Akun + Hapus Daftar ────────────────────┐")
    if not s.accounts:
        print("  │ Belum ada akun di daftar.")
        print("  └────────────────────────────────────────────────┘")
        pause()
        return

    print_account_rows(s)
    print("  │")
    index = choose_account_index(s, "Nomor akun yang ingin di-logout")
    if index is None:
        print("  └────────────────────────────────────────────────┘")
        pause()
        return

    phone = s.accounts[index]
    if not ask_yn(f"Logout dan hapus {phone} dari daftar?", False):
        print("  │ Batal.")
        print("  └────────────────────────────────────────────────┘")
        pause()
        return

    ok, message = await AccountManager.logout_account(
        s.api_id, s.api_hash, Path(s.sessions_dir), phone,
    )
    try:
        s.accounts = remove_configured_account(phone)
    except ValueError:
        s.accounts = get_configured_accounts()

    status_mark = "✓" if ok else "⚠"
    print(f"  │ {status_mark} {message}")
    print(f"  │ ✓ Akun dihapus dari daftar: {phone}")
    print("  └────────────────────────────────────────────────┘")
    pause()


async def action_logout_all_accounts(s: Settings) -> None:
    print()
    print("  ┌─ Logout Semua Akun ─────────────────────────────┐")
    if not s.accounts:
        print("  │ Belum ada akun di daftar.")
        print("  └────────────────────────────────────────────────┘")
        pause()
        return

    print(f"  │ Total akun: {len(s.accounts)}")
    print_account_rows(s)
    print("  │")
    print("  │ Semua session Telegram akan di-logout.")
    print("  │ Nomor akun tetap disimpan di daftar agar bisa login lagi.")
    if not ask_yn("Lanjut logout semua akun?", False):
        print("  │ Batal.")
        print("  └────────────────────────────────────────────────┘")
        pause()
        return

    results = await AccountManager.logout_all_accounts(
        s.api_id, s.api_hash, Path(s.sessions_dir), list(s.accounts),
    )
    ok_count = 0
    for phone, ok, message in results:
        status_mark = "✓" if ok else "⚠"
        print(f"  │ {status_mark} {phone}: {message}")
        if ok:
            ok_count += 1

    print("  │")
    print(f"  │ ✓ Selesai: {ok_count}/{len(results)} akun diproses")
    print("  │ ✓ Semua akun tetap ada di daftar dan bisa login ulang nanti")
    print("  └────────────────────────────────────────────────┘")
    pause()


async def action_accounts(s: Settings) -> None:
    while True:
        print()
        print("  ┌─ Kelola Akun ───────────────────────────────────┐")
        print(f"  │ Session dir: {Path(s.sessions_dir)}")
        print(f"  │ Total akun : {len(s.accounts)}")
        print("  │")
        print_account_rows(s)
        print("  │")
        print("  │  1. Cek koneksi akun")
        print("  │  2. Tambah akun")
        print("  │  3. Reset session akun")
        print("  │  4. Logout akun + hapus daftar")
        print("  │  5. Logout semua akun")
        print("  │  0. ← Kembali")
        print("  └────────────────────────────────────────────────┘")

        choice = ask("Pilih", "0")

        if choice == "0":
            break
        if choice == "1":
            await action_check_accounts(s)
        elif choice == "2":
            await action_add_account(s)
        elif choice == "3":
            action_reset_account_session(s)
        elif choice == "4":
            await action_logout_account(s)
        elif choice == "5":
            await action_logout_all_accounts(s)
        else:
            print("  ⚠ Pilihan tidak valid")


def action_settings(s: Settings) -> None:
    while True:
        print()
        print("  ┌─ Setting ───────────────────────────────────────┐")
        print(f"  │  1.  Workers        : {s.workers}")
        print(f"  │  2.  Max size (MB)  : {s.max_size_mb}")
        print(f"  │  3.  Retries        : {s.retries}")
        print(f"  │  4.  Speed limit    : {s.speed_limit_mb or 'unlimited'} MB/s")
        print(f"  │  5.  Sort default   : {s.sort}")
        print(f"  │  6.  Photo mode     : {s.photo_mode}")
        print(f"  │  7.  Cleanup        : {'ya' if s.cleanup else 'tidak'}")
        print(f"  │  8.  Skip uploaded  : {'ya' if s.skip_uploaded else 'tidak'}")
        print(f"  │  9.  Thumbnail      : {s.thumb_size}px")
        print(f"  │  0.  ← Kembali")
        print("  └────────────────────────────────────────────────┘")

        choice = ask("Pilih", "0")

        if choice == "0":
            break
        elif choice == "1":
            try:
                s.workers = max(1, int(ask("Workers", str(s.workers))))
            except ValueError:
                print("  ⚠ Harus angka")
        elif choice == "2":
            try:
                s.max_size_mb = max(100, int(ask("Max size MB", str(s.max_size_mb))))
            except ValueError:
                print("  ⚠ Harus angka")
        elif choice == "3":
            try:
                s.retries = max(1, int(ask("Retries", str(s.retries))))
            except ValueError:
                print("  ⚠ Harus angka")
        elif choice == "4":
            try:
                s.speed_limit_mb = max(0, float(ask("Speed limit MB/s", str(s.speed_limit_mb))))
            except ValueError:
                print("  ⚠ Harus angka")
        elif choice == "5":
            s.sort = ask_choice(
                "Sort", ["name", "smallest", "largest", "newest", "oldest"], s.sort,
            )
        elif choice == "6":
            s.photo_mode = "dokumen" if s.photo_mode == "foto" else "foto"
            print(f"  ✓ Photo mode: {s.photo_mode}")
        elif choice == "7":
            s.cleanup = not s.cleanup
            print(f"  ✓ Cleanup: {'ya' if s.cleanup else 'tidak'}")
        elif choice == "8":
            s.skip_uploaded = not s.skip_uploaded
            print(f"  ✓ Skip uploaded: {'ya' if s.skip_uploaded else 'tidak'}")
        elif choice == "9":
            try:
                s.thumb_size = max(100, int(ask("Thumbnail px", str(s.thumb_size))))
            except ValueError:
                print("  ⚠ Harus angka")


# ═══════════════════════════════════════════════════════════════════════
# Main loop
# ═══════════════════════════════════════════════════════════════════════

async def interactive() -> None:
    setup_logging()
    s = Settings()
    clear()
    header()
    print(
        f"  FFmpeg: {'✓' if has_ffmpeg() else '✗'}  │  "
        f"Akun: {len(s.accounts)}  │  "
        f"FFprobe: {'✓' if has_ffprobe() else '✗'}"
    )
    print()

    while True:
        show_menu(s)
        choice = ask("Pilih menu", "")

        if choice == "1":
            await action_upload(s)
            clear()
            header()
        elif choice == "2":
            action_target(s)
            clear()
            header()
        elif choice == "3":
            await action_accounts(s)
            clear()
            header()
        elif choice == "4":
            action_settings(s)
            clear()
            header()
        elif choice == "5":
            print("\n  Sampai jumpa! 👋\n")
            break
        else:
            print("  ⚠ Pilihan tidak valid")


def main() -> None:
    try:
        import uvloop
        uvloop.install()
    except ImportError:
        pass
    try:
        asyncio.run(interactive())
    except KeyboardInterrupt:
        print("\n\n  Dihentikan. 👋\n")
        sys.exit(130)


if __name__ == "__main__":
    main()
