"""
database.py — SQLite local store.

Schema matches §3.3.7.x sensor thresholds:
  temperature, humidity          — DHT22  (°C, %)
  ammonia_ppm, ammonia_raw       — MQ-137 converted + raw ADC
  sound_db,    sound_raw         — LM386  converted + raw ADC
  temp_status, hum_status,
  ammonia_status, sound_status   — 0=normal  1=warning  2=critical
  synced                         — 0=pending Supabase sync
"""

import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import config

# ── Persistent connection ─────────────────────────────────────────────────────
_conn: sqlite3.Connection | None = None
_lock = threading.Lock()   # SQLite WAL allows concurrent reads; lock for writes


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript("""
            PRAGMA journal_mode = WAL;
            PRAGMA synchronous  = NORMAL;
            PRAGMA cache_size   = -8000;
            PRAGMA temp_store   = MEMORY;
        """)
    return _conn


# ── Schema ────────────────────────────────────────────────────────────────────
def init() -> None:
    conn = _get_conn()
    with _lock:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sensor_logs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at     TEXT    NOT NULL
                                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),

                -- DHT22
                temperature     REAL,
                humidity        REAL,

                -- MQ-137
                ammonia_ppm     REAL,
                ammonia_raw     INTEGER,

                -- LM386
                sound_db        REAL,
                sound_raw       INTEGER,

                -- Alert flags  0=normal  1=warning  2=critical
                temp_status     INTEGER NOT NULL DEFAULT 0,
                hum_status      INTEGER NOT NULL DEFAULT 0,
                ammonia_status  INTEGER NOT NULL DEFAULT 0,
                sound_status    INTEGER NOT NULL DEFAULT 0,

                synced          INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_recorded_at
                ON sensor_logs (recorded_at);
            CREATE INDEX IF NOT EXISTS idx_synced
                ON sensor_logs (synced);
            CREATE INDEX IF NOT EXISTS idx_ammonia_status
                ON sensor_logs (ammonia_status);
            CREATE INDEX IF NOT EXISTS idx_sound_status
                ON sensor_logs (sound_status);
        """)
    print(f"[db] Ready → {config.DB_PATH}")


# ── Write ─────────────────────────────────────────────────────────────────────
def insert_reading(
    temperature:    float | None,
    humidity:       float | None,
    ammonia_ppm:    float | None,
    ammonia_raw:    int   | None,
    sound_db:       float | None,
    sound_raw:      int   | None,
    temp_status:    int,
    hum_status:     int,
    ammonia_status: int,
    sound_status:   int,
    recorded_at:    str | None = None,
) -> int:
    ts = recorded_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    conn = _get_conn()
    with _lock:
        cur = conn.execute(
            """
            INSERT INTO sensor_logs (
                recorded_at,
                temperature, humidity,
                ammonia_ppm, ammonia_raw,
                sound_db,    sound_raw,
                temp_status, hum_status, ammonia_status, sound_status
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (ts,
             temperature, humidity,
             ammonia_ppm, ammonia_raw,
             sound_db,    sound_raw,
             temp_status, hum_status, ammonia_status, sound_status),
        )
        conn.commit()
    return cur.lastrowid


# ── Read (for sync) ───────────────────────────────────────────────────────────
def get_unsynced(limit: int = config.SYNC_BATCH_SIZE) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT id, recorded_at,
               temperature, humidity,
               ammonia_ppm, ammonia_raw,
               sound_db,    sound_raw,
               temp_status, hum_status, ammonia_status, sound_status
        FROM   sensor_logs
        WHERE  synced = 0
        ORDER  BY id ASC
        LIMIT  ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_synced(ids: list[int]) -> None:
    if not ids:
        return
    conn = _get_conn()
    placeholders = ",".join("?" * len(ids))
    with _lock:
        conn.execute(
            f"UPDATE sensor_logs SET synced = 1 WHERE id IN ({placeholders})",
            ids,
        )
        conn.commit()


# ── Stats ─────────────────────────────────────────────────────────────────────
def get_stats() -> dict:
    conn = _get_conn()
    row = conn.execute("""
        SELECT
            COUNT(*)                                          AS total,
            SUM(CASE WHEN synced         = 0 THEN 1 ELSE 0 END) AS pending,
            SUM(CASE WHEN temp_status    > 0 THEN 1 ELSE 0 END) AS temp_alerts,
            SUM(CASE WHEN hum_status     > 0 THEN 1 ELSE 0 END) AS hum_alerts,
            SUM(CASE WHEN ammonia_status > 0 THEN 1 ELSE 0 END) AS ammonia_alerts,
            SUM(CASE WHEN sound_status   > 0 THEN 1 ELSE 0 END) AS sound_alerts
        FROM sensor_logs
    """).fetchone()
    return dict(row)


# ── Latest reading (for camera overlay) ──────────────────────────────────────
def get_latest() -> dict | None:
    conn = _get_conn()
    row = conn.execute(
        """
        SELECT temperature, humidity,
               ammonia_ppm, sound_db,
               temp_status, hum_status, ammonia_status, sound_status,
               recorded_at
        FROM   sensor_logs
        ORDER  BY id DESC
        LIMIT  1
        """
    ).fetchone()
    return dict(row) if row else None
