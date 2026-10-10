# Poultry Sensor Logger

Records temperature, humidity, ammonia, and sound from an Arduino Uno,
burns live telemetry into continuous video using OpenCV, and syncs all
data to Supabase. **No Node.js or FFmpeg required — Python + Arduino only.**

---

## Files

```
sensor/
├── arduino/
│   └── sensor_reader.ino     ← upload this to the Arduino Uno
├── python/
│   ├── config.py             ← ALL settings and thresholds live here
│   ├── database.py           ← SQLite layer
│   ├── collector.py          ← Arduino serial reader + alert evaluation
│   ├── sync.py               ← SQLite → Supabase store-and-forward
│   ├── camera.py             ← OpenCV recording + overlay
│   └── main.py               ← entry point (starts all threads)
├── .env.example              ← copy to .env and add Supabase credentials
├── requirements.txt          ← pip dependencies
├── start.bat                 ← Windows launcher
└── start.sh                  ← Linux mini PC launcher
```

---

## Setup

### 1 — Camera (TP-Link Tapo C530WS)

The system uses the Tapo C530WS as a **wireless IP camera** — it connects to your
Wi-Fi router, and the mini PC (Windows or Linux) pulls a live RTSP video stream
from it over the local network. No USB cable is needed.

**Steps:**
1. Mount and power the Tapo C530WS; add it to the Tapo app on your phone.
2. In the Tapo app → select the camera → **Settings (⚙) → Advanced Settings → RTSP**
   - Enable RTSP
   - Set an RTSP **username** and **password** (you choose these)
3. Find the camera's **local IP address** under **Device Info** in the app,
   or check your router's DHCP client list.
4. Add those values to your `.env` file (see step 3 — Credentials below):
   ```
   TAPO_IP=192.168.1.100      ← your camera's IP
   TAPO_USER=admin            ← RTSP username you set in step 2
   TAPO_PASSWORD=your-pass    ← RTSP password you set in step 2
   TAPO_STREAM=main           ← "main" = 1080p/2K, "sub" = ~360p
   ```
   The software automatically connects to
   `rtsp://<user>:<password>@<ip>:554/stream1` (main) or `/stream2` (sub).

> **Tip — headless mini PC:** The preview window is disabled by default
> on non-Windows systems. Run with a monitor attached, or set `SHOW_PREVIEW`
> via the GUI if you want a live view.

---

### 2 — Python
**Windows:** Download from https://www.python.org — check **"Add Python to PATH"** during install.

**Linux mini PC:**
```bash
sudo apt install -y python3 python3-pip
```

### 3 — Arduino IDE
1. Download from https://www.arduino.cc/en/software
2. Open Arduino IDE → **Tools → Manage Libraries**, install:
   - `DHT sensor library` by Adafruit
   - `Adafruit Unified Sensor`
3. Open `arduino/sensor_reader.ino`, select **Tools → Board → Arduino Uno**
4. Select **Tools → Port** → your Arduino COM port
5. Click **Upload**

### 4 — Credentials
```bash
# Windows
copy .env.example .env
notepad .env

# Linux mini PC
cp .env.example .env
nano .env
```
Fill in `SUPABASE_URL`, `SUPABASE_KEY`, `TAPO_IP`, `TAPO_USER`, and `TAPO_PASSWORD`, then save.

### 4 — Supabase table (run once in SQL Editor)
```sql
CREATE TABLE IF NOT EXISTS sensor_logs (
  id              BIGINT       PRIMARY KEY,
  recorded_at     TIMESTAMPTZ  NOT NULL,

  -- DHT22
  temperature     REAL,            -- °C  (null if sensor failed)
  humidity        REAL,            -- %

  -- MQ-137
  ammonia_ppm     REAL,            -- ppm (converted)
  ammonia_raw     INTEGER,         -- raw ADC 0–1023

  -- LM386
  sound_db        REAL,            -- dB  (converted)
  sound_raw       INTEGER,         -- raw ADC 0–1023

  -- Alert flags  0=normal  1=warning  2=critical
  -- temp_status    : 1 = ≥ 30 °C
  -- hum_status     : 1 = 40–49% or 71–75%   2 = < 40% or > 75%
  -- ammonia_status : 1 = 10–24 ppm           2 = ≥ 25 ppm
  -- sound_status   : 1 = > 75 dB
  temp_status     SMALLINT NOT NULL DEFAULT 0,
  hum_status      SMALLINT NOT NULL DEFAULT 0,
  ammonia_status  SMALLINT NOT NULL DEFAULT 0,
  sound_status    SMALLINT NOT NULL DEFAULT 0
);

CREATE INDEX ON sensor_logs (recorded_at DESC);
CREATE INDEX ON sensor_logs (ammonia_status) WHERE ammonia_status > 0;
CREATE INDEX ON sensor_logs (sound_status)   WHERE sound_status   > 0;
```

### 5 — Run

**Windows** — double-click `start.bat`
(requires Python 3.10 or newer). On first run, the launcher creates `venv/`
and installs all packages from `requirements.txt` into it. Later runs reuse
that environment; changed requirements or an unfinished installation trigger
another install. Setup errors stop the launcher instead of starting with
missing dependencies.

The launcher runs `python/main.py`, which keeps sensor collection, camera
recording, and Supabase syncing running together. Pending readings are uploaded
at startup and then checked every **30 seconds** with the included saved
settings. The console shows upload results and the time until the next check.
Failed uploads remain in SQLite for retry on the next cycle. To change the
interval, press **S** in the preview and adjust the sync setting.

**Linux mini PC:**
```bash
chmod +x start.sh
./start.sh
```

The logger starts with the saved settings and begins recording immediately.
When the live preview window is visible, press **S** to open the settings.
Recording and sensor collection continue while the settings window is open;
click **Start Monitoring** to save and apply changes, or close it to keep the
current settings.

### Paired video and sensor saves

With the **Sensor Feed** preview focused:
- Press **M** to save the current segment immediately. Recording continues in a
  new folder, and the automatic-save timer restarts.
- Press **S** to open Settings. **Auto-save video + SQLite every** controls the
  save interval (3 minutes through 8 hours).
- Press **Q** or **Esc** to save the current segment and stop. Closing the
  preview or using Ctrl+C also finalizes the current recording.

Each segment has its own folder under `recordings/`:

```text
recordings/rec_2026-10-10_14-30-00_123456/
├── video.mp4
└── sensors.sqlite
```

The standalone SQLite file contains:
- `sensor_logs`: one snapshot per saved video frame, with `frame_index` (starting
  at 0), `elapsed_seconds`, `video_time` (starting at `00:00`), sensor values,
  raw ADC readings, alert flags, and the original reading timestamp.
- `recording`: video filename, FPS, dimensions, start/end times, frame count,
  duration, and save reason (`manual`, `automatic`, `schedule_end`, etc.).

Elapsed sensor time follows the encoded MP4 timeline (`frame_index / fps`),
so sensor snapshots match video playback even if the stream slows down.
Snapshots repeat the latest displayed reading until a new reading arrives;
missing readings remain N/A rather than being invented. `recorded_at` identifies
the age of the reading; `captured_at` is when the frame snapshot was taken.
The central SQLite database and Supabase sync continue unchanged.

The sensor HUD is in a **separate black strip below the camera image**. The
original image is not obscured, cropped, or resized; the MP4 is taller by the
height of the strip. Detection should use the original camera region, excluding
this strip. Left/right alignment and sensor visibility are adjustable in
Settings. Old top-position settings are automatically mapped to the bottom.
If the feed disconnects, the current pair is finalized and reconnection starts
a fresh pair instead of hiding a gap in one recording.

Keyboard controls require a desktop preview (`SHOW_PREVIEW=true`). With preview
disabled, timed saves and clean-shutdown saves still work; M is unavailable.

---

## Configuration

All settings are in `sensor/python/config.py` — every tunable value is marked `[CONFIG]`.

| Setting | Default | Notes |
|---------|---------|-------|
| `SERIAL_PORT` | `COM4` / `/dev/ttyACM0` | Arduino USB port |
| `SERIAL_BAUD` | `115200` | Must match Arduino sketch |
| `TAPO_IP` | *(from .env)* | Camera's local IP address |
| `TAPO_USER` | `admin` | RTSP username set in Tapo app |
| `TAPO_PASSWORD` | *(from .env)* | RTSP password set in Tapo app |
| `TAPO_STREAM` | `main` | `"main"` = 1080p/2K, `"sub"` = ~360p |
| `TAPO_RECONNECT_DELAY_S` | `5.0` | Seconds between reconnect attempts |
| `SHOW_PREVIEW` | `True` on Windows, `False` on Linux | Live preview window |
| `VIDEO_SEGMENT_DURATION` | `3600` | Seconds per paired video/SQLite save (1 hour) |
| `SYNC_INTERVAL_S` | `30` | Supabase sync every 30 s |
| `MQ137_MAX_PPM` | `100` | Calibrate against reference gas |

---

## Alert Thresholds (§3.3.7.x)

| Sensor | Normal | Warning | Critical |
|--------|--------|---------|----------|
| Temperature | 18–29 °C | ≥ 30 °C | — |
| Humidity | 50–70 % | 40–49 % or 71–75 % | < 40 % or > 75 % |
| Ammonia | < 10 ppm | 10–24 ppm | ≥ 25 ppm |
| Sound | ≤ 75 dB | > 75 dB | — |

---

## Auto-start on boot (Linux mini PC)

Replace `youruser` below with the Linux account that will run the logger,
and adjust the paths if the project doesn't live in that account's home
directory.

```bash
sudo nano /etc/systemd/system/sensor-logger.service
```
```ini
[Unit]
Description=Poultry Sensor Logger
After=network.target

[Service]
ExecStart=/usr/bin/python3 /home/youruser/sensor/python/main.py
WorkingDirectory=/home/youruser/sensor
Restart=always
RestartSec=5
User=youruser
EnvironmentFile=/home/youruser/sensor/.env

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable sensor-logger
sudo systemctl start sensor-logger
sudo journalctl -fu sensor-logger   # live logs
```

---

## Wiring

| Sensor | Arduino Pin |
|--------|------------|
| DHT22 DATA | Digital 2 (+ 10 kΩ pull-up to 3.3 V) |
| MQ-137 AOUT | A0 |
| LM386 AOUT | A1 |
