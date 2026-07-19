"""
camera.py — OpenCV camera recording with live sensor overlay.

Records continuous video to hourly MP4 segments.
Sensor values and alert status are burned into every frame.
"""

import os
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

import config
import collector   # live telemetry snapshot

# ── Recording schedule ────────────────────────────────────────────────────────
# Populated by apply_settings() before run() is called.
_schedule: dict = {
    "rec_always":  True,
    "rec_start_h": 6,
    "rec_start_m": 0,
    "rec_end_h":   18,
    "rec_end_m":   0,
}

# ── Overlay visibility settings ───────────────────────────────────────────────
# Which sensors to burn into the video frame, and whether to show the HUD at all.
_overlay: dict = {
    "overlay_enabled":  True,
    "show_temp":        True,
    "show_humidity":    True,
    "show_ammonia":     True,
    "show_sound":       True,
    "overlay_position": "top-left",
}

# Sensor key → (display label, data keys, unit, decimals, eval function)
_SENSOR_META = [
    ("temp",     "Temp",    "temperature",  " °C",  1, "temp_status"),
    ("humidity", "Humidity", "humidity",     " %",   0, "hum_status"),
    ("ammonia",  "Ammonia", "ammonia_ppm",  " ppm", 1, "ammonia_status"),
    ("sound",    "Sound",   "sound_db",     " dB",  1, "sound_status"),
]


def apply_settings(s: dict) -> None:
    """Called by main.py after the setup UI closes."""
    for key in _schedule:
        if key in s:
            _schedule[key] = s[key]
    for key in _overlay:
        if key in s:
            _overlay[key] = s[key]
    # Segment duration is already applied to config by config.apply_settings().
    enabled_sensors = [
        name for name in ("temp", "humidity", "ammonia", "sound")
        if _overlay.get(f"show_{name}", True)
    ]
    print(
        f"[camera] Schedule — "
        + ("24/7" if _schedule["rec_always"] else
           f"{_schedule['rec_start_h']:02d}:{_schedule['rec_start_m']:02d}"
           f" → {_schedule['rec_end_h']:02d}:{_schedule['rec_end_m']:02d}")
        + f" | segment {config.VIDEO_SEGMENT_DURATION // 60} min"
    )
    print(
        f"[camera] Overlay — "
        + ("disabled" if not _overlay["overlay_enabled"]
           else f"position={_overlay['overlay_position']} "
                f"sensors=[{', '.join(enabled_sensors) or 'none'}]")
    )


def _in_recording_window() -> bool:
    """Return True if the current local time is inside the recording window."""
    if _schedule.get("rec_always", True):
        return True
    now   = datetime.now()
    start = now.replace(hour=_schedule["rec_start_h"],
                        minute=_schedule["rec_start_m"], second=0, microsecond=0)
    end   = now.replace(hour=_schedule["rec_end_h"],
                        minute=_schedule["rec_end_m"], second=0, microsecond=0)
    if start <= end:
        return start <= now <= end
    # Overnight window (e.g. 22:00 → 06:00)
    return now >= start or now <= end


# ── Overlay colour scheme ─────────────────────────────────────────────────────
# OpenCV uses (B, G, R) tuples
_COLOR = {
    "text"     : (255, 255, 255),   # white
    "ok"       : (100, 220, 100),   # green
    "warn"     : (0,   200, 255),   # amber
    "crit"     : (50,   50, 255),   # red
    "box_bg"   : (0,     0,   0),   # black
    "separator": (150, 150, 150),   # grey
}

_FONT      = cv2.FONT_HERSHEY_SIMPLEX
_LINE_TYPE = cv2.LINE_AA
_BOX_ALPHA = 0.55   # overlay box opacity

# Status → colour lookup
_STATUS_COLOR = {
    config.STATUS_NORMAL  : _COLOR["ok"],
    config.STATUS_WARNING : _COLOR["warn"],
    config.STATUS_CRITICAL: _COLOR["crit"],
}


# ── Overlay rendering ─────────────────────────────────────────────────────────
def _fmt(val, unit: str, decimals: int = 1) -> str:
    if val is None:
        return "N/A"
    return f"{round(float(val), decimals)}{unit}" if decimals else f"{int(val)}{unit}"


def _draw_overlay(frame: np.ndarray, data: dict) -> np.ndarray:
    """Burn the HUD onto frame.  Respects _overlay toggle + sensor selection."""

    # Nothing to do if the whole overlay is disabled
    if not _overlay.get("overlay_enabled", True):
        return frame

    now      = datetime.now()
    recorded = (data.get("recorded_at") or "—")[:19].replace("T", " ")

    # Build only the rows the user has enabled
    # _SENSOR_META: (key, label, data_field, unit, decimals, status_field)
    rows = []
    for sensor_key, label, data_field, unit, decimals, status_field in _SENSOR_META:
        if _overlay.get(f"show_{sensor_key}", True):
            rows.append((
                label,
                _fmt(data.get(data_field), unit, decimals),
                data.get(status_field, 0),
            ))

    # If every sensor was unchecked render only the clock + timestamp
    font_scale_hdr  = 0.55
    font_scale_row  = 0.52
    font_scale_time = 0.40
    thickness       = 1
    padding         = 10
    line_gap        = 6

    # ── Measure all lines to size the box ────────────────────────────────────
    hdr_text  = f"  {now.strftime('%Y-%m-%d')}   {now.strftime('%H:%M:%S')}  "
    time_text = f"  Last: {recorded}"

    sensor_texts  = [f"  {lbl:11s}: {val}  {config.STATUS_LABEL[st]}" for lbl, val, st in rows]
    all_texts     = [hdr_text] + sensor_texts + [time_text]
    all_scales    = [font_scale_hdr] + [font_scale_row] * len(rows) + [font_scale_time]

    sizes = [cv2.getTextSize(t, _FONT, s, thickness)[0] for t, s in zip(all_texts, all_scales)]
    box_w = max(w for w, _ in sizes) + padding * 2
    box_h = padding * 2 + sum(h for _, h in sizes) + line_gap * len(all_texts) + 4

    # ── Position — use runtime setting, fall back to config ──────────────────
    h_f, w_f = frame.shape[:2]
    margin    = 12
    pos       = _overlay.get("overlay_position", config.OVERLAY_POSITION)

    if pos == "top-right":
        bx, by = w_f - box_w - margin, margin
    elif pos == "bottom-left":
        bx, by = margin, h_f - box_h - margin
    elif pos == "bottom-right":
        bx, by = w_f - box_w - margin, h_f - box_h - margin
    else:   # top-left (default)
        bx, by = margin, margin

    # Clamp to frame edges
    bx = max(0, min(bx, w_f - box_w))
    by = max(0, min(by, h_f - box_h))

    # ── Semi-transparent background box ──────────────────────────────────────
    roi     = frame[by:by + box_h, bx:bx + box_w]
    overlay_bg = roi.copy()
    cv2.rectangle(overlay_bg, (0, 0), (box_w, box_h), _COLOR["box_bg"], -1)
    cv2.addWeighted(overlay_bg, _BOX_ALPHA, roi, 1 - _BOX_ALPHA, 0, roi)
    frame[by:by + box_h, bx:bx + box_w] = roi

    # ── Text lines ────────────────────────────────────────────────────────────
    cy = by + padding

    # Header — date/time
    (_, lh), _ = cv2.getTextSize(hdr_text, _FONT, font_scale_hdr, thickness)
    cy += lh
    cv2.putText(frame, hdr_text, (bx + padding, cy),
                _FONT, font_scale_hdr, _COLOR["text"], thickness, _LINE_TYPE)
    cy += line_gap

    if rows:
        # Separator
        cv2.line(frame, (bx + padding, cy), (bx + box_w - padding, cy),
                 _COLOR["separator"], 1, _LINE_TYPE)
        cy += line_gap + 2

        # Sensor rows — value in white, status badge in status colour
        for lbl, val, st in rows:
            row_text = f"  {lbl:11s}: {val}"
            st_text  = f"  {config.STATUS_LABEL[st]}"
            (_, lh), _ = cv2.getTextSize(row_text + st_text, _FONT, font_scale_row, thickness)
            cy += lh
            cv2.putText(frame, row_text, (bx + padding, cy),
                        _FONT, font_scale_row, _COLOR["text"], thickness, _LINE_TYPE)
            tw = cv2.getTextSize(row_text, _FONT, font_scale_row, thickness)[0][0]
            cv2.putText(frame, st_text, (bx + padding + tw, cy),
                        _FONT, font_scale_row, _STATUS_COLOR[st], thickness, _LINE_TYPE)
            cy += line_gap

    # Separator before timestamp
    cv2.line(frame, (bx + padding, cy), (bx + box_w - padding, cy),
             _COLOR["separator"], 1, _LINE_TYPE)
    cy += line_gap + 2

    # Last-reading timestamp
    (_, lh), _ = cv2.getTextSize(time_text, _FONT, font_scale_time, thickness)
    cy += lh
    cv2.putText(frame, time_text, (bx + padding, cy),
                _FONT, font_scale_time, _COLOR["separator"], thickness, _LINE_TYPE)

    return frame


# ── Video segment writer ──────────────────────────────────────────────────────
def _new_writer(width: int, height: int) -> tuple[cv2.VideoWriter, str]:
    Path(config.VIDEO_SAVE_PATH).mkdir(parents=True, exist_ok=True)
    ts   = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = str(Path(config.VIDEO_SAVE_PATH) / f"rec_{ts}{config.VIDEO_EXT}")
    fourcc = cv2.VideoWriter_fourcc(*config.VIDEO_FOURCC)
    writer = cv2.VideoWriter(path, fourcc, config.VIDEO_FPS, (width, height))
    print(f"[camera] Recording → {path}")
    return writer, path


# ── RTSP stream helpers ───────────────────────────────────────────────────────
def _open_rtsp(rtsp_url: str) -> cv2.VideoCapture | None:
    """Open the Tapo RTSP stream via FFmpeg backend.

    CAP_PROP_BUFFERSIZE = 1 keeps the frame close to real-time by
    discarding buffered frames rather than playing them back late.
    Returns None if the stream cannot be opened.
    """
    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


# ── Main loop ─────────────────────────────────────────────────────────────────
def run(stop_event: threading.Event) -> None:
    rtsp_url = config.TAPO_RTSP_URL

    # Validate that TAPO_IP is configured
    if not config.TAPO_IP:
        print("[camera] TAPO_IP is not set — add it to your .env file. "
              "Recording skipped.")
        return
    if not config.TAPO_PASSWORD:
        print("[camera] TAPO_PASSWORD is not set — add it to your .env file. "
              "Recording skipped.")
        return

    print(f"[camera] Connecting to Tapo C530WS at {config.TAPO_IP} "
          f"({config.TAPO_STREAM} stream) …")

    cap = _open_rtsp(rtsp_url)
    if cap is None:
        print(f"[camera] Cannot connect to RTSP stream at {config.TAPO_IP}. "
              f"Check the IP, credentials, and that RTSP is enabled in the Tapo app. "
              f"Recording skipped.")
        return

    # Resolution comes from the camera — do not force-set it over RTSP
    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"[camera] Connected — {actual_w}×{actual_h} @ {config.VIDEO_FPS} fps")

    writer: cv2.VideoWriter | None = None
    seg_start   = time.time()
    frame_delay = 1.0 / config.VIDEO_FPS
    was_recording = False   # track transitions for log messages

    while not stop_event.is_set():
        loop_start = time.time()

        ok, frame = cap.read()
        if not ok:
            # Network hiccup or camera rebooted — attempt reconnect
            print(f"[camera] Stream lost — retrying in "
                  f"{config.TAPO_RECONNECT_DELAY_S:.0f} s …")
            cap.release()
            cap = None
            time.sleep(config.TAPO_RECONNECT_DELAY_S)
            cap = _open_rtsp(rtsp_url)
            if cap is None:
                print("[camera] Reconnect failed — will retry …")
            else:
                actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                print(f"[camera] Reconnected — {actual_w}×{actual_h}")
            continue

        # Burn overlay into frame
        data  = collector.get_live()
        frame = _draw_overlay(frame, data)

        # ── Recording schedule check ──────────────────────────────────────────
        active = _in_recording_window()

        if active and not was_recording:
            # Entered the recording window — open a new segment
            writer, _ = _new_writer(actual_w, actual_h)
            seg_start = time.time()
            was_recording = True

        elif not active and was_recording:
            # Left the recording window — release the current file
            if writer:
                writer.release()
                writer = None
            sh = _schedule["rec_start_h"]
            sm = _schedule["rec_start_m"]
            print(f"[camera] Outside recording window — paused until "
                  f"{sh:02d}:{sm:02d}.")
            was_recording = False

        # Write frame only when inside the window
        if active and writer:
            writer.write(frame)

            # Rotate segment when duration is exceeded
            if time.time() - seg_start >= config.VIDEO_SEGMENT_DURATION:
                writer.release()
                writer, _ = _new_writer(actual_w, actual_h)
                seg_start = time.time()

        # ── Preview window ────────────────────────────────────────────────────
        if config.SHOW_PREVIEW:
            # Show a "PAUSED" banner when outside the recording window
            display = frame.copy()
            if not active:
                h_f, w_f = display.shape[:2]
                msg = "REC PAUSED — outside scheduled window"
                (tw, th), _ = cv2.getTextSize(msg, _FONT, 0.55, 1)
                cv2.rectangle(display,
                              (w_f // 2 - tw // 2 - 8, h_f - 36),
                              (w_f // 2 + tw // 2 + 8, h_f - 10),
                              (0, 0, 0), -1)
                cv2.putText(display, msg,
                            (w_f // 2 - tw // 2, h_f - 16),
                            _FONT, 0.55, (0, 180, 255), 1, _LINE_TYPE)
            cv2.imshow("Sensor Feed", display)
            if cv2.waitKey(1) & 0xFF in (ord("q"), ord("Q"), 27):
                stop_event.set()
                break

        # Pace the loop to target FPS
        elapsed = time.time() - loop_start
        sleep   = frame_delay - elapsed
        if sleep > 0:
            time.sleep(sleep)

    # Cleanup
    if writer:
        writer.release()
    if cap is not None:
        cap.release()
    if config.SHOW_PREVIEW:
        cv2.destroyAllWindows()
    print("[camera] Stopped.")
