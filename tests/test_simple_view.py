"""Tests for the Simple view and first-run guide pieces (no window needed)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app_loader import get_app  # noqa: E402

APP = get_app()


class ViewModeTests(unittest.TestCase):
    def test_normalize_accepts_any_capitals(self):
        self.assertEqual(APP["normalize_view_mode"]("simple"), "Simple")
        self.assertEqual(APP["normalize_view_mode"]("  ADVANCED "), "Advanced")

    def test_normalize_bad_values_use_fallback(self):
        self.assertEqual(APP["normalize_view_mode"]("other"), "Advanced")
        self.assertEqual(APP["normalize_view_mode"](5, "Simple"), "Simple")
        self.assertEqual(APP["normalize_view_mode"](None), "Advanced")

    def test_new_install_is_simple_with_guide_unseen(self):
        self.assertEqual(APP["choose_startup_view"](False, None), ("Simple", False))

    def test_old_settings_file_keeps_advanced_and_skips_guide(self):
        self.assertEqual(APP["choose_startup_view"](True, {"theme": "dark"}), ("Advanced", True))

    def test_saved_choices_are_kept(self):
        data = {"view_mode": "Simple", "first_run_guide_done": False}
        self.assertEqual(APP["choose_startup_view"](True, data), ("Simple", False))

    def test_unreadable_or_odd_settings_file(self):
        self.assertEqual(APP["choose_startup_view"](True, None), ("Advanced", True))
        odd = {"view_mode": "x", "first_run_guide_done": "no"}
        self.assertEqual(APP["choose_startup_view"](True, odd), ("Advanced", True))


class SettingsRoundTripTests(unittest.TestCase):
    def test_view_settings_in_snapshot(self):
        APP["view_mode_var"].set("Advanced")
        APP["first_run_guide_done_var"].set(True)
        snapshot = APP["get_current_settings_snapshot"]()
        self.assertEqual(snapshot["view_mode"], "Advanced")
        self.assertIs(snapshot["first_run_guide_done"], True)

    def test_bad_view_mode_is_repaired_by_validation(self):
        APP["view_mode_var"].set("nonsense")
        settings = APP["validate_settings"]()
        self.assertEqual(settings["view_mode"], "Simple")
        self.assertEqual(APP["view_mode_var"].get(), "Simple")

    def test_profiles_never_carry_view_settings(self):
        for name in ("PROFILE_LOCAL_ONLY_KEYS", "AUTO_PROFILE_KEEP_KEYS"):
            if name in APP:
                self.assertIn("view_mode", APP[name])
                self.assertIn("first_run_guide_done", APP[name])


class SettingsSearchTests(unittest.TestCase):
    INDEX = [
        {"title": "Typing Speed", "tab": "Speed", "text": "Min delay Max delay Target WPM"},
        {"title": "Mistakes", "tab": "Mistakes", "text": "Typo chance adjacent key pause"},
        {"title": "Thinking Pauses", "tab": "Speed", "text": "Pause length"},
    ]

    def test_empty_query_gives_nothing(self):
        self.assertEqual(APP["find_settings_matches"]("   ", self.INDEX), [])

    def test_title_matches_come_first(self):
        found = APP["find_settings_matches"]("pause", self.INDEX)
        self.assertEqual([e["title"] for e in found], ["Thinking Pauses", "Mistakes"])

    def test_all_words_must_match_and_capitals_do_not_matter(self):
        found = APP["find_settings_matches"]("TYPO CHANCE", self.INDEX)
        self.assertEqual([e["title"] for e in found], ["Mistakes"])
        self.assertEqual(APP["find_settings_matches"]("typo wpm", self.INDEX), [])

    def test_limit_is_respected(self):
        many = [{"title": f"Card {i}", "tab": "Input", "text": ""} for i in range(20)]
        self.assertEqual(len(APP["find_settings_matches"]("card", many)), 5)
        self.assertEqual(len(APP["find_settings_matches"]("card", many, limit=3)), 3)


if __name__ == "__main__":
    unittest.main()
