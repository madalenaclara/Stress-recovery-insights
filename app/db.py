"""Tiny SQLite persistence layer for daily metrics.

One row per day. Re-ingesting the same day merges non-null fields, so repeatedly
POSTing overlapping exports from Health Auto Export is safe and idempotent.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator, Optional

from .scoring import DailyMetrics

DB_PATH = os.environ.get("SRI_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "data.db"))

_FIELDS = ("hrv", "resting_hr", "sleep_hours", "respiratory_rate",
           "wrist_temperature", "spo2", "avg_daytime_hr", "active_energy")


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _conn() as conn:
        cols = ", ".join(f"{f} REAL" for f in _FIELDS)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS daily_metrics (
                date TEXT PRIMARY KEY,
                {cols}
            )
        """)


def _row_to_metrics(row: sqlite3.Row) -> DailyMetrics:
    return DailyMetrics(date=row["date"], **{f: row[f] for f in _FIELDS})


def upsert_day(day: DailyMetrics) -> None:
    """Insert or merge a day, preferring incoming non-null values."""
    with _conn() as conn:
        existing = conn.execute(
            "SELECT * FROM daily_metrics WHERE date = ?", (day.date,)
        ).fetchone()

        values = {f: getattr(day, f) for f in _FIELDS}
        if existing:
            for f in _FIELDS:
                if values[f] is None:
                    values[f] = existing[f]

        placeholders = ", ".join(f":{f}" for f in _FIELDS)
        assignments = ", ".join(f"{f}=:{f}" for f in _FIELDS)
        params = dict(values, date=day.date)
        conn.execute(
            f"INSERT INTO daily_metrics (date, {', '.join(_FIELDS)}) "
            f"VALUES (:date, {placeholders}) "
            f"ON CONFLICT(date) DO UPDATE SET {assignments}",
            params,
        )


def get_all() -> list[DailyMetrics]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM daily_metrics ORDER BY date").fetchall()
        return [_row_to_metrics(r) for r in rows]


def get_history_before(date: str, window_days: int) -> list[DailyMetrics]:
    """Return up to `window_days` rows strictly before `date` (for baselines)."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM daily_metrics WHERE date < ? ORDER BY date DESC LIMIT ?",
            (date, window_days),
        ).fetchall()
        return [_row_to_metrics(r) for r in reversed(rows)]


def clear() -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM daily_metrics")
