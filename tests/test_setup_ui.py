"""Unit tests for setup settings validation and safe file handling."""

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

# This environment may not include Tk's native display module. These tests
# exercise the non-visual settings logic, so provide import-only stubs if needed.
try:
    import tkinter  # noqa: F401
except ImportError:
    tkinter_stub = types.ModuleType("tkinter")
    for widget_name in (
        "Frame", "Label", "Spinbox", "StringVar", "BooleanVar", "Button",
        "Canvas", "Checkbutton", "OptionMenu", "Scrollbar", "Tk",
    ):
        setattr(tkinter_stub, widget_name, type(widget_name, (), {}))
    tkinter_stub.TclError = type("TclError", (Exception,), {})
    tkinter_stub.messagebox = types.ModuleType("tkinter.messagebox")
    sys.modules["tkinter"] = tkinter_stub
    sys.modules["tkinter.messagebox"] = tkinter_stub.messagebox

import setup_ui


class SetupSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path_patch = patch.object(
            setup_ui, "SETTINGS_PATH", Path(self.temp.name) / "settings.json"
        )
        self.path_patch.start()
        self.warning_patch = patch.object(setup_ui, "SETTINGS_LOAD_WARNING", None)
        self.warning_patch.start()

    def tearDown(self):
        self.warning_patch.stop()
        self.path_patch.stop()
        self.temp.cleanup()

    def test_default_settings_pass_validation(self):
        self.assertEqual(setup_ui._validate_values(setup_ui.DEFAULTS), [])

    def test_rejects_numeric_values_outside_widget_ranges(self):
        for key, invalid in (
            ("temp_warn_high", 46),
            ("hum_warn_low", 61),
            ("ammonia_crit_ppm", 101),
            ("sound_warn_db", float("inf")),
            ("rec_start_h", 24),
            ("rec_end_m", 60),
            ("rec_start_m", 4.5),
        ):
            with self.subTest(key=key, invalid=invalid):
                settings = {**setup_ui.DEFAULTS, key: invalid}
                self.assertTrue(setup_ui._validate_values(settings))

    def test_rejects_wrong_types_and_inconsistent_thresholds(self):
        wrong_type = {**setup_ui.DEFAULTS, "temp_warn_high": "40"}
        inconsistent = {**setup_ui.DEFAULTS, "ammonia_crit_ppm": 5.0}
        self.assertTrue(setup_ui._validate_values(wrong_type))
        self.assertTrue(setup_ui._validate_values(inconsistent))

    def test_invalid_json_uses_defaults_with_warning_and_keeps_original(self):
        original = "{ not valid json"
        setup_ui.SETTINGS_PATH.write_text(original, encoding="utf-8")
        loaded = setup_ui.load_settings()
        self.assertEqual(loaded, setup_ui.DEFAULTS)
        self.assertIn("could not be loaded", setup_ui.SETTINGS_LOAD_WARNING)
        self.assertEqual(setup_ui.SETTINGS_PATH.read_text(encoding="utf-8"), original)

    def test_saving_valid_settings_backs_up_invalid_original(self):
        original = "{ not valid json"
        setup_ui.SETTINGS_PATH.write_text(original, encoding="utf-8")
        setup_ui.save_settings(dict(setup_ui.DEFAULTS))
        backups = list(setup_ui.SETTINGS_PATH.parent.glob("settings.invalid-*.json.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), original)
        saved = json.loads(setup_ui.SETTINGS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(saved, setup_ui.DEFAULTS)

    def test_invalid_values_cannot_be_saved(self):
        setup_ui.SETTINGS_PATH.write_text('{"untouched": true}', encoding="utf-8")
        invalid = {**setup_ui.DEFAULTS, "rec_start_h": 99}
        with self.assertRaises(ValueError):
            setup_ui.save_settings(invalid)
        self.assertEqual(
            setup_ui.SETTINGS_PATH.read_text(encoding="utf-8"),
            '{"untouched": true}',
        )


if __name__ == "__main__":
    unittest.main()