"""
collector.py — Arduino serial reader.

Pipeline:
  Arduino JSON frame
    → ADC conversions  (raw → ppm / dB)
    → alert evaluation (§3.3.7.x thresholds)
    → SQLite insert
    → live telemetry snapshot  (read by camera.py for overlay)
"""

import json
import math
import random
import threading
import time

import serial
import serial.tools.list_ports

import config
import database

# ── Live telemetry snapshot ───────────────────────────────────────────────────
# Updated by the serial thread; read by camera.py.
# Initialised with None; seeded from the DB on first run() call so the overlay
# shows the last known values immediately instead of N/A while waiting for the
# first Arduino frame.
_lock = threading.Lock()
_live: dict = {
    "temperature"   : None,
    "humidity"      : None,
    "ammonia_ppm"   : None,
    "sound_db"      : None,
    "temp_status"   : config.STATUS_NORMAL,
    "hum_status"    : config.STATUS_NORMAL,
    "ammonia_status": config.STATUS_NORMAL,
    "sound_status"  : config.STATUS_NORMAL,
    "recorded_at"   : None,
}


def get_live() -> dict:
    """Return a snapshot of the latest sensor values (thread-safe)."""
    with _lock:
        return dict(_live)


def _update_live(**kwargs) -> None:
    """
    Merge kwargs into _live.
    Only non-None values overwrite existing entries so that a transient
    sensor failure (e.g. DHT22 returning null) does not erase the last
    known good reading from the overlay.
    """
    with _lock:
        for key, value in kwargs.items():
            if value is not None:
                _live[key] = value
            elif _live.get(key) is None:
                # Accept None only if we have never had a good reading.
                _live[key] = value


def _seed_from_db() -> None:
    """
    On startup, load the most recent row from SQLite into _live so the camera
    overlay immediately shows the last known values rather than all N/A.
    """
    try:
        row = database.get_latest()
        if row:
            with _lock:
                for key in (
                    "temperature", "humidity", "ammonia_ppm", "sound_db",
                    "temp_status", "hum_status", "ammonia_status",
                    "sound_status", "recorded_at",
                ):
                    if row.get(key) is not None:
                        _live[key] = row[key]
            print("[collector] Seeded overlay from last DB row.")
    except Exception as exc:
        print(f"[collector] Could not seed from DB: {exc}")


# ── Auto-detect Arduino port ──────────────────────────────────────────────────
# [CONFIG] Add your board's USB vendor ID here if auto-detect fails.
_ARDUINO_VIDS = {0x2341, 0x1A86, 0x0403}


def _find_port() -> str | None:
    for p in serial.tools.list_ports.comports():
        if p.vid in _ARDUINO_VIDS:
            return p.device
    return None


# ── Main loop ─────────────────────────────────────────────────────────────────
def run(stop_event: threading.Event) -> None:
    # Show last known readings on the overlay while connecting.
    _seed_from_db()

    if config.SIMULATE:
        print(
            f"[collector] Simulation mode enabled — generating readings every "
            f"{config.SIMULATION_INTERVAL_S:g} s (no Arduino required)."
        )
        _simulation_loop(stop_event)
        return

    port = config.SERIAL_PORT or _find_port()
    if not port:
        print(
            "[collector] Arduino not found. Set SERIAL_PORT or run with "
            "--simulate to test without hardware."
        )
        return

    while not stop_event.is_set():
        try:
            _read_loop(port, stop_event)
        except serial.SerialException as exc:
            print(f"[collector] Serial error: {exc} — retrying in 5 s …")
            stop_event.wait(5)
        except Exception as exc:
            print(f"[collector] Unexpected error: {exc} — retrying in 5 s …")
            stop_event.wait(5)


def _simulation_loop(stop_event: threading.Event) -> None:
    """Generate realistic sensor payloads so the full pipeline can be tested."""
    started = time.monotonic()
    while not stop_event.is_set():
        elapsed = time.monotonic() - started
        # Small variations keep the overlay and database useful during a test.
        temperature = round(24.0 + 2.0 * math.sin(elapsed / 45.0) + random.uniform(-0.4, 0.4), 1)
        humidity = round(60.0 + 7.0 * math.sin(elapsed / 60.0 + 1.0) + random.uniform(-1.0, 1.0), 1)
        ammonia_raw = max(0, min(1023, int(110 + 25 * math.sin(elapsed / 35.0) + random.uniform(-8, 8))))
        sound_raw = max(0, min(1023, int(500 + 70 * math.sin(elapsed / 12.0) + random.uniform(-35, 35))))
        _process({
            "temperature": temperature,
            "humidity": humidity,
            "mq137_raw": ammonia_raw,
            "sound_rms": sound_raw,
            "warming": False,
        })
        stop_event.wait(config.SIMULATION_INTERVAL_S)


def _read_loop(port: str, stop_event: threading.Event) -> None:
    print(f"[collector] Opening {port} @ {config.SERIAL_BAUD} baud …")
    with serial.Serial(port, config.SERIAL_BAUD, timeout=10) as ser:
        print("[collector] Connected to Arduino.")
        while not stop_event.is_set():
            raw = ser.readline()
            if not raw:
                continue

            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("{"):
                print(f"[arduino]   {line}")
                continue

            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"[collector] Bad JSON ({exc}): {line!r}")
                continue

            if "error" in payload:
                print(f"[arduino]   error: {payload['error']}")
                continue

            if "info" in payload:
                print(f"[arduino]   {payload['info']}")
                continue

            _process(payload)


def _process(payload: dict) -> None:
    from datetime import datetime, timezone

    temperature = payload.get("temperature")   # °C   or None (DHT22 null)
    humidity    = payload.get("humidity")      # %    or None (DHT22 null)
    ammonia_raw = payload.get("mq137_raw")     # ADC 0–1023 or None

    # Accept raw readings from the current sketch and calculated values from
    # older sketches.  Do not use `or` here: zero is a valid ADC reading.
    sound_raw = payload.get("sound_rms")
    if sound_raw is None:
        sound_raw = payload.get("sound_peak")

    warming = bool(payload.get("warming", 0))

    # During MQ-137 warm-up the reading is meaningless — treat as None so the
    # overlay shows the last known good value and no false alert is stored.
    ammonia_ppm = None if warming else (
        config.adc_to_ppm(ammonia_raw)
        if ammonia_raw is not None
        else payload.get("ammonia_ppm", payload.get("ammonia"))
    )
    sound_db = (
        config.adc_to_db(sound_raw)
        if sound_raw is not None
        else payload.get("sound_db", payload.get("sound"))
    )

    t_status = config.eval_temp(temperature)
    h_status = config.eval_humidity(humidity)
    a_status = config.eval_ammonia(ammonia_ppm)
    s_status = config.eval_sound(sound_db)

    recorded_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    # Persist to SQLite (always — even None values are useful for gap analysis).
    row_id = database.insert_reading(
        temperature    = temperature,
        humidity       = humidity,
        ammonia_ppm    = ammonia_ppm,
        ammonia_raw    = ammonia_raw,
        sound_db       = sound_db,
        sound_raw      = sound_raw,
        temp_status    = t_status,
        hum_status     = h_status,
        ammonia_status = a_status,
        sound_status   = s_status,
        recorded_at    = recorded_at,
    )

    # Merge into live snapshot — None values keep the last known good reading.
    _update_live(
        temperature    = temperature,
        humidity       = humidity,
        ammonia_ppm    = ammonia_ppm,
        sound_db       = sound_db,
        temp_status    = t_status,
        hum_status     = h_status,
        ammonia_status = a_status,
        sound_status   = s_status,
        recorded_at    = recorded_at,
    )

    # Console log — show warming notice for ammonia when relevant.
    nh3_display = "warming up…" if (warming and ammonia_raw is not None) else ammonia_ppm
    print(
        f"[collector] #{row_id} {recorded_at[11:19]}"
        f"  temp={temperature}°C"
        f"  hum={humidity}%"
        f"  NH3={nh3_display} ppm"
        f"  snd={sound_db} dB"
        + (
            "  !! " + "  ".join(
                f"{lbl}:{config.STATUS_LABEL[st]}"
                for lbl, st in [
                    ("TEMP", t_status), ("HUM", h_status),
                    ("NH3", a_status),  ("SND", s_status),
                ]
                if st
            )
            if any([t_status, h_status, a_status, s_status]) else ""
        )
    )
