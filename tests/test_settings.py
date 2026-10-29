"""Settings validation: good values pass, bad values are refused with a message."""

import math
import unittest

from app_loader import get_app

APP = get_app()
MESSAGES = APP["__messagebox__"]

# Every box that holds a number the typing loop uses.
NUMBER_BOXES = (
    "min_delay_var", "max_delay_var", "paragraph_pause_min_var", "paragraph_pause_max_var",
    "sentence_pause_min_var", "sentence_pause_max_var", "break_duration_min_var",
    "break_duration_max_var", "typo_chance_var", "paraphrase_chance_var",
    "lookback_edit_chance_var", "select_retype_chance_var", "overshoot_chance_var",
    "activity_sensitivity_var", "fatigue_strength_var", "typing_mood_strength_var",
    "difficulty_pause_strength_var", "burst_pause_strength_var", "decision_pause_strength_var",
    "rhythm_strength_var", "writing_habit_strength_var", "essay_pace_strength_var",
    "warmup_strength_var", "cluster_strength_var", "smart_fix_visible_chance_var",
)


class SettingsTestCase(unittest.TestCase):
    """Sets boxes for one test, then puts every box back."""

    def setUp(self):
        self._saved = {}
        MESSAGES.calls.clear()

    def tearDown(self):
        for name, value in self._saved.items():
            APP[name].set(value)

    def put(self, **boxes):
        for name, value in boxes.items():
            if name not in self._saved:
                self._saved[name] = APP[name].get()
            APP[name].set(value)

    def validate(self):
        MESSAGES.calls.clear()
        return APP["validate_settings"]()

    def assert_refused(self, **boxes):
        self.put(**boxes)
        self.assertIsNone(self.validate(), msg="not refused: " + ", ".join(boxes))
        self.assertTrue(MESSAGES.calls, msg="no message was shown for " + repr(boxes))


class DefaultSettingsTests(SettingsTestCase):
    def test_default_settings_are_valid(self):
        settings = self.validate()
        self.assertIsInstance(settings, dict)
        self.assertEqual(MESSAGES.calls, [])

    def test_default_values_are_sane(self):
        settings = self.validate()
        self.assertLessEqual(settings["min_delay"], settings["max_delay"])
        self.assertTrue(0 <= settings["typo_chance"] <= 100)
        self.assertEqual(settings["text_size_percent"], 100)
        self.assertEqual(settings["preview_speed"], 1.0)

    def test_valid_edit_is_used(self):
        self.put(min_delay_var="0.2", max_delay_var="0.4")
        settings = self.validate()
        self.assertEqual((settings["min_delay"], settings["max_delay"]), (0.2, 0.4))


class RefusedValueTests(SettingsTestCase):
    def test_words_instead_of_numbers(self):
        self.assert_refused(min_delay_var="abc")
        self.assert_refused(typo_chance_var="lots")

    def test_negative_delay(self):
        self.assert_refused(min_delay_var="-1")

    def test_min_above_max(self):
        self.assert_refused(min_delay_var="1", max_delay_var="0.5")
        self.assert_refused(paragraph_pause_min_var="9", paragraph_pause_max_var="1")
        self.assert_refused(sentence_pause_min_var="9", sentence_pause_max_var="1")

    def test_chance_outside_zero_to_one_hundred(self):
        self.assert_refused(typo_chance_var="150")
        self.assert_refused(typo_chance_var="-3")

    def test_mistake_frequency_rules(self):
        self.assert_refused(mistake_min_var="0")
        self.assert_refused(mistake_min_var="300", mistake_max_var="200")
        self.assert_refused(mistake_min_var="1.5")

    def test_break_frequency_rules(self):
        self.assert_refused(break_min_var="9", break_max_var="2")

    def test_boxes_of_a_turned_off_feature_are_not_checked(self):
        self.put(enable_mistakes_var=False, mistake_min_var="not a number")
        self.assertIsInstance(self.validate(), dict)


class NotANumberTests(SettingsTestCase):
    """nan and inf pass every "less than" check, so they need their own guard.

    A box either refuses the value with a message, or quietly puts back a safe
    number. What must never happen is a non-finite number reaching the typing loop.
    """

    def test_nan_and_infinity_never_get_through(self):
        for name in NUMBER_BOXES:
            for text in ("nan", "inf", "-inf"):
                with self.subTest(box=name, text=text):
                    self.put(**{name: text})
                    settings = self.validate()
                    if settings is None:
                        self.assertTrue(MESSAGES.calls, msg="refused without a message")
                    else:
                        for key, value in settings.items():
                            if isinstance(value, float):
                                self.assertTrue(math.isfinite(value), msg=key)
                    self.put(**{name: self._saved[name]})

    def test_delay_and_pause_boxes_refuse_with_a_message(self):
        for name in ("min_delay_var", "max_delay_var", "paragraph_pause_max_var",
                     "sentence_pause_max_var", "break_duration_max_var"):
            for text in ("nan", "inf"):
                with self.subTest(box=name, text=text):
                    self.assert_refused(**{name: text})
                    self.put(**{name: self._saved[name]})

    def test_every_returned_number_is_finite(self):
        settings = self.validate()
        for key, value in settings.items():
            if isinstance(value, float):
                self.assertTrue(math.isfinite(value), msg=key)


class ClampTests(SettingsTestCase):
    def test_strengths_and_countdown_are_limited_not_refused(self):
        self.put(rhythm_strength_var="9999", start_countdown_seconds_var="500")
        settings = self.validate()
        self.assertEqual(settings["rhythm_strength"], 200.0)
        self.assertEqual(settings["start_countdown_seconds"], 60)

    def test_clamp_helpers_handle_junk(self):
        for name in ("clamp_cluster_strength", "clamp_rhythm_strength", "clamp_essay_strength",
                     "clamp_warmup_strength", "clamp_mood_strength"):
            clamp = APP[name]
            for junk in ("abc", float("nan"), None):
                value = clamp(junk)
                self.assertTrue(math.isfinite(value) and 0.0 <= value <= 200.0, msg=(name, junk))
            self.assertEqual(clamp(-5), 0.0, msg=name)
            self.assertTrue(0.0 < clamp(10 ** 6) <= 200.0, msg=name)

    def test_start_countdown_clamp(self):
        clamp = APP["clamp_start_countdown"]
        self.assertEqual([clamp(-5), clamp(500), clamp("abc"), clamp(float("nan"))], [0, 60, 10, 10])

    def test_text_size_snaps_to_a_choice(self):
        clamp = APP["clamp_text_size_percent"]
        self.assertEqual(clamp("abc"), 100)
        self.assertEqual(clamp(float("nan")), 100)
        self.assertEqual(clamp(130), 125)
        self.assertIn(clamp(10 ** 6), APP["TEXT_SIZE_CHOICES"])

    def test_preview_speed_clamp(self):
        clamp = APP["clamp_preview_speed"]
        self.assertEqual(clamp("abc"), 1.0)
        self.assertEqual(clamp(10 ** 6), 20.0)


class SnapshotTests(SettingsTestCase):
    def test_saving_then_loading_changes_nothing(self):
        before = APP["get_current_settings_snapshot"]()
        APP["apply_settings_snapshot"](before, include_files=False, rebind_hotkeys=False)
        self.assertEqual(APP["get_current_settings_snapshot"](), before)

    def test_loaded_values_reach_the_boxes(self):
        snapshot = dict(APP["get_current_settings_snapshot"]())
        self.put(min_delay_var="0.05")
        snapshot["min_delay"] = "0.07"
        APP["apply_settings_snapshot"](snapshot, include_files=False, rebind_hotkeys=False)
        self.assertEqual(APP["min_delay_var"].get(), "0.07")

    def test_damaged_settings_data_is_ignored(self):
        before = APP["get_current_settings_snapshot"]()
        for junk in ("garbage", None, 5, ["a"]):
            APP["apply_settings_snapshot"](junk, include_files=False, rebind_hotkeys=False)
        self.assertEqual(APP["get_current_settings_snapshot"](), before)

    def test_settings_that_stay_on_this_computer_are_listed(self):
        keep = APP["AUTO_PROFILE_KEEP_KEYS"]
        for key in ("input_path", "file_queue", "text_size_percent", "check_typed_text", "theme"):
            self.assertIn(key, keep)


if __name__ == "__main__":
    unittest.main()
