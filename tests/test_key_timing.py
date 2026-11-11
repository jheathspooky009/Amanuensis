"""Real key press timing: hold times, held Shift, overlap, and safe release."""

import math
import random
import unittest

from app_loader import get_app
from test_settings import SettingsTestCase

APP = get_app()


class FakeKeyboard:
    """Records key events instead of pressing real keys."""

    def __init__(self, fail_on=None):
        self.events = []
        self.fail_on = fail_on

    def press(self, name):
        if name == self.fail_on:
            raise ValueError("not mapped")
        self.events.append(("down", name))

    def release(self, name):
        self.events.append(("up", name))

    def write(self, text):
        self.events.append(("write", text))


class PlanKeyPressTests(unittest.TestCase):
    def test_letters_and_space(self):
        plan = APP["plan_key_press"]
        self.assertEqual(plan("a"), ("a", False))
        self.assertEqual(plan("Q"), ("q", True))
        self.assertEqual(plan(" "), ("space", False))

    def test_capital_without_shift_hold_uses_old_way(self):
        self.assertIsNone(APP["plan_key_press"]("Q", shift_hold=False))

    def test_everything_else_uses_old_way(self):
        plan = APP["plan_key_press"]
        for char in ("1", ".", ",", "!", "\n", "\u00e9", "ab", "", None, 5):
            self.assertIsNone(plan(char), msg=repr(char))


class HoldTimeTests(unittest.TestCase):
    def test_hold_is_inside_the_range(self):
        for _ in range(200):
            seconds = APP["draw_key_hold_seconds"](40, 110)
            self.assertTrue(0.040 <= seconds <= 0.110)

    def test_swapped_and_junk_ranges_are_cleaned(self):
        draw = APP["draw_key_hold_seconds"]
        self.assertTrue(0.04 <= draw(110, 40) <= 0.11)
        for junk in ("abc", float("nan"), float("inf"), None):
            seconds = draw(junk, junk)
            self.assertTrue(math.isfinite(seconds) and 0.01 <= seconds <= 0.5)

    def test_clean_key_hold_ms_limits(self):
        clean = APP["clean_key_hold_ms"]
        self.assertEqual(clean(1, 40.0), 10.0)
        self.assertEqual(clean(9999, 40.0), 500.0)
        self.assertEqual(clean("x", 40.0), 40.0)


class OverlapTests(unittest.TestCase):
    def test_only_opposite_hand_lowercase_letters(self):
        pair = APP["are_opposite_hand_letters"]
        self.assertTrue(pair("t", "i"))
        self.assertFalse(pair("e", "d"))
        self.assertFalse(pair("t", "T"))
        self.assertFalse(pair("t", " "))
        self.assertFalse(pair(None, "a"))

    def test_chance_and_gap_rules(self):
        overlap = APP["should_overlap_keys"]
        self.assertTrue(overlap("t", "i", 0.08, 100))
        self.assertFalse(overlap("t", "i", 0.08, 0))
        self.assertFalse(overlap("t", "i", 0.50, 100))
        self.assertFalse(overlap("e", "d", 0.08, 100))
        self.assertFalse(overlap("t", "i", float("nan"), 100))
        self.assertFalse(overlap("t", "i", 0.08, "lots"))

    def test_chance_is_roughly_respected(self):
        rng = random.Random(7)
        hits = sum(APP["should_overlap_keys"]("t", "i", 0.08, 30, rng) for _ in range(2000))
        self.assertTrue(450 < hits < 750, msg=hits)


class PressSequenceTests(unittest.TestCase):
    """Runs the real press code against a fake keyboard, with no waiting."""

    def setUp(self):
        self.fake = FakeKeyboard()
        self.saved = {name: APP[name] for name in ("keyboard", "time")}
        APP["keyboard"] = self.fake

        class NoSleep:
            @staticmethod
            def sleep(seconds):
                return None

        APP["time"] = NoSleep
        APP["release_all_held_keys"]()
        self.settings = {"enable_key_hold": True, "key_hold_min_ms": 40, "key_hold_max_ms": 110}

    def tearDown(self):
        APP["release_all_held_keys"]()
        APP.update(self.saved)

    def test_letter_goes_down_then_up(self):
        APP["_send_char"](self.settings, "a")
        self.assertEqual(self.fake.events, [("down", "a"), ("up", "a")])

    def test_capital_holds_shift_around_the_letter(self):
        APP["_send_char"](self.settings, "A")
        self.assertEqual(
            self.fake.events,
            [("down", "shift"), ("down", "a"), ("up", "a"), ("up", "shift")],
        )

    def test_digit_and_punctuation_use_the_old_way(self):
        APP["_send_char"](self.settings, "1")
        APP["_send_char"](self.settings, ".")
        self.assertEqual(self.fake.events, [("write", "1"), ("write", ".")])

    def test_off_means_old_way(self):
        APP["_send_char"]({"enable_key_hold": False}, "a")
        self.assertEqual(self.fake.events, [("write", "a")])

    def test_preview_presses_nothing(self):
        APP["run_on_main_thread"] = lambda func: None
        APP["_send_char"]({"enable_key_hold": True, "preview_mode": True}, "a")
        self.assertEqual(self.fake.events, [])

    def test_hold_seconds_are_returned(self):
        spent = APP["_send_char"](self.settings, "a")
        self.assertTrue(0.04 <= spent <= 0.11)

    def test_overlap_presses_next_key_before_releasing_the_last(self):
        APP["_send_char"](self.settings, "t", allow_overlap=True, keep_down=True)
        self.assertEqual(self.fake.events, [("down", "t")])
        APP["_send_char"](self.settings, "i", allow_overlap=True)
        self.assertEqual(
            self.fake.events,
            [("down", "t"), ("down", "i"), ("up", "t"), ("up", "i")],
        )

    def test_key_left_down_is_released_by_the_next_plain_send(self):
        APP["_send_char"](self.settings, "t", allow_overlap=True, keep_down=True)
        APP["_send_char"](self.settings, "x")
        self.assertEqual(self.fake.events[1], ("up", "t"))

    def test_release_all_lets_go_of_everything(self):
        APP["_send_char"](self.settings, "t", allow_overlap=True, keep_down=True)
        APP["release_all_held_keys"]()
        self.assertEqual(self.fake.events[-1], ("up", "t"))
        self.assertEqual(APP["_held_keys"], [])
        APP["release_all_held_keys"]()  # nothing held: does nothing more
        self.assertEqual(self.fake.events.count(("up", "t")), 1)

    def test_unmappable_key_falls_back_to_writing_it(self):
        self.fake.fail_on = "a"
        APP["_send_char"](self.settings, "a")
        self.assertEqual(self.fake.events, [("write", "a")])
        self.assertEqual(APP["_held_keys"], [])

    def test_failure_after_shift_still_releases_shift(self):
        self.fake.fail_on = "a"
        APP["_send_char"](self.settings, "A")
        self.assertIn(("up", "shift"), self.fake.events)
        self.assertEqual(self.fake.events[-1], ("write", "A"))
        self.assertEqual(APP["_held_keys"], [])

    def test_run_of_characters_is_typed_in_order(self):
        APP["_send_char"](self.settings, "hi")
        downs = [name for kind, name in self.fake.events if kind == "down"]
        self.assertEqual(downs, ["h", "i"])
        self.assertEqual(APP["_held_keys"], [])


class KeyTimingSettingsTests(SettingsTestCase):
    def test_defaults_are_off_and_valid(self):
        settings = self.validate()
        self.assertFalse(settings["enable_key_hold"])
        self.assertFalse(settings["enable_key_overlap"])
        self.assertTrue(settings["enable_shift_hold"])
        self.assertEqual((settings["key_hold_min_ms"], settings["key_hold_max_ms"]), (40.0, 110.0))

    def test_good_values_pass(self):
        self.put(enable_key_hold_var=True, key_hold_min_ms_var="20", key_hold_max_ms_var="90",
                 enable_key_overlap_var=True, key_overlap_chance_var="50")
        settings = self.validate()
        self.assertEqual((settings["key_hold_min_ms"], settings["key_hold_max_ms"]), (20.0, 90.0))
        self.assertEqual(settings["key_overlap_chance"], 50.0)

    def test_bad_values_are_refused_when_on(self):
        self.put(enable_key_hold_var=True)
        self.assert_refused(key_hold_min_ms_var="abc")
        self.put(key_hold_min_ms_var="40")
        self.assert_refused(key_hold_min_ms_var="nan")
        self.put(key_hold_min_ms_var="40")
        self.assert_refused(key_hold_max_ms_var="inf")
        self.put(key_hold_max_ms_var="110")
        self.assert_refused(key_hold_min_ms_var="100", key_hold_max_ms_var="50")
        self.put(key_hold_min_ms_var="40", key_hold_max_ms_var="110")
        self.assert_refused(key_hold_max_ms_var="5000")
        self.put(key_hold_max_ms_var="110")
        self.assert_refused(key_hold_min_ms_var="1")

    def test_bad_overlap_chance_is_refused(self):
        self.put(enable_key_hold_var=True, enable_key_overlap_var=True)
        self.assert_refused(key_overlap_chance_var="150")
        self.put(key_overlap_chance_var="30")
        self.assert_refused(key_overlap_chance_var="nan")

    def test_boxes_are_not_checked_when_off(self):
        self.put(enable_key_hold_var=False, key_hold_min_ms_var="junk", key_overlap_chance_var="junk")
        self.assertIsInstance(self.validate(), dict)

    def test_overlap_needs_key_timing(self):
        self.put(enable_key_hold_var=False, enable_key_overlap_var=True)
        self.assertFalse(self.validate()["enable_key_overlap"])

    def test_snapshot_round_trip_and_old_files(self):
        self.put(enable_key_hold_var=True, key_hold_min_ms_var="25", enable_key_overlap_var=True)
        snapshot = APP["get_current_settings_snapshot"]()
        self.put(enable_key_hold_var=False, key_hold_min_ms_var="40", enable_key_overlap_var=False)
        APP["apply_settings_snapshot"](snapshot, include_files=False, rebind_hotkeys=False)
        self.assertTrue(APP["enable_key_hold_var"].get())
        self.assertEqual(APP["key_hold_min_ms_var"].get(), "25")
        old = {key: value for key, value in snapshot.items()
               if not key.startswith(("enable_key_", "key_", "enable_shift_hold"))}
        APP["apply_settings_snapshot"](old, include_files=False, rebind_hotkeys=False)
        self.assertFalse(APP["enable_key_hold_var"].get())
        self.assertEqual(APP["key_hold_min_ms_var"].get(), "40")

    def test_section_reset_lists_the_new_boxes(self):
        names = APP["SECTION_RESET_VARS"]["Key Press Timing"]
        self.assertEqual(len(names), 6)


if __name__ == "__main__":
    unittest.main()
