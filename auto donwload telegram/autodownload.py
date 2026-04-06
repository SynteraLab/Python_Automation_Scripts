#!/usr/bin/env python3
"""
⚡ Telegram Media Downloader Pro v2.0
High-performance concurrent media downloader for Telegram

Features:
  • Concurrent downloads (5-10x faster)
  • Persistent media database (JSON)
  • Smart retry with exponential backoff
  • Resume / skip existing files
  • Filter by type, size, date
  • Select & batch download
  • Export to CSV / JSON
  • Colored terminal UI
  • FloodWait handling
  • Logging to file
"""

import os
import re
import sys
import json
import csv
import time
import sqlite3
import shutil
import logging
import asyncio
import inspect
import subprocess
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple, Any
from urllib.parse import urlparse, parse_qs

from telethon import TelegramClient
from telethon.tl.types import (
    MessageMediaPhoto,
    MessageMediaDocument,
    DocumentAttributeVideo,
    DocumentAttributeAudio,
)
from telethon.errors import FloodWaitError

try:
    from telethon.tl.functions.messages import GetForumTopicsRequest
except Exception:
    GetForumTopicsRequest = None


# ═══════════════════════════════════════════════════════════════
#  CONFIGURATION — MULTI ACCOUNT
# ═══════════════════════════════════════════════════════════════

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "tdl_config.json")

DEFAULT_CONFIG = {
    "accounts": {
        "akun1": {
            "api_id": ,
            "api_hash": "a",
            "phone": "+",
            "session_name": "session_akun1"
        },
        "akun2": {
            "api_id": ,
            "api_hash": "",
            "phone": "+",
            "session_name": "session_akun2"
        }
    },
    "active_account": "akun1",
    "download_folder": os.path.join(os.path.expanduser("~"), "Downloads"),
    "database_folder": "data",
    "log_folder": "logs",
    "session_folder": "sessions",
    "max_concurrent_downloads": 5,
    "download_part_size_kb": 512,
    "max_retries": 3,
    "retry_delay": 3,
    "scan_limit": 2000,
    "items_per_page": 15,
    "skip_existing": True,
    "flood_wait_threshold": 60,
    "default_topic_only": False,
    "default_topic_id": 0,
    "include_text_messages": True,
    "auto_scrape_interval": 90,
    "auto_scrape_limit": 300,
    "auto_scrape_auto_download": True,
}


def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            cfg = json.load(f)
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        return cfg
    save_config(DEFAULT_CONFIG)
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=4)


def abs_path(path: str) -> str:
    if os.path.isabs(path):
        return path
    return os.path.join(BASE_DIR, path)


# ═══════════════════════════════════════════════════════════════
#  TERMINAL UI HELPERS
# ═══════════════════════════════════════════════════════════════

class C:
    """ANSI escape codes."""
    RST = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    RED = "\033[31m"
    GRN = "\033[32m"
    YEL = "\033[33m"
    BLU = "\033[34m"
    MAG = "\033[35m"
    CYN = "\033[36m"
    WHT = "\033[37m"
    GRY = "\033[90m"

    CLR = "\033[2K"  # clear line


def fmt_size(b: float) -> str:
    if b is None or b == 0:
        return "0 B"
    for u in ("B", "KB", "MB", "GB", "TB"):
        if abs(b) < 1024.0:
            return f"{b:.2f} {u}"
        b /= 1024.0
    return f"{b:.2f} PB"


def fmt_dur(s: float) -> str:
    if s < 60:
        return f"{s:.0f}s"
    if s < 3600:
        m, sec = divmod(int(s), 60)
        return f"{m}m {sec}s"
    h, rem = divmod(int(s), 3600)
    m, sec = divmod(rem, 60)
    return f"{h}h {m}m {sec}s"


def clean_text_body(text: str) -> str:
    if not text:
        return ""
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.strip() for ln in t.split("\n")]

    out: List[str] = []
    prev_blank = False
    for ln in lines:
        if not ln:
            if out and not prev_blank:
                out.append("")
            prev_blank = True
            continue
        out.append(ln)
        prev_blank = False

    return "\n".join(out).strip()


def text_filename_from_message(msg_id: int, text: str) -> str:
    first_line = clean_text_body(text).split("\n", 1)[0] if text else ""
    slug = re.sub(r"\s+", "_", first_line.lower())
    slug = re.sub(r"[^a-zA-Z0-9_\-]", "", slug).strip("_-")
    if not slug:
        slug = "message"
    slug = slug[:40]
    return f"text_{msg_id}_{slug}.txt"


def pbar(cur: int, tot: int, w: int = 28) -> str:
    if tot <= 0:
        return f"⟦{'·' * w}⟧   0%"

    r = max(0.0, min(cur / tot, 1.0))
    pct = int(r * 100)
    if r >= 1.0:
        pct = 100

    # 8 sub-steps per cell so visual movement can update around 1%
    units = r * w * 8
    full = int(units // 8)
    part = int(units % 8)
    partial = "▏▎▍▌▋▊▉"

    bar = "█" * full
    if part > 0 and full < w:
        bar += partial[part - 1]
    bar += "·" * (w - len(bar))

    return f"⟦{bar}⟧ {pct:3d}%"


def cls():
    os.system("cls" if os.name == "nt" else "clear")


def header(title: str, w: int = 82):
    print(f"\n{C.CYN}{'═' * w}{C.RST}")
    print(f"  {C.BOLD}{C.WHT}{title}{C.RST}")
    print(f"{C.CYN}{'═' * w}{C.RST}")


def banner():
    print(
        f"""
{C.CYN}{C.BOLD}
  ╔═══════════════════════════════════════════════════════════════╗
  ║            ⚡  TELEGRAM MEDIA DOWNLOADER PRO  ⚡             ║
  ║                                                               ║
  ║      Concurrent · Resumable · Filterable · Professional       ║
  ╚═══════════════════════════════════════════════════════════════╝
{C.RST}"""
    )


# ═══════════════════════════════════════════════════════════════
#  DATA MODEL
# ═══════════════════════════════════════════════════════════════

@dataclass
class MediaItem:
    number: int
    message_id: int
    chat_id: int
    media_type: str          # photo / video / audio / image / document
    type_icon: str
    filename: str
    size: int                # bytes
    size_str: str
    date: str                # ISO
    caption: str
    link: str
    mime_type: str
    duration: int = 0
    width: int = 0
    height: int = 0
    downloaded: bool = False
    download_path: str = ""

    # ---- serialisation ----
    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> "MediaItem":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ═══════════════════════════════════════════════════════════════
#  PERSISTENT MEDIA DATABASE
# ═══════════════════════════════════════════════════════════════

class MediaDatabase:
    def __init__(self, folder: str = "data"):
        self.folder = folder
        os.makedirs(folder, exist_ok=True)

        self.items: Dict[int, MediaItem] = {}   # number -> item
        self.msg_idx: Dict[int, int] = {}       # msg_id -> number
        self.selected: List[int] = []
        self.chat_id: Optional[int] = None
        self.topic_id: Optional[int] = None
        self.chat_name: str = ""
        self.last_scan: Optional[str] = None
        self.total_scanned: int = 0

    # ---- persistence ----
    @property
    def _path(self) -> Optional[str]:
        if self.chat_id is None:
            return None
        safe = str(self.chat_id).replace("-", "n")
        suffix = f"_topic_{self.topic_id}" if self.topic_id else ""
        return os.path.join(self.folder, f"media_{safe}{suffix}.json")

    def save(self):
        p = self._path
        if not p:
            return
        blob = {
            "chat_id": self.chat_id,
            "topic_id": self.topic_id,
            "chat_name": self.chat_name,
            "last_scan": self.last_scan,
            "total_scanned": self.total_scanned,
            "items": {str(k): v.to_dict() for k, v in self.items.items()},
        }
        with open(p, "w", encoding="utf-8") as f:
            json.dump(blob, f, ensure_ascii=False, indent=2)

    def load(self, chat_id: int, topic_id: Optional[int] = None) -> bool:
        self.chat_id = chat_id
        self.topic_id = topic_id
        p = self._path
        if not p or not os.path.exists(p):
            return False
        with open(p, "r", encoding="utf-8") as f:
            blob = json.load(f)
        self.chat_name = blob.get("chat_name", "")
        self.topic_id = blob.get("topic_id", self.topic_id)
        self.last_scan = blob.get("last_scan")
        self.total_scanned = blob.get("total_scanned", 0)
        self.items.clear()
        self.msg_idx.clear()
        for _k, v in blob.get("items", {}).items():
            it = MediaItem.from_dict(v)
            self.items[it.number] = it
            self.msg_idx[it.message_id] = it.number
        return True

    def clear(self):
        self.items.clear()
        self.msg_idx.clear()
        self.selected.clear()

    def add(self, it: MediaItem):
        self.items[it.number] = it
        self.msg_idx[it.message_id] = it.number

    # ---- queries ----
    def get(self, num: int) -> Optional[MediaItem]:
        return self.items.get(num)

    def all_sorted(self) -> List[MediaItem]:
        return sorted(self.items.values(), key=lambda x: x.number)

    def search(self, q: str) -> List[MediaItem]:
        q = q.lower()
        return sorted(
            [i for i in self.items.values()
             if q in i.filename.lower() or q in (i.caption or "").lower()],
            key=lambda x: x.number,
        )

    def filter_type(self, t: str) -> List[MediaItem]:
        return sorted(
            [i for i in self.items.values() if i.media_type == t],
            key=lambda x: x.number,
        )

    def filter_size(self, lo_mb: float = 0, hi_mb: float = float("inf")) -> List[MediaItem]:
        lo = lo_mb * 1048576
        hi = hi_mb * 1048576
        return sorted(
            [i for i in self.items.values() if lo <= (i.size or 0) <= hi],
            key=lambda x: x.number,
        )

    def filter_date(self, after: str = "", before: str = "9999") -> List[MediaItem]:
        return sorted(
            [i for i in self.items.values()
             if after <= (i.date[:10] if i.date else "") <= before],
            key=lambda x: x.number,
        )

    def stats(self) -> dict:
        s: Dict[str, Any] = {"total": len(self.items), "types": {},
                              "total_size": 0, "downloaded": 0}
        for i in self.items.values():
            s["types"][i.media_type] = s["types"].get(i.media_type, 0) + 1
            s["total_size"] += i.size or 0
            if i.downloaded:
                s["downloaded"] += 1
        return s

    # ---- export ----
    def export_csv(self, path: str) -> int:
        rows = self.all_sorted()
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["#", "MsgID", "Type", "Filename", "Size",
                         "Date", "Link", "Caption", "Downloaded"])
            for i in rows:
                w.writerow([i.number, i.message_id, i.media_type,
                            i.filename, i.size_str, i.date,
                            i.link, (i.caption or "")[:200], i.downloaded])
        return len(rows)

    def export_json(self, path: str) -> int:
        rows = self.all_sorted()
        with open(path, "w", encoding="utf-8") as f:
            json.dump([r.to_dict() for r in rows], f, ensure_ascii=False, indent=2)
        return len(rows)

    # ---- selection ----
    def select(self, nums: List[int]) -> int:
        added = 0
        for n in nums:
            if n in self.items and n not in self.selected:
                self.selected.append(n)
                added += 1
        self.selected.sort()
        return added

    def unselect(self, nums: List[int]) -> int:
        removed = 0
        for n in nums:
            if n in self.selected:
                self.selected.remove(n)
                removed += 1
        return removed

    def clear_sel(self):
        self.selected.clear()

    def selected_items(self) -> List[MediaItem]:
        return [self.items[n] for n in self.selected if n in self.items]


# ═══════════════════════════════════════════════════════════════
#  DOWNLOAD ENGINE  (concurrent, retry, resume)
# ═══════════════════════════════════════════════════════════════

class DownloadEngine:
    def __init__(self, client: TelegramClient, cfg: dict):
        self.client = client
        self.cfg = cfg
        self.dl_folder = abs_path(cfg["download_folder"])
        os.makedirs(self.dl_folder, exist_ok=True)
        self.part_size_kb = int(cfg.get("download_part_size_kb", 512) or 512)
        self.part_size_kb = max(64, min(512, self.part_size_kb))
        self._supports_part_size_kb: Optional[bool] = None

        self.session_bytes = 0
        self.session_files = 0
        self.session_skipped = 0
        self.session_failed = 0
        self.session_start = time.time()

    def _can_use_part_size_kb(self) -> bool:
        if self._supports_part_size_kb is not None:
            return self._supports_part_size_kb
        try:
            sig = inspect.signature(self.client.download_media)
            self._supports_part_size_kb = "part_size_kb" in sig.parameters
        except Exception:
            self._supports_part_size_kb = False
        return self._supports_part_size_kb

    async def _download_media_safe(self, media, fp: str, progress_callback=None):
        kwargs = {}
        if progress_callback is not None:
            kwargs["progress_callback"] = progress_callback
        if self._can_use_part_size_kb():
            kwargs["part_size_kb"] = self.part_size_kb

        try:
            return await self.client.download_media(media, fp, **kwargs)
        except TypeError as exc:
            msg = str(exc)
            if "part_size_kb" in msg and "unexpected keyword argument" in msg:
                self._supports_part_size_kb = False
                kwargs.pop("part_size_kb", None)
                return await self.client.download_media(media, fp, **kwargs)
            raise

    @staticmethod
    def _format_text_payload(item: MediaItem, message) -> str:
        body = clean_text_body(message.text or getattr(message, "message", "") or item.caption or "")
        if not body:
            body = "(empty text)"

        lines = [
            "TELEGRAM TEXT EXPORT",
            "=" * 70,
            f"Chat ID    : {item.chat_id}",
            f"Message ID : {item.message_id}",
            f"Date       : {item.date or '-'}",
            f"Type       : {item.media_type}",
            f"Link       : {item.link or '-'}",
            "-" * 70,
            body,
            "",
        ]
        return "\n".join(lines)

    def _write_text_file(self, fp: str, item: MediaItem, message) -> int:
        payload = self._format_text_payload(item, message)
        with open(fp, "w", encoding="utf-8") as f:
            f.write(payload)
        return os.path.getsize(fp)

    # ── single file (with progress bar) ──────────────────────
    async def download_single(
        self,
        message,
        filename: str,
        *,
        quiet: bool = False,
        item: Optional[MediaItem] = None,
    ) -> Tuple[bool, int]:
        """Download one file. Returns (success, bytes)."""
        fp = os.path.join(self.dl_folder, filename)

        # resume: skip existing
        if self.cfg["skip_existing"] and os.path.exists(fp):
            sz = os.path.getsize(fp)
            if sz > 0:
                if not quiet:
                    print(f"  {C.YEL}⏭  Skip (exists): {filename} "
                          f"({fmt_size(sz)}){C.RST}")
                self.session_skipped += 1
                return True, 0

        is_text = (
            (item is not None and item.media_type == "text")
            or (not getattr(message, "media", None)
                and bool(clean_text_body(message.text or getattr(message, "message", ""))))
        )

        if is_text:
            text_item = item or MediaItem(
                number=0,
                message_id=int(getattr(message, "id", 0) or 0),
                chat_id=0,
                media_type="text",
                type_icon="📝",
                filename=filename,
                size=0,
                size_str="0 B",
                date=message.date.strftime("%Y-%m-%d %H:%M:%S") if getattr(message, "date", None) else "",
                caption=clean_text_body(message.text or getattr(message, "message", "")),
                link="",
                mime_type="text/plain",
            )

            try:
                sz = self._write_text_file(fp, text_item, message)
                text_item.downloaded = True
                text_item.download_path = fp
                self.session_files += 1
                self.session_bytes += sz
                if not quiet:
                    print(f"  {C.GRN}✅ Saved text: {filename} ({fmt_size(sz)}){C.RST}")
                return True, sz
            except Exception as exc:
                if not quiet:
                    print(f"  {C.RED}❌ Failed write text: {str(exc)[:80]}{C.RST}")
                self.session_failed += 1
                return False, 0

        retries = self.cfg["max_retries"]
        base_delay = self.cfg["retry_delay"]

        for attempt in range(1, retries + 1):
            try:
                t0 = time.time()
                last_print = [t0]

                def _cb(cur, tot):
                    now = time.time()
                    if quiet:
                        return
                    if now - last_print[0] < 0.35 and cur < tot:
                        return
                    last_print[0] = now
                    el = now - t0
                    spd = cur / el if el > 0 else 0
                    eta = (tot - cur) / spd if spd > 0 else 0
                    print(
                        f"\r  {C.CYN}{pbar(cur, tot)}{C.RST} "
                        f"{fmt_size(cur)}/{fmt_size(tot)}  "
                        f"{C.GRN}⚡{fmt_size(spd)}/s{C.RST}  "
                        f"{C.YEL}ETA {fmt_dur(eta)}{C.RST}   ",
                        end="", flush=True,
                    )

                res = await self._download_media_safe(
                    message.media,
                    fp,
                    progress_callback=_cb,
                )

                if res:
                    el = time.time() - t0
                    sz = os.path.getsize(fp)
                    spd = sz / el if el > 0 else 0
                    if not quiet:
                        print(
                            f"\r{C.CLR}  {C.GRN}✅ {filename} "
                            f"{C.DIM}({fmt_size(sz)} in {fmt_dur(el)} "
                            f"@ {fmt_size(spd)}/s){C.RST}"
                        )
                    self.session_files += 1
                    self.session_bytes += sz
                    return True, sz

                raise RuntimeError("download returned None")

            except FloodWaitError as e:
                if e.seconds > self.cfg["flood_wait_threshold"]:
                    if not quiet:
                        print(f"\n  {C.RED}⚠  Flood wait {e.seconds}s — skipping{C.RST}")
                    self.session_failed += 1
                    return False, 0
                if not quiet:
                    print(f"\n  {C.YEL}⏳ Flood wait {e.seconds}s "
                          f"(attempt {attempt}/{retries}){C.RST}")
                await asyncio.sleep(e.seconds + 1)

            except Exception as exc:
                if not quiet:
                    print()
                if attempt < retries:
                    delay = base_delay * (2 ** (attempt - 1))
                    if not quiet:
                        print(f"  {C.YEL}⚠  Retry {attempt}/{retries} in "
                              f"{delay}s — {str(exc)[:60]}{C.RST}")
                    await asyncio.sleep(delay)
                else:
                    if not quiet:
                        print(f"  {C.RED}❌ Failed after {retries} tries: "
                              f"{str(exc)[:80]}{C.RST}")
                    self.session_failed += 1
                    return False, 0

        return False, 0

    # ── concurrent batch ─────────────────────────────────────
    async def download_batch(
        self,
        items: List[MediaItem],
        msg_cache: Dict[int, Any],
        confirm: bool = True,
    ) -> dict:
        """Download many files concurrently. Returns summary dict."""
        total = len(items)
        if total == 0:
            return {"ok": 0, "fail": 0, "skip": 0, "bytes": 0}

        conc = self.cfg["max_concurrent_downloads"]
        sem = asyncio.Semaphore(conc)
        lock = asyncio.Lock()
        ctr = {"ok": 0, "fail": 0, "skip": 0, "bytes": 0, "idx": 0}
        t0 = time.time()
        est_total = sum(max(i.size or 0, 0) for i in items)

        progress: Dict[int, Dict[str, Any]] = {}
        stop_progress = asyncio.Event()

        def _set_progress(item: MediaItem):
            progress[item.number] = {
                "cur": 0,
                "tot": max(int(item.size or 0), 0),
                "base_tot": max(int(item.size or 0), 0),
            }

        def _update_progress(item_num: int, cur: int, tot: int):
            st = progress.get(item_num)
            if not st:
                return
            st["cur"] = max(int(cur or 0), 0)
            if tot and int(tot) > 0:
                st["tot"] = max(int(st.get("tot", 0) or 0), int(tot))

        def _clear_progress(item_num: int):
            progress.pop(item_num, None)

        def _build_progress_line() -> str:
            done = ctr["idx"]
            elapsed = max(time.time() - t0, 1e-6)
            inflight = sum(max(int(v.get("cur", 0) or 0), 0) for v in progress.values())
            live_bytes = max(int(ctr["bytes"] + inflight), 0)
            speed = live_bytes / elapsed if elapsed > 0 else 0.0
            spin = "◐◓◑◒"[int(time.time() * 6) % 4]

            def _compact(v: float) -> str:
                return fmt_size(v).replace(" ", "")

            dynamic_extra = sum(
                max(int(v.get("tot", 0) or 0) - int(v.get("base_tot", 0) or 0), 0)
                for v in progress.values()
            )
            dynamic_total = max(est_total + dynamic_extra, live_bytes)

            if dynamic_total > 0:
                rem = max(dynamic_total - live_bytes, 0)
                eta = rem / speed if speed > 0 else 0
                line = (
                    f"  {C.CYN}{spin}{C.RST} {C.WHT}{pbar(live_bytes, dynamic_total, 14)}{C.RST} "
                    f"{C.DIM}{done}/{total} o{ctr['ok']} s{ctr['skip']} f{ctr['fail']}{C.RST}"
                    f" | {_compact(live_bytes)}/{_compact(dynamic_total)}"
                    f" | {_compact(speed)}/s"
                )
                if rem > 0 and speed > 0:
                    line += f" | {fmt_dur(eta)}"
            else:
                line = (
                    f"  {C.CYN}{spin}{C.RST} {C.WHT}{pbar(done, total, 14)}{C.RST} "
                    f"{C.DIM}{done}/{total} o{ctr['ok']} s{ctr['skip']} f{ctr['fail']}{C.RST}"
                    f" | {_compact(speed)}/s"
                )

            return line

        async def _print_event(msg: str):
            async with lock:
                print(f"\r{C.CLR}", end="", flush=True)
                print(msg)

        async def _progress_loop():
            while not stop_progress.is_set():
                async with lock:
                    print(f"\r{C.CLR}{_build_progress_line()}   ", end="", flush=True)
                await asyncio.sleep(0.4)
            async with lock:
                print(f"\r{C.CLR}", end="", flush=True)

        header(f"BATCH DOWNLOAD — {total} files  |  {conc} workers", 82)

        # preview
        print(f"\n  {C.DIM}Files queued:{C.RST}")
        for it in items[:25]:
            tag = f"{C.GRN}⏭{C.RST}" if (
                self.cfg["skip_existing"]
                and os.path.exists(os.path.join(self.dl_folder, it.filename))
                and os.path.getsize(os.path.join(self.dl_folder, it.filename)) > 0
            ) else f"{C.YEL}⏬{C.RST}"
            print(f"  {tag}  #{it.number:>4d}  "
                  f"{it.filename:<44s}  {it.size_str:>10s}")
        if total > 25:
            print(f"  {C.DIM}   … and {total - 25} more{C.RST}")
        print(f"\n  {C.BOLD}Estimated total: {fmt_size(est_total)}{C.RST}")

        if confirm:
            go = input(f"\n  {C.YEL}▶  Start? (y/n): {C.RST}").strip().lower()
            if go != "y":
                print(f"  {C.RED}Cancelled{C.RST}")
                return ctr

        print(f"\n{C.CYN}{'─' * 82}{C.RST}")
        progress_task = asyncio.create_task(_progress_loop())

        async def _worker(item: MediaItem):
            async with sem:
                # resolve message
                msg = msg_cache.get(item.message_id)
                if not msg:
                    try:
                        msg = await self.client.get_messages(
                            item.chat_id, ids=item.message_id
                        )
                    except Exception as e:
                        async with lock:
                            ctr["idx"] += 1
                            ctr["fail"] += 1
                            idx = ctr["idx"]
                        await _print_event(
                            f"  {C.RED}❌ [{idx}/{total}] "
                            f"#{item.number} fetch error: {e}{C.RST}"
                        )
                        return

                if not msg:
                    async with lock:
                        ctr["idx"] += 1
                        ctr["fail"] += 1
                        idx = ctr["idx"]
                    await _print_event(
                        f"  {C.RED}❌ [{idx}/{total}] "
                        f"#{item.number} message not found{C.RST}"
                    )
                    return

                is_text_item = item.media_type == "text"
                if (not is_text_item) and (not msg.media):
                    async with lock:
                        ctr["idx"] += 1
                        ctr["fail"] += 1
                        idx = ctr["idx"]
                    await _print_event(
                        f"  {C.RED}❌ [{idx}/{total}] "
                        f"#{item.number} no media{C.RST}"
                    )
                    return

                fp = os.path.join(self.dl_folder, item.filename)

                # skip existing
                if self.cfg["skip_existing"] and os.path.exists(fp):
                    sz = os.path.getsize(fp)
                    if sz > 0:
                        async with lock:
                            ctr["idx"] += 1
                            ctr["skip"] += 1
                            self.session_skipped += 1
                            idx = ctr["idx"]
                        await _print_event(
                            f"  {C.YEL}⏭  [{idx}/{total}] "
                            f"#{item.number} {item.filename} "
                            f"(exists){C.RST}"
                        )
                        return

                if is_text_item:
                    try:
                        sz = self._write_text_file(fp, item, msg)
                        async with lock:
                            ctr["idx"] += 1
                            ctr["ok"] += 1
                            ctr["bytes"] += sz
                            self.session_files += 1
                            self.session_bytes += sz
                            item.downloaded = True
                            item.download_path = fp
                            idx = ctr["idx"]
                        await _print_event(
                            f"  {C.GRN}✅ [{idx}/{total}]{C.RST} "
                            f"#{item.number} {item.filename} "
                            f"{C.DIM}{fmt_size(sz)}{C.RST}"
                        )
                    except Exception as exc:
                        async with lock:
                            ctr["idx"] += 1
                            ctr["fail"] += 1
                            self.session_failed += 1
                            idx = ctr["idx"]
                        await _print_event(
                            f"  {C.RED}❌ [{idx}/{total}] "
                            f"#{item.number} {item.filename} "
                            f"— {str(exc)[:50]}{C.RST}"
                        )
                    return

                # download with retry
                retries = self.cfg["max_retries"]
                base_delay = self.cfg["retry_delay"]
                last_err = "unknown error"

                for attempt in range(1, retries + 1):
                    try:
                        t1 = time.time()
                        _set_progress(item)

                        def _cb(cur, tot):
                            _update_progress(item.number, cur, tot)

                        result = await self._download_media_safe(
                            msg.media,
                            fp,
                            progress_callback=_cb,
                        )
                        if result:
                            _clear_progress(item.number)
                            el = time.time() - t1
                            sz = os.path.getsize(fp)
                            spd = sz / el if el > 0 else 0
                            async with lock:
                                ctr["idx"] += 1
                                ctr["ok"] += 1
                                ctr["bytes"] += sz
                                self.session_files += 1
                                self.session_bytes += sz
                                item.downloaded = True
                                item.download_path = fp
                                elapsed = time.time() - t0
                                avg = ctr["bytes"] / elapsed if elapsed else 0
                                idx = ctr["idx"]
                            await _print_event(
                                f"  {C.GRN}✅ [{idx}/{total}]{C.RST} "
                                f"#{item.number} {item.filename}  "
                                f"{C.DIM}{fmt_size(sz)} "
                                f"@ {fmt_size(spd)}/s  "
                                f"[avg {fmt_size(avg)}/s]{C.RST}"
                            )
                            return

                        raise RuntimeError("None result")

                    except FloodWaitError as e:
                        _clear_progress(item.number)
                        if e.seconds > self.cfg["flood_wait_threshold"]:
                            last_err = f"flood wait {e.seconds}s"
                            break
                        last_err = f"flood wait {e.seconds}s"
                        await _print_event(
                            f"  {C.YEL}⏳ #{item.number} flood wait {e.seconds}s "
                            f"(attempt {attempt}/{retries}){C.RST}"
                        )
                        await asyncio.sleep(e.seconds + 1)

                    except Exception as exc:
                        _clear_progress(item.number)
                        last_err = str(exc)
                        if attempt < retries:
                            delay = base_delay * (2 ** (attempt - 1))
                            await _print_event(
                                f"  {C.YEL}⚠  #{item.number} retry {attempt}/{retries} "
                                f"in {delay}s — {str(exc)[:50]}{C.RST}"
                            )
                            await asyncio.sleep(delay)
                        else:
                            break

                _clear_progress(item.number)
                async with lock:
                    ctr["idx"] += 1
                    ctr["fail"] += 1
                    self.session_failed += 1
                    idx = ctr["idx"]
                await _print_event(
                    f"  {C.RED}❌ [{idx}/{total}] "
                    f"#{item.number} {item.filename} "
                    f"— {last_err[:50]}{C.RST}"
                )

        try:
            # launch all workers
            await asyncio.gather(
                *[_worker(it) for it in items],
                return_exceptions=True,
            )
        finally:
            stop_progress.set()
            await progress_task

        # summary
        elapsed = time.time() - t0
        avg = ctr["bytes"] / elapsed if elapsed > 0 else 0
        print(f"\n{C.CYN}{'═' * 82}{C.RST}")
        print(f"  {C.BOLD}{C.GRN}✅  BATCH COMPLETE{C.RST}")
        print(f"{C.CYN}{'═' * 82}{C.RST}")
        print(f"  {C.GRN}Success   : {ctr['ok']}{C.RST}")
        print(f"  {C.YEL}Skipped   : {ctr['skip']}{C.RST}")
        print(f"  {C.RED}Failed    : {ctr['fail']}{C.RST}")
        print(f"  {C.WHT}Downloaded: {fmt_size(ctr['bytes'])}{C.RST}")
        print(f"  {C.WHT}Time      : {fmt_dur(elapsed)}{C.RST}")
        print(f"  {C.WHT}Avg speed : {fmt_size(avg)}/s{C.RST}")
        print(f"  {C.WHT}Location  : {os.path.abspath(self.dl_folder)}{C.RST}")
        print(f"{C.CYN}{'═' * 82}{C.RST}")

        return ctr


# ═══════════════════════════════════════════════════════════════
#  MAIN APPLICATION
# ═══════════════════════════════════════════════════════════════

class TelegramDownloader:
    def __init__(self):
        self.cfg = load_config()
        self._init_logging()
        for d in (
            self.cfg["download_folder"],
            self.cfg["database_folder"],
            self.cfg["log_folder"],
            self.cfg["session_folder"],
        ):
            os.makedirs(abs_path(d), exist_ok=True)

        # Client dibuat setelah pilih akun
        self.client: Optional[TelegramClient] = None
        self.db = MediaDatabase(abs_path(self.cfg["database_folder"]))
        self.engine: Optional[DownloadEngine] = None
        self.msg_cache: Dict[int, Any] = {}
        self.active_account: str = ""
        self.current_session_base: str = ""
        self.preview_folder = os.path.join(abs_path(self.cfg["database_folder"]), ".preview_cache")
        os.makedirs(self.preview_folder, exist_ok=True)
        self.auto_state_file = os.path.join(
            abs_path(self.cfg["database_folder"]),
            "autoscrape_state.json",
        )

    def _init_logging(self):
        os.makedirs(abs_path(self.cfg["log_folder"]), exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[
                logging.FileHandler(
                    os.path.join(
                        abs_path(self.cfg["log_folder"]),
                        f"tdl_{datetime.now():%Y%m%d}.log",
                    ),
                    encoding="utf-8",
                )
            ],
        )

    def _resolve_session_name(self, session_name: str) -> str:
        name = (session_name or "session_default").strip()
        if name.endswith(".session"):
            name = name[:-8]

        if os.path.isabs(name):
            return name

        folder = abs_path(self.cfg["session_folder"])
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, os.path.basename(name))

    def _migrate_session_files(self, old_name: str, new_name: str):
        old = (old_name or "").strip()
        if old.endswith(".session"):
            old = old[:-8]

        for ext in (".session", ".session-journal"):
            src_candidates = []

            if old:
                src_candidates.append(old + ext)
                if not os.path.isabs(old):
                    src_candidates.append(os.path.join(BASE_DIR, old + ext))

            dst = new_name + ext
            if os.path.exists(dst):
                continue

            for src in src_candidates:
                if not src or not os.path.exists(src):
                    continue
                if os.path.abspath(src) == os.path.abspath(dst):
                    break
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                try:
                    os.replace(src, dst)
                except OSError:
                    shutil.copy2(src, dst)
                logging.info("Migrated session file: %s -> %s", src, dst)
                break
     # ══════════════════════════════════════════════════════════
    #  TAMBAHKAN 3 METHOD BARU INI ↓↓↓
    # ══════════════════════════════════════════════════════════

    def select_account(self) -> dict:
        """Pilih akun saat startup"""
        accounts = self.cfg.get("accounts", {})

        if not accounts:
            print(f"  {C.RED}❌ Tidak ada akun! Edit {CONFIG_FILE}{C.RST}")
            sys.exit(1)

        if len(accounts) == 1:
            name = list(accounts.keys())[0]
            self.active_account = name
            return accounts[name]

        print(f"\n  {C.BOLD}👤 PILIH AKUN{C.RST}\n")
        print(f"  {C.CYN}{'─' * 50}{C.RST}")

        names = list(accounts.keys())
        for i, name in enumerate(names, 1):
            acc = accounts[name]
            phone = acc.get("phone", "?")
            default = " ⭐" if name == self.cfg.get("active_account") else ""
            print(f"    {C.CYN}[{i}]{C.RST}  {C.WHT}{name:<15s}{C.RST}"
                  f"  📱 {phone}{C.GRN}{default}{C.RST}")

        print(f"\n    {C.CYN}[0]{C.RST}  ➕ Tambah akun baru")
        print(f"  {C.CYN}{'─' * 50}{C.RST}")

        while True:
            ch = input(f"\n  Pilih (1-{len(names)}): ").strip()

            if ch == "0":
                return self._add_new_account()

            try:
                idx = int(ch) - 1
                if 0 <= idx < len(names):
                    self.active_account = names[idx]
                    self.cfg["active_account"] = self.active_account
                    save_config(self.cfg)
                    return accounts[names[idx]]
            except ValueError:
                pass

            print(f"  {C.RED}Pilihan tidak valid{C.RST}")

    def _add_new_account(self) -> dict:
        """Tambah akun baru secara interaktif"""
        print(f"\n  {C.BOLD}➕ TAMBAH AKUN BARU{C.RST}\n")

        name = input(f"  Nama akun (contoh: akun3): ").strip()
        if not name:
            name = f"akun{len(self.cfg['accounts']) + 1}"

        phone = input(f"  Nomor HP (+62...): ").strip()
        api_id = input(f"  API ID []: ").strip()
        api_hash = input(f"  API Hash [...]: ").strip()

        acc = {
            "api_id": int(api_id) if api_id else ,
            "api_hash": api_hash if api_hash else "",
            "phone": phone,
            "session_name": f"session_{name}"
        }

        self.cfg["accounts"][name] = acc
        self.cfg["active_account"] = name
        self.active_account = name
        save_config(self.cfg)

        print(f"\n  {C.GRN}✅ Akun '{name}' ditambahkan!{C.RST}")
        return acc

    def _create_client(self, acc: dict):
        """Buat TelegramClient dari akun yang dipilih"""
        session_name = self._resolve_session_name(acc.get("session_name", ""))
        self._migrate_session_files(acc.get("session_name", ""), session_name)
        self.current_session_base = session_name
        self.client = TelegramClient(
            session_name,
            acc["api_id"],
            acc["api_hash"]
        )

    def _session_lock_holders(self) -> str:
        if not self.current_session_base:
            return ""
        db_path = self.current_session_base + ".session"
        if not os.path.exists(db_path):
            return ""
        try:
            proc = subprocess.run(
                ["lsof", db_path],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
            if len(lines) <= 1:
                return ""

            holders = []
            for ln in lines[1:6]:
                parts = ln.split()
                if len(parts) >= 2:
                    holders.append(f"{parts[0]}(pid {parts[1]})")
            return ", ".join(holders)
        except Exception:
            return ""

    async def _connect_account(self, acc: dict):
        if self.client is None:
            raise RuntimeError("Telegram client is not initialized")

        attempts = 4
        for i in range(1, attempts + 1):
            try:
                await self.client.connect()
                if not await self.client.is_user_authorized():
                    await self.client.start(phone=acc["phone"])
                return await self.client.get_me()
            except sqlite3.OperationalError as e:
                if "database is locked" not in str(e).lower():
                    raise

                try:
                    await self.client.disconnect()
                except Exception:
                    pass

                if i < attempts:
                    wait_s = i * 2
                    print(
                        f"  {C.YEL}⚠ Session DB terkunci, retry {i}/{attempts} "
                        f"dalam {wait_s}s...{C.RST}"
                    )
                    await asyncio.sleep(wait_s)
                    continue

                holders = self._session_lock_holders()
                detail = f" Proses aktif: {holders}." if holders else ""
                raise RuntimeError(
                    "Session database terkunci (database is locked). "
                    "Tutup instance script lain yang pakai akun/session ini, "
                    "lalu jalankan lagi." + detail
                )

    # ══════════════════════════════════════════════════════════
    #  SELESAI — method lama di bawah ini JANGAN DIUBAH
    # ══════════════════════════════════════════════════════════

    # ── link parser ──────────────────────────────────────────
    @staticmethod
    def _normalize_chat_target(raw_target: Any) -> Any:
        """Normalize numeric chat targets to Telegram peer IDs."""
        if isinstance(raw_target, str):
            s = raw_target.strip()
            if s.lstrip("-").isdigit():
                n = int(s)
                if n > 0:
                    # Full peer id form: 100xxxxxxxxxx -> -100xxxxxxxxxx
                    if s.startswith("100"):
                        return -n
                    # Short private-id form often used in links: xxxxxxxxxx -> -100xxxxxxxxxx
                    if len(s) >= 9:
                        return int(f"-100{s}")
                return n
        return raw_target

    @staticmethod
    def parse_link(link: str) -> Tuple[Optional[Any], Optional[int]]:
        for pat, private in [
            (r"t\.me/c/(\d+)/(\d+)(?:/(\d+))?", True),
            (r"t\.me/([^/\?]+)/(\d+)(?:/(\d+))?", False),
        ]:
            m = re.search(pat, link)
            if m:
                cid = int(f"-100{m.group(1)}") if private else m.group(1)
                cid = TelegramDownloader._normalize_chat_target(cid)
                msg_id = int(m.group(3) or m.group(2))
                return cid, msg_id
        return None, None

    @staticmethod
    def parse_chat_and_topic(raw: str) -> Tuple[Optional[Any], Optional[int]]:
        """Parse input target to (chat_target, topic_id)."""
        s = (raw or "").strip()
        if not s:
            return None, None

        if "t.me/" not in s:
            if s.lstrip("-").isdigit():
                return TelegramDownloader._normalize_chat_target(s), None
            return s, None

        for pat, private in [
            (r"t\.me/c/(\d+)/(\d+)(?:/(\d+))?", True),
            (r"t\.me/([^/\?]+)/(\d+)(?:/(\d+))?", False),
        ]:
            m = re.search(pat, s)
            if not m:
                continue

            chat_target = int(f"-100{m.group(1)}") if private else m.group(1)
            chat_target = TelegramDownloader._normalize_chat_target(chat_target)

            # For topic links:
            # - .../<topic_id>/<msg_id>  -> topic_id is segment 2
            # - private links .../c/<id>/<topic_id> can also be topic header links
            # For non-private links .../<msg_id>, segment 2 is usually message id.
            topic_id = None
            if m.group(3):
                topic_id = int(m.group(2))
            elif private:
                topic_id = int(m.group(2))

            try:
                parsed = urlparse(s)
                qs = parse_qs(parsed.query)
                if qs.get("thread") and qs["thread"][0].isdigit():
                    topic_id = int(qs["thread"][0])
                elif qs.get("topic") and qs["topic"][0].isdigit():
                    topic_id = int(qs["topic"][0])
            except Exception:
                pass

            return chat_target, topic_id

        return None, None

    # ── extract info from message ────────────────────────────
    def _extract(self, msg, num: int, chat_id: int) -> Optional[MediaItem]:
        link = ""
        cid = str(chat_id)
        if cid.startswith("-100"):
            link = f"https://t.me/c/{cid[4:]}/{msg.id}"

        raw_text = (msg.text or getattr(msg, "message", "") or "")
        cap = clean_text_body(raw_text)
        ds = msg.date.strftime("%Y-%m-%d %H:%M:%S") if msg.date else ""

        if not msg.media:
            if not self.cfg.get("include_text_messages", True):
                return None
            if not cap:
                return None

            sz = len(cap.encode("utf-8"))
            return MediaItem(
                number=num,
                message_id=msg.id,
                chat_id=chat_id,
                media_type="text",
                type_icon="📝",
                filename=text_filename_from_message(msg.id, cap),
                size=sz,
                size_str=fmt_size(sz),
                date=ds,
                caption=cap,
                link=link,
                mime_type="text/plain",
            )

        if isinstance(msg.media, MessageMediaPhoto):
            return MediaItem(
                number=num, message_id=msg.id, chat_id=chat_id,
                media_type="photo", type_icon="📸",
                filename=f"photo_{msg.id}.jpg",
                size=0, size_str="N/A", date=ds,
                caption=cap, link=link, mime_type="image/jpeg",
            )

        if isinstance(msg.media, MessageMediaDocument):
            doc = msg.media.document
            sz = doc.size or 0
            mime = doc.mime_type or ""
            fn = ""
            dur = w = h = 0

            for a in doc.attributes:
                if hasattr(a, "file_name") and a.file_name:
                    fn = a.file_name
                if isinstance(a, DocumentAttributeVideo):
                    dur = int(a.duration) if a.duration else 0
                    w, h = a.w or 0, a.h or 0
                if isinstance(a, DocumentAttributeAudio):
                    dur = int(a.duration) if a.duration else 0

            if "video" in mime:
                mt, icon = "video", "🎬"
                fn = fn or f"video_{msg.id}.mp4"
            elif "audio" in mime or "ogg" in mime:
                mt, icon = "audio", "🎵"
                fn = fn or f"audio_{msg.id}.mp3"
            elif "image" in mime:
                mt, icon = "image", "🖼️"
                fn = fn or f"image_{msg.id}.jpg"
            elif "pdf" in mime:
                mt, icon = "document", "📕"
                fn = fn or f"doc_{msg.id}.pdf"
            else:
                mt, icon = "document", "📄"
                ext = mime.split("/")[-1] if "/" in mime else "bin"
                fn = fn or f"file_{msg.id}.{ext}"

            fn = re.sub(r'[<>:"/\\|?*]', "_", fn)

            return MediaItem(
                number=num, message_id=msg.id, chat_id=chat_id,
                media_type=mt, type_icon=icon, filename=fn,
                size=sz, size_str=fmt_size(sz), date=ds,
                caption=cap, link=link, mime_type=mime,
                duration=dur, width=w, height=h,
            )
        return None

    # ── parse flexible number input ──────────────────────────
    @staticmethod
    def parse_nums(s: str) -> List[int]:
        """Parse '1,3,5-10,15' -> [1,3,5,6,7,8,9,10,15]"""
        nums: List[int] = []
        for part in s.replace(" ", "").split(","):
            if "-" in part:
                try:
                    a, b = part.split("-", 1)
                    nums.extend(range(int(a), int(b) + 1))
                except ValueError:
                    pass
            else:
                try:
                    nums.append(int(part))
                except ValueError:
                    pass
        return sorted(set(nums))

    # ── scan channel ─────────────────────────────────────────
    async def scan(self, chat_id: int, limit: int = None, topic_id: Optional[int] = None) -> int:
        limit = limit or self.cfg["scan_limit"]
        header("SCANNING MEDIA", 82)

        existing = self.db.load(chat_id, topic_id)
        if existing and self.db.items:
            st = self.db.stats()
            print(f"\n  {C.YEL}📦 Cached database: {st['total']} media  "
                  f"(scanned {self.db.last_scan}){C.RST}")
            ch = input(f"  {C.YEL}Use cache (c) or Rescan (r)? {C.RST}").strip().lower()
            if ch != "r":
                return len(self.db.items)

        self.db.clear()
        self.db.chat_id = chat_id
        self.db.topic_id = topic_id
        self.msg_cache.clear()

        try:
            ent = await self.client.get_entity(chat_id)
            self.db.chat_name = (
                getattr(ent, "title", "") or
                getattr(ent, "username", "") or
                str(chat_id)
            )
        except Exception:
            self.db.chat_name = str(chat_id)

        print(f"\n  {C.CYN}📡 Scanning: {self.db.chat_name}{C.RST}")
        if topic_id:
            print(f"  {C.CYN}🧵 Topic ID: {topic_id}{C.RST}")
        print(f"  {C.DIM}Limit: {limit:,} messages{C.RST}\n")

        mc = 0
        ts = 0
        t0 = time.time()

        iter_kwargs = {"limit": limit}
        if topic_id:
            iter_kwargs["reply_to"] = topic_id

        async for message in self.client.iter_messages(chat_id, **iter_kwargs):
            ts += 1
            item = self._extract(message, mc + 1, chat_id)
            if item:
                mc += 1
                self.db.add(item)
                self.msg_cache[message.id] = message
            if ts % 250 == 0:
                el = time.time() - t0
                rate = ts / el if el > 0 else 0
                print(
                    f"\r  {C.CYN}⏳ {ts:,} msgs | {mc:,} media | "
                    f"{rate:.0f} msg/s{C.RST}     ",
                        end="", flush=True,
                )

        # Some forum groups may not return thread messages with reply_to filter.
        # Fallback: scan recent messages and keep only messages in target topic.
        if topic_id and ts == 0:
            fallback_limit = max(limit * 3, limit)
            print(f"\n  {C.YEL}↺ Topic fallback scan (limit {fallback_limit})...{C.RST}")
            ts = 0
            async for message in self.client.iter_messages(chat_id, limit=fallback_limit):
                rt = getattr(message, "reply_to", None)
                top_id = getattr(rt, "reply_to_top_id", None) if rt else None
                if message.id != topic_id and top_id != topic_id:
                    continue
                ts += 1
                item = self._extract(message, mc + 1, chat_id)
                if item:
                    mc += 1
                    self.db.add(item)
                    self.msg_cache[message.id] = message

        el = time.time() - t0
        self.db.total_scanned = ts
        self.db.last_scan = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.db.save()

        st = self.db.stats()
        icons = {"photo": "📸", "video": "🎬", "audio": "🎵",
                 "image": "🖼️", "document": "📄", "text": "📝"}

        print(f"\r{C.CLR}")
        print(f"  {C.GRN}{'═' * 55}{C.RST}")
        print(f"  {C.GRN}✅  SCAN COMPLETE{C.RST}")
        print(f"  {C.GRN}{'═' * 55}{C.RST}")
        print(f"    Chat      : {self.db.chat_name}")
        print(f"    Messages  : {ts:,}")
        print(f"    Items     : {mc:,} (media + text)")
        print(f"    Time      : {fmt_dur(el)}")
        print(f"    {'─' * 35}")
        for t, c in sorted(st["types"].items()):
            print(f"    {icons.get(t, '📎')} {t:<12s}: {c:,}")
        print(f"    {'─' * 35}")
        print(f"    💾 Total size : {fmt_size(st['total_size'])}")
        print(f"  {C.GRN}{'═' * 55}{C.RST}\n")

        scope = f"topic={topic_id}" if topic_id else "all"
        logging.info(f"Scan: {ts} msgs, {mc} items in {self.db.chat_name} [{scope}]")
        return mc

    # ── fetch messages for download ──────────────────────────
    async def _ensure_cached(self, items: List[MediaItem]):
        miss = [i for i in items if i.message_id not in self.msg_cache]
        if not miss:
            return
        print(f"  {C.DIM}Fetching {len(miss)} message(s)…{C.RST}")
        for batch_start in range(0, len(miss), 100):
            batch = miss[batch_start:batch_start + 100]
            try:
                msgs = await self.client.get_messages(
                    batch[0].chat_id,
                    ids=[i.message_id for i in batch],
                )
                for m in msgs:
                    if m:
                        self.msg_cache[m.id] = m
            except Exception as e:
                logging.error(f"Fetch batch error: {e}")

    # ── display helpers ──────────────────────────────────────
    def _show_page(self, items: List[MediaItem], page: int,
                   pp: int, title: str = "MEDIA BROWSER") -> Tuple[int, int]:
        tp = max(1, (len(items) - 1) // pp + 1)
        page = max(0, min(page, tp - 1))
        s, e = page * pp, min((page + 1) * pp, len(items))
        sl = items[s:e]

        cls()
        print(f"\n{C.CYN}{'═' * 88}{C.RST}")
        print(f"  {C.BOLD}{C.WHT}{title}{C.RST}")
        print(f"  {C.DIM}Chat: {self.db.chat_name}  |  "
              f"Total: {len(items)}  |  "
              f"Page {page+1}/{tp}{C.RST}")
        if self.db.selected:
            print(f"  {C.GRN}✅ Selected: {len(self.db.selected)}{C.RST}")
        print(f"{C.CYN}{'═' * 88}{C.RST}\n")

        # table header
        print(f"  {C.BOLD}"
              f"{'':>2s} {'':>1s} {'#':>5s} {'Pg':>3s}  {'TYPE':^6s}  "
              f"{'FILENAME':<42s}  {'SIZE':>10s}  {'DATE':>12s}"
              f"{C.RST}")
        print(f"  {C.DIM}{'─' * 84}{C.RST}")

        tc = {"video": C.MAG, "photo": C.CYN, "audio": C.YEL,
              "image": C.BLU, "document": C.WHT, "text": C.GRN}

        for pos, it in enumerate(sl, 1):
            sel = f"{C.GRN}✓{C.RST}" if it.number in self.db.selected else " "
            dl = f"{C.GRN}↓{C.RST}" if it.downloaded else " "
            fn = it.filename[:40] + ".." if len(it.filename) > 42 else it.filename
            dt = it.date[:10] if it.date else ""
            c = tc.get(it.media_type, C.WHT)
            print(f"  {sel} {dl} {C.BOLD}#{it.number:>4d}{C.RST}  "
                  f"{pos:>3d}  {it.type_icon:^6s}  {c}{fn:<42s}{C.RST}  "
                  f"{it.size_str:>10s}  {C.DIM}{dt:>12s}{C.RST}")
            if it.caption:
                cap = it.caption[:72].replace("\n", " ")
                print(f"  {'':>14s}{C.DIM}💬 {cap}{C.RST}")

        # command reference
        print(f"\n{C.CYN}{'═' * 88}{C.RST}")
        cmds = [
            (f"{C.GRN}DOWNLOAD{C.RST}",
             f"5 (langsung) │ d 1-10 │ x=download selected │ dp=page"),
            (f"{C.YEL}SELECT{C.RST}",
             f"s 5 │ a (all) │ sp (page) │ pick (pilih baris page)"),
            (f"{C.CYN}NAV{C.RST}",
             f"n next │ p prev │ g 3 (page) │ j 50 (jump to #)"),
            (f"{C.MAG}FILTER{C.RST}",
             f"f video/text │ size 10-100 │ date 2024-01-01 │ reset"),
            (f"{C.BLU}TOOLS{C.RST}",
             f"i 5 │ r(review) │ search kw │ stats │ export │ h │ q"),
        ]
        for label, desc in cmds:
            print(f"  {label:>22s}  {C.DIM}│{C.RST}  {desc}")
        print(f"{C.CYN}{'═' * 88}{C.RST}")

        return page, tp

    def _show_detail(self, it: MediaItem):
        sel = f"{C.GRN}SELECTED{C.RST}" if it.number in self.db.selected else ""
        dl = f"{C.GRN}DOWNLOADED{C.RST}" if it.downloaded else ""
        print(f"\n{C.CYN}{'═' * 62}{C.RST}")
        print(f"  {C.BOLD}ITEM #{it.number}{C.RST}  {sel}  {dl}")
        print(f"{C.CYN}{'═' * 62}{C.RST}")
        rows = [
            ("Number", f"#{it.number}"),
            ("Message ID", str(it.message_id)),
            ("Type", f"{it.type_icon} {it.media_type}"),
            ("Filename", it.filename),
            ("Size", f"{it.size_str} ({it.size:,} B)"),
            ("MIME", it.mime_type),
            ("Date", it.date),
        ]
        if it.duration:
            rows.append(("Duration", fmt_dur(it.duration)))
        if it.width:
            rows.append(("Resolution", f"{it.width}×{it.height}"))
        if it.link:
            rows.append(("Link", it.link))
        for label, val in rows:
            print(f"  {C.WHT}{label:<14s}:{C.RST}  {val}")
        if it.caption:
            print(f"  {C.WHT}Caption:{C.RST}")
            for ln in it.caption[:500].split("\n"):
                print(f"    {C.DIM}{ln}{C.RST}")
        if it.download_path:
            print(f"  {C.WHT}Saved to:{C.RST}  {it.download_path}")
        print(f"{C.CYN}{'═' * 62}{C.RST}")

    def _show_browser_help(self):
        header("BANTUAN CEPAT", 72)
        print(f"\n  {C.BOLD}Perintah paling sering:{C.RST}")
        print(f"    {C.CYN}5{C.RST} / {C.CYN}d 5{C.RST}           -> download item #5")
        print(f"    {C.CYN}s 1-10{C.RST} / {C.CYN}a{C.RST}         -> pilih item / pilih semua")
        print(f"    {C.CYN}x{C.RST} / {C.CYN}ds{C.RST}            -> download semua yang dipilih")
        print(f"    {C.CYN}c{C.RST} / {C.CYN}u all{C.RST}         -> kosongkan pilihan")
        print(f"    {C.CYN}n{C.RST} / {C.CYN}p{C.RST} / {C.CYN}g 3{C.RST}      -> pindah halaman")
        print(f"    {C.CYN}r{C.RST} / {C.CYN}review{C.RST}        -> lihat satu per satu")
        print(f"    {C.CYN}v{C.RST} (di review mode)      -> preview thumbnail")

        print(f"\n  {C.BOLD}Filter cepat:{C.RST}")
        print(f"    {C.CYN}f video{C.RST}          -> filter tipe (video/photo/audio/document/text)")
        print(f"    {C.CYN}size 10-100{C.RST}      -> filter ukuran MB")
        print(f"    {C.CYN}date 2024-01-01{C.RST}  -> filter tanggal")
        print(f"    {C.CYN}reset{C.RST}            -> reset filter")

        print(f"\n  {C.BOLD}Lainnya:{C.RST}")
        print(f"    {C.CYN}i 5{C.RST}  {C.DIM}(detail){C.RST}   {C.CYN}search keyword{C.RST}  {C.DIM}(cari){C.RST}")
        print(f"    {C.CYN}stats{C.RST} {C.DIM}(ringkasan){C.RST}   {C.CYN}export{C.RST} {C.DIM}(csv/json){C.RST}   {C.CYN}q{C.RST} {C.DIM}(keluar){C.RST}")
        input(f"\n  {C.DIM}Press Enter…{C.RST}")

    async def _review_mode(self, items: List[MediaItem], start_idx: int = 0):
        if not items:
            print(f"  {C.YEL}Tidak ada item untuk direview{C.RST}")
            await asyncio.sleep(1)
            return

        idx = max(0, min(start_idx, len(items) - 1))
        while True:
            it = items[idx]
            cls()
            print(f"\n{C.CYN}{'═' * 78}{C.RST}")
            print(f"  {C.BOLD}REVIEW MODE{C.RST}  {C.DIM}({idx + 1}/{len(items)}){C.RST}")
            print(f"{C.CYN}{'═' * 78}{C.RST}")
            print(f"  #{it.number}  {it.type_icon} {it.media_type}  {C.DIM}{it.size_str} · {it.date[:10] if it.date else '-'}{C.RST}")
            print(f"  {C.WHT}{it.filename}{C.RST}")
            if it.caption:
                cap = it.caption.replace("\n", " ")
                print(f"  {C.DIM}💬 {cap[:160]}{C.RST}")

            state = []
            if it.number in self.db.selected:
                state.append("selected")
            if it.downloaded:
                state.append("downloaded")
            if state:
                print(f"  {C.GRN}Status: {', '.join(state)}{C.RST}")

            print(f"\n  {C.CYN}n{C.RST}=next  {C.CYN}p{C.RST}=prev  {C.CYN}j 120{C.RST}=jump #  {C.CYN}s{C.RST}=toggle select  {C.CYN}d{C.RST}=download  {C.CYN}v{C.RST}=preview  {C.CYN}q{C.RST}=exit")
            cmd = input(f"\n  {C.BOLD}review➤{C.RST} ").strip().lower()

            if cmd in ("", "n", "next"):
                idx = min(idx + 1, len(items) - 1)
            elif cmd in ("p", "prev"):
                idx = max(idx - 1, 0)
            elif cmd.startswith("j ") or cmd.startswith("jump "):
                try:
                    n = int(cmd.split(maxsplit=1)[1])
                    found = False
                    for i, x in enumerate(items):
                        if x.number >= n:
                            idx = i
                            found = True
                            break
                    if not found:
                        idx = len(items) - 1
                except (ValueError, IndexError):
                    print(f"  {C.RED}Usage: j 120{C.RST}")
                    await asyncio.sleep(0.8)
            elif cmd.isdigit():
                n = int(cmd)
                found = False
                for i, x in enumerate(items):
                    if x.number >= n:
                        idx = i
                        found = True
                        break
                if not found:
                    idx = len(items) - 1
            elif cmd in ("s", "sel", "select"):
                if it.number in self.db.selected:
                    self.db.unselect([it.number])
                    print(f"  {C.YEL}- unselected #{it.number}{C.RST}")
                else:
                    self.db.select([it.number])
                    print(f"  {C.GRN}+ selected #{it.number}{C.RST}")
                await asyncio.sleep(0.5)
            elif cmd in ("d", "dl", "download"):
                await self._ensure_cached([it])
                await self.engine.download_batch([it], self.msg_cache)
                self.db.save()
                input(f"\n  {C.DIM}Press Enter…{C.RST}")
            elif cmd in ("v", "view", "preview", "thumb"):
                await self._preview_thumbnail(it)
                await asyncio.sleep(0.7)
            elif cmd in ("q", "quit", "exit"):
                break
            else:
                print(f"  {C.RED}Perintah tidak dikenal{C.RST}")
                await asyncio.sleep(0.8)

    async def _preview_thumbnail(self, it: MediaItem):
        if self.client is None:
            print(f"  {C.RED}Client belum siap{C.RST}")
            return

        if it.media_type == "text":
            print(f"  {C.YEL}Preview thumbnail tidak tersedia untuk text{C.RST}")
            return

        await self._ensure_cached([it])
        msg = self.msg_cache.get(it.message_id)
        if not msg:
            print(f"  {C.RED}Message tidak ditemukan di cache{C.RST}")
            return

        base = os.path.join(self.preview_folder, f"{it.chat_id}_{it.message_id}")
        out = base + ".jpg"

        if not os.path.exists(out):
            saved = None
            try:
                saved = await self.client.download_media(msg, file=out, thumb=0)
            except Exception:
                saved = None

            if not saved:
                if it.media_type in ("photo", "image") and it.size <= 15 * 1024 * 1024:
                    try:
                        saved = await self.client.download_media(msg, file=out)
                    except Exception:
                        saved = None
                else:
                    print(f"  {C.YEL}Thumbnail tidak tersedia untuk media ini{C.RST}")
                    return

            if isinstance(saved, str):
                out = saved

        if not os.path.exists(out):
            print(f"  {C.RED}Gagal membuat preview{C.RST}")
            return

        subprocess.run(["open", out], check=False)
        print(f"  {C.GRN}🖼 Preview dibuka: {out}{C.RST}")

    # ── interactive browser ──────────────────────────────────
    async def browse(self, chat_id: int, topic_id: Optional[int] = None):
        total = await self.scan(chat_id, topic_id=topic_id)
        if total == 0:
            print(f"  {C.RED}No media/text found!{C.RST}")
            return
        input(f"\n  {C.DIM}Press Enter to browse…{C.RST}")

        items = self.db.all_sorted()
        filt = None
        scope = f"topic={topic_id}" if topic_id else "all topics"
        pg = 0
        pp = self.cfg["items_per_page"]

        while True:
            pg, tp = self._show_page(
                items, pg, pp,
                title=f"MEDIA BROWSER ({scope})" + (f"  [{filt}]" if filt else ""),
            )
            raw = input(f"\n  {C.BOLD}➤{C.RST} ").strip()
            if not raw:
                continue
            sidx = pg * pp
            eidx = min((pg + 1) * pp, len(items))
            page_items = items[sidx:eidx]
            key = raw.lower().strip()

            if key in ("h", "help", "?"):
                self._show_browser_help()
                continue

            if key == "a":
                act, arg = "s", "all"
            elif key == "x":
                act, arg = "ds", ""
            elif key == "c":
                act, arg = "u", "all"
            else:
                parts = raw.split(maxsplit=1)
                act = parts[0].lower()
                arg = parts[1] if len(parts) > 1 else ""

            # ── navigation ──
            if act in ("n", "next"):
                pg = min(pg + 1, tp - 1)
            elif act in ("p", "prev"):
                pg = max(pg - 1, 0)
            elif act in ("g", "goto"):
                try:
                    pg = max(0, int(arg) - 1)
                except ValueError:
                    pass
            elif act in ("j", "jump"):
                try:
                    tgt = int(arg)
                    for idx, it in enumerate(items):
                        if it.number >= tgt:
                            pg = idx // pp
                            break
                except ValueError:
                    pass

            # ── download ──
            elif act in ("d", "dl", "download", "unduh"):
                if not arg:
                    print(f"  {C.RED}Usage: d 5  |  d 1-10  |  d 1,3,5{C.RST}")
                    await asyncio.sleep(1.5)
                    continue
                nums = self.parse_nums(arg)
                dl = [i for i in items if i.number in nums]
                if dl:
                    await self._ensure_cached(dl)
                    await self.engine.download_batch(dl, self.msg_cache)
                    self.db.save()
                    input(f"\n  {C.DIM}Press Enter…{C.RST}")
                else:
                    print(f"  {C.RED}No matching media{C.RST}")
                    await asyncio.sleep(1)

            elif act in ("ds", "x"):
                sel = self.db.selected_items()
                if sel:
                    base_dir = self.engine.dl_folder
                    target_dir = base_dir

                    print(f"\n  {C.BOLD}OUTPUT MODE{C.RST}")
                    print(f"    {C.CYN}[1]{C.RST} Jadikan folder (isi nama folder)")
                    print(f"    {C.CYN}[2]{C.RST} Tanpa folder (langsung di download folder)")
                    ch = input(f"\n  Pilih output [2]: ").strip()

                    if ch == "1":
                        raw_name = input("  Nama folder: ").strip()
                        if not raw_name:
                            raw_name = f"batch_{datetime.now():%Y%m%d_%H%M%S}"
                        safe_name = re.sub(r"[\\/:*?\"<>|]", "_", raw_name).strip(" .")
                        if not safe_name:
                            safe_name = f"batch_{datetime.now():%Y%m%d_%H%M%S}"
                        target_dir = os.path.join(base_dir, safe_name)
                        os.makedirs(target_dir, exist_ok=True)
                        self.engine.dl_folder = target_dir
                        print(f"  {C.CYN}📁 Target folder: {target_dir}{C.RST}")

                    await self._ensure_cached(sel)
                    try:
                        await self.engine.download_batch(sel, self.msg_cache)
                    finally:
                        self.engine.dl_folder = base_dir
                    self.db.save()
                    self.db.clear_sel()
                    print(f"  {C.GRN}✅ Download selesai. Kembali ke menu utama...{C.RST}")
                    await asyncio.sleep(0.8)
                    break
                else:
                    print(f"  {C.YEL}Nothing selected{C.RST}")
                    await asyncio.sleep(1)

            elif act == "dp":
                if page_items:
                    await self._ensure_cached(page_items)
                    await self.engine.download_batch(page_items, self.msg_cache)
                    self.db.save()
                    input(f"\n  {C.DIM}Press Enter…{C.RST}")
                else:
                    print(f"  {C.YEL}Halaman kosong{C.RST}")
                    await asyncio.sleep(1)

            # ── select ──
            elif act in ("s", "sel", "select", "pilih"):
                if arg == "all":
                    nums = [i.number for i in items]
                else:
                    nums = self.parse_nums(arg)
                valid = [n for n in nums if any(i.number == n for i in items)]
                a = self.db.select(valid)
                print(f"  {C.GRN}+{a} selected (total {len(self.db.selected)}){C.RST}")
                await asyncio.sleep(0.7)

            elif act == "sp":
                nums = [i.number for i in page_items]
                a = self.db.select(nums)
                print(f"  {C.GRN}+{a} selected from page{C.RST}")
                await asyncio.sleep(0.7)

            elif act == "up":
                r = self.db.unselect([i.number for i in page_items])
                print(f"  {C.YEL}-{r} unselected from page{C.RST}")
                await asyncio.sleep(0.7)

            elif act in ("pick", "ppick"):
                if not page_items:
                    print(f"  {C.YEL}Halaman kosong{C.RST}")
                    await asyncio.sleep(1)
                    continue
                p = input("  Pilih baris halaman (contoh: 1,3,5-8): ").strip()
                local_nums = self.parse_nums(p)
                chosen = []
                for n in local_nums:
                    if 1 <= n <= len(page_items):
                        chosen.append(page_items[n - 1].number)
                a = self.db.select(chosen)
                print(f"  {C.GRN}+{a} selected from page rows{C.RST}")
                await asyncio.sleep(0.9)

            elif act in ("u", "unsel", "unselect", "batal"):
                if arg == "all":
                    self.db.clear_sel()
                    print(f"  {C.YEL}Selection cleared{C.RST}")
                else:
                    r = self.db.unselect(self.parse_nums(arg))
                    print(f"  {C.YEL}-{r} unselected{C.RST}")
                await asyncio.sleep(0.7)

            # ── filter ──
            elif act in ("ft", "f", "type"):
                if arg:
                    items = self.db.filter_type(arg)
                    filt = f"type={arg}"
                    pg = 0
                else:
                    types = sorted(set(i.media_type for i in self.db.items.values()))
                    print(f"  {C.DIM}Types: {', '.join(types)}{C.RST}")
                    await asyncio.sleep(2)
            elif act in ("fs", "size"):
                try:
                    if "-" in arg:
                        lo, hi = arg.split("-", 1)
                        items = self.db.filter_size(float(lo), float(hi))
                    else:
                        items = self.db.filter_size(float(arg))
                    filt = f"size={arg}MB"
                    pg = 0
                except ValueError:
                    print(f"  {C.RED}Usage: fs 10-100{C.RST}")
                    await asyncio.sleep(1.5)
            elif act in ("fd", "date"):
                if arg:
                    items = self.db.filter_date(after=arg)
                    filt = f"date≥{arg}"
                    pg = 0
            elif act in ("fr", "reset"):
                items = self.db.all_sorted()
                filt = None
                pg = 0

            # ── info / tools ──
            elif act in ("i", "info"):
                try:
                    it = self.db.get(int(arg))
                    if it:
                        self._show_detail(it)
                    else:
                        print(f"  {C.RED}Not found{C.RST}")
                except ValueError:
                    print(f"  {C.RED}Usage: i 5{C.RST}")
                input(f"  {C.DIM}Press Enter…{C.RST}")

            elif act in ("r", "review", "lihat"):
                start_idx = pg * pp
                if arg.isdigit():
                    n = int(arg)
                    if 1 <= n <= len(page_items):
                        target_number = page_items[n - 1].number
                        for i, it in enumerate(items):
                            if it.number == target_number:
                                start_idx = i
                                break
                    else:
                        for i, it in enumerate(items):
                            if it.number >= n:
                                start_idx = i
                                break
                await self._review_mode(items, start_idx)

            elif act == "search":
                kw = arg or input(f"  {C.CYN}🔍 Search: {C.RST}")
                res = self.db.search(kw)
                if res:
                    items = res
                    filt = f"search='{kw}'"
                    pg = 0
                    print(f"  {C.GRN}Found {len(res)} results{C.RST}")
                else:
                    print(f"  {C.RED}No results{C.RST}")
                await asyncio.sleep(1)

            elif act == "stats":
                st = self.db.stats()
                ic = {"photo": "📸", "video": "🎬", "audio": "🎵",
                      "image": "🖼️", "document": "📄", "text": "📝"}
                print(f"\n  {C.BOLD}📊 DATABASE{C.RST}")
                print(f"  {'─' * 40}")
                print(f"  Total      : {st['total']:,}")
                print(f"  Size       : {fmt_size(st['total_size'])}")
                print(f"  Downloaded : {st['downloaded']}")
                print(f"  Selected   : {len(self.db.selected)}")
                for t, c in sorted(st["types"].items()):
                    print(f"  {ic.get(t, '📎')} {t:<12s}: {c:,}")
                if self.engine:
                    print(f"\n  {C.BOLD}⚡ SESSION{C.RST}")
                    print(f"  {'─' * 40}")
                    print(f"  Files   : {self.engine.session_files}")
                    print(f"  Bytes   : {fmt_size(self.engine.session_bytes)}")
                    print(f"  Skipped : {self.engine.session_skipped}")
                    print(f"  Failed  : {self.engine.session_failed}")
                    se = time.time() - self.engine.session_start
                    if se > 0 and self.engine.session_bytes > 0:
                        print(f"  Avg spd : {fmt_size(self.engine.session_bytes / se)}/s")
                input(f"\n  {C.DIM}Press Enter…{C.RST}")

            elif act == "export":
                print(f"\n  1. CSV   2. JSON")
                ch = input(f"  Choose: ").strip()
                base = re.sub(r"[^\w]", "_", self.db.chat_name)
                if ch == "1":
                    fp = os.path.join(self.cfg["database_folder"], f"{base}.csv")
                    n = self.db.export_csv(fp)
                    print(f"  {C.GRN}✅ {n} items → {fp}{C.RST}")
                elif ch == "2":
                    fp = os.path.join(self.cfg["database_folder"], f"{base}.json")
                    n = self.db.export_json(fp)
                    print(f"  {C.GRN}✅ {n} items → {fp}{C.RST}")
                input(f"  {C.DIM}Press Enter…{C.RST}")

            elif act in ("q", "quit", "exit"):
                if self.db.selected:
                    if input(
                        f"  {C.YEL}⚠ {len(self.db.selected)} selected. "
                        f"Quit? (y/n): {C.RST}"
                    ).strip().lower() != "y":
                        continue
                self.db.save()
                break

            else:
                # try as direct number download
                try:
                    nums = self.parse_nums(raw)
                    if nums:
                        dl = [i for i in items if i.number in nums]
                        if dl:
                            await self._ensure_cached(dl)
                            await self.engine.download_batch(dl, self.msg_cache)
                            self.db.save()
                            input(f"\n  {C.DIM}Press Enter…{C.RST}")
                            continue
                except Exception:
                    pass
                print(f"  {C.RED}Unknown command: {raw}{C.RST}")
                print(f"  {C.DIM}Ketik h untuk bantuan cepat.{C.RST}")
                await asyncio.sleep(1)

    async def _download_from_single_link(self, link: str, ask_confirm: bool = True) -> int:
        cid, mid = self.parse_link(link)
        if not cid or not mid:
            print(f"  {C.RED}❌ Invalid link: {link}{C.RST}")
            return 0

        ent = await self._resolve_entity_fallback(cid)
        msg = await self.client.get_messages(ent, ids=mid)
        msg_text = clean_text_body(msg.text or getattr(msg, "message", "")) if msg else ""
        has_text = bool(msg_text) and self.cfg.get("include_text_messages", True)
        if not msg or (not msg.media and not has_text):
            print(f"  {C.RED}❌ No media/text found: {link}{C.RST}")
            return 0

        chat_real_id = ent.id
        chosen_msgs = [msg]

        if getattr(msg, "grouped_id", None):
            lo = max(1, msg.id - 120)
            hi = msg.id + 120
            nearby_ids = list(range(lo, hi + 1))
            nearby = await self.client.get_messages(ent, ids=nearby_ids)
            album = [
                m for m in (nearby or [])
                if m and getattr(m, "grouped_id", None) == msg.grouped_id and m.media
            ]
            album.sort(key=lambda x: x.id)

            if len(album) > 1:
                ref_idx = 1
                for i, m in enumerate(album, 1):
                    if m.id == msg.id:
                        ref_idx = i
                        break

                print(f"\n  {C.CYN}📚 Album terdeteksi: {len(album)} media{C.RST}")
                print(f"  {C.DIM}Link mengarah ke urutan #{ref_idx} dalam album.{C.RST}")
                for i, m in enumerate(album, 1):
                    itx = self._extract(m, i, chat_real_id)
                    if not itx:
                        continue
                    print(
                        f"    {C.CYN}[{i:>2d}]{C.RST} {itx.type_icon} "
                        f"{itx.filename[:44]:<44s} {C.DIM}{itx.size_str}{C.RST}"
                    )

                if ask_confirm:
                    pick = input(
                        f"\n  {C.CYN}Pilih: 5 | 1,3,5-7 | all "
                        f"[default {ref_idx}]: {C.RST}"
                    ).strip()
                    if pick.lower() in ("all", "a", "*"):
                        chosen_nums = list(range(1, len(album) + 1))
                    elif pick:
                        chosen_nums = self.parse_nums(pick)
                    else:
                        chosen_nums = [ref_idx]
                else:
                    # Batch auto mode: no extra questions, auto select all media in album.
                    chosen_nums = list(range(1, len(album) + 1))

                valid = []
                for n in chosen_nums:
                    if 1 <= n <= len(album) and n not in valid:
                        valid.append(n)
                if not valid:
                    valid = [ref_idx]

                chosen_msgs = [album[n - 1] for n in valid]

        if len(chosen_msgs) == 1:
            msg = chosen_msgs[0]
            it = self._extract(msg, 0, chat_real_id)
            if not it:
                return 0
            self._show_detail(it)
            if ask_confirm:
                ok = input(f"\n  {C.GRN}📥 Download? (y/n): {C.RST}").strip().lower() == "y"
                if not ok:
                    return 0
            self.msg_cache[msg.id] = msg
            await self.engine.download_single(msg, it.filename, item=it)
            return 1

        batch_items = []
        for i, m in enumerate(chosen_msgs, 1):
            it = self._extract(m, i, chat_real_id)
            if it:
                batch_items.append(it)
                self.msg_cache[m.id] = m

        if not batch_items:
            print(f"  {C.RED}❌ Tidak ada media valid untuk diunduh{C.RST}")
            return 0

        print(f"\n  {C.CYN}🧺 Dipilih {len(batch_items)} media dari album{C.RST}")
        if ask_confirm:
            ok = input(f"  {C.GRN}📥 Download semua yang dipilih? (y/n): {C.RST}").strip().lower() == "y"
            if not ok:
                return 0
        await self.engine.download_batch(batch_items, self.msg_cache, confirm=ask_confirm)
        return len(batch_items)

    # ── download from link ───────────────────────────────────
    async def dl_from_link(self):
        header("DOWNLOAD FROM LINK", 60)
        print(f"\n  {C.DIM}Paste satu atau banyak link (1 baris per link).{C.RST}")
        print(f"  {C.DIM}Tekan Enter kosong untuk mulai proses.{C.RST}\n")

        lines = []
        while True:
            ln = input("  link> ").strip()
            if not ln:
                break
            lines.append(ln)

        if not lines:
            return

        links = []
        seen = set()
        for ln in lines:
            parts = re.split(r"[\s,]+", ln)
            for p in parts:
                p = p.strip()
                if "t.me/" not in p:
                    continue
                if p not in seen:
                    seen.add(p)
                    links.append(p)

        if not links:
            print(f"  {C.RED}❌ Tidak ada link Telegram valid{C.RST}")
            return

        if len(links) == 1:
            try:
                await self._download_from_single_link(links[0], ask_confirm=True)
            except Exception as e:
                print(f"  {C.RED}❌ {e}{C.RST}")
                logging.error(f"Link download: {e}")
            return

        print(f"\n  {C.CYN}🔗 Batch mode: {len(links)} link{C.RST}")
        auto = input(f"  {C.CYN}Auto download tanpa konfirmasi tiap link? (Y/n): {C.RST}").strip().lower()
        ask_confirm = auto == "n"
        if not ask_confirm:
            print(f"  {C.DIM}Mode auto aktif: tanpa pertanyaan tambahan.{C.RST}")

        ok_links = 0
        fail_links = 0
        downloaded = 0

        for i, link in enumerate(links, 1):
            print(f"\n  {C.BOLD}[{i}/{len(links)}]{C.RST} {link}")
            try:
                n = await self._download_from_single_link(link, ask_confirm=ask_confirm)
                if n > 0:
                    ok_links += 1
                    downloaded += n
                else:
                    fail_links += 1
            except Exception as e:
                fail_links += 1
                print(f"  {C.RED}❌ {e}{C.RST}")
                logging.error(f"Link download: {link} -> {e}")

        print(f"\n{C.CYN}{'═' * 72}{C.RST}")
        print(f"  {C.BOLD}BATCH LINK SUMMARY{C.RST}")
        print(f"{C.CYN}{'═' * 72}{C.RST}")
        print(f"  {C.GRN}Sukses link : {ok_links}{C.RST}")
        print(f"  {C.RED}Gagal link  : {fail_links}{C.RST}")
        print(f"  {C.WHT}Media diunduh: {downloaded}{C.RST}")

    # ── settings menu ────────────────────────────────────────
    async def settings(self):
        while True:
            cls()
            header("SETTINGS", 60)
            opts = [
                ("1", "Download folder", self.cfg["download_folder"]),
                ("2", "Concurrent downloads", self.cfg["max_concurrent_downloads"]),
                ("15", "Download chunk (KB)", self.cfg["download_part_size_kb"]),
                ("3", "Max retries", self.cfg["max_retries"]),
                ("4", "Retry delay (s)", self.cfg["retry_delay"]),
                ("5", "Scan limit", self.cfg["scan_limit"]),
                ("6", "Items per page", self.cfg["items_per_page"]),
                ("7", "Skip existing", self.cfg["skip_existing"]),
                ("8", "Flood wait limit (s)", self.cfg["flood_wait_threshold"]),
                ("9", "Default topic mode", self.cfg["default_topic_only"]),
                ("10", "Default topic ID", self.cfg["default_topic_id"]),
                ("11", "Auto scrape interval (s)", self.cfg["auto_scrape_interval"]),
                ("12", "Auto scrape limit", self.cfg["auto_scrape_limit"]),
                ("13", "Auto scrape auto-download", self.cfg["auto_scrape_auto_download"]),
                ("14", "Include text messages", self.cfg["include_text_messages"]),
            ]
            print()
            for n, lbl, val in opts:
                print(f"    {C.CYN}[{n}]{C.RST} {lbl:<24s}: {C.WHT}{val}{C.RST}")
            print(f"\n    {C.CYN}[0]{C.RST} Back")

            ch = input(f"\n  Edit (0-15): ").strip()
            if ch == "0":
                break
            elif ch == "1":
                v = input(f"  Folder [{self.cfg['download_folder']}]: ").strip()
                if v:
                    self.cfg["download_folder"] = v
                    os.makedirs(abs_path(v), exist_ok=True)
                    if self.engine:
                        self.engine.dl_folder = abs_path(v)
            elif ch == "2":
                v = input("  Concurrent (1-20): ").strip()
                if v.isdigit():
                    self.cfg["max_concurrent_downloads"] = max(1, min(20, int(v)))
            elif ch == "15":
                v = input("  Chunk size KB (64-512): ").strip()
                if v.isdigit():
                    self.cfg["download_part_size_kb"] = max(64, min(512, int(v)))
                    if self.engine:
                        self.engine.part_size_kb = self.cfg["download_part_size_kb"]
            elif ch == "3":
                v = input("  Retries (1-10): ").strip()
                if v.isdigit():
                    self.cfg["max_retries"] = max(1, min(10, int(v)))
            elif ch == "4":
                v = input("  Delay (s): ").strip()
                if v.isdigit():
                    self.cfg["retry_delay"] = max(1, int(v))
            elif ch == "5":
                v = input("  Scan limit: ").strip()
                if v.isdigit():
                    self.cfg["scan_limit"] = max(100, int(v))
            elif ch == "6":
                v = input("  Per page (5-50): ").strip()
                if v.isdigit():
                    self.cfg["items_per_page"] = max(5, min(50, int(v)))
            elif ch == "7":
                self.cfg["skip_existing"] = not self.cfg["skip_existing"]
            elif ch == "8":
                v = input("  Flood threshold (s): ").strip()
                if v.isdigit():
                    self.cfg["flood_wait_threshold"] = max(10, int(v))
            elif ch == "9":
                self.cfg["default_topic_only"] = not self.cfg["default_topic_only"]
            elif ch == "10":
                v = input("  Topic ID default (0=off): ").strip()
                if v.lstrip("-").isdigit():
                    self.cfg["default_topic_id"] = max(0, int(v))
            elif ch == "11":
                v = input("  Auto scrape interval (s): ").strip()
                if v.isdigit():
                    self.cfg["auto_scrape_interval"] = max(10, int(v))
            elif ch == "12":
                v = input("  Auto scrape limit: ").strip()
                if v.isdigit():
                    self.cfg["auto_scrape_limit"] = max(50, int(v))
            elif ch == "13":
                self.cfg["auto_scrape_auto_download"] = not self.cfg["auto_scrape_auto_download"]
            elif ch == "14":
                self.cfg["include_text_messages"] = not self.cfg["include_text_messages"]
            save_config(self.cfg)

    async def _resolve_topic_id(self, chat_entity, topic_raw: str) -> Optional[int]:
        s = (topic_raw or "").strip()
        if not s:
            return None
        if s.isdigit() and int(s) > 0:
            return int(s)

        if GetForumTopicsRequest is None:
            return None

        if self.client is None:
            return None

        query = s.lower()
        offset_id = 0
        offset_topic = 0

        try:
            for _ in range(10):
                res = await self.client(GetForumTopicsRequest(
                    peer=chat_entity,
                    offset_date=0,
                    offset_id=offset_id,
                    offset_topic=offset_topic,
                    limit=100,
                    q=s,
                ))
                topics = list(getattr(res, "topics", []) or [])
                if not topics:
                    break

                exact = None
                partial = None
                for t in topics:
                    title = (getattr(t, "title", "") or "").strip()
                    title_l = title.lower()
                    if title_l == query:
                        exact = t
                        break
                    if query in title_l and partial is None:
                        partial = t

                picked = exact or partial
                if picked is not None:
                    return int(getattr(picked, "id", 0) or 0) or None

                last = topics[-1]
                next_offset_id = int(getattr(last, "top_message", 0) or 0)
                next_offset_topic = int(getattr(last, "id", 0) or 0)
                if next_offset_id == offset_id and next_offset_topic == offset_topic:
                    break
                offset_id = next_offset_id
                offset_topic = next_offset_topic
        except Exception:
            return None

        return None

    async def _resolve_entity_fallback(self, target: Any):
        """Resolve entity with numeric-ID fallbacks for channel/group IDs."""
        if self.client is None:
            raise RuntimeError("Client belum siap")

        candidates: List[Any] = []

        def _add(v: Any):
            if v is None:
                return
            if v not in candidates:
                candidates.append(v)

        _add(target)

        if isinstance(target, str):
            s = target.strip()
            if s.lstrip("-").isdigit():
                n = int(s)
                _add(n)
                if n > 0:
                    _add(-n)
                    _add(int(f"-100{n}"))
                elif s.startswith("-100"):
                    short = s[4:]
                    if short.isdigit():
                        _add(int(short))
        elif isinstance(target, int):
            n = target
            if n > 0:
                _add(-n)
                _add(int(f"-100{n}"))
            else:
                s = str(n)
                if s.startswith("-100"):
                    short = s[4:]
                    if short.isdigit():
                        _add(int(short))

        last_exc: Optional[Exception] = None
        for cand in candidates:
            try:
                return await self.client.get_entity(cand)
            except Exception as e:
                last_exc = e
                continue

        if last_exc:
            raise last_exc
        raise ValueError("Entity tidak ditemukan")

    async def _resolve_chat_topic(self, raw: str) -> Tuple[Optional[int], Optional[int]]:
        target, topic_id = self.parse_chat_and_topic(raw)
        if target is None:
            return None, None

        ent = await self._resolve_entity_fallback(target)

        if topic_id is None and self.cfg.get("default_topic_only"):
            default_topic = int(self.cfg.get("default_topic_id", 0) or 0)
            topic_id = default_topic if default_topic > 0 else None

        if topic_id is None:
            topic_raw = input(
                f"  {C.CYN}Topic ID / nama (opsional, Enter=semua topik): {C.RST}"
            ).strip()
            if topic_raw:
                resolved_topic = await self._resolve_topic_id(ent, topic_raw)
                if resolved_topic:
                    topic_id = resolved_topic
                    print(f"  {C.GRN}✅ Topic terpilih: {topic_id}{C.RST}")
                else:
                    if GetForumTopicsRequest is None:
                        raise ValueError(
                            "Versi Telethon kamu belum mendukung lookup nama topic. "
                            "Pakai Topic ID numerik atau link topic (t.me/.../topic_id)."
                        )
                    raise ValueError(
                        f"Topic '{topic_raw}' tidak ditemukan. Gunakan Topic ID numerik atau link topic."
                    )

        return ent.id, topic_id

    @staticmethod
    def _auto_scope_key(chat_id: int, topic_id: Optional[int]) -> str:
        return f"{chat_id}:{topic_id or 0}"

    def _load_auto_state(self) -> Dict[str, int]:
        if not os.path.exists(self.auto_state_file):
            return {}
        try:
            with open(self.auto_state_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                return {}
            out: Dict[str, int] = {}
            for k, v in raw.items():
                try:
                    out[str(k)] = int(v)
                except (TypeError, ValueError):
                    continue
            return out
        except Exception:
            return {}

    def _save_auto_state(self, state: Dict[str, int]):
        with open(self.auto_state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    @staticmethod
    def _topic_from_message(msg) -> Optional[int]:
        rt = getattr(msg, "reply_to", None)
        if not rt:
            return None
        return getattr(rt, "reply_to_top_id", None)

    async def _latest_scope_message_id(
        self,
        chat_id: int,
        topic_id: Optional[int],
        limit: int,
    ) -> int:
        if self.client is None:
            return 0

        if topic_id:
            try:
                async for m in self.client.iter_messages(chat_id, limit=1, reply_to=topic_id):
                    return int(m.id)
            except Exception:
                pass

            fallback_limit = max(limit * 3, limit, 300)
            async for m in self.client.iter_messages(chat_id, limit=fallback_limit):
                top_id = self._topic_from_message(m)
                if m.id == topic_id or top_id == topic_id:
                    return int(m.id)
            return 0

        async for m in self.client.iter_messages(chat_id, limit=1):
            return int(m.id)
        return 0

    async def _collect_new_media(
        self,
        chat_id: int,
        topic_id: Optional[int],
        limit: int,
        last_id: int,
    ) -> Tuple[List[MediaItem], int, int]:
        if self.client is None:
            return [], 0, last_id

        scanned = 0
        max_seen = last_id
        fresh_msgs = []

        iter_kwargs = {"limit": limit}
        if topic_id:
            iter_kwargs["reply_to"] = topic_id

        used_fallback = False
        try:
            async for message in self.client.iter_messages(chat_id, **iter_kwargs):
                scanned += 1
                mid = int(getattr(message, "id", 0) or 0)
                if mid <= last_id:
                    break
                if mid > max_seen:
                    max_seen = mid
                if message.media or (
                    self.cfg.get("include_text_messages", True)
                    and clean_text_body(message.text or getattr(message, "message", ""))
                ):
                    fresh_msgs.append(message)
        except Exception:
            if not topic_id:
                raise
            used_fallback = True

        if topic_id and (scanned == 0 or used_fallback):
            fallback_limit = max(limit * 3, limit, 300)
            async for message in self.client.iter_messages(chat_id, limit=fallback_limit):
                mid = int(getattr(message, "id", 0) or 0)
                if mid <= last_id:
                    break
                top_id = self._topic_from_message(message)
                if message.id != topic_id and top_id != topic_id:
                    continue

                scanned += 1
                if mid > max_seen:
                    max_seen = mid
                if message.media or (
                    self.cfg.get("include_text_messages", True)
                    and clean_text_body(message.text or getattr(message, "message", ""))
                ):
                    fresh_msgs.append(message)

        uniq = []
        seen = set()
        for m in fresh_msgs:
            if m.id in seen:
                continue
            seen.add(m.id)
            uniq.append(m)
        uniq.sort(key=lambda x: x.id)

        items = []
        for i, m in enumerate(uniq, 1):
            it = self._extract(m, i, chat_id)
            if it:
                items.append(it)
                self.msg_cache[m.id] = m

        return items, scanned, max_seen

    async def auto_scrape(self):
        if self.client is None or self.engine is None:
            print(f"  {C.RED}Client belum siap{C.RST}")
            return

        header("AUTO SCRAPING", 72)
        print(f"\n  {C.DIM}Monitor grup/topik terus-menerus dan ambil media baru otomatis.{C.RST}")
        print(f"  {C.DIM}Stop kapan saja dengan Ctrl+C.{C.RST}\n")

        grp = input(f"  {C.CYN}Group ID / @username / link topic: {C.RST}").strip()
        if not grp:
            return

        try:
            chat_id, topic_id = await self._resolve_chat_topic(grp)
            if chat_id is None:
                raise ValueError("Target chat tidak valid")
        except Exception as e:
            print(f"  {C.RED}❌ {e}{C.RST}")
            input(f"\n  {C.DIM}Press Enter…{C.RST}")
            return

        def_limit = int(self.cfg.get("auto_scrape_limit", 300) or 300)
        def_interval = int(self.cfg.get("auto_scrape_interval", 90) or 90)
        def_auto = bool(self.cfg.get("auto_scrape_auto_download", True))

        lim_raw = input(f"  {C.CYN}Limit per cycle [{def_limit}]: {C.RST}").strip()
        interval_raw = input(f"  {C.CYN}Interval detik [{def_interval}]: {C.RST}").strip()
        mode_default = "1" if def_auto else "2"
        mode = input(
            f"  {C.CYN}Mode [1] scrape+download, [2] scrape-only [{mode_default}]: {C.RST}"
        ).strip()

        limit = max(50, int(lim_raw)) if lim_raw.isdigit() else def_limit
        interval = max(10, int(interval_raw)) if interval_raw.isdigit() else def_interval
        auto_download = (mode or mode_default) != "2"

        self.cfg["auto_scrape_limit"] = limit
        self.cfg["auto_scrape_interval"] = interval
        self.cfg["auto_scrape_auto_download"] = auto_download
        save_config(self.cfg)

        try:
            ent = await self.client.get_entity(chat_id)
            chat_name = (
                getattr(ent, "title", "")
                or getattr(ent, "username", "")
                or str(chat_id)
            )
        except Exception:
            chat_name = str(chat_id)

        scope = f"{chat_name} | topic {topic_id}" if topic_id else chat_name

        state = self._load_auto_state()
        key = self._auto_scope_key(chat_id, topic_id)
        last_id = int(state.get(key, 0) or 0)

        if last_id <= 0:
            seed = input(
                f"  {C.CYN}Mulai dari [1] pesan terbaru (skip history), [2] ambil recent [{1}]: {C.RST}"
            ).strip()
            if seed != "2":
                last_id = await self._latest_scope_message_id(chat_id, topic_id, limit)
                state[key] = last_id
                self._save_auto_state(state)
                print(f"  {C.DIM}Baseline set ke message #{last_id}{C.RST}")

        print(f"\n  {C.GRN}✅ Auto scrape aktif{C.RST}")
        print(f"  {C.WHT}Scope    : {scope}{C.RST}")
        print(f"  {C.WHT}Limit    : {limit} msg/cycle{C.RST}")
        print(f"  {C.WHT}Interval : {interval}s{C.RST}")
        print(f"  {C.WHT}Mode     : {'download otomatis' if auto_download else 'scrape-only'}{C.RST}")
        print(f"  {C.WHT}Include  : {'media + text' if self.cfg.get('include_text_messages', True) else 'media only'}{C.RST}")
        print(f"  {C.DIM}Tekan Ctrl+C untuk berhenti.{C.RST}\n")

        cycle = 0
        try:
            while True:
                cycle += 1
                t0 = time.time()

                items, scanned, max_seen = await self._collect_new_media(
                    chat_id=chat_id,
                    topic_id=topic_id,
                    limit=limit,
                    last_id=last_id,
                )

                if max_seen > last_id:
                    last_id = max_seen
                    state[key] = last_id
                    self._save_auto_state(state)

                if items:
                    print(
                        f"  {C.CYN}[cycle {cycle}]{C.RST} "
                        f"new items: {C.GRN}{len(items)}{C.RST} "
                        f"| scanned: {scanned} | last_id: {last_id}"
                    )
                    logging.info(
                        "AutoScrape hit: scope=%s count=%s scanned=%s",
                        key, len(items), scanned,
                    )
                    if auto_download:
                        await self.engine.download_batch(items, self.msg_cache, confirm=False)
                    else:
                        for it in items[:10]:
                            print(
                                f"    {it.type_icon} #{it.message_id} "
                                f"{it.filename[:50]} {C.DIM}{it.size_str}{C.RST}"
                            )
                        if len(items) > 10:
                            print(f"    {C.DIM}... and {len(items) - 10} more{C.RST}")
                else:
                    print(
                        f"  {C.DIM}[cycle {cycle}] tidak ada item baru "
                        f"(scanned {scanned}, last_id {last_id}){C.RST}"
                    )

                elapsed = time.time() - t0
                wait_s = max(1, interval - int(elapsed))
                await asyncio.sleep(wait_s)

        except KeyboardInterrupt:
            print(f"\n  {C.YEL}⏹ Auto scrape dihentikan.{C.RST}")
            logging.info("AutoScrape stopped: scope=%s last_id=%s", key, last_id)
            input(f"\n  {C.DIM}Press Enter…{C.RST}")

    async def export_text_topic(self):
        if self.client is None:
            print(f"  {C.RED}Client belum siap{C.RST}")
            return

        header("EXPORT TEXT TOPIC", 72)
        print(f"\n  {C.DIM}Ekspor teks materi topic ke 1 file TXT rapi (one-shot).{C.RST}\n")

        grp = input(f"  {C.CYN}Group ID / @username / link topic: {C.RST}").strip()
        if not grp:
            return

        try:
            chat_id, topic_id = await self._resolve_chat_topic(grp)
            if chat_id is None:
                raise ValueError("Target chat tidak valid")
        except Exception as e:
            print(f"  {C.RED}❌ {e}{C.RST}")
            input(f"\n  {C.DIM}Press Enter…{C.RST}")
            return

        if not topic_id:
            go_all = input(
                f"  {C.YEL}⚠ Topic tidak terdeteksi. Lanjut semua topik? (y/N): {C.RST}"
            ).strip().lower()
            if go_all != "y":
                print(f"  {C.DIM}Dibatalkan. Jalankan lagi dan isi Topic ID / nama topic.{C.RST}")
                input(f"\n  {C.DIM}Press Enter…{C.RST}")
                return

        lim_raw = input(f"  {C.CYN}Ambil berapa pesan text? [0=semua]: {C.RST}").strip()
        limit = int(lim_raw) if lim_raw.isdigit() else 0

        out_dir = os.path.join(abs_path(self.cfg["download_folder"]), "text_exports")
        os.makedirs(out_dir, exist_ok=True)

        topic_tag = f"topic_{topic_id}" if topic_id else "all"
        default_name = f"text_{chat_id}_{topic_tag}_{datetime.now():%Y%m%d_%H%M%S}.txt"
        file_name = input(f"  {C.CYN}Nama file [{default_name}]: {C.RST}").strip() or default_name
        file_name = re.sub(r"[\\/:*?\"<>|]", "_", file_name).strip(" .")
        if not file_name.lower().endswith(".txt"):
            file_name += ".txt"
        out_path = os.path.join(out_dir, file_name)

        try:
            ent = await self.client.get_entity(chat_id)
            chat_name = (
                getattr(ent, "title", "")
                or getattr(ent, "username", "")
                or str(chat_id)
            )
        except Exception:
            chat_name = str(chat_id)

        print(f"\n  {C.CYN}Memproses...{C.RST}")
        print(f"  {C.DIM}Chat: {chat_name} | Topic: {topic_id or '-'}{C.RST}")

        msg_iter_limit = limit if limit > 0 else None
        rows = []
        scanned = 0
        kept = 0

        def _append_row(message):
            nonlocal kept
            body = clean_text_body(message.text or getattr(message, "message", ""))
            if not body:
                return
            kept += 1
            dt = message.date.strftime("%Y-%m-%d %H:%M:%S") if getattr(message, "date", None) else "-"
            rows.append(
                f"{'=' * 78}\n"
                f"[{kept}] message_id={message.id} | date={dt}\n"
                f"{'-' * 78}\n"
                f"{body}\n"
            )

        try:
            if topic_id:
                try:
                    async for m in self.client.iter_messages(
                        chat_id,
                        limit=msg_iter_limit,
                        reply_to=topic_id,
                        reverse=True,
                    ):
                        scanned += 1
                        _append_row(m)
                        if scanned % 500 == 0:
                            print(f"  {C.DIM}scanned {scanned}... kept {kept}{C.RST}")
                except Exception:
                    fallback_cap = msg_iter_limit if msg_iter_limit else 20000
                    async for m in self.client.iter_messages(chat_id, limit=fallback_cap, reverse=True):
                        scanned += 1
                        rt = getattr(m, "reply_to", None)
                        top_id = getattr(rt, "reply_to_top_id", None) if rt else None
                        if m.id != topic_id and top_id != topic_id:
                            continue
                        _append_row(m)
                        if scanned % 500 == 0:
                            print(f"  {C.DIM}scanned {scanned}... kept {kept}{C.RST}")
            else:
                async for m in self.client.iter_messages(chat_id, limit=msg_iter_limit, reverse=True):
                    scanned += 1
                    _append_row(m)
                    if scanned % 500 == 0:
                        print(f"  {C.DIM}scanned {scanned}... kept {kept}{C.RST}")

            if kept == 0:
                print(f"  {C.YEL}Tidak ada text yang bisa diekspor.{C.RST}")
                input(f"\n  {C.DIM}Press Enter…{C.RST}")
                return

            header_lines = [
                "TELEGRAM TOPIC TEXT EXPORT",
                "=" * 78,
                f"Chat      : {chat_name}",
                f"Chat ID   : {chat_id}",
                f"Topic ID  : {topic_id or '-'}",
                f"Scanned   : {scanned}",
                f"Text rows : {kept}",
                f"Generated : {datetime.now():%Y-%m-%d %H:%M:%S}",
                "=" * 78,
                "",
            ]
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("\n".join(header_lines))
                f.write("\n".join(rows))

            print(f"\n  {C.GRN}✅ Export selesai{C.RST}")
            print(f"  {C.WHT}Scanned   : {scanned}{C.RST}")
            print(f"  {C.WHT}Text rows : {kept}{C.RST}")
            print(f"  {C.WHT}File      : {out_path}{C.RST}")
            logging.info("ExportText: chat=%s topic=%s scanned=%s kept=%s", chat_id, topic_id, scanned, kept)

        except Exception as e:
            print(f"  {C.RED}❌ Export gagal: {e}{C.RST}")
            logging.error("Export text topic failed: %s", e)

        input(f"\n  {C.DIM}Press Enter…{C.RST}")

    # ── main loop ────────────────────────────────────────────
       # ── manage accounts ──────────────────────────────────────
    async def _manage_accounts(self):
        while True:
            cls()
            header("KELOLA AKUN", 60)

            accounts = self.cfg.get("accounts", {})
            print()
            for i, (name, acc) in enumerate(accounts.items(), 1):
                active = f" {C.GRN}◀ ACTIVE{C.RST}" if name == self.active_account else ""
                print(f"    {C.CYN}[{i}]{C.RST}  {C.WHT}{name:<15s}{C.RST}"
                      f"  📱 {acc['phone']}"
                      f"  {C.DIM}session: {acc['session_name']}{C.RST}{active}")

            print(f"\n  {C.BOLD}OPSI:{C.RST}")
            print(f"    {C.CYN}[a]{C.RST}  ➕ Tambah akun")
            print(f"    {C.CYN}[d]{C.RST}  🗑️  Hapus akun")
            print(f"    {C.CYN}[e]{C.RST}  ✏️  Edit akun")
            print(f"    {C.CYN}[0]{C.RST}  ↩️  Kembali")

            ch = input(f"\n  Pilih: ").strip().lower()

            if ch == "0":
                break

            elif ch == "a":
                self._add_new_account()
                input(f"\n  {C.DIM}Press Enter…{C.RST}")

            elif ch == "d":
                name = input(f"  Nama akun yang dihapus: ").strip()
                if name in accounts:
                    if name == self.active_account:
                        print(f"  {C.RED}❌ Tidak bisa hapus akun aktif!{C.RST}")
                    elif input(f"  {C.YEL}Hapus '{name}'? (y/n): {C.RST}"
                               ).strip().lower() == "y":
                        del self.cfg["accounts"][name]
                        save_config(self.cfg)
                        sf = accounts[name].get("session_name", "")
                        for ext in (".session", ".session-journal"):
                            if os.path.exists(sf + ext):
                                os.remove(sf + ext)
                        print(f"  {C.GRN}✅ Dihapus{C.RST}")
                else:
                    print(f"  {C.RED}Akun tidak ditemukan{C.RST}")
                input(f"\n  {C.DIM}Press Enter…{C.RST}")

            elif ch == "e":
                name = input(f"  Nama akun yang diedit: ").strip()
                if name in accounts:
                    acc = accounts[name]
                    print(f"\n  {C.DIM}Kosongkan untuk tidak mengubah{C.RST}\n")
                    phone = input(f"  Phone [{acc['phone']}]: ").strip()
                    api_id = input(f"  API ID [{acc['api_id']}]: ").strip()
                    api_hash = input(f"  API Hash [{acc['api_hash'][:10]}...]: ").strip()
                    if phone:
                        acc["phone"] = phone
                    if api_id:
                        acc["api_id"] = int(api_id)
                    if api_hash:
                        acc["api_hash"] = api_hash
                    save_config(self.cfg)
                    print(f"  {C.GRN}✅ Updated{C.RST}")
                else:
                    print(f"  {C.RED}Akun tidak ditemukan{C.RST}")
                input(f"\n  {C.DIM}Press Enter…{C.RST}")

    # ── main loop ────────────────────────────────────────────
    async def run(self):
        banner()

        # === PILIH AKUN ===
        acc = self.select_account()
        self._create_client(acc)

        print(f"\n  {C.DIM}Connecting as {C.WHT}{self.active_account}"
              f"{C.DIM}...{C.RST}")
        me = await self._connect_account(acc)
        print(f"  {C.GRN}✅ {me.first_name} "
              f"(@{me.username or 'N/A'}) — {acc['phone']}{C.RST}\n")

        self.engine = DownloadEngine(self.client, self.cfg)

        while True:
            cls()
            banner()

            # Show active account
            print(f"  {C.DIM}Active: {C.GRN}{self.active_account} "
                  f"({acc['phone']}){C.RST}\n")

            print(f"  {C.BOLD}MAIN MENU{C.RST}\n")
            print(f"    {C.CYN}[1]{C.RST}  📂  Browse & Download")
            print(f"    {C.CYN}[2]{C.RST}  🔗  Download from Link")
            print(f"    {C.CYN}[3]{C.RST}  📊  Scan / Rebuild DB")
            print(f"    {C.CYN}[4]{C.RST}  ⚙️   Settings")
            print(f"    {C.CYN}[5]{C.RST}  📋  List Chats")
            print(f"    {C.CYN}[6]{C.RST}  🔄  Ganti Akun")
            print(f"    {C.CYN}[7]{C.RST}  👤  Kelola Akun")
            print(f"    {C.CYN}[8]{C.RST}  🤖  Auto Scraping")
            print(f"    {C.CYN}[9]{C.RST}  📝  Export Text Topic (TXT)")
            print(f"    {C.CYN}[0]{C.RST}  🚪  Exit\n")

            ch = input(f"  {C.BOLD}Select (0-9): {C.RST}").strip()

            if ch == "1":
                grp = input(
                    f"\n  {C.CYN}Group ID / @username / link topic: {C.RST}"
                ).strip()
                if grp:
                    try:
                        chat_id, topic_id = await self._resolve_chat_topic(grp)
                        if chat_id is None:
                            raise ValueError("Target chat tidak valid")
                        await self.browse(chat_id, topic_id=topic_id)
                    except Exception as e:
                        print(f"  {C.RED}❌ {e}{C.RST}")
                        input(f"\n  {C.DIM}Press Enter…{C.RST}")

            elif ch == "2":
                await self.dl_from_link()

            elif ch == "3":
                grp = input(
                    f"\n  {C.CYN}Group ID / @username / link topic: {C.RST}"
                ).strip()
                if grp:
                    lim = input(
                        f"  {C.CYN}Limit [{self.cfg['scan_limit']}]: {C.RST}"
                    ).strip()
                    lim = int(lim) if lim.isdigit() else self.cfg["scan_limit"]
                    try:
                        chat_id, topic_id = await self._resolve_chat_topic(grp)
                        if chat_id is None:
                            raise ValueError("Target chat tidak valid")
                        await self.scan(chat_id, lim, topic_id=topic_id)
                    except Exception as e:
                        print(f"  {C.RED}❌ {e}{C.RST}")
                    input(f"\n  {C.DIM}Press Enter…{C.RST}")

            elif ch == "4":
                await self.settings()

            elif ch == "5":
                header("YOUR CHATS", 60)
                print(f"\n  {C.DIM}Loading…{C.RST}\n")
                dialogs = await self.client.get_dialogs(limit=50)
                for dlg in dialogs:
                    e = dlg.entity
                    ico = ("👥" if getattr(e, "megagroup", False) else
                           "📢" if getattr(e, "broadcast", False) else
                           "🤖" if getattr(e, "bot", False) else "💬")
                    un = getattr(e, "username", "") or ""
                    print(
                        f"  {ico} {C.WHT}{dlg.name or '?':<35s}{C.RST}  "
                        f"{C.DIM}ID:{e.id}{C.RST}"
                        + (f"  {C.CYN}@{un}{C.RST}" if un else "")
                    )
                input(f"\n  {C.DIM}Press Enter…{C.RST}")

            # === GANTI AKUN ===
            elif ch == "6":
                await self.client.disconnect()
                acc = self.select_account()
                self._create_client(acc)
                print(f"\n  {C.DIM}Connecting...{C.RST}")
                me = await self._connect_account(acc)
                print(f"  {C.GRN}✅ Switched to {me.first_name} "
                      f"(@{me.username or 'N/A'}){C.RST}")
                self.engine = DownloadEngine(self.client, self.cfg)
                self.msg_cache.clear()
                input(f"\n  {C.DIM}Press Enter…{C.RST}")

            # === KELOLA AKUN ===
            elif ch == "7":
                await self._manage_accounts()

            elif ch == "8":
                await self.auto_scrape()

            elif ch == "9":
                await self.export_text_topic()

            elif ch == "0":
                print(f"\n  {C.CYN}👋 Goodbye!{C.RST}\n")
                break

# ═══════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════

# ✅ NEW — works on all Python versions
if __name__ == "__main__":
    # Create event loop explicitly (fixes Python 3.10+)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    app = TelegramDownloader()
    try:
        loop.run_until_complete(app.run())
    except KeyboardInterrupt:
        print(f"\n\n  {C.YEL}⚠  Interrupted{C.RST}\n")
    except Exception as e:
        print(f"\n  {C.RED}❌ Fatal: {e}{C.RST}")
        logging.exception("Fatal error")
    finally:
        try:
            if app.client.is_connected():
                loop.run_until_complete(app.client.disconnect())
        except Exception:
            pass
        loop.close()


#cd "/Users/shofwan/Documents/PROJECT PYTHON/auto donwload telegram goji"
#source venv/bin/activate
#python3 autodownload.py
