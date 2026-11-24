import json
import threading
import time
import unittest
from unittest.mock import patch

import mainV120


class StopTests(unittest.TestCase):
    def test_timing_waits_cancel_promptly(self):
        for wait in (mainV120.sleep_until, mainV120.smart_sleep_until):
            with self.subTest(wait=wait.__name__):
                stop_event = threading.Event()
                timer = threading.Timer(0.02, stop_event.set)
                started = time.perf_counter()
                timer.start()
                try:
                    self.assertFalse(wait(started + 10, stop_event))
                    self.assertLess(time.perf_counter() - started, 1)
                finally:
                    timer.cancel()
                    timer.join()

    def test_stop_releases_a_held_key(self):
        stop_event = threading.Event()
        key_events = []

        def key_down(key):
            key_events.append(("down", key))
            stop_event.set()

        objects = [{
            "time_ms": 0,
            "end_time_ms": 5000,
            "x": 256,
            "y": 192,
            "end_x": 256,
            "end_y": 192,
            "is_slider": False,
            "is_spinner": True,
        }]

        with patch.multiple(
                mainV120,
                RELAX_MODE=True,
                MOVEMENT_MODE="predictive",
                ENABLE_CLICK_POS_GATE=False,
                HIT_BIAS_MS=0,
                CLICK_VARIATION_MS=0,
        ):
            with patch.object(
                mainV120, "get_state", return_value=stop_event
            ), patch.object(mainV120, "set_status"), patch.object(
                mainV120, "key_down", side_effect=key_down
            ), patch.object(
                mainV120,
                "key_up",
                side_effect=lambda key: key_events.append(("up", key)),
            ):
                mainV120.relax_loop(objects, mainV120.now() + 0.02, 0, 1)

        self.assertEqual(key_events, [("down", "z"), ("up", "z")])

    def test_worker_setup_does_not_clear_an_existing_stop(self):
        stop_event = threading.Event()
        stop_event.set()
        key_events = []
        objects = [{
            "time_ms": 0,
            "end_time_ms": 5000,
            "x": 256,
            "y": 192,
            "end_x": 256,
            "end_y": 192,
            "is_slider": False,
            "is_spinner": True,
        }]

        with patch.multiple(
                mainV120,
                RELAX_MODE=True,
                MOVEMENT_MODE="predictive",
                ENABLE_CLICK_POS_GATE=False,
                HIT_BIAS_MS=0,
                CLICK_VARIATION_MS=0,
        ):
            with patch.object(
                mainV120, "get_state", return_value=stop_event
            ), patch.object(mainV120, "set_status"), patch.object(
                mainV120,
                "key_down",
                side_effect=lambda key: key_events.append(("down", key)),
            ), patch.object(
                mainV120,
                "key_up",
                side_effect=lambda key: key_events.append(("up", key)),
            ):
                mainV120.relax_loop(objects, mainV120.now() + 0.02, 0, 1)

        self.assertTrue(stop_event.is_set())
        self.assertEqual(key_events, [])

    def test_leaving_play_stops_workers_but_keeps_menu_arm(self):
        stop_event = threading.Event()
        state = {"speed_change": 1.0, "_manual_armed": True}

        def get_state(key=None):
            return stop_event if key == "stop_event" else state.copy()

        def set_state(updates):
            state.update(updates)

        with patch.object(mainV120, "get_state", side_effect=get_state), patch.object(
            mainV120, "set_state", side_effect=set_state
        ), patch.object(mainV120, "set_status"):
            mainV120.on_message(None, json.dumps({"state": {"name": "menu"}}))

        self.assertTrue(stop_event.is_set())
        self.assertTrue(state["_manual_armed"])


if __name__ == "__main__":
    unittest.main()
