"""[[key:Name]] and [[wait]] markers (Update #65)."""

import random
import threading
import time as time_module
import unittest

from app_loader import get_app

APP = get_app()
KEY, WAIT = APP["ACTION_KEY"], APP["ACTION_WAIT"]


def settings(**changes):
    base = {"enable_pause_markers": True, "enable_format_markers": True, "enable_action_markers": True}
    base.update(changes)
    return base


class StripActionMarkerTests(unittest.TestCase):
    def test_key_and_wait_removed_with_positions(self):
        text, positions, spans, problems = APP["strip_action_markers"]("ab[[key:Tab]]cd[[wait]]ef")
        self.assertEqual(text, "abcdef")
        self.assertEqual(positions, {2: [(KEY, "tab")], 4: [(WAIT, "")]})
        self.assertEqual(len(spans), 2)
        self.assertEqual(problems, [])

    def test_names_ignore_capitals_and_spaces_and_accept_aliases(self):
        for raw, name in (("[[KEY:TAB]]", "tab"), ("[[ key : Shift + Tab ]]", "shift+tab"),
                          ("[[key:Esc]]", "escape"), ("[[key:Page Down]]", "pagedown"),
                          ("[[key:Return]]", "enter")):
            _text, positions, _spans, problems = APP["strip_action_markers"](raw)
            self.assertEqual(positions, {0: [(KEY, name)]}, msg=raw)
            self.assertEqual(problems, [])

    def test_unknown_key_stays_in_the_text_and_is_reported(self):
        text, positions, _spans, problems = APP["strip_action_markers"]("a[[key:Tabb]]b")
        self.assertEqual(text, "a[[key:Tabb]]b")
        self.assertEqual(positions, {})
        self.assertEqual(len(problems), 1)

    def test_several_markers_at_one_spot_keep_their_order(self):
        _text, positions, _spans, _problems = APP["strip_action_markers"]("x[[key:Tab]][[wait]][[key:Enter]]y")
        self.assertEqual(positions, {1: [(KEY, "tab"), (WAIT, ""), (KEY, "enter")]})

    def test_markers_at_start_and_end(self):
        text, positions, _spans, _problems = APP["strip_action_markers"]("[[wait]]hi[[key:Enter]]")
        self.assertEqual(text, "hi")
        self.assertEqual(positions, {0: [(WAIT, "")], 2: [(KEY, "enter")]})


class PrepareTests(unittest.TestCase):
    def test_positions_line_up_with_clean_text(self):
        prepared = APP["prepare_input_text"]("Name[[key:Tab]]Pat[[key:Enter]]", settings())
        self.assertEqual(prepared.clean_text, "NamePat")
        self.assertEqual(prepared.action_map, {4: [(KEY, "tab")], 7: [(KEY, "enter")]})

    def test_nothing_of_a_marker_is_ever_typed(self):
        raw = "a[[key:Tab]]b[[wait]]c[[key:Shift+Tab]]d"
        self.assertEqual(APP["prepare_input_text"](raw, settings()).clean_text, "abcd")

    def test_protected_text_before_a_marker_keeps_correct_ranges(self):
        prepared = APP["prepare_input_text"]("[[AB12]][[key:Tab]]x [[CD]]", settings())
        self.assertEqual(prepared.clean_text, "AB12x CD")
        self.assertEqual(prepared.action_map, {4: [(KEY, "tab")]})
        for start, end in prepared.protected_ranges:
            self.assertIn(prepared.clean_text[start:end], ("AB12", "CD"))

    def test_wait_is_not_protected_text(self):
        prepared = APP["prepare_input_text"]("a[[wait]]b", settings())
        self.assertEqual(prepared.clean_text, "ab")
        self.assertEqual(prepared.protected_ranges, [])

    def test_pause_and_format_positions_stay_correct(self):
        prepared = APP["prepare_input_text"]("ab[[key:Tab]]c{{pause:2}}d[[b]]e[[/b]]", settings())
        self.assertEqual(prepared.clean_text, "abcde")
        self.assertEqual(prepared.pause_map, {3: 2.0})
        self.assertEqual(prepared.format_map, {4: ["bold"], 5: ["bold"]})
        self.assertEqual(prepared.action_map, {2: [(KEY, "tab")]})

    def test_markers_removed_but_not_acted_on_when_turned_off(self):
        prepared = APP["prepare_input_text"]("a[[key:Tab]]b[[wait]]c", settings(enable_action_markers=False))
        self.assertEqual(prepared.clean_text, "abc")
        self.assertEqual(prepared.action_map, {})

    def test_unknown_key_is_typed_as_normal_text(self):
        prepared = APP["prepare_input_text"]("a[[key:Nope]]b", settings())
        self.assertEqual(prepared.clean_text, "akey:Nopeb")
        self.assertEqual(prepared.action_map, {})


class SmartFixSafetyTests(unittest.TestCase):
    FIX = dict(enable_smart_fixes=True, smart_fix_spacing=True, smart_fix_punctuation=True,
               smart_fix_capitalization=True, smart_fix_repeated_words=True)

    def test_text_next_to_a_marker_is_never_changed(self):
        # Without a marker this gets fixed (a missing space after a period).
        plain = APP["prepare_input_text"]("end.Next one", settings(**self.FIX))
        self.assertNotEqual(plain.clean_text, "end.Next one")
        marked = APP["prepare_input_text"]("end.[[key:Tab]]Next one", settings(**self.FIX))
        self.assertEqual(marked.clean_text, "end.Next one")
        self.assertEqual(marked.action_map, {4: [(KEY, "tab")]})

    def test_markers_follow_the_text_when_fixes_happen_elsewhere(self):
        prepared = APP["prepare_input_text"]("hello  there[[key:Tab]]ok", settings(**self.FIX))
        position = next(iter(prepared.action_map))
        self.assertTrue(prepared.clean_text[:position].endswith("there"))
        self.assertEqual(prepared.clean_text[position:], "ok")


class PlanningTests(unittest.TestCase):
    def test_planning_map_marks_every_marker_spot(self):
        planning = APP["make_planning_format_map"]({3: ["bold"]}, {5: [(KEY, "tab")], 3: [(WAIT, "")]})
        self.assertEqual(planning, {3: ["bold"], 5: []})

    def test_real_format_map_is_not_changed(self):
        original = {3: ["bold"]}
        APP["make_planning_format_map"](original, {5: [(KEY, "tab")]})
        self.assertEqual(original, {3: ["bold"]})

    def test_second_revision_never_plans_in_a_paragraph_with_a_marker(self):
        text = "The quick brown fox jumps over the lazy dog because it was important.\n"
        mask = [False] * len(text)
        config = {"enable_lookback_edit": True, "lookback_edit_chance": 100.0}
        planning = APP["make_planning_format_map"]({}, {20: [(KEY, "tab")]})
        for _ in range(30):
            swaps, fixes = APP["plan_lookback_edits"](text, mask, config, 0, planning, {}, {}, {})
            self.assertEqual((swaps, fixes), ({}, {}))

    def test_select_retype_never_plans_a_word_next_to_a_marker(self):
        text = "Please remember that this important thing is very different today. "
        mask = [False] * len(text)
        config = {"enable_select_retype": True, "select_retype_chance": 100.0}
        position = text.index("important")
        planning = APP["make_planning_format_map"]({}, {position: [(KEY, "tab")]})
        for _ in range(40):
            plan = APP["plan_select_retypes"](text, mask, config, 0, planning, {}, {}, {}, {})
            for edit in plan.values():
                self.assertFalse(edit["start"] - 1 <= position <= edit["end"] + 1)


class TypoWallTests(unittest.TestCase):
    """Typos must never be typed across a marker."""

    def setUp(self):
        self.sent = []
        self._saved = {name: APP[name] for name in ("_send_char", "_send_backspace", "paced_sleep", "log_event")}
        APP["_send_char"] = lambda settings, text, **kw: self.sent.append(text) or 0.0
        APP["_send_backspace"] = lambda settings: None
        APP["paced_sleep"] = lambda seconds: None
        APP["log_event"] = lambda *args, **kw: None

    def tearDown(self):
        APP.update(self._saved)

    def test_lookahead_stops_at_a_marker(self):
        text = "abcdef"
        mask = [False] * len(text)
        for _ in range(60):
            self.sent.clear()
            APP["_simulate_adjacent_key_typo"]({}, "a", text, 0, mask, True, walls={2})
            # The wrong letter, and at most the one real letter before the wall.
            self.assertLessEqual(len(self.sent), 2)
            self.assertNotIn("c", self.sent)

    def test_doubled_and_missed_space_stop_at_a_marker(self):
        text = "ab cd"
        mask = [False] * len(text)
        for _ in range(60):
            self.sent.clear()
            APP["_simulate_doubled_letter_typo"]({}, text, 1, mask, walls={2})
            self.assertEqual(self.sent, ["b", "b"])
            self.sent.clear()
            APP["_simulate_missed_space_typo"]({}, text, 2, mask, walls={3})
            self.assertEqual(self.sent, [])


class RunMarkerTests(unittest.TestCase):
    def setUp(self):
        self.pressed = []
        names = ("pyautogui", "paced_sleep", "log_event", "release_all_held_keys",
                 "_lookback_gate", "alert_auto_pause", "run_on_main_thread", "_note_blocked_time")
        self._saved = {name: APP.get(name) for name in names}
        outer = self

        class FakeGui:
            @staticmethod
            def press(key):
                outer.pressed.append((key,))

            @staticmethod
            def hotkey(*keys):
                outer.pressed.append(tuple(keys))

        APP["pyautogui"] = FakeGui
        APP["paced_sleep"] = lambda seconds: None
        APP["log_event"] = lambda *args, **kw: None
        APP["release_all_held_keys"] = lambda: None
        APP["_lookback_gate"] = lambda settings, check_focus: True
        APP["alert_auto_pause"] = lambda settings: None
        APP["run_on_main_thread"] = lambda func: None
        APP["_note_blocked_time"] = lambda seconds: None
        APP["stop_event"].clear()
        APP["pause_event"].clear()

    def tearDown(self):
        for name, value in self._saved.items():
            if value is None:
                APP.pop(name, None)
            else:
                APP[name] = value
        APP["stop_event"].clear()
        APP["pause_event"].clear()

    def wait_until_paused(self):
        deadline = time_module.time() + 3
        while not APP["pause_event"].is_set() and time_module.time() < deadline:
            time_module.sleep(0.02)

    def test_keys_are_pressed_in_order(self):
        actions = [(KEY, "tab"), (KEY, "shift+tab"), (KEY, "escape"), (KEY, "pagedown")]
        ok, waited = APP["_run_action_markers"]({}, actions, 5)
        self.assertTrue(ok)
        self.assertEqual(waited, 0.0)
        self.assertEqual(self.pressed, [("tab",), ("shift", "tab"), ("esc",), ("pagedown",)])

    def test_preview_presses_nothing_and_never_waits(self):
        actions = [(KEY, "enter"), (WAIT, ""), (KEY, "tab")]
        ok, waited = APP["_run_action_markers"]({"preview_mode": True}, actions, 0)
        self.assertTrue(ok)
        self.assertEqual((self.pressed, waited), ([], 0.0))
        self.assertFalse(APP["pause_event"].is_set())

    def test_stop_before_a_key_presses_nothing(self):
        APP["stop_event"].set()
        ok, _waited = APP["_run_action_markers"]({}, [(KEY, "enter")], 0)
        self.assertFalse(ok)
        self.assertEqual(self.pressed, [])

    def test_wait_pauses_until_resume(self):
        outcome = {}
        thread = threading.Thread(
            target=lambda: outcome.update(result=APP["_run_action_markers"]({}, [(WAIT, ""), (KEY, "tab")], 3))
        )
        thread.start()
        self.wait_until_paused()
        self.assertTrue(APP["pause_event"].is_set())
        self.assertEqual(self.pressed, [])  # the Tab waits for the person
        APP["pause_event"].clear()
        thread.join(5)
        self.assertTrue(outcome["result"][0])
        self.assertGreater(outcome["result"][1], 0.0)
        self.assertEqual(self.pressed, [("tab",)])

    def test_stop_during_a_wait_ends_it(self):
        outcome = {}
        thread = threading.Thread(
            target=lambda: outcome.update(result=APP["_run_action_markers"]({}, [(WAIT, ""), (KEY, "tab")], 0))
        )
        thread.start()
        self.wait_until_paused()
        APP["stop_event"].set()
        thread.join(5)
        self.assertFalse(outcome["result"][0])
        self.assertEqual(self.pressed, [])


class EstimateAndCheckTests(unittest.TestCase):
    def test_counts_and_estimates(self):
        prepared = APP["prepare_input_text"]("a[[key:Tab]]b[[key:Tab]]c[[wait]]d", settings())
        self.assertEqual(APP["count_action_markers"](prepared.action_map), (2, 1))
        self.assertEqual(APP["count_action_markers"](prepared.action_map, 2), (1, 1))
        self.assertAlmostEqual(APP["estimate_action_marker_seconds"](prepared), 0.8)
        self.assertIn("1 wait for you", APP["describe_wait_markers"](prepared))
        plain = APP["prepare_input_text"]("abc", settings())
        self.assertEqual(APP["describe_wait_markers"](plain), "")

    def test_real_run_with_markers_is_not_text_checked(self):
        state = APP["state"]
        state.text_check_results = []
        APP["run_typed_text_check"](
            {"check_typed_text": True}, "hello world", 0, {}, "file.txt", {5: [(KEY, "tab")]}
        )
        self.assertEqual(len(state.text_check_results), 1)
        self.assertEqual(state.text_check_results[0]["status"], "unreadable")
        self.assertIn("key or wait markers", state.text_check_results[0]["reason"])

    def test_markers_before_the_resume_point_do_not_block_the_check(self):
        self.assertFalse(APP["has_action_markers"]({2: [(KEY, "tab")]}, 5))
        self.assertTrue(APP["has_action_markers"]({7: [(KEY, "tab")]}, 5))


class SettingTests(unittest.TestCase):
    def test_setting_is_valid_saved_and_local_only(self):
        APP["__messagebox__"].calls.clear()
        values = APP["validate_settings"]()
        self.assertIs(values["enable_action_markers"], True)
        snapshot = APP["get_current_settings_snapshot"]()
        self.assertIn("enable_action_markers", snapshot)
        self.assertIn("enable_action_markers", APP["PROFILE_LOCAL_ONLY_KEYS"])
        self.assertIn("enable_action_markers", APP["AUTO_PROFILE_KEEP_KEYS"])
        self.assertNotIn("enable_action_markers", APP["get_shareable_profile_data"]())

    def test_a_profile_never_changes_it(self):
        var = APP["enable_action_markers_var"]
        var.set(False)
        try:
            snapshot = dict(APP["get_current_settings_snapshot"]())
            snapshot["enable_action_markers"] = True
            APP["apply_settings_snapshot"](snapshot, include_files=False, rebind_hotkeys=False)
            self.assertIs(var.get(), False)
            APP["apply_settings_snapshot"](snapshot, include_files=True, rebind_hotkeys=False)
            self.assertIs(var.get(), True)
        finally:
            var.set(True)

    def test_reset_section_exists(self):
        self.assertIn("Key & Wait Markers", APP["SECTION_RESET_VARS"])


class RandomFileTests(unittest.TestCase):
    def test_random_marker_files_never_lose_or_type_a_marker(self):
        rng = random.Random(65)
        pieces = ["abc ", "[[key:Tab]]", "[[wait]]", "[[b]]", "[[/b]]", "{{pause:1}}", "[[SECRET1]]", "x.", "\n"]
        for _ in range(200):
            raw = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 14)))
            prepared = APP["prepare_input_text"](raw, settings())
            self.assertNotIn("key:", prepared.clean_text)
            self.assertNotIn("wait]]", prepared.clean_text)
            self.assertEqual(
                APP["count_action_markers"](prepared.action_map),
                (raw.count("[[key:Tab]]"), raw.count("[[wait]]")),
                msg=raw,
            )
            for position in prepared.action_map:
                self.assertTrue(0 <= position <= len(prepared.clean_text), msg=raw)


if __name__ == "__main__":
    unittest.main()
