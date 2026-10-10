"""One video segment and its frame-aligned, standalone SQLite telemetry."""

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2

import config


def video_time(seconds: float) -> str:
    """Minutes may exceed 59, so even long clips use an unambiguous MM:SS."""
    total = int(seconds)
    return f"{total // 60:02d}:{total % 60:02d}"


FIELDS = (
    "recorded_at", "temperature", "humidity", "ammonia_ppm", "ammonia_raw",
    "sound_db", "sound_raw", "temp_status", "hum_status",
    "ammonia_status", "sound_status",
)


class Recording:
    """Owned only by the camera thread; never touches the Supabase sync DB."""

    def __init__(self, width: int, height: int):
        self.folder = Path(config.VIDEO_SAVE_PATH) / datetime.now().strftime(
            "rec_%Y-%m-%d_%H-%M-%S_%f"
        )
        self.folder.mkdir(parents=True, exist_ok=False)
        self.fps = config.VIDEO_FPS
        self.size = (width, height)
        self.frames = 0
        self.started = time.monotonic()
        self.closed = False
        self.writer = cv2.VideoWriter(
            str(self.folder / "video.mp4"),
            cv2.VideoWriter_fourcc(*config.VIDEO_FOURCC),
            self.fps, self.size,
        )
        if not self.writer.isOpened():
            self.writer.release()
            raise RuntimeError(f"Cannot create MP4 recording in {self.folder}")
        try:
            self.db = sqlite3.connect(self.folder / "sensors.sqlite")
            self.db.executescript("""
                CREATE TABLE sensor_logs (
                    frame_index INTEGER PRIMARY KEY,
                    elapsed_seconds REAL NOT NULL,
                    video_time TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    recorded_at TEXT,
                    temperature REAL, humidity REAL,
                    ammonia_ppm REAL, ammonia_raw INTEGER,
                    sound_db REAL, sound_raw INTEGER,
                    temp_status INTEGER, hum_status INTEGER,
                    ammonia_status INTEGER, sound_status INTEGER
                );
                CREATE TABLE recording (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    video_file TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    fps REAL NOT NULL,
                    width INTEGER NOT NULL, height INTEGER NOT NULL,
                    frame_count INTEGER NOT NULL DEFAULT 0,
                    duration_seconds REAL NOT NULL DEFAULT 0,
                    save_reason TEXT NOT NULL DEFAULT 'recording'
                );
            """)
            self.db.execute(
                "INSERT INTO recording (id, video_file, started_at, fps, width, height) "
                "VALUES (1, 'video.mp4', ?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), self.fps, width, height),
            )
            self.db.commit()
        except Exception:
            self.writer.release()
            if hasattr(self, "db"):
                self.db.close()
            raise
        print(f"[camera] Recording video + sensor data → {self.folder}")

    @property
    def elapsed(self) -> float:
        # This follows the encoded MP4 timeline, not a possibly delayed camera.
        return self.frames / self.fps

    def write(self, frame, data: dict) -> None:
        if self.closed:
            raise RuntimeError("Cannot write to a saved recording")
        if (frame.shape[1], frame.shape[0]) != self.size:
            raise ValueError("Recording frame dimensions changed")
        self.writer.write(frame)
        self.db.execute(
            "INSERT INTO sensor_logs VALUES (" + ",".join(["?"] * 15) + ")",
            (self.frames, self.elapsed, video_time(self.elapsed),
             datetime.now(timezone.utc).isoformat(),
             *(data.get(key) for key in FIELDS)),
        )
        self.frames += 1
        # Bound pending transactions to roughly one second of video.
        if self.frames % max(1, int(self.fps)) == 0:
            self.db.commit()

    def close(self, reason: str) -> None:
        if self.closed:
            return
        self.closed = True
        self.writer.release()  # finalizes MP4 before announcing the save
        try:
            self.db.execute(
                "UPDATE recording SET ended_at=?, frame_count=?, "
                "duration_seconds=?, save_reason=? WHERE id=1",
                (datetime.now(timezone.utc).isoformat(), self.frames,
                 self.elapsed, reason),
            )
            self.db.commit()
        finally:
            self.db.close()
        print(f"[camera] Saved ({reason}) → {self.folder}")
