"""Sync regression tests using temporary SQLite and a fake Supabase endpoint."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

import config
import database
import sync


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path_patch = patch.object(
            config, "DB_PATH", str(Path(self.temp.name) / "readings.db")
        )
        self.path_patch.start()
        database._conn = None
        database.init()
        self.client = Mock()
        self.client_patch = patch.object(sync, "_client", self.client)
        self.client_patch.start()

    def tearDown(self):
        database._conn.close()
        database._conn = None
        self.client_patch.stop()
        self.path_patch.stop()
        self.temp.cleanup()

    def insert(self, temperature=24.0):
        return database.insert_reading(
            temperature, 60.0, 5.0, 51, 65.0, 512, 0, 0, 0, 0
        )

    def test_new_readings_upload_after_the_first_pass(self):
        first = self.insert()
        self.assertEqual(sync._sync_once(), 1)
        second = self.insert(25.0)
        self.assertEqual(sync._sync_once(), 1)
        self.assertEqual(sync._sync_once(), 0)
        payloads = self.client.table.return_value.upsert.call_args_list
        self.assertEqual(
            [call.args[0][0]["id"] for call in payloads], [first, second]
        )
        self.assertEqual(database.get_stats()["pending"], 0)

    def test_failed_upload_remains_pending_and_is_retried(self):
        row_id = self.insert()
        execute = self.client.table.return_value.upsert.return_value.execute
        execute.side_effect = [ConnectionError("temporary network failure"), Mock()]
        self.assertEqual(sync._sync_once(), 0)
        self.assertEqual([r["id"] for r in database.get_unsynced()], [row_id])
        self.assertEqual(sync._sync_once(), 1)
        self.assertEqual(database.get_stats()["pending"], 0)

    def test_multiple_batches_are_drained(self):
        for _ in range(config.SYNC_BATCH_SIZE + 1):
            self.insert()
        self.assertEqual(sync._sync_once(), config.SYNC_BATCH_SIZE + 1)
        self.assertEqual(self.client.table.return_value.upsert.call_count, 2)
        self.assertEqual(database.get_stats()["pending"], 0)

    def test_worker_keeps_uploading_on_later_intervals(self):
        self.insert()
        waits = []
        outer = self

        class StopAfterThreeCycles:
            def is_set(self):
                return len(waits) >= 3

            def wait(self, timeout):
                waits.append(timeout)
                if len(waits) < 3:
                    outer.insert(24.0 + len(waits))

        with patch.object(config, "SYNC_INTERVAL_S", 30):
            sync.run(StopAfterThreeCycles())
        self.assertEqual(waits, [30, 30, 30])
        self.assertEqual(self.client.table.return_value.upsert.call_count, 3)
        self.assertEqual(database.get_stats()["pending"], 0)

    def test_saved_settings_do_not_override_sync_to_ten_minutes(self):
        saved = json.loads((ROOT / "settings.json").read_text())
        self.assertEqual(saved["sync_interval_s"], 30)

    def test_launcher_targets_the_continuous_monitoring_entry_point(self):
        launcher = (ROOT / "start.bat").read_text()
        self.assertIn('cd /d "%~dp0"', launcher)
        self.assertIn('-m venv "%VENV_DIR%"', launcher)
        self.assertIn('-m pip install -r "%REQ_FILE%"', launcher)
        self.assertIn('"%~dp0python\\main.py" %*', launcher)
        self.assertNotIn("supabase_sync.py", launcher)
        self.assertNotIn("python setup_ui.py", launcher)


if __name__ == "__main__":
    unittest.main()