"""
config.py — Single source of truth for all settings.
All [CONFIG] labels mark values you may need to change.
"""

import os
import platform
from pathlib import Path
from urllib.parse import quote

# Detect platform
IS_WINDOWS: bool = platform.system() == "Windows"

# Root sensor/ directory (one level up from python/)
SENSOR_DIR: Path = Path(__file__).parent.parent

def _env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean environment variable without accepting ambiguous values."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# ── Serial Port ───────────────────────────────────────────────────────────────
# [CONFIG] USB port the Arduino Uno is connected to. Leave blank to auto-detect.
#   Windows      : "COM3", "COM4" … (check Arduino IDE → Tools → Port)
#   Linux mini PC: "/dev/ttyACM0"  or  "/dev/ttyUSB0"
SERIAL_PORT: str = os.environ.get("SERIAL_PORT", "").strip()

# [CONFIG] Must match BAUD_RATE in sensor_reader.ino
SERIAL_BAUD: int = 115200

# Run without an Arduino. This exercises the database, sync, and camera
# overlay while the mini PC is being commissioned.
SIMULATE: bool = _env_bool("SIMULATE", False)
SIMULATION_INTERVAL_S: float = float(
    os.environ.get("SIMULATION_INTERVAL_S", "2.0")
)

# ── Database ──────────────────────────────────────────────────────────────────
# [CONFIG] Path to the local SQLite file.
#   Windows example  : "C:/Users/YourName/Documents/sensor_data.db"
#   Linux mini PC    : "/home/youruser/sensor_data.db"
DB_PATH: str = os.environ.get(
    "DB_PATH",
    str(SENSOR_DIR / "data" / "sensor_data.db"),
)

# ── Supabase ──────────────────────────────────────────────────────────────────
SUPABASE_URL:   str = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY:   str = os.environ.get("SUPABASE_KEY", "")
SUPABASE_TABLE: str = "sensor_logs"

# [CONFIG] Rows per upsert call. Lower if you hit Supabase payload-size errors.
SYNC_BATCH_SIZE: int = 500

# [CONFIG] Seconds between Supabase sync attempts.
SYNC_INTERVAL_S: float = 30.0

# ── Tapo C530WS IP Camera (RTSP over Wi-Fi or routed network) ─────────────────
# The mini PC can reach the camera locally, through a VPN, or through a
# firewall/NAT rule that forwards an external RTSP port to the camera.
#
# How to find / set credentials:
#   1. Open the Tapo app → select your C530WS → tap ⚙ Settings
#   2. Go to Advanced Settings → RTSP — enable it and set a username/password
#   3. Set TAPO_CONNECTION_MODE to local, remote, or auto in .env
#
# local: try only the camera's LAN address
# remote: try only TAPO_REMOTE_RTSP_URL or TAPO_REMOTE_HOST
# auto: try local first, then remote (recommended when the mini PC may move)
TAPO_CONNECTION_MODE: str = os.environ.get(
    "TAPO_CONNECTION_MODE", "auto"
).strip().lower()
TAPO_IP:       str = os.environ.get("TAPO_IP", "").strip()
TAPO_USER:     str = os.environ.get("TAPO_USER", "admin")
TAPO_PASSWORD: str = os.environ.get("TAPO_PASSWORD", "")

# [CONFIG] Stream quality.
#   "main" → high-quality stream (1080p / 2K)  — uses more bandwidth
#   "sub"  → lower-resolution stream (~360p)   — better for slow networks
TAPO_STREAM: str = os.environ.get("TAPO_STREAM", "main")

# You can provide a complete URL when a router/VPN uses a non-standard path or
# port. Otherwise the host/port settings below build the standard stream URL.
TAPO_LOCAL_RTSP_URL: str = os.environ.get("TAPO_LOCAL_RTSP_URL", "").strip()
TAPO_REMOTE_RTSP_URL: str = os.environ.get("TAPO_REMOTE_RTSP_URL", "").strip()
TAPO_REMOTE_HOST: str = os.environ.get("TAPO_REMOTE_HOST", "").strip()
TAPO_LOCAL_PORT: int = int(os.environ.get("TAPO_LOCAL_PORT", "554"))
TAPO_REMOTE_PORT: int = int(os.environ.get("TAPO_REMOTE_PORT", "554"))


def _build_rtsp_url(host: str, port: int) -> str:
    """Build an RTSP URL while safely escaping credentials with special chars."""
    if not host:
        return ""
    stream_path = "stream1" if TAPO_STREAM.lower() == "main" else "stream2"
    user = quote(TAPO_USER, safe="")
    password = quote(TAPO_PASSWORD, safe="")
    return f"rtsp://{user}:{password}@{host}:{port}/{stream_path}"


def get_tapo_endpoints() -> list[tuple[str, str]]:
    """Return ordered (label, URL) endpoints according to connection mode."""
    local_url = TAPO_LOCAL_RTSP_URL or _build_rtsp_url(TAPO_IP, TAPO_LOCAL_PORT)
    remote_url = TAPO_REMOTE_RTSP_URL or _build_rtsp_url(
        TAPO_REMOTE_HOST, TAPO_REMOTE_PORT
    )
    mode = (
        TAPO_CONNECTION_MODE
        if TAPO_CONNECTION_MODE in {"local", "remote", "auto"}
        else "auto"
    )
    candidates = []
    if mode in {"local", "auto"} and local_url:
        candidates.append(("local", local_url))
    if mode in {"remote", "auto"} and remote_url:
        candidates.append(("remote", remote_url))

    unique = []
    seen = set()
    for label, url in candidates:
        if url not in seen:
            unique.append((label, url))
            seen.add(url)
    return unique


# Backwards-compatible name used by older integrations.
TAPO_RTSP_URL: str = TAPO_LOCAL_RTSP_URL or _build_rtsp_url(
    TAPO_IP, TAPO_LOCAL_PORT
)

# [CONFIG] Seconds to wait between each RTSP connection attempt.
TAPO_RECONNECT_DELAY_S: float = 5.0

# [CONFIG] How many rounds to try Tapo endpoints before using a USB camera.
TAPO_MAX_CONNECT_TRIES: int = 5

# [CONFIG] Local camera index to fall back to when Tapo is unreachable.
#   0 = first USB/built-in camera, 1 = second, etc.
TAPO_FALLBACK_CAMERA_INDEX: int = int(os.environ.get("TAPO_FALLBACK_CAMERA_INDEX", "0"))

# [CONFIG] Show a live preview window while recording.
# Set SHOW_PREVIEW=false for a mini PC running without a desktop session.
SHOW_PREVIEW: bool = _env_bool("SHOW_PREVIEW", IS_WINDOWS)

# [CONFIG] Folder where video segments are saved.
VIDEO_SAVE_PATH: str = str(SENSOR_DIR / "recordings")

# [CONFIG] Length of each video segment in seconds. 3600 = 1 hour.
VIDEO_SEGMENT_DURATION: int = 3600

# [CONFIG] Where to place the sensor overlay on the video frame.
#   Options: "top-left", "top-right", "bottom-left", "bottom-right"
OVERLAY_POSITION: str = "top-left"

# [CONFIG] Camera capture resolution — must match your camera's native resolution.
VIDEO_WIDTH:  int = 1280
VIDEO_HEIGHT: int = 720

# [CONFIG] Frames per second.
VIDEO_FPS: float = 20.0

# [CONFIG] OpenCV FourCC codec.
#   "mp4v" → .mp4  (works on Windows and Linux)
#   "XVID" → .avi  (alternative if mp4v has issues)
VIDEO_FOURCC: str = "mp4v"
VIDEO_EXT:    str = ".mp4"

# ── Sensor Conversion ─────────────────────────────────────────────────────────
# [CONFIG] MQ-137 full-scale PPM at ADC = 1023.
#   formula: ppm = (raw / 1023) * MQ137_MAX_PPM
#   100 ppm gives good resolution in the 0–25 ppm warning range.
#   Calibrate against a reference gas for accurate absolute values.
MQ137_MAX_PPM: float = 100.0

# [CONFIG] LM386 ADC 0–1023 mapped to this dB range.
SOUND_DB_MIN: float = 40.0
SOUND_DB_MAX: float = 90.0

# ── Alert Thresholds (§3.3.7.x — poultry welfare guidelines) ─────────────────
# Source: Hendrix Genetics (2026), Poultry Hub (2026), Goel et al. (2021),
#         Wang et al. (2025)
# These are the boot-time defaults.  setup_ui.py lets the user override them
# at startup; apply_settings() writes the chosen values back here so all
# modules that import config see the updated values.

# Temperature (°C)
TEMP_RANGE_MIN: float = 18.0   # recommended minimum for laying hens
TEMP_RANGE_MAX: float = 29.0   # recommended maximum
TEMP_WARN_HIGH: float = 30.0   # ≥ 30 °C → heat stress

# Humidity (%)
HUMIDITY_RANGE_MIN: float = 50.0
HUMIDITY_RANGE_MAX: float = 70.0
HUMIDITY_WARN_LOW:  float = 40.0   # < 40 % → dust / respiratory irritation
HUMIDITY_WARN_HIGH: float = 75.0   # > 75 % → wet litter / heat dissipation issues
HUMIDITY_CRIT_LOW:  float = 35.0   # < 35 % → critical
HUMIDITY_CRIT_HIGH: float = 80.0   # > 80 % → critical

# Ammonia (ppm)
AMMONIA_SAFE_PPM: float = 10.0   # safe upper limit  (warn threshold)
AMMONIA_WARN_PPM: float = 25.0   # ≥ 25 ppm → harmful to respiratory health

# Sound (dB)
SOUND_WARN_DB: float = 75.0      # > 75 dB → potential flock distress

# ── Status codes ──────────────────────────────────────────────────────────────
STATUS_NORMAL   = 0
STATUS_WARNING  = 1
STATUS_CRITICAL = 2

STATUS_LABEL = {
    STATUS_NORMAL  : "OK",
    STATUS_WARNING : "WARN",
    STATUS_CRITICAL: "CRIT",
}

# ── ADC conversion helpers ────────────────────────────────────────────────────
_MQ137_SCALE = MQ137_MAX_PPM / 1023.0
_SOUND_RANGE = SOUND_DB_MAX - SOUND_DB_MIN


def adc_to_ppm(raw) -> float | None:
    """Convert MQ-137 raw ADC (0–1023) to PPM."""
    if raw is None:
        return None
    return round(raw * _MQ137_SCALE, 2)


def adc_to_db(raw) -> float | None:
    """Convert LM386 raw ADC (0–1023) to dB."""
    if raw is None:
        return None
    return round(SOUND_DB_MIN + (raw / 1023.0) * _SOUND_RANGE, 1)


# ── Alert evaluation helpers ──────────────────────────────────────────────────

def eval_temp(t) -> int:
    """
    NORMAL  : 18–29 °C
    WARNING : ≥ 30 °C  (heat stress — §3.3.7.1)
    """
    if t is None:
        return STATUS_NORMAL
    if t >= TEMP_WARN_HIGH:
        return STATUS_WARNING
    return STATUS_NORMAL


def eval_humidity(h) -> int:
    """
    NORMAL   : HUMIDITY_WARN_LOW – HUMIDITY_WARN_HIGH
    WARNING  : between warn and crit bounds
    CRITICAL : < HUMIDITY_CRIT_LOW  or  > HUMIDITY_CRIT_HIGH
    """
    if h is None:
        return STATUS_NORMAL
    if h < HUMIDITY_CRIT_LOW or h > HUMIDITY_CRIT_HIGH:
        return STATUS_CRITICAL
    if h < HUMIDITY_WARN_LOW or h > HUMIDITY_WARN_HIGH:
        return STATUS_WARNING
    return STATUS_NORMAL


def eval_ammonia(ppm) -> int:
    """
    NORMAL   : < AMMONIA_SAFE_PPM
    WARNING  : AMMONIA_SAFE_PPM – AMMONIA_WARN_PPM
    CRITICAL : ≥ AMMONIA_WARN_PPM
    """
    if ppm is None:
        return STATUS_NORMAL
    if ppm >= AMMONIA_WARN_PPM:
        return STATUS_CRITICAL
    if ppm >= AMMONIA_SAFE_PPM:
        return STATUS_WARNING
    return STATUS_NORMAL


def eval_sound(db_val) -> int:
    """
    NORMAL  : ≤ SOUND_WARN_DB
    WARNING : > SOUND_WARN_DB
    """
    if db_val is None:
        return STATUS_NORMAL
    if db_val > SOUND_WARN_DB:
        return STATUS_WARNING
    return STATUS_NORMAL


# ── Runtime settings override ─────────────────────────────────────────────────

def apply_settings(s: dict) -> None:
    """
    Called by main.py after the setup UI returns.
    Overwrites the module-level threshold variables so every module
    that imports config sees the user-chosen values.
    """
    global TEMP_WARN_HIGH
    global HUMIDITY_WARN_LOW, HUMIDITY_WARN_HIGH
    global HUMIDITY_CRIT_LOW, HUMIDITY_CRIT_HIGH
    global AMMONIA_SAFE_PPM, AMMONIA_WARN_PPM
    global SOUND_WARN_DB
    global SYNC_INTERVAL_S, VIDEO_SEGMENT_DURATION

    TEMP_WARN_HIGH       = float(s.get("temp_warn_high",   TEMP_WARN_HIGH))
    HUMIDITY_WARN_LOW    = float(s.get("hum_warn_low",     HUMIDITY_WARN_LOW))
    HUMIDITY_WARN_HIGH   = float(s.get("hum_warn_high",    HUMIDITY_WARN_HIGH))
    HUMIDITY_CRIT_LOW    = float(s.get("hum_crit_low",     HUMIDITY_CRIT_LOW))
    HUMIDITY_CRIT_HIGH   = float(s.get("hum_crit_high",    HUMIDITY_CRIT_HIGH))
    AMMONIA_SAFE_PPM     = float(s.get("ammonia_warn_ppm", AMMONIA_SAFE_PPM))
    AMMONIA_WARN_PPM     = float(s.get("ammonia_crit_ppm", AMMONIA_WARN_PPM))
    SOUND_WARN_DB        = float(s.get("sound_warn_db",    SOUND_WARN_DB))
    SYNC_INTERVAL_S      = float(s.get("sync_interval_s",  SYNC_INTERVAL_S))
    VIDEO_SEGMENT_DURATION = int(s.get("segment_seconds",  VIDEO_SEGMENT_DURATION))

    print(
        f"[config] Thresholds applied — "
        f"Temp warn≥{TEMP_WARN_HIGH}°C | "
        f"Hum warn {HUMIDITY_WARN_LOW}–{HUMIDITY_WARN_HIGH}% "
        f"crit <{HUMIDITY_CRIT_LOW} >{HUMIDITY_CRIT_HIGH}% | "
        f"NH3 warn≥{AMMONIA_SAFE_PPM} crit≥{AMMONIA_WARN_PPM} ppm | "
        f"Sound warn>{SOUND_WARN_DB} dB"
    )
