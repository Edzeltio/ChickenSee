"""
sync.py — Store-and-forward sync from SQLite to Supabase.

Runs on its own thread. Every SYNC_INTERVAL_S seconds it drains all
unsynced rows in batches and upserts them to the Supabase table.
"""

import threading
import time

import config
import database

# ── Supabase client (created once) ────────────────────────────────────────────
_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client

    if not config.SUPABASE_URL or not config.SUPABASE_KEY:
        print("[sync] SUPABASE_URL / SUPABASE_KEY not set — sync disabled.")
        return None

    try:
        from supabase import create_client
        _client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
        print(f"[sync] Supabase client ready → table: {config.SUPABASE_TABLE}")
    except Exception as exc:
        print(f"[sync] Failed to create Supabase client: {exc}")

    return _client


# ── Sync one pass ─────────────────────────────────────────────────────────────
def _sync_once() -> int:
    """Drain all pending rows. Returns total rows synced this pass."""
    client = _get_client()
    if client is None:
        return 0

    total = 0
    while True:
        rows = database.get_unsynced()
        if not rows:
            break

        ids = [r["id"] for r in rows]

        # Upload the required telemetry fields only. Local raw ADC values and
        # alert bookkeeping remain local implementation details.
        payload = [
            {
                "id": r["id"],
                "recorded_at": r["recorded_at"],
                "temperature": r["temperature"],
                "humidity": r["humidity"],
                "ammonia_ppm": r["ammonia_ppm"],
                "sound_db": r["sound_db"],
            }
            for r in rows
        ]

        try:
            client.table(config.SUPABASE_TABLE).upsert(payload).execute()
            database.mark_synced(ids)
            total += len(ids)
        except Exception as exc:
            print(f"[sync] Upsert failed: {exc}")
            break   # try again next interval

    return total


# ── Runtime settings override ─────────────────────────────────────────────────

def apply_settings(s: dict) -> None:
    """Called by main.py after setup UI; sync interval is already written to
    config by config.apply_settings(), so nothing extra is needed here.
    This hook exists for future per-module overrides."""
    interval = s.get("sync_interval_s", config.SYNC_INTERVAL_S)
    print(f"[sync] Interval set to {interval} s")


# ── Main loop ─────────────────────────────────────────────────────────────────
def run(stop_event: threading.Event) -> None:
    _get_client()   # validate credentials on startup
    while not stop_event.is_set():
        try:
            synced = _sync_once()
            if synced:
                print(f"[sync] Uploaded {synced} row(s) to Supabase.")
        except Exception as exc:
            print(f"[sync] Unexpected error: {exc}")

        # Use the (possibly user-overridden) interval from config.
        # Wake up early if stop is requested.
        stop_event.wait(timeout=config.SYNC_INTERVAL_S)
