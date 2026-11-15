"""Auto-pause when the screen locks: the decision, the pause, the resume safety valve, and the setting."""

import unittest

from app_loader import get_app

APP = get_app()
MESSAGES = APP["__messagebox__"]

LOCKED = APP["SCREEN_STATE_LOCKED"]
SESSION = APP["SCREEN_STATE_SESSION"]
DISCONNECTED = APP["SESSION_STATE_DISCONNECTED"]


class ClassifyTests(unittest.TestCase):
    def classify(self, desktop_reachable, session_state):
        return APP["classify_screen_state"](desktop_reachable, session_state)

    def test_a_normal_desktop_and_active_session_may_type(self):
        self.assertIsNone(self.classify(True, 0))
        self.assertIsNone(self.classify(True, None))

    def test_a_refused_desktop_means_locked(self):
        self.assertEqual(self.classify(False, 0), LOCKED)
        self.assertEqual(self.classify(False, None), LOCKED)

    def test_a_disconnected_session_means_session_changed(self):
        self.assertEqual(self.classify(True, DISCONNECTED), SESSION)
        self.assertEqual(self.classify(None, DISCONNECTED), SESSION)

    def test_locked_wins_when_both_are_true(self):
        self.assertEqual(self.classify(False, DISCONNECTED), LOCKED)

    def test_unreadable_means_fine(self):
        self.assertIsNone(self.classify(None, None))

    def test_other_connect_states_are_fine(self):
        for number in (0, 1, 2, 3, 5, 6, 7, 8, 9, -1, 99):
            self.assertIsNone(self.classify(True, number), msg=number)

    def test_only_a_real_false_counts_as_locked(self):
        for junk in (0, "", [], "False", None):
            self.assertIsNone(self.classify(junk, 0), msg=repr(junk))


class ReadScreenStateTests(unittest.TestCase):
    def test_a_computer_that_is_not_windows_never_pauses(self):
        # The tests run on any system. Off Windows the answer is "could not tell".
        import sys
        if sys.platform == "win32":
            self.skipTest("this check is for computers that are not Windows")
        self.assertEqual(APP["read_screen_state"](), (None, None))

    def test_the_answer_is_always_two_values_of_the_right_kind(self):
        reachable, session = APP["read_screen_state"]()
        self.assertIn(reachable, (True, False, None))
        self.assertTrue(session is None or isinstance(session, int))


class PauseTests(unittest.TestCase):
    """Pausing uses the shared pause event, so the fake screen is swapped in and out."""

    def setUp(self):
        self._real_read = APP["read_screen_state"]
        self._real_main = APP["run_on_main_thread"]
        self.main_calls = []
        APP["run_on_main_thread"] = lambda action: self.main_calls.append(action)
        APP["pause_event"].clear()
        APP["screen_guard_override_event"].clear()
        APP["state"].status = "Typing..."
        self.screen = (True, 0)
        APP["read_screen_state"] = lambda: self.screen

    def tearDown(self):
        APP["read_screen_state"] = self._real_read
        APP["run_on_main_thread"] = self._real_main
        APP["pause_event"].clear()
        APP["screen_guard_override_event"].clear()
        APP["state"].status = "Idle"

    def test_a_normal_screen_does_not_pause(self):
        self.assertFalse(APP["pause_for_blocked_screen"]())
        self.assertFalse(APP["pause_event"].is_set())
        self.assertEqual(APP["state"].status, "Typing...")
        self.assertEqual(self.main_calls, [])

    def test_a_locked_screen_pauses_and_says_why(self):
        self.screen = (False, 0)
        self.assertTrue(APP["pause_for_blocked_screen"]())
        self.assertTrue(APP["pause_event"].is_set())
        self.assertTrue(APP["state"].status.startswith("Screen locked"))
        self.assertEqual(len(self.main_calls), 1)  # only the Resume button text, no beep

    def test_a_switched_session_pauses_and_says_why(self):
        self.screen = (True, DISCONNECTED)
        self.assertTrue(APP["pause_for_blocked_screen"]())
        self.assertTrue(APP["pause_event"].is_set())
        self.assertTrue(APP["state"].status.startswith("Session changed"))

    def test_the_beep_only_plays_when_asked(self):
        self.screen = (False, 0)
        APP["pause_for_blocked_screen"](False)
        without_beep = len(self.main_calls)
        APP["pause_event"].clear()
        APP["pause_for_blocked_screen"](True)
        self.assertEqual(len(self.main_calls) - without_beep, 2)
        self.assertIn(APP["play_auto_pause_alert"], self.main_calls)

    def test_it_pauses_once_not_again_while_paused(self):
        self.screen = (False, 0)
        APP["pause_for_blocked_screen"](True)
        calls_after_first = len(self.main_calls)
        for _ in range(5):
            self.assertTrue(APP["pause_for_blocked_screen"](True))
        self.assertEqual(len(self.main_calls), calls_after_first)

    def test_a_pause_the_person_made_is_left_alone(self):
        APP["pause_event"].set()
        APP["state"].status = "Paused"
        self.screen = (False, 0)
        self.assertTrue(APP["pause_for_blocked_screen"](True))
        self.assertEqual(APP["state"].status, "Paused")
        self.assertEqual(self.main_calls, [])

    def test_a_still_locked_screen_pauses_again_after_resume(self):
        self.screen = (False, 0)
        APP["pause_for_blocked_screen"]()
        APP["pause_event"].clear()
        APP["pause_for_blocked_screen"]()
        self.assertTrue(APP["pause_event"].is_set())

    def test_an_override_stops_all_pausing(self):
        APP["screen_guard_override_event"].set()
        self.screen = (False, 0)
        self.assertFalse(APP["pause_for_blocked_screen"]())
        self.assertFalse(APP["pause_event"].is_set())


class ResumeSafetyValveTests(unittest.TestCase):
    def setUp(self):
        self._real_read = APP["read_screen_state"]
        APP["screen_guard_override_event"].clear()
        self.screen = (True, 0)
        APP["read_screen_state"] = lambda: self.screen

    def tearDown(self):
        APP["read_screen_state"] = self._real_read
        APP["screen_guard_override_event"].clear()
        APP["state"].status = "Idle"

    def test_resuming_after_a_real_unlock_keeps_the_check_on(self):
        APP["state"].status = "Screen locked \u2014 paused"
        APP["note_resume_while_screen_blocked"]()
        self.assertFalse(APP["screen_guard_override_event"].is_set())

    def test_resuming_while_it_still_reads_locked_turns_the_check_off(self):
        APP["state"].status = "Screen locked \u2014 paused"
        self.screen = (False, 0)
        APP["note_resume_while_screen_blocked"]()
        self.assertTrue(APP["screen_guard_override_event"].is_set())

    def test_the_same_goes_for_a_switched_session(self):
        APP["state"].status = "Session changed \u2014 paused"
        self.screen = (True, DISCONNECTED)
        APP["note_resume_while_screen_blocked"]()
        self.assertTrue(APP["screen_guard_override_event"].is_set())

    def test_resuming_from_any_other_pause_never_turns_it_off(self):
        self.screen = (False, 0)
        for status in ("Paused", "Activity detected \u2014 paused", "Focus lost \u2014 paused", "Typing..."):
            APP["state"].status = status
            APP["note_resume_while_screen_blocked"]()
            self.assertFalse(APP["screen_guard_override_event"].is_set(), msg=status)


class StatusNoteTests(unittest.TestCase):
    def tearDown(self):
        APP["state"].status = "Idle"

    def test_the_pause_waits_keep_every_auto_pause_note(self):
        for status in ("Activity detected \u2014 paused", "Screen locked \u2014 paused", "Session changed \u2014 paused"):
            APP["state"].status = status
            self.assertTrue(APP["_status_is_auto_pause_note"](), msg=status)

    def test_other_statuses_are_not_notes(self):
        for status in ("Paused", "Typing...", "Focus lost \u2014 paused", "Research Break (5s left)...", ""):
            APP["state"].status = status
            self.assertFalse(APP["_status_is_auto_pause_note"](), msg=status)

    def test_every_pause_text_is_recognized_as_a_screen_note(self):
        for text in APP["SCREEN_PAUSE_STATUS_TEXT"].values():
            APP["state"].status = text
            self.assertTrue(APP["_status_is_screen_note"](), msg=text)


class SettingTests(unittest.TestCase):
    def setUp(self):
        self._saved = APP["auto_pause_screen_lock_var"].get()
        MESSAGES.calls.clear()

    def tearDown(self):
        APP["auto_pause_screen_lock_var"].set(self._saved)

    def test_it_is_on_by_default(self):
        self.assertTrue(APP["auto_pause_screen_lock_var"].get())
        self.assertTrue(APP["validate_settings"]()["auto_pause_screen_lock"])

    def test_off_reaches_the_settings(self):
        APP["auto_pause_screen_lock_var"].set(False)
        self.assertFalse(APP["validate_settings"]()["auto_pause_screen_lock"])
        self.assertEqual(MESSAGES.calls, [])

    def test_it_survives_saving_and_loading(self):
        APP["auto_pause_screen_lock_var"].set(False)
        snapshot = APP["get_current_settings_snapshot"]()
        self.assertIs(snapshot["auto_pause_screen_lock"], False)
        APP["auto_pause_screen_lock_var"].set(True)
        APP["apply_settings_snapshot"](snapshot, include_files=True, rebind_hotkeys=False)
        self.assertFalse(APP["auto_pause_screen_lock_var"].get())

    def test_older_files_without_the_setting_load_as_on(self):
        APP["auto_pause_screen_lock_var"].set(False)
        snapshot = dict(APP["get_current_settings_snapshot"]())
        del snapshot["auto_pause_screen_lock"]
        APP["apply_settings_snapshot"](snapshot, include_files=True, rebind_hotkeys=False)
        self.assertTrue(APP["auto_pause_screen_lock_var"].get())

    def test_the_reset_button_of_its_card_puts_it_back_on(self):
        self.assertIn(APP["auto_pause_screen_lock_var"], APP["SECTION_RESET_VARS"]["Activity Auto-Pause"])

    def test_profiles_carry_it_but_starter_profiles_never_change_it(self):
        self.assertIn("auto_pause_screen_lock", APP["get_shareable_profile_data"]())
        self.assertIn("auto_pause_screen_lock", APP["STARTER_PROFILE_KEEP_KEYS"])
        for name, profile in APP["STARTER_PROFILES"].items():
            self.assertNotIn("auto_pause_screen_lock", profile["settings"], msg=name)


if __name__ == "__main__":
    unittest.main()
