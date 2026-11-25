import json
import threading
import unittest
from unittest.mock import patch

import mainV1


class MainV1SyncTests(unittest.TestCase):
    def setUp(self):
        self.original_state = mainV1.get_state()
        self.original_stop_set = self.original_state["stop_event"].is_set()
        self.test_stop_event = threading.Event()
        with mainV1.state_lock:
            mainV1._state.update({
                "current_path": None,
                "hit_objects": [],
                "play_state": "menu",
                "live_ms": None,
                "speed_change": 1.0,
                "stop_event": self.test_stop_event,
                "relax_thread": None,
                "_auto_started": False,
                "_manual_armed": False,
            })

    def tearDown(self):
        with mainV1.state_lock:
            mainV1._state.clear()
            mainV1._state.update(self.original_state)
        if self.original_stop_set:
            self.original_state["stop_event"].set()
        else:
            self.original_state["stop_event"].clear()

    def test_cursor_mapping_ignores_sensitivity_scale(self):
        with patch.object(mainV1, "CURSOR_SENS", 2.0):
            x, y = mainV1.osu_to_screen(128, 96)

        self.assertAlmostEqual(x, mainV1.PF_LEFT + mainV1.PF_W * 0.25)
        self.assertAlmostEqual(y, mainV1.PF_TOP + mainV1.PF_H * 0.25)

    def test_sync_anchor_uses_packet_receipt_and_configured_delay(self):
        anchor = mainV1.sync_wall_from_tosu_live(
            live_ms=1000,
            first_note_ms=3000,
            rate=1.5,
            received_at=50.0,
        )

        expected = 50.0 + (2000 / 1000 / 1.5) - 0.016
        self.assertAlmostEqual(anchor, expected)

    def test_prestart_sync_reproduces_missing_lead_in(self):
        anchor = mainV1.sync_wall_from_tosu_live(
            live_ms=0,
            first_note_ms=1000,
            rate=1.0,
            received_at=50.0,
        )

        self.assertAlmostEqual(anchor, 51.784)

    def test_sleep_until_cancels_when_run_is_stopped(self):
        self.test_stop_event.set()

        self.assertFalse(
            mainV1.sleep_until(mainV1.time.perf_counter() + 10, self.test_stop_event)
        )

    def test_menu_packets_preserve_a_pending_sync_arm(self):
        mainV1.set_state({"_manual_armed": True})
        with patch.object(mainV1, "resolve_path", return_value=None):
            mainV1.on_message(None, json.dumps({"state": {"name": "menu"}}))

        self.assertTrue(mainV1.get_state("_manual_armed"))

    def test_autostart_waits_for_current_map_and_valid_live_time(self):
        map_path = r"C:\maps\current.osu"
        objects = [{"time_ms": 2000}]
        mainV1.set_state({"_manual_armed": True})
        message = json.dumps({
            "state": {"name": "play"},
            "beatmap": {"time": {"live": 100}},
        })

        with patch.object(mainV1, "resolve_path", return_value=map_path), patch.object(
            mainV1, "parse_osu_file", return_value=objects
        ), patch.object(mainV1.time, "perf_counter", return_value=123.456), patch.object(
            mainV1.threading, "Thread"
        ) as thread_factory:
            mainV1.on_message(None, message)

        self.assertEqual(mainV1.get_state("current_path"), map_path)
        self.assertEqual(mainV1.get_state("hit_objects"), objects)
        self.assertTrue(mainV1.get_state("_auto_started"))
        self.assertFalse(mainV1.get_state("_manual_armed"))
        thread_factory.assert_called_once_with(
            target=mainV1.start_from_tosu_live,
            args=(100.0, 123.456, map_path),
            daemon=True,
        )
        thread_factory.return_value.start.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
