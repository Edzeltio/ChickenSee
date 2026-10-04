"""
setup_ui.py — Startup configuration window.

Shows a settings GUI before the monitoring system starts.
Settings are persisted to sensor/settings.json between runs.
"""

import json
import math
import shutil
import sys
import tempfile
import tkinter as tk
from tkinter import messagebox
from datetime import datetime
from pathlib import Path

# ── Persistent settings file ──────────────────────────────────────────────────
SETTINGS_PATH = Path(__file__).parent.parent / "settings.json"

DEFAULTS: dict = {
    # Sensor alert thresholds
    "temp_warn_high":   30.0,   # °C  — warning when temp ≥ this
    "hum_crit_low":     35.0,   # %   — critical when humidity < this
    "hum_warn_low":     40.0,   # %   — warning  when humidity < this
    "hum_warn_high":    75.0,   # %   — warning  when humidity > this
    "hum_crit_high":    80.0,   # %   — critical when humidity > this
    "ammonia_warn_ppm": 10.0,   # ppm — warning  when NH3 ≥ this
    "ammonia_crit_ppm": 25.0,   # ppm — critical when NH3 ≥ this
    "sound_warn_db":    75.0,   # dB  — warning  when sound > this
    # Recording schedule
    "rec_always":       True,   # ignore start/end window, record 24/7
    "rec_start_h":      6,
    "rec_start_m":      0,
    "rec_end_h":        18,
    "rec_end_m":        0,
    "segment_seconds":  3600,   # seconds per video file
    # Supabase sync
    "sync_interval_s":  30,
    # Video overlay
    "overlay_enabled":  True,   # show the HUD box at all
    "show_temp":        True,   # temperature row
    "show_humidity":    True,   # humidity row
    "show_ammonia":     True,   # ammonia row
    "show_sound":       True,   # sound row
    "overlay_position": "top-left",   # top-left / top-right / bottom-left / bottom-right
}

SEGMENT_OPTIONS = [
    ("3 minutes",   180),
    ("5 minutes",   300),
    ("15 minutes",  900),
    ("30 minutes",  1800),
    ("1 hour",      3600),
    ("2 hours",     7200),
    ("4 hours",     14400),
    ("8 hours",     28800),
]

SYNC_OPTIONS = [
    ("1 seconds",  1),
    ("5 seconds",  5),
    ("15 seconds",  15),
    ("30 seconds",  30),
    ("1 minute",    60),
    ("3 minutes",   180),
    ("5 minutes",   300),
    ("10 minutes",  600),
    ("15 minutes",  900),
    ("30 minutes",  1800),
]

POSITION_OPTIONS = ["top-left", "top-right", "bottom-left", "bottom-right"]
SETTINGS_LOAD_WARNING: str | None = None


def _validate_values(settings: dict) -> list[str]:
    """Validate all editable values, including values typed into spinboxes."""
    errors = []
    ranges = [
        ("temp_warn_high", "Temperature warning", 20, 45),
        ("hum_warn_low", "Humidity warning low", 10, 60),
        ("hum_warn_high", "Humidity warning high", 60, 95),
        ("hum_crit_low", "Humidity critical low", 5, 50),
        ("hum_crit_high", "Humidity critical high", 70, 100),
        ("ammonia_warn_ppm", "Ammonia warning", 1, 50),
        ("ammonia_crit_ppm", "Ammonia critical", 5, 100),
        ("sound_warn_db", "Sound warning", 50, 110),
    ]

    for key, label, minimum, maximum in ranges:
        raw_value = settings.get(key)
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            errors.append(f"{label} must be a number between {minimum} and {maximum}.")
            continue
        try:
            value = float(raw_value)
        except (KeyError, TypeError, ValueError, OverflowError):
            errors.append(f"{label} must be a number between {minimum} and {maximum}.")
            continue
        if not math.isfinite(value) or not minimum <= value <= maximum:
            errors.append(f"{label} must be between {minimum} and {maximum}.")

    for key, label, maximum in (
        ("rec_start_h", "Start hour", 23),
        ("rec_end_h", "End hour", 23),
        ("rec_start_m", "Start minute", 59),
        ("rec_end_m", "End minute", 59),
    ):
        raw_value = settings.get(key)
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            errors.append(f"{label} must be a whole number from 0 to {maximum}.")
            continue
        try:
            value = float(raw_value)
        except (KeyError, TypeError, ValueError, OverflowError):
            errors.append(f"{label} must be a whole number from 0 to {maximum}.")
            continue
        if not math.isfinite(value) or not value.is_integer() or not 0 <= value <= maximum:
            errors.append(f"{label} must be a whole number from 0 to {maximum}.")

    for key, label in (
        ("rec_always", "24/7 recording"),
        ("overlay_enabled", "Video overlay"),
        ("show_temp", "Temperature overlay"),
        ("show_humidity", "Humidity overlay"),
        ("show_ammonia", "Ammonia overlay"),
        ("show_sound", "Sound overlay"),
    ):
        if type(settings.get(key)) is not bool:
            errors.append(f"{label} must be enabled or disabled.")

    if settings.get("segment_seconds") not in {value for _, value in SEGMENT_OPTIONS}:
        errors.append("Video segment duration must be one of the listed options.")
    if settings.get("sync_interval_s") not in {value for _, value in SYNC_OPTIONS}:
        errors.append("Supabase upload interval must be one of the listed options.")
    if settings.get("overlay_position") not in POSITION_OPTIONS:
        errors.append("Overlay position must be one of the listed options.")

    try:
        if float(settings["hum_crit_low"]) >= float(settings["hum_warn_low"]):
            errors.append("Humidity critical-low must be less than warn-low.")
        if float(settings["hum_warn_high"]) >= float(settings["hum_crit_high"]):
            errors.append("Humidity warn-high must be less than critical-high.")
        if float(settings["ammonia_warn_ppm"]) >= float(settings["ammonia_crit_ppm"]):
            errors.append("Ammonia warning must be less than critical.")
    except (KeyError, TypeError, ValueError, OverflowError):
        pass  # Individual range errors explain the invalid or missing values.
    return errors


def _read_settings_file() -> dict:
    saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    if not isinstance(saved, dict):
        raise ValueError("The settings file must contain a JSON object.")
    settings = {**DEFAULTS, **saved}
    errors = _validate_values(settings)
    if errors:
        raise ValueError("Invalid saved settings: " + " ".join(errors))
    return settings


def _backup_invalid_settings() -> Path | None:
    """Keep an invalid settings file before replacing it with valid values."""
    if not SETTINGS_PATH.exists():
        return None
    try:
        _read_settings_file()
        return None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = SETTINGS_PATH.with_name(
            f"{SETTINGS_PATH.stem}.invalid-{timestamp}{SETTINGS_PATH.suffix}.bak"
        )
        suffix = 1
        while backup.exists():
            backup = SETTINGS_PATH.with_name(
                f"{SETTINGS_PATH.stem}.invalid-{timestamp}-{suffix}"
                f"{SETTINGS_PATH.suffix}.bak"
            )
            suffix += 1
        shutil.copy2(SETTINGS_PATH, backup)
        return backup

# ── Colour palette ─────────────────────────────────────────────────────────────
BG       = "#12131f"   # window background
PANEL    = "#1c1d30"   # section panel
HEADER   = "#0e0f1a"   # title bar
BORDER   = "#2e3060"   # separator / border
ACCENT   = "#7c6af7"   # purple accent
ACCENT2  = "#00cfff"   # cyan accent
TEXT     = "#e8e8f8"   # primary text
SUB      = "#8888aa"   # secondary / hint text
OK       = "#4ade80"   # green
WARN     = "#fbbf24"   # amber
CRIT     = "#f87171"   # red
ENTRY    = "#1e1f38"   # entry background
BTN      = "#7c6af7"   # button background
BTN_HOV  = "#9d8eff"   # button hover
BTN_TXT  = "#ffffff"

FONT_TITLE  = ("Segoe UI", 17, "bold")
FONT_SUB    = ("Segoe UI", 9)
FONT_HEAD   = ("Segoe UI", 10, "bold")
FONT_LABEL  = ("Segoe UI", 10)
FONT_SMALL  = ("Segoe UI", 8)
FONT_BTN    = ("Segoe UI", 11, "bold")


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_settings() -> dict:
    global SETTINGS_LOAD_WARNING
    SETTINGS_LOAD_WARNING = None
    if not SETTINGS_PATH.exists():
        return dict(DEFAULTS)
    try:
        return _read_settings_file()
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        SETTINGS_LOAD_WARNING = (
            f"Saved settings could not be loaded: {exc}\n\n"
            "Defaults are shown. The original settings file will be backed up "
            "if you save these values."
        )
        return dict(DEFAULTS)


def save_settings(s: dict) -> None:
    errors = _validate_values(s)
    if errors:
        raise ValueError("Cannot save invalid settings: " + " ".join(errors))

    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _backup_invalid_settings()
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=SETTINGS_PATH.parent,
            prefix=f".{SETTINGS_PATH.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            json.dump(s, temp_file, indent=2, allow_nan=False)
            temp_file.write("\n")
        temp_path.replace(SETTINGS_PATH)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def _find_option_index(options: list[tuple], value) -> int:
    for i, (_, v) in enumerate(options):
        if v == value:
            return i
    return 0


# ── Widget helpers ────────────────────────────────────────────────────────────

def _frame(parent, **kw) -> tk.Frame:
    return tk.Frame(parent, bg=kw.pop("bg", PANEL), **kw)


def _label(parent, text: str, font=FONT_LABEL, fg=TEXT, **kw) -> tk.Label:
    return tk.Label(parent, text=text, font=font, fg=fg,
                    bg=kw.pop("bg", parent["bg"]), **kw)


def _spin(parent, from_: float, to: float, increment: float = 1.0,
          width: int = 6, fmt: str = "%.1f") -> tk.Spinbox:
    sv = tk.StringVar()
    sb = tk.Spinbox(
        parent, from_=from_, to=to, increment=increment,
        textvariable=sv, width=width, format=fmt,
        font=FONT_LABEL, bg=ENTRY, fg=TEXT, insertbackground=TEXT,
        buttonbackground=BORDER, relief="flat", bd=0,
        highlightthickness=1, highlightcolor=ACCENT,
        highlightbackground=BORDER,
    )
    return sb, sv


def _combo(parent, options: list[str], width: int = 16) -> tk.StringVar:
    sv = tk.StringVar(value=options[0])
    cb = tk.OptionMenu(parent, sv, *options)
    cb.config(
        bg=ENTRY, fg=TEXT, activebackground=ACCENT, activeforeground=TEXT,
        font=FONT_LABEL, relief="flat", bd=0,
        highlightthickness=1, highlightcolor=ACCENT,
        highlightbackground=BORDER, width=width, anchor="w",
        indicatoron=True,
    )
    cb["menu"].config(bg=PANEL, fg=TEXT, activebackground=ACCENT,
                      activeforeground=TEXT, font=FONT_LABEL)
    return cb, sv


def _sep(parent, pady: tuple = (10, 4)) -> None:
    tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", pady=pady)


def _row(parent, label: str, *widgets, hint: str = "") -> tk.Frame:
    """One labelled row with right-aligned widgets."""
    f = _frame(parent, bg=PANEL)
    f.pack(fill="x", pady=3)
    _label(f, label, font=FONT_LABEL, fg=TEXT, bg=PANEL).pack(side="left", padx=(0, 8))
    if hint:
        _label(f, hint, font=FONT_SMALL, fg=SUB, bg=PANEL).pack(side="left")
    for w in reversed(widgets):
        w.pack(side="right", padx=2)
    return f


# ── Main UI class ─────────────────────────────────────────────────────────────

class SetupUI:
    def __init__(self, exit_on_close: bool = True):
        self.result: dict | None = None
        self.exit_on_close = exit_on_close
        self.settings = load_settings()

        self.root = tk.Tk()
        self.root.title("ChickenSee — Startup Configuration")
        self.root.configure(bg=BG)
        self.root.resizable(True, True)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build()
        self._center()
        if SETTINGS_LOAD_WARNING:
            messagebox.showwarning(
                "Saved settings need attention",
                SETTINGS_LOAD_WARNING,
                parent=self.root,
            )

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        root = self.root

        # ── Title bar (always visible, never scrolls) ─────────────────────────
        hdr = tk.Frame(root, bg=HEADER, pady=18)
        hdr.pack(fill="x", side="top")
        _label(hdr, "🐔  ChickenSee", font=FONT_TITLE, fg=TEXT, bg=HEADER).pack()
        _label(hdr, "Environmental Monitor — Startup Configuration",
               font=FONT_SUB, fg=SUB, bg=HEADER).pack(pady=(2, 0))

        # ── Button bar (always visible at the bottom, never scrolls) ──────────
        btn_bar = tk.Frame(root, bg=BG, pady=16)
        btn_bar.pack(fill="x", side="bottom")
        self._build_buttons(btn_bar)

        # ── Thin separator between button bar and scroll area ──────────────────
        tk.Frame(root, bg=BORDER, height=1).pack(fill="x", side="bottom")

        # ── Scrollable middle section ─────────────────────────────────────────
        scroll_wrap = tk.Frame(root, bg=BG)
        scroll_wrap.pack(fill="both", expand=True, side="top")

        self._canvas = tk.Canvas(scroll_wrap, bg=BG, highlightthickness=0,
                                 borderwidth=0)
        scrollbar = tk.Scrollbar(scroll_wrap, orient="vertical",
                                 command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        # Inner frame that holds all the sections
        body = tk.Frame(self._canvas, bg=BG)
        self._body_window = self._canvas.create_window(
            (0, 0), window=body, anchor="nw"
        )

        # Resize canvas scroll-region whenever body changes size
        body.bind("<Configure>", self._on_body_configure)
        # Resize body width when canvas is resized
        self._canvas.bind("<Configure>", self._on_canvas_configure)

        # Mouse-wheel scrolling (Windows + Linux)
        self._canvas.bind_all("<MouseWheel>",      self._on_mousewheel)   # Windows
        self._canvas.bind_all("<Button-4>",        self._on_mousewheel)   # Linux up
        self._canvas.bind_all("<Button-5>",        self._on_mousewheel)   # Linux down

        # ── Sections inside the scrollable body ───────────────────────────────
        pad = tk.Frame(body, bg=BG, height=18)
        pad.pack()

        self._build_thresholds(body)
        tk.Frame(body, bg=BG, height=12).pack()
        self._build_overlay(body)
        tk.Frame(body, bg=BG, height=12).pack()
        self._build_schedule(body)
        tk.Frame(body, bg=BG, height=12).pack()
        self._build_sync(body)

        tk.Frame(body, bg=BG, height=18).pack()

    # ── Scroll helpers ────────────────────────────────────────────────────────

    def _on_body_configure(self, event=None) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        # Keep the inner body as wide as the canvas
        self._canvas.itemconfig(self._body_window, width=event.width)

    def _on_mousewheel(self, event) -> None:
        if event.num == 4:          # Linux scroll up
            self._canvas.yview_scroll(-1, "units")
        elif event.num == 5:        # Linux scroll down
            self._canvas.yview_scroll(1, "units")
        else:                       # Windows (event.delta is ±120 multiples)
            self._canvas.yview_scroll(int(-event.delta / 120), "units")

    # ── Section: Sensor Thresholds ────────────────────────────────────────────

    def _build_thresholds(self, parent: tk.Frame) -> None:
        s = self.settings

        panel = _frame(parent, bg=PANEL, padx=18, pady=14)
        panel.pack(fill="x")

        # Section heading
        hrow = _frame(panel, bg=PANEL)
        hrow.pack(fill="x", pady=(0, 10))
        _label(hrow, "  SENSOR ALERT THRESHOLDS", font=FONT_HEAD,
               fg=ACCENT, bg=PANEL).pack(side="left")
        _label(hrow, "  Values trigger colour change on the overlay",
               font=FONT_SMALL, fg=SUB, bg=PANEL).pack(side="left")

        _sep(panel, pady=(0, 10))

        # ── Temperature ───────────────────────────────────────────────────────
        _label(panel, "🌡  Temperature", font=("Segoe UI", 10, "bold"),
               fg=WARN, bg=PANEL).pack(anchor="w", pady=(0, 4))

        tf = _frame(panel, bg=PANEL)
        tf.pack(fill="x", pady=(0, 10))

        self._temp_warn_sb, self._temp_warn_sv = _spin(tf, 20, 45, 0.5, width=5)
        self._temp_warn_sb.pack(side="right", padx=2)
        self._temp_warn_sv.set(f"{s['temp_warn_high']:.1f}")
        _label(tf, "°C", font=FONT_LABEL, fg=SUB, bg=PANEL).pack(side="right")
        _label(tf, "Warning when temp ≥",
               font=FONT_LABEL, fg=TEXT, bg=PANEL).pack(side="right", padx=(0, 6))
        _label(tf, f"  Normal: 18–29 °C", font=FONT_SMALL,
               fg=SUB, bg=PANEL).pack(side="left")

        # ── Humidity ──────────────────────────────────────────────────────────
        _label(panel, "💧  Humidity", font=("Segoe UI", 10, "bold"),
               fg=ACCENT2, bg=PANEL).pack(anchor="w", pady=(0, 4))

        hf = _frame(panel, bg=PANEL)
        hf.pack(fill="x", pady=(0, 4))
        self._hum_warn_lo_sb, self._hum_warn_lo_sv = _spin(hf, 10, 60, 1, width=5, fmt="%.0f")
        self._hum_warn_lo_sb.pack(side="right", padx=2)
        self._hum_warn_lo_sv.set(f"{s['hum_warn_low']:.0f}")
        _label(hf, "%", font=FONT_LABEL, fg=SUB, bg=PANEL).pack(side="right")
        _label(hf, "Warn low <", font=FONT_LABEL, fg=TEXT, bg=PANEL).pack(side="right", padx=(0, 6))

        hf2 = _frame(panel, bg=PANEL)
        hf2.pack(fill="x", pady=(0, 4))
        self._hum_warn_hi_sb, self._hum_warn_hi_sv = _spin(hf2, 60, 95, 1, width=5, fmt="%.0f")
        self._hum_warn_hi_sb.pack(side="right", padx=2)
        self._hum_warn_hi_sv.set(f"{s['hum_warn_high']:.0f}")
        _label(hf2, "%", font=FONT_LABEL, fg=SUB, bg=PANEL).pack(side="right")
        _label(hf2, "Warn high >", font=FONT_LABEL, fg=TEXT, bg=PANEL).pack(side="right", padx=(0, 6))

        hf3 = _frame(panel, bg=PANEL)
        hf3.pack(fill="x", pady=(0, 4))
        self._hum_crit_lo_sb, self._hum_crit_lo_sv = _spin(hf3, 5, 50, 1, width=5, fmt="%.0f")
        self._hum_crit_lo_sb.pack(side="right", padx=2)
        self._hum_crit_lo_sv.set(f"{s['hum_crit_low']:.0f}")
        _label(hf3, "%", font=FONT_LABEL, fg=SUB, bg=PANEL).pack(side="right")
        _label(hf3, "Critical low <", font=FONT_LABEL, fg=CRIT, bg=PANEL).pack(side="right", padx=(0, 6))

        hf4 = _frame(panel, bg=PANEL)
        hf4.pack(fill="x", pady=(0, 10))
        self._hum_crit_hi_sb, self._hum_crit_hi_sv = _spin(hf4, 70, 100, 1, width=5, fmt="%.0f")
        self._hum_crit_hi_sb.pack(side="right", padx=2)
        self._hum_crit_hi_sv.set(f"{s['hum_crit_high']:.0f}")
        _label(hf4, "%", font=FONT_LABEL, fg=SUB, bg=PANEL).pack(side="right")
        _label(hf4, "Critical high >", font=FONT_LABEL, fg=CRIT, bg=PANEL).pack(side="right", padx=(0, 6))

        # ── Ammonia ───────────────────────────────────────────────────────────
        _label(panel, "☁  Ammonia (NH₃)", font=("Segoe UI", 10, "bold"),
               fg=WARN, bg=PANEL).pack(anchor="w", pady=(0, 4))

        af = _frame(panel, bg=PANEL)
        af.pack(fill="x", pady=(0, 4))
        self._nh3_warn_sb, self._nh3_warn_sv = _spin(af, 1, 50, 1, width=5, fmt="%.0f")
        self._nh3_warn_sb.pack(side="right", padx=2)
        self._nh3_warn_sv.set(f"{s['ammonia_warn_ppm']:.0f}")
        _label(af, "ppm", font=FONT_LABEL, fg=SUB, bg=PANEL).pack(side="right")
        _label(af, "Warning when NH₃ ≥", font=FONT_LABEL, fg=TEXT, bg=PANEL).pack(side="right", padx=(0, 6))

        af2 = _frame(panel, bg=PANEL)
        af2.pack(fill="x", pady=(0, 10))
        self._nh3_crit_sb, self._nh3_crit_sv = _spin(af2, 5, 100, 1, width=5, fmt="%.0f")
        self._nh3_crit_sb.pack(side="right", padx=2)
        self._nh3_crit_sv.set(f"{s['ammonia_crit_ppm']:.0f}")
        _label(af2, "ppm", font=FONT_LABEL, fg=SUB, bg=PANEL).pack(side="right")
        _label(af2, "Critical when NH₃ ≥", font=FONT_LABEL, fg=CRIT, bg=PANEL).pack(side="right", padx=(0, 6))

        # ── Sound ─────────────────────────────────────────────────────────────
        _label(panel, "🔊  Sound Level", font=("Segoe UI", 10, "bold"),
               fg=WARN, bg=PANEL).pack(anchor="w", pady=(0, 4))

        sf = _frame(panel, bg=PANEL)
        sf.pack(fill="x")
        self._snd_warn_sb, self._snd_warn_sv = _spin(sf, 50, 110, 1, width=5, fmt="%.0f")
        self._snd_warn_sb.pack(side="right", padx=2)
        self._snd_warn_sv.set(f"{s['sound_warn_db']:.0f}")
        _label(sf, "dB", font=FONT_LABEL, fg=SUB, bg=PANEL).pack(side="right")
        _label(sf, "Warning when sound >", font=FONT_LABEL, fg=TEXT, bg=PANEL).pack(side="right", padx=(0, 6))
        _label(sf, "  Normal: ≤ 75 dB", font=FONT_SMALL, fg=SUB, bg=PANEL).pack(side="left")

    # ── Section: Video Overlay ────────────────────────────────────────────────

    def _build_overlay(self, parent: tk.Frame) -> None:
        s = self.settings

        panel = _frame(parent, bg=PANEL, padx=18, pady=14)
        panel.pack(fill="x")

        hrow = _frame(panel, bg=PANEL)
        hrow.pack(fill="x", pady=(0, 10))
        _label(hrow, "  VIDEO OVERLAY", font=FONT_HEAD,
               fg=ACCENT, bg=PANEL).pack(side="left")
        _label(hrow, "  Choose which sensors appear burned into recorded video",
               font=FONT_SMALL, fg=SUB, bg=PANEL).pack(side="left")

        _sep(panel, pady=(0, 10))

        # ── Master on/off toggle ──────────────────────────────────────────────
        self._overlay_enabled_var = tk.BooleanVar(value=s["overlay_enabled"])
        master_row = _frame(panel, bg=PANEL)
        master_row.pack(fill="x", pady=(0, 6))
        tk.Checkbutton(
            master_row,
            text="  Show sensor overlay on recorded video",
            variable=self._overlay_enabled_var,
            font=("Segoe UI", 10, "bold"), fg=TEXT, bg=PANEL,
            selectcolor=ENTRY, activebackground=PANEL, activeforeground=TEXT,
            command=self._toggle_overlay,
        ).pack(side="left")

        # ── Per-sensor checkboxes ─────────────────────────────────────────────
        self._overlay_sensors_frame = _frame(panel, bg=PANEL)
        self._overlay_sensors_frame.pack(fill="x", pady=(0, 8))

        self._show_temp_var     = tk.BooleanVar(value=s["show_temp"])
        self._show_humidity_var = tk.BooleanVar(value=s["show_humidity"])
        self._show_ammonia_var  = tk.BooleanVar(value=s["show_ammonia"])
        self._show_sound_var    = tk.BooleanVar(value=s["show_sound"])

        sensor_checks = [
            ("🌡  Temperature  (DHT22)",   self._show_temp_var),
            ("💧  Humidity       (DHT22)", self._show_humidity_var),
            ("☁   Ammonia NH₃  (MQ-137)",  self._show_ammonia_var),
            ("🔊  Sound Level   (LM386)",   self._show_sound_var),
        ]

        cols_frame = _frame(self._overlay_sensors_frame, bg=PANEL)
        cols_frame.pack(fill="x", padx=(24, 0))
        for i, (label, var) in enumerate(sensor_checks):
            row = i // 2
            col = i % 2
            tk.Checkbutton(
                cols_frame,
                text=f"  {label}",
                variable=var,
                font=FONT_LABEL, fg=TEXT, bg=PANEL,
                selectcolor=ENTRY, activebackground=PANEL, activeforeground=TEXT,
            ).grid(row=row, column=col, sticky="w", padx=(0, 32), pady=2)

        # ── Overlay position ──────────────────────────────────────────────────
        pos_frame = _frame(panel, bg=PANEL)
        pos_frame.pack(fill="x")

        _label(pos_frame, "Position on video frame:",
               font=FONT_LABEL, fg=TEXT, bg=PANEL).pack(side="left", padx=(0, 10))

        self._overlay_pos_sv = tk.StringVar(value=s.get("overlay_position", "top-left"))
        pos_menu = tk.OptionMenu(pos_frame, self._overlay_pos_sv, *POSITION_OPTIONS)
        pos_menu.config(
            bg=ENTRY, fg=TEXT, activebackground=ACCENT, activeforeground=TEXT,
            font=FONT_LABEL, relief="flat", bd=0,
            highlightthickness=1, highlightcolor=ACCENT,
            highlightbackground=BORDER, width=14, anchor="w",
        )
        pos_menu["menu"].config(bg=PANEL, fg=TEXT, activebackground=ACCENT,
                                activeforeground=TEXT, font=FONT_LABEL)
        pos_menu.pack(side="left")

        # Set initial enabled/disabled state
        self._toggle_overlay()

    def _toggle_overlay(self) -> None:
        """Grey out per-sensor checkboxes when the whole overlay is disabled."""
        state = "normal" if self._overlay_enabled_var.get() else "disabled"

        def _set_state(widget):
            try:
                widget.config(state=state)
            except tk.TclError:
                pass
            for child in widget.winfo_children():
                _set_state(child)

        _set_state(self._overlay_sensors_frame)

    # ── Section: Recording Schedule ───────────────────────────────────────────

    def _build_schedule(self, parent: tk.Frame) -> None:
        s = self.settings

        panel = _frame(parent, bg=PANEL, padx=18, pady=14)
        panel.pack(fill="x")

        hrow = _frame(panel, bg=PANEL)
        hrow.pack(fill="x", pady=(0, 10))
        _label(hrow, "  RECORDING SCHEDULE", font=FONT_HEAD,
               fg=ACCENT, bg=PANEL).pack(side="left")
        _label(hrow, "  When to record and how long each video file lasts",
               font=FONT_SMALL, fg=SUB, bg=PANEL).pack(side="left")

        _sep(panel, pady=(0, 10))

        # ── 24/7 toggle ───────────────────────────────────────────────────────
        self._rec_always_var = tk.BooleanVar(value=s["rec_always"])
        cb_frame = _frame(panel, bg=PANEL)
        cb_frame.pack(fill="x", pady=(0, 8))
        cb = tk.Checkbutton(
            cb_frame, text="  Record 24 hours / 7 days (ignore schedule below)",
            variable=self._rec_always_var,
            font=FONT_LABEL, fg=TEXT, bg=PANEL, selectcolor=ENTRY,
            activebackground=PANEL, activeforeground=TEXT,
            command=self._toggle_schedule,
        )
        cb.pack(side="left")

        # ── Start / End time ──────────────────────────────────────────────────
        self._time_frame = _frame(panel, bg=PANEL)
        self._time_frame.pack(fill="x", pady=(0, 8))

        # Start
        _label(self._time_frame, "Start time:", font=FONT_LABEL, fg=TEXT,
               bg=PANEL).pack(side="left", padx=(0, 8))
        self._start_h_sb, self._start_h_sv = _spin(self._time_frame, 0, 23, 1, width=3, fmt="%02.0f")
        self._start_h_sb.pack(side="left")
        self._start_h_sv.set(f"{s['rec_start_h']:02d}")
        _label(self._time_frame, ":", font=("Segoe UI", 12, "bold"),
               fg=SUB, bg=PANEL).pack(side="left", padx=2)
        self._start_m_sb, self._start_m_sv = _spin(self._time_frame, 0, 59, 5, width=3, fmt="%02.0f")
        self._start_m_sb.pack(side="left")
        self._start_m_sv.set(f"{s['rec_start_m']:02d}")

        # End
        _label(self._time_frame, "   End time:", font=FONT_LABEL, fg=TEXT,
               bg=PANEL).pack(side="left", padx=(16, 8))
        self._end_h_sb, self._end_h_sv = _spin(self._time_frame, 0, 23, 1, width=3, fmt="%02.0f")
        self._end_h_sb.pack(side="left")
        self._end_h_sv.set(f"{s['rec_end_h']:02d}")
        _label(self._time_frame, ":", font=("Segoe UI", 12, "bold"),
               fg=SUB, bg=PANEL).pack(side="left", padx=2)
        self._end_m_sb, self._end_m_sv = _spin(self._time_frame, 0, 59, 5, width=3, fmt="%02.0f")
        self._end_m_sb.pack(side="left")
        self._end_m_sv.set(f"{s['rec_end_m']:02d}")

        # ── Segment duration ──────────────────────────────────────────────────
        seg_frame = _frame(panel, bg=PANEL)
        seg_frame.pack(fill="x")
        _label(seg_frame, "Save a new video file every:",
               font=FONT_LABEL, fg=TEXT, bg=PANEL).pack(side="left", padx=(0, 10))

        seg_labels = [label for label, _ in SEGMENT_OPTIONS]
        cur_seg_label = next(
            (lbl for lbl, v in SEGMENT_OPTIONS if v == s["segment_seconds"]),
            seg_labels[2]   # default: 1 hour
        )
        self._seg_sv = tk.StringVar(value=cur_seg_label)
        seg_menu = tk.OptionMenu(seg_frame, self._seg_sv, *seg_labels)
        seg_menu.config(bg=ENTRY, fg=TEXT, activebackground=ACCENT,
                        activeforeground=TEXT, font=FONT_LABEL,
                        relief="flat", bd=0,
                        highlightthickness=1, highlightcolor=ACCENT,
                        highlightbackground=BORDER, width=14, anchor="w")
        seg_menu["menu"].config(bg=PANEL, fg=TEXT, activebackground=ACCENT,
                                activeforeground=TEXT, font=FONT_LABEL)
        seg_menu.pack(side="left")

        # Apply initial toggle state
        self._toggle_schedule()

    def _toggle_schedule(self) -> None:
        """Grey out start/end time widgets when 24/7 is checked."""
        state = "disabled" if self._rec_always_var.get() else "normal"
        fg    = SUB        if self._rec_always_var.get() else TEXT
        for w in self._time_frame.winfo_children():
            try:
                w.config(state=state)
            except tk.TclError:
                pass

    # ── Section: Supabase Sync ────────────────────────────────────────────────

    def _build_sync(self, parent: tk.Frame) -> None:
        s = self.settings

        panel = _frame(parent, bg=PANEL, padx=18, pady=14)
        panel.pack(fill="x")

        hrow = _frame(panel, bg=PANEL)
        hrow.pack(fill="x", pady=(0, 10))
        _label(hrow, "  SUPABASE SYNC", font=FONT_HEAD,
               fg=ACCENT, bg=PANEL).pack(side="left")
        _label(hrow, "  How often to upload sensor data to the cloud",
               font=FONT_SMALL, fg=SUB, bg=PANEL).pack(side="left")

        _sep(panel, pady=(0, 10))

        sf = _frame(panel, bg=PANEL)
        sf.pack(fill="x")
        _label(sf, "Upload data to Supabase every:",
               font=FONT_LABEL, fg=TEXT, bg=PANEL).pack(side="left", padx=(0, 10))

        sync_labels = [label for label, _ in SYNC_OPTIONS]
        cur_sync_label = next(
            (lbl for lbl, v in SYNC_OPTIONS if v == s["sync_interval_s"]),
            sync_labels[0]
        )
        self._sync_sv = tk.StringVar(value=cur_sync_label)
        sync_menu = tk.OptionMenu(sf, self._sync_sv, *sync_labels)
        sync_menu.config(bg=ENTRY, fg=TEXT, activebackground=ACCENT,
                         activeforeground=TEXT, font=FONT_LABEL,
                         relief="flat", bd=0,
                         highlightthickness=1, highlightcolor=ACCENT,
                         highlightbackground=BORDER, width=14, anchor="w")
        sync_menu["menu"].config(bg=PANEL, fg=TEXT, activebackground=ACCENT,
                                  activeforeground=TEXT, font=FONT_LABEL)
        sync_menu.pack(side="left")

        _label(sf, "  (Supabase credentials configured in .env)",
               font=FONT_SMALL, fg=SUB, bg=PANEL).pack(side="left", padx=(12, 0))

    # ── Buttons ───────────────────────────────────────────────────────────────

    def _build_buttons(self, parent: tk.Frame) -> None:
        bf = _frame(parent, bg=BG)
        bf.pack()

        reset_btn = tk.Button(
            bf, text="↺  Load Defaults",
            font=("Segoe UI", 10), fg=SUB, bg=PANEL,
            activebackground=BORDER, activeforeground=TEXT,
            relief="flat", bd=0, padx=16, pady=8, cursor="hand2",
            command=self._load_defaults,
        )
        reset_btn.pack(side="left", padx=(0, 12))

        start_btn = tk.Button(
            bf, text="  🐔  Start Monitoring  ",
            font=FONT_BTN, fg=BTN_TXT, bg=BTN,
            activebackground=BTN_HOV, activeforeground=BTN_TXT,
            relief="flat", bd=0, padx=28, pady=12, cursor="hand2",
            command=self._on_start,
        )
        start_btn.pack(side="left")

    # ── Actions ───────────────────────────────────────────────────────────────

    def _read_values(self) -> dict | None:
        """Read all widget values; return None if validation fails."""
        try:
            tw  = float(self._temp_warn_sv.get())
            hlw = float(self._hum_warn_lo_sv.get())
            hhw = float(self._hum_warn_hi_sv.get())
            hlc = float(self._hum_crit_lo_sv.get())
            hhc = float(self._hum_crit_hi_sv.get())
            aw  = float(self._nh3_warn_sv.get())
            ac  = float(self._nh3_crit_sv.get())
            sw  = float(self._snd_warn_sv.get())
            sh  = float(self._start_h_sv.get())
            sm  = float(self._start_m_sv.get())
            eh  = float(self._end_h_sv.get())
            em  = float(self._end_m_sv.get())
        except ValueError as exc:
            messagebox.showerror("Invalid input", f"Please enter valid numbers.\n\n{exc}",
                                 parent=self.root)
            return None

        seg_label = self._seg_sv.get()
        sync_label = self._sync_sv.get()
        values = {
            "temp_warn_high": tw,
            "hum_warn_low": hlw,
            "hum_warn_high": hhw,
            "hum_crit_low": hlc,
            "hum_crit_high": hhc,
            "ammonia_warn_ppm": aw,
            "ammonia_crit_ppm": ac,
            "sound_warn_db": sw,
            "rec_always": self._rec_always_var.get(),
            "rec_start_h": sh,
            "rec_start_m": sm,
            "rec_end_h": eh,
            "rec_end_m": em,
            "overlay_enabled": self._overlay_enabled_var.get(),
            "show_temp": self._show_temp_var.get(),
            "show_humidity": self._show_humidity_var.get(),
            "show_ammonia": self._show_ammonia_var.get(),
            "show_sound": self._show_sound_var.get(),
            "segment_seconds": next(
                (v for lbl, v in SEGMENT_OPTIONS if lbl == seg_label), None
            ),
            "sync_interval_s": next(
                (v for lbl, v in SYNC_OPTIONS if lbl == sync_label), None
            ),
            "overlay_position": self._overlay_pos_sv.get(),
        }
        errors = _validate_values(values)
        if errors:
            messagebox.showerror("Invalid settings",
                                 "\n".join(errors), parent=self.root)
            return None

        for key in ("rec_start_h", "rec_start_m", "rec_end_h", "rec_end_m"):
            values[key] = int(values[key])
        return values

    def _on_start(self) -> None:
        values = self._read_values()
        if values is None:
            return
        try:
            save_settings(values)
        except (OSError, TypeError, ValueError) as exc:
            messagebox.showerror(
                "Could not save settings",
                f"Settings were not saved:\n\n{exc}",
                parent=self.root,
            )
            return
        self.result = values
        self.root.destroy()

    def _load_defaults(self) -> None:
        d = DEFAULTS
        self._temp_warn_sv.set(f"{d['temp_warn_high']:.1f}")
        self._hum_warn_lo_sv.set(f"{d['hum_warn_low']:.0f}")
        self._hum_warn_hi_sv.set(f"{d['hum_warn_high']:.0f}")
        self._hum_crit_lo_sv.set(f"{d['hum_crit_low']:.0f}")
        self._hum_crit_hi_sv.set(f"{d['hum_crit_high']:.0f}")
        self._nh3_warn_sv.set(f"{d['ammonia_warn_ppm']:.0f}")
        self._nh3_crit_sv.set(f"{d['ammonia_crit_ppm']:.0f}")
        self._snd_warn_sv.set(f"{d['sound_warn_db']:.0f}")
        self._rec_always_var.set(d["rec_always"])
        self._start_h_sv.set(f"{d['rec_start_h']:02d}")
        self._start_m_sv.set(f"{d['rec_start_m']:02d}")
        self._end_h_sv.set(f"{d['rec_end_h']:02d}")
        self._end_m_sv.set(f"{d['rec_end_m']:02d}")
        seg_label = next(lbl for lbl, v in SEGMENT_OPTIONS if v == d["segment_seconds"])
        self._seg_sv.set(seg_label)
        sync_label = next(lbl for lbl, v in SYNC_OPTIONS if v == d["sync_interval_s"])
        self._sync_sv.set(sync_label)
        self._toggle_schedule()
        # Overlay defaults
        self._overlay_enabled_var.set(d["overlay_enabled"])
        self._show_temp_var.set(d["show_temp"])
        self._show_humidity_var.set(d["show_humidity"])
        self._show_ammonia_var.set(d["show_ammonia"])
        self._show_sound_var.set(d["show_sound"])
        self._overlay_pos_sv.set(d.get("overlay_position", "top-left"))
        self._toggle_overlay()

    def _on_close(self) -> None:
        if messagebox.askokcancel("Quit", "Cancel setup and exit the program?",
                                  parent=self.root):
            self.root.destroy()
            if self.exit_on_close:
                sys.exit(0)

    # ── Utilities ─────────────────────────────────────────────────────────────

    def _center(self) -> None:
        self.root.update_idletasks()

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()

        # Natural (unconstrained) size
        nat_w = self.root.winfo_reqwidth()
        nat_h = self.root.winfo_reqheight()

        # Always at least 560 px wide; cap height to screen − 80 px (taskbar room)
        win_w = max(nat_w, 560)
        win_h = min(nat_h, sh - 80)

        # Enforce a sensible minimum height so the scroll area is usable
        win_h = max(win_h, 480)

        x = max(0, (sw - win_w) // 2)
        y = max(0, (sh - win_h) // 2)
        self.root.geometry(f"{win_w}x{win_h}+{x}+{y}")
        self.root.minsize(520, 400)

    def show(self) -> dict | None:
        """Display the window; block until the user clicks Start or closes."""
        self.root.mainloop()
        return self.result


# ── Convenience entry point ───────────────────────────────────────────────────

def show(exit_on_close: bool = True) -> dict | None:
    """
    Show the setup window and return the chosen settings dict.
    Calls sys.exit(0) if the user closes the window without clicking Start.
    """
    ui = SetupUI(exit_on_close=exit_on_close)
    result = ui.show()
    if result is None and exit_on_close:
        sys.exit(0)
    return result
