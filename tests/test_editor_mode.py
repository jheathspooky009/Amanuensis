"""Editor Type: which keys are added at the start of a line, and the setting itself."""

import unittest

from app_loader import get_app

APP = get_app()
MESSAGES = APP["__messagebox__"]

PLAIN = APP["EDITOR_MODE_PLAIN"]
DOCS = APP["EDITOR_MODE_DOCS"]
CODE = APP["EDITOR_MODE_CODE"]
CLEAR_INDENT = APP["EDITOR_ACTION_CLEAR_INDENT"]
LIST_MARKER = APP["EDITOR_ACTION_LIST_MARKER"]


class NormalizeTests(unittest.TestCase):
    def test_known_modes_are_kept(self):
        for mode in (PLAIN, DOCS, CODE):
            self.assertEqual(APP["normalize_editor_mode"](mode), mode)

    def test_anything_else_becomes_plain_text(self):
        for junk in ("", "word", None, 5, ["Code editor"], "code editor"):
            self.assertEqual(APP["normalize_editor_mode"](junk), PLAIN, msg=repr(junk))

    def test_the_three_choices_are_in_order(self):
        self.assertEqual(APP["EDITOR_MODES"], (PLAIN, DOCS, CODE))
        self.assertEqual(PLAIN, "Plain text")


class FindListMarkerTests(unittest.TestCase):
    def find(self, text, idx=0):
        return APP["find_editor_list_marker"](text, idx)

    def test_numbers_letters_and_bullets(self):
        self.assertEqual(self.find("1. First"), ("", "1."))
        self.assertEqual(self.find("10. Ten"), ("", "10."))
        self.assertEqual(self.find("2) Second"), ("", "2)"))
        self.assertEqual(self.find("a. Item"), ("", "a."))
        self.assertEqual(self.find("B) Item"), ("", "B)"))
        self.assertEqual(self.find("- Item"), ("", "-"))
        self.assertEqual(self.find("* Item"), ("", "*"))
        self.assertEqual(self.find("+ Item"), ("", "+"))
        self.assertEqual(self.find("\u2022 Item"), ("", "\u2022"))

    def test_leading_spaces_and_tabs_are_returned(self):
        self.assertEqual(self.find("  - Item"), ("  ", "-"))
        self.assertEqual(self.find("\t1. Item"), ("\t", "1."))

    def test_text_that_only_looks_like_a_marker_is_left_alone(self):
        for text in ("Hello world", "1.5 miles", "e.g. this", "U.S. Army", "-- dash", "1000. big",
                     "1.", "-", "ab. no", "**bold**", "[[1. protected]]"):
            self.assertIsNone(self.find(text), msg=text)

    def test_only_the_start_of_a_line_counts(self):
        text = "Intro\n2. Two\nmid 3. not"
        self.assertIsNone(self.find(text, 1))
        self.assertEqual(self.find(text, 6), ("", "2."))
        self.assertIsNone(self.find(text, text.index("3.")))

    def test_bad_positions_do_not_crash(self):
        self.assertIsNone(self.find("1. x", -1))
        self.assertIsNone(self.find("1. x", 99))
        self.assertIsNone(self.find("", 0))

    def test_too_much_leading_space_is_not_a_marker(self):
        self.assertIsNone(self.find(" " * 13 + "- Item"))


class PlanTests(unittest.TestCase):
    def plan(self, mode, text, idx):
        return APP["plan_editor_line_start"](mode, text, idx)

    def test_plain_text_never_adds_keys(self):
        text = "1. One\n    indented\n"
        for idx in range(len(text)):
            self.assertIsNone(self.plan(PLAIN, text, idx))

    def test_unknown_mode_is_plain_text(self):
        self.assertIsNone(self.plan("nonsense", "1. One\n- Two", 0))

    def test_code_editor_clears_every_line_after_the_first(self):
        text = "def f():\n    return 1\n\nx = 2\n"
        starts = [i for i in range(1, len(text)) if text[i - 1] == "\n"]
        self.assertEqual(len(starts), 3)
        for idx in range(len(text)):
            expected = (CLEAR_INDENT, "", "") if idx in starts else None
            self.assertEqual(self.plan(CODE, text, idx), expected, msg=idx)

    def test_code_editor_leaves_a_list_marker_line_alone(self):
        self.assertIsNone(self.plan(CODE, "1. One", 0))

    def test_docs_plans_a_list_marker_only_at_a_line_start(self):
        text = "Plan:\n1. One\n  - Two\nnot a list\n"
        self.assertEqual(self.plan(DOCS, text, 6), (LIST_MARKER, "", "1."))
        self.assertEqual(self.plan(DOCS, text, text.index("  - Two")), (LIST_MARKER, "  ", "-"))
        self.assertIsNone(self.plan(DOCS, text, 0))
        self.assertIsNone(self.plan(DOCS, text, 7))
        self.assertIsNone(self.plan(DOCS, text, text.index("not")))

    def test_docs_first_line_can_be_a_list(self):
        self.assertEqual(self.plan(DOCS, "- first\n", 0), (LIST_MARKER, "", "-"))

    def test_docs_does_not_clear_indents(self):
        self.assertIsNone(self.plan(DOCS, "a\n    b", 2))


class SpanTests(unittest.TestCase):
    def test_span_is_clear_when_nothing_is_planned(self):
        clear = APP["editor_marker_span_is_clear"]
        self.assertTrue(clear(10, 3, ({}, set(), {99: "x"})))

    def test_span_is_blocked_by_any_position_inside_it(self):
        clear = APP["editor_marker_span_is_clear"]
        self.assertFalse(clear(10, 3, ({10: "b"},)))
        self.assertFalse(clear(10, 3, ({}, {12})))
        self.assertTrue(clear(10, 3, ({13: "b"},)))
        self.assertTrue(clear(10, 0, ({10: "b"},)))


class FakeKeys:
    """Records the keys the editor helpers press instead of pressing them."""

    def __init__(self):
        self.events = []

    # keyboard.write(text)
    def write(self, text):
        self.events.append(("type", text))

    # pyautogui.press(key) and pyautogui.hotkey(*keys)
    def press(self, key):
        self.events.append(("press", key))

    def hotkey(self, *keys):
        self.events.append(("hotkey", keys))


class TinyEditor:
    """A one-line editor that follows only the keys the helpers use: typing,
    Left, Right, Backspace, and Shift+Home (select back to the line start)."""

    def __init__(self, line="", cursor=None):
        self.line = line
        self.cursor = len(line) if cursor is None else cursor
        self.selection_start = None

    def apply(self, events):
        for kind, value in events:
            if kind == "type":
                for char in value:
                    self._type(char)
            elif kind == "press" and value == "left":
                self.cursor = max(0, self.cursor - 1)
            elif kind == "press" and value == "right":
                self.cursor = min(len(self.line), self.cursor + 1)
            elif kind == "press" and value == "backspace":
                self._backspace()
            elif kind == "hotkey" and value == ("shift", "home"):
                self.selection_start = 0
            else:
                raise AssertionError(f"unexpected key: {kind} {value}")

    def _type(self, char):
        start = self.selection_start if self.selection_start is not None else self.cursor
        self.line = self.line[:start] + char + self.line[self.cursor:]
        self.cursor = start + 1
        self.selection_start = None

    def _backspace(self):
        if self.selection_start is not None:
            self.line = self.line[:self.selection_start] + self.line[self.cursor:]
            self.cursor = self.selection_start
            self.selection_start = None
        elif self.cursor > 0:
            self.line = self.line[:self.cursor - 1] + self.line[self.cursor:]
            self.cursor -= 1


class KeySequenceTests(unittest.TestCase):
    """Runs the two key sequences against fake keys and a tiny editor."""

    def setUp(self):
        self.keys = FakeKeys()
        self._saved = {name: APP.get(name) for name in ("keyboard", "pyautogui", "paced_sleep")}
        APP["keyboard"] = self.keys
        APP["pyautogui"] = self.keys
        APP["paced_sleep"] = lambda seconds: None

    def tearDown(self):
        for name, value in self._saved.items():
            if value is None:
                APP.pop(name, None)
            else:
                APP[name] = value

    def run_list_marker(self, lead, marker, settings=None):
        APP["_run_editor_list_marker"](settings or {}, lead, marker, 0.0, 0.0)

    def run_clear_indent(self, settings=None):
        APP["_run_editor_clear_indent"](settings or {})

    def test_list_marker_key_order(self):
        self.run_list_marker("", "1.")
        self.assertEqual(
            self.keys.events,
            [("type", " "), ("press", "left"), ("type", "1"), ("type", "."), ("press", "right")],
        )

    def test_list_marker_leaves_the_same_text_on_screen(self):
        for lead, marker in (("", "1."), ("", "12)"), ("  ", "-"), ("\t", "a."), ("", "\u2022")):
            with self.subTest(lead=lead, marker=marker):
                self.keys.events.clear()
                self.run_list_marker(lead, marker)
                editor = TinyEditor()
                editor.apply(self.keys.events)
                self.assertEqual(editor.line, lead + marker + " ")
                self.assertEqual(editor.cursor, len(editor.line))

    def test_the_marker_is_never_followed_by_a_typed_space(self):
        # The editor makes the list when the space is typed AFTER the marker.
        self.run_list_marker("", "1.")
        typed = [value for kind, value in self.keys.events if kind == "type"]
        self.assertEqual(typed[-1], ".")

    def test_clear_indent_key_order(self):
        self.run_clear_indent()
        self.assertEqual(
            self.keys.events,
            [("hotkey", ("shift", "home")), ("type", " "), ("press", "backspace")],
        )

    def test_clear_indent_empties_the_line_whatever_the_editor_added(self):
        for added in ("", " ", "    ", "\t\t", "        "):
            with self.subTest(added=repr(added)):
                self.keys.events.clear()
                self.run_clear_indent()
                editor = TinyEditor(added)
                editor.apply(self.keys.events)
                self.assertEqual(editor.line, "")

    def test_clear_indent_keeps_text_after_the_cursor(self):
        self.run_clear_indent()
        editor = TinyEditor("    rest of line", cursor=4)
        editor.apply(self.keys.events)
        self.assertEqual(editor.line, "rest of line")

    def test_preview_runs_press_nothing(self):
        self.run_list_marker("", "1.", {"preview_mode": True})
        self.run_clear_indent({"preview_mode": True})
        self.assertEqual(self.keys.events, [])


class EditorModeSettingTests(unittest.TestCase):
    def setUp(self):
        self._saved = APP["editor_mode_var"].get()
        MESSAGES.calls.clear()

    def tearDown(self):
        APP["editor_mode_var"].set(self._saved)

    def test_default_is_plain_text(self):
        APP["editor_mode_var"].set(PLAIN)
        self.assertEqual(APP["validate_settings"]()["editor_mode"], PLAIN)

    def test_a_chosen_mode_reaches_the_settings(self):
        for mode in (DOCS, CODE):
            APP["editor_mode_var"].set(mode)
            self.assertEqual(APP["validate_settings"]()["editor_mode"], mode)

    def test_a_bad_value_is_put_back_to_plain_text(self):
        APP["editor_mode_var"].set("Notepad of doom")
        settings = APP["validate_settings"]()
        self.assertEqual(settings["editor_mode"], PLAIN)
        self.assertEqual(APP["editor_mode_var"].get(), PLAIN)
        self.assertEqual(MESSAGES.calls, [])

    def test_the_mode_survives_saving_and_loading(self):
        APP["editor_mode_var"].set(CODE)
        snapshot = APP["get_current_settings_snapshot"]()
        self.assertEqual(snapshot["editor_mode"], CODE)
        APP["editor_mode_var"].set(PLAIN)
        APP["apply_settings_snapshot"](snapshot, include_files=False, rebind_hotkeys=False)
        self.assertEqual(APP["editor_mode_var"].get(), CODE)

    def test_older_files_without_the_setting_load_as_plain_text(self):
        APP["editor_mode_var"].set(DOCS)
        snapshot = dict(APP["get_current_settings_snapshot"]())
        del snapshot["editor_mode"]
        APP["apply_settings_snapshot"](snapshot, include_files=False, rebind_hotkeys=False)
        self.assertEqual(APP["editor_mode_var"].get(), PLAIN)

    def test_a_damaged_file_value_loads_as_plain_text(self):
        snapshot = dict(APP["get_current_settings_snapshot"]())
        for junk in ("Vim", 7, None, ["Code editor"], {"a": 1}):
            snapshot["editor_mode"] = junk
            APP["apply_settings_snapshot"](snapshot, include_files=False, rebind_hotkeys=False)
            self.assertEqual(APP["editor_mode_var"].get(), PLAIN, msg=repr(junk))

    def test_starter_profiles_leave_the_mode_alone(self):
        self.assertIn("editor_mode", APP["STARTER_PROFILE_KEEP_KEYS"])
        for name, profile in APP["STARTER_PROFILES"].items():
            self.assertNotIn("editor_mode", profile["settings"], msg=name)

    def test_the_card_has_a_reset_section(self):
        self.assertIn(APP["editor_mode_var"], APP["SECTION_RESET_VARS"]["Editor Type"])


if __name__ == "__main__":
    unittest.main()
