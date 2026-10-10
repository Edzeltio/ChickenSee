"""
camera.py — OpenCV camera recording with live sensor overlay.

Records continuous video to hourly MP4 segments.
Sensor values and alert status are burned into every frame.
"""

import threading
import time
from datetime import datetime

import cv2
import numpy as np

import config
import collector   # live telemetry snapshot
from recording import Recording, video_time

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
    "overlay_position": "bottom-left",
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


def _draw_overlay(frame: np.ndarray, data: dict, elapsed: float = 0) -> np.ndarray:
    """Append a sensor strip below the image; never draw over camera pixels."""

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
    hdr_text  = f"  Video {video_time(elapsed)} | {now.strftime('%Y-%m-%d %H:%M:%S')}"
    time_text = f"  Last: {recorded}"

    sensor_texts  = [f"  {lbl:11s}: {val}  {config.STATUS_LABEL[st]}" for lbl, val, st in rows]
    all_texts     = [hdr_text] + sensor_texts + [time_text]
    all_scales    = [font_scale_hdr] + [font_scale_row] * len(rows) + [font_scale_time]

    # Fit narrow camera feeds without changing/cropping the original image.
    max_text_width = max(
        cv2.getTextSize(t, _FONT, s, thickness)[0][0]
        for t, s in zip(all_texts, all_scales)
    )
    fit = min(1.0, max(1, frame.shape[1] - padding * 2 - 24) / max_text_width)
    font_scale_hdr *= fit
    font_scale_row *= fit
    font_scale_time *= fit
    all_scales = [font_scale_hdr] + [font_scale_row] * len(rows) + [font_scale_time]
    sizes = [cv2.getTextSize(t, _FONT, s, thickness)[0] for t, s in zip(all_texts, all_scales)]
    box_w = max(w for w, _ in sizes) + padding * 2
    box_h = padding * 2 + sum(h for _, h in sizes) + line_gap * len(all_texts) + 4

    # ── Position — use runtime setting, fall back to config ──────────────────
    image_h, w_f = frame.shape[:2]
    footer_h = box_h + 24
    # MP4 codecs generally require even dimensions.
    footer_h += (image_h + footer_h) % 2
    frame = np.concatenate((frame, np.zeros((footer_h, w_f, 3), dtype=frame.dtype)))
    h_f = frame.shape[0]
    margin    = 12
    pos       = _overlay.get("overlay_position", config.OVERLAY_POSITION)

    bx = w_f - box_w - margin if pos.endswith("right") else margin
    by = image_h + margin

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


# ── Camera open helpers ───────────────────────────────────────────────────────
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


def _open_rtsp_with_retries(
    endpoints: list[tuple[str, str]], max_tries: int
) -> tuple[cv2.VideoCapture | None, str | None, int | None]:
    """Try local/remote endpoints in order for each retry round."""
    for attempt in range(1, max_tries + 1):
        for index, (label, rtsp_url) in enumerate(endpoints):
            print(
                f"[camera] Tapo {label} connect attempt "
                f"{attempt}/{max_tries} …"
            )
            cap = _open_rtsp(rtsp_url)
            if cap is not None:
                return cap, rtsp_url, index
        if attempt < max_tries:
            time.sleep(config.TAPO_RECONNECT_DELAY_S)
    return None, None, None


def _open_local_camera(index: int) -> cv2.VideoCapture | None:
    """Open a locally attached camera (USB or built-in) by index."""
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  config.VIDEO_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.VIDEO_HEIGHT)
    return cap


# ── Main loop ─────────────────────────────────────────────────────────────────
def run(
    stop_event: threading.Event,
    settings_requested: threading.Event | None = None,
) -> None:
    tapo_endpoints = config.get_tapo_endpoints()
    rtsp_url: str | None = None
    tapo_endpoint_index: int | None = None
    using_rtsp = False   # tracks which source is active for reconnect logic

    # ── Initial connection ────────────────────────────────────────────────────
    if tapo_endpoints:
        print(
            f"[camera] Connecting to Tapo ({config.TAPO_CONNECTION_MODE} mode; "
            f"{len(tapo_endpoints)} endpoint(s); {config.TAPO_STREAM} stream) …"
        )
        cap, rtsp_url, tapo_endpoint_index = _open_rtsp_with_retries(
            tapo_endpoints, config.TAPO_MAX_CONNECT_TRIES
        )
        if cap is not None:
            using_rtsp = True
        else:
            print(
                f"[camera] Tapo endpoints unreachable after "
                f"{config.TAPO_MAX_CONNECT_TRIES} rounds — falling back to local camera "
                f"(index {config.TAPO_FALLBACK_CAMERA_INDEX})."
            )
    else:
        print(
            "[camera] No Tapo endpoint configured — using local camera "
            f"(index {config.TAPO_FALLBACK_CAMERA_INDEX})."
        )
        cap = None

    if not using_rtsp:
        cap = _open_local_camera(config.TAPO_FALLBACK_CAMERA_INDEX)
        if cap is None:
            print(
                f"[camera] Cannot open local camera "
                f"(index {config.TAPO_FALLBACK_CAMERA_INDEX}). "
                f"Recording skipped."
            )
            return

    # Resolution: RTSP reports its own; local camera honours the set request
    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    source_label = (f"Tapo RTSP ({tapo_endpoints[tapo_endpoint_index][0]})"
                    if using_rtsp and tapo_endpoint_index is not None
                    else f"local camera [{config.TAPO_FALLBACK_CAMERA_INDEX}]")
    print(f"[camera] {source_label} — {actual_w}×{actual_h} @ {config.VIDEO_FPS} fps")

    _record_loop(cap, stop_event, settings_requested, tapo_endpoints, using_rtsp)


def _record_loop(cap, stop_event, settings_requested, tapo_endpoints, using_rtsp):
    recording: Recording | None = None
    print("[camera] Preview keys: M = save video + SQLite, S = settings, Q/Esc = save and quit.")
    try:
        while not stop_event.is_set():
            loop_start = time.monotonic()
            ok, frame = cap.read() if cap is not None else (False, None)
            if not ok:
                # Do not append post-reconnect footage to a clip with a hidden gap.
                if recording:
                    recording.close("feed_lost")
                    recording = None
                if cap is not None:
                    cap.release()
                    cap = None
                print("[camera] Feed lost — retrying …")
                if stop_event.wait(config.TAPO_RECONNECT_DELAY_S):
                    break
                if using_rtsp:
                    cap, _, _ = _open_rtsp_with_retries(tapo_endpoints, 1)
                else:
                    cap = _open_local_camera(config.TAPO_FALLBACK_CAMERA_INDEX)
                continue

            active = _in_recording_window()
            data = collector.get_live()
            elapsed = recording.elapsed if recording else 0
            raw_frame = frame
            frame = _draw_overlay(raw_frame, data, elapsed)
            size = (frame.shape[1], frame.shape[0])

            if recording:
                reason = None
                if not active:
                    reason = "schedule_end"
                elif size != recording.size:
                    reason = "settings_or_resolution_changed"
                elif time.monotonic() - recording.started >= config.VIDEO_SEGMENT_DURATION:
                    reason = "automatic"
                if reason:
                    recording.close(reason)
                    recording = None
                    # This frame belongs to the new clip, starting at 00:00.
                    # Redraw from original image, not from the existing footer.
                    frame = _draw_overlay(raw_frame, data, 0)

            if active:
                if recording is None:
                    recording = Recording(frame.shape[1], frame.shape[0])
                recording.write(frame, data)

            if config.SHOW_PREVIEW:
                display = frame.copy()
                msg = "M: save | S: settings | Q: save and quit"
                if not active:
                    msg = "REC PAUSED | S: settings | Q: quit"
                # Controls are preview-only, not burned into the saved MP4.
                cv2.putText(display, msg, (10, display.shape[0] - 4),
                            _FONT, 0.4, _COLOR["text"], 1, _LINE_TYPE)
                cv2.imshow("Sensor Feed", display)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("m"), ord("M")):
                    if recording:
                        recording.close("manual")
                        recording = None
                    else:
                        print("[camera] Nothing to save — outside the recording schedule.")
                elif key in (ord("s"), ord("S")) and settings_requested is not None:
                    settings_requested.set()
                elif key in (ord("q"), ord("Q"), 27):
                    stop_event.set()
                elif cv2.getWindowProperty("Sensor Feed", cv2.WND_PROP_VISIBLE) < 1:
                    stop_event.set()

            stop_event.wait(max(0, 1.0 / config.VIDEO_FPS - (time.monotonic() - loop_start)))
    except Exception as exc:
        print(f"[camera] Recording failed: {exc}")
        stop_event.set()
        raise
    finally:
        try:
            if recording:
                recording.close("shutdown")
        finally:
            if cap is not None:
                cap.release()
            if config.SHOW_PREVIEW:
                cv2.destroyAllWindows()
        print("[camera] Stopped.")
