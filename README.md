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
└── start.sh                  ← Raspberry Pi launcher
```

---

## Setup

### 1 — Python
**Windows:** Download from https://www.python.org — check **"Add Python to PATH"** during install.

**Raspberry Pi:**
```bash
sudo apt install -y python3 python3-pip
```

### 2 — Arduino IDE
1. Download from https://www.arduino.cc/en/software
2. Open Arduino IDE → **Tools → Manage Libraries**, install:
   - `DHT sensor library` by Adafruit
   - `Adafruit Unified Sensor`
3. Open `arduino/sensor_reader.ino`, select **Tools → Board → Arduino Uno**
4. Select **Tools → Port** → your Arduino COM port
5. Click **Upload**

### 3 — Credentials
```bash
# Windows
copy sensor\.env.example sensor\.env
notepad sensor\.env

# Raspberry Pi
cp sensor/.env.example sensor/.env
nano sensor/.env
```
Fill in `SUPABASE_URL` and `SUPABASE_KEY`, save.

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
(installs pip packages automatically on first run)

**Raspberry Pi:**
```bash
chmod +x sensor/start.sh
./sensor/start.sh
```

---

## Configuration

All settings are in `sensor/python/config.py` — every tunable value is marked `[CONFIG]`.

| Setting | Default | Notes |
|---------|---------|-------|
| `SERIAL_PORT` | `COM3` / `/dev/ttyACM0` | Arduino USB port |
| `SERIAL_BAUD` | `115200` | Must match Arduino sketch |
| `CAMERA_INDEX` | `0` | 0 = first camera |
| `SHOW_PREVIEW` | `True` | Set `False` for headless RPi |
| `VIDEO_SEGMENT_DURATION` | `3600` | Seconds per file (1 hour) |
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

## Auto-start on boot (Raspberry Pi)

```bash
sudo nano /etc/systemd/system/sensor-logger.service
```
```ini
[Unit]
Description=Poultry Sensor Logger
After=network.target

[Service]
ExecStart=/usr/bin/python3 /home/pi/sensor/python/main.py
WorkingDirectory=/home/pi/sensor
Restart=always
RestartSec=5
User=pi
EnvironmentFile=/home/pi/sensor/.env

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
