"""Hardware-free tests for Arduino discovery, reconnect, and shutdown."""

import sys
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

# Import collector without requiring pyserial or an attached Arduino.
serial_module = types.ModuleType("serial")
serial_module.SerialException = type("SerialException", (Exception,), {})
serial_tools = types.ModuleType("serial.tools")
serial_ports = types.ModuleType("serial.tools.list_ports")
serial_ports.comports = Mock(return_value=[])
serial_tools.list_ports = serial_ports
serial_module.tools = serial_tools
sys.modules.setdefault("serial", serial_module)
sys.modules.setdefault("serial.tools", serial_tools)
sys.modules.setdefault("serial.tools.list_ports", serial_ports)

import collector
import config


class StopEvent:
    """Event test double whose retry waits can trigger deterministic actions."""

    def __init__(self, on_wait=None):
        self.stopped = False
        self.waits = []
        self.on_wait = on_wait

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.on_wait:
            self.on_wait(self, len(self.waits))
        return self.stopped


class CollectorReconnectTests(unittest.TestCase):
    def setUp(self):
        self.seed_patch = patch.object(collector, "_seed_from_db")
        self.seed_patch.start()
        self.simulate_patch = patch.object(config, "SIMULATE", False)
        self.simulate_patch.start()
        self.retry_patch = patch.object(collector, "ARDUINO_RETRY_INTERVAL_S", 0.01)
        self.retry_patch.start()
        self.port_patch = patch.object(config, "SERIAL_PORT", "")
        self.port_patch.start()

    def tearDown(self):
        self.seed_patch.stop()
        self.simulate_patch.stop()
        self.retry_patch.stop()
        self.port_patch.stop()

    def test_waits_when_missing_then_reads_after_board_is_attached(self):
        stop_event = StopEvent()
        find_port = Mock(side_effect=[None, "COM7"])

        def read_and_stop(port, event):
            self.assertEqual(port, "COM7")
            event.set()

        with (
            patch.object(collector, "_find_port", find_port),
            patch.object(collector, "_read_loop", side_effect=read_and_stop),
        ):
            collector.run(stop_event)

        self.assertEqual(find_port.call_count, 2)
        self.assertEqual(stop_event.waits, [0.01])

    def test_rediscovers_port_after_disconnect(self):
        stop_event = StopEvent()
        find_port = Mock(side_effect=["COM3", "COM9"])
        read_loop = Mock()
        def read_side_effect(port, event):
            if read_loop.call_count == 1:
                raise collector.serial.SerialException("USB disconnected")
            event.set()

        read_loop.side_effect = read_side_effect
        with (
            patch.object(collector, "_find_port", find_port),
            patch.object(collector, "_read_loop", read_loop),
        ):
            collector.run(stop_event)

        self.assertEqual(
            [call.args[0] for call in read_loop.call_args_list], ["COM3", "COM9"]
        )
        self.assertEqual(stop_event.waits, [0.01])

    def test_explicit_port_is_reused_without_auto_detection(self):
        stop_event = StopEvent()
        with (
            patch.object(config, "SERIAL_PORT", "COM4"),
            patch.object(collector, "_find_port") as find_port,
            patch.object(
                collector,
                "_read_loop",
            ) as read_loop,
        ):
            def read_side_effect(port, event):
                if read_loop.call_count == 1:
                    raise collector.serial.SerialException("temporary disconnect")
                event.set()

            read_loop.side_effect = read_side_effect
            collector.run(stop_event)

        find_port.assert_not_called()
        self.assertEqual(
            [call.args[0] for call in read_loop.call_args_list], ["COM4", "COM4"]
        )

    def test_shutdown_while_waiting_without_hardware_is_prompt(self):
        stop_event = StopEvent(on_wait=lambda event, _count: event.set())
        with (
            patch.object(collector, "_find_port", return_value=None),
            patch.object(collector, "_read_loop") as read_loop,
        ):
            collector.run(stop_event)

        self.assertEqual(stop_event.waits, [0.01])
        read_loop.assert_not_called()


if __name__ == "__main__":
    unittest.main()