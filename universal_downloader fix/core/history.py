"""
SQLite download history with search and stats.
"""

import sqlite3
import os
import json
import csv
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict
import logging

logger = logging.getLogger(__name__)


class DownloadHistory:
    """SQLite database for download history and stats."""

    def __init__(self, db_path: str = None):
        if not db_path:
            db_path = str(Path.home() / ".universal_downloader" / "history.db")
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS downloads (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    url         TEXT NOT NULL,
                    title       TEXT,
                    extractor   TEXT,
                    quality     TEXT,
                    filepath    TEXT,
                    filesize    INTEGER DEFAULT 0,
                    duration    INTEGER DEFAULT 0,
                    status      TEXT DEFAULT 'completed',
                    error       TEXT,
                    started_at  TEXT,
                    finished_at TEXT,
                    created_at  TEXT DEFAULT (datetime('now','localtime'))
                );
                CREATE INDEX IF NOT EXISTS idx_url ON downloads(url);
                CREATE INDEX IF NOT EXISTS idx_status ON downloads(status);
            """)

    def record(self, url: str, title: str = "", extractor: str = "",
               quality: str = "", filepath: str = "", filesize: int = 0,
               duration: int = 0, status: str = "completed", error: str = ""):
        """Record a download."""
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO downloads (url, title, extractor, quality, filepath,
                    filesize, duration, status, error, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (url, title, extractor, quality, filepath, filesize,
                  duration, status, error, datetime.now().isoformat()))

    def get_history(self, limit: int = 50, status: str = None,
                    search: str = None) -> List[Dict]:
        """Get download history."""
        with self._conn() as conn:
            if search:
                rows = conn.execute("""
                    SELECT * FROM downloads
                    WHERE title LIKE ? OR url LIKE ?
                    ORDER BY created_at DESC LIMIT ?
                """, (f'%{search}%', f'%{search}%', limit)).fetchall()
            elif status:
                rows = conn.execute("""
                    SELECT * FROM downloads WHERE status = ?
                    ORDER BY created_at DESC LIMIT ?
                """, (status, limit)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM downloads ORDER BY created_at DESC LIMIT ?
                """, (limit,)).fetchall()
            return [dict(r) for r in rows]

    def get_stats(self) -> Dict:
        """Get download statistics."""
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM downloads").fetchone()[0]
            success = conn.execute(
                "SELECT COUNT(*) FROM downloads WHERE status='completed'"
            ).fetchone()[0]
            failed = conn.execute(
                "SELECT COUNT(*) FROM downloads WHERE status='failed'"
            ).fetchone()[0]
            total_bytes = conn.execute(
                "SELECT COALESCE(SUM(filesize),0) FROM downloads WHERE status='completed'"
            ).fetchone()[0]
            return {
                "total": total,
                "successful": success,
                "failed": failed,
                "total_bytes": total_bytes,
                "total_gb": round(total_bytes / (1024**3), 2) if total_bytes else 0,
            }

    def is_downloaded(self, url: str) -> bool:
        """Check if URL was already downloaded."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM downloads WHERE url=? AND status='completed' LIMIT 1",
                (url,)
            ).fetchone()
            return row is not None

    def clear(self):
        """Clear all history."""
        with self._conn() as conn:
            conn.execute("DELETE FROM downloads")

    def export_csv(self, filepath: str):
        """Export history to CSV."""
        history = self.get_history(limit=999999)
        if not history:
            return
        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=history[0].keys())
            writer.writeheader()
            writer.writerows(history)
