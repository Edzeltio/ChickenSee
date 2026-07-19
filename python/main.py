"""
main.py — Entry point for the sensor logger.

Flow:
  1. Load .env
  2. Show startup configuration UI (setup_ui.py)
  3. Apply chosen settings to config + camera
  4. Start three daemon threads: collector, sync, camera

Press Ctrl+C or close the preview window to stop cleanly.
"""

import signal
import sys
import threading
from pathlib import Path

# ── 1. Load .env before importing anything that reads env vars ────────────────
from dotenv import load_dotenv

_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
    print(f"[main] Loaded env from {_env_path}")
else:
    print(f"[main] No .env found at {_env_path} — using system environment variables.")

# ── 2. Show the configuration UI ─────────────────────────────────────────────
# This blocks until the user clicks "Start Monitoring" (or exits).
import setup_ui   # noqa: E402

print("[main] Opening configuration window …")
settings = setup_ui.show()   # returns dict; calls sys.exit(0) if window closed
print("[main] Configuration confirmed — starting system …")

# ── 3. Apply settings to config + camera ─────────────────────────────────────
import config    # noqa: E402
import database  # noqa: E402
import collector # noqa: E402
import sync      # noqa: E402
import camera    # noqa: E402

config.apply_settings(settings)
camera.apply_settings(settings)
sync.apply_settings(settings)


def main() -> None:
    stop_event = threading.Event()

    def _shutdown(sig, _frame):
        print(f"\n[main] Signal {sig} received — shutting down …")
        stop_event.set()

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    database.init()

    threads = [
        threading.Thread(target=collector.run, args=(stop_event,),
                         name="collector", daemon=True),
        threading.Thread(target=sync.run,      args=(stop_event,),
                         name="sync",      daemon=True),
        threading.Thread(target=camera.run,    args=(stop_event,),
                         name="camera",    daemon=True),
    ]

    print(f"[main] Starting {len(threads)} threads …")
    for t in threads:
        t.start()

    stop_event.wait()

    print("[main] Waiting for threads to finish …")
    for t in threads:
        t.join(timeout=5)

    print("[main] Done.")
    sys.exit(0)


if __name__ == "__main__":
    main()
