"""Keyboard layouts: the key tables, the typo and finger code that reads them, and the setting."""

import random
import string
import unittest

from app_loader import get_app

APP = get_app()
MESSAGES = APP["__messagebox__"]

QWERTY = APP["KEYBOARD_LAYOUT_QWERTY"]
AZERTY = APP["KEYBOARD_LAYOUT_AZERTY"]
QWERTZ = APP["KEYBOARD_LAYOUT_QWERTZ"]
DVORAK = APP["KEYBOARD_LAYOUT_DVORAK"]
COLEMAK = APP["KEYBOARD_LAYOUT_COLEMAK"]
LAYOUTS = APP["KEYBOARD_LAYOUTS"]


def neighbors(letter, layout):
    return set(APP["get_adjacent_letters"](letter, layout))


class LayoutListTests(unittest.TestCase):
    def test_the_five_choices_are_in_order_and_qwerty_is_first(self):
        self.assertEqual(LAYOUTS, (QWERTY, AZERTY, QWERTZ, DVORAK, COLEMAK))
        self.assertEqual(QWERTY, "QWERTY")

    def test_known_layouts_are_kept(self):
        for layout in LAYOUTS:
            self.assertEqual(APP["normalize_keyboard_layout"](layout), layout)

    def test_anything_else_becomes_qwerty(self):
        for junk in ("", "azerty", "Dvorak ", None, 5, ["Dvorak"], {"a": 1}):
            self.assertEqual(APP["normalize_keyboard_layout"](junk), QWERTY, msg=repr(junk))


class RowTests(unittest.TestCase):
    def test_every_layout_has_three_rows_of_ten_keys(self):
        for layout in LAYOUTS:
            rows = APP["KEYBOARD_LAYOUT_ROWS"][layout]
            self.assertEqual(len(rows), 3, msg=layout)
            for row in rows:
                self.assertEqual(len(row), 10, msg=layout)

    def test_every_layout_holds_each_letter_exactly_once(self):
        for layout in LAYOUTS:
            letters = "".join(APP["KEYBOARD_LAYOUT_ROWS"][layout]).replace(".", "")
            self.assertEqual(sorted(letters), sorted(string.ascii_lowercase), msg=layout)

    def test_the_known_layouts_differ_from_qwerty_where_they_should(self):
        rows = APP["KEYBOARD_LAYOUT_ROWS"]
        self.assertEqual(rows[AZERTY][0][:2], "az")
        self.assertEqual(rows[AZERTY][1][9], "m")
        self.assertEqual(rows[QWERTZ][0][5], "z")
        self.assertEqual(rows[QWERTZ][2][0], "y")
        self.assertEqual(rows[DVORAK][1], "aoeuidhtns")
        self.assertEqual(rows[COLEMAK][1], "arstdhneio")


class QwertyUnchangedTests(unittest.TestCase):
    """QWERTY keeps the hand-made tables, and the row rules rebuild the same tables."""

    def test_qwerty_uses_the_original_tables(self):
        fingers, adjacency = APP["_layout_tables"](QWERTY)
        self.assertIs(fingers, APP["FINGER_FOR_LETTER"])
        self.assertIs(adjacency, APP["QWERTY_ADJACENCY"])

    def test_the_row_rules_rebuild_the_original_finger_table(self):
        fingers, _ = APP["_build_layout_tables"](APP["KEYBOARD_LAYOUT_ROWS"][QWERTY])
        self.assertEqual(fingers, APP["FINGER_FOR_LETTER"])

    def test_the_row_rules_rebuild_the_original_neighbors(self):
        _, adjacency = APP["_build_layout_tables"](APP["KEYBOARD_LAYOUT_ROWS"][QWERTY])
        original = APP["QWERTY_ADJACENCY"]
        self.assertEqual(set(adjacency), set(original))
        for letter in original:
            self.assertEqual(set(adjacency[letter]), set(original[letter]), msg=letter)

    def test_the_default_layout_is_qwerty(self):
        self.assertEqual(APP["active_keyboard_layout"], QWERTY)
        self.assertEqual(neighbors("a", None), set("qwsz"))


class NeighborTests(unittest.TestCase):
    def test_every_letter_has_neighbors_on_every_layout(self):
        for layout in LAYOUTS:
            for letter in string.ascii_lowercase:
                around = neighbors(letter, layout)
                self.assertTrue(around, msg=(layout, letter))
                self.assertNotIn(letter, around, msg=(layout, letter))
                self.assertTrue(around <= set(string.ascii_lowercase), msg=(layout, letter))

    def test_neighbors_go_both_ways(self):
        for layout in LAYOUTS:
            for letter in string.ascii_lowercase:
                for other in neighbors(letter, layout):
                    self.assertIn(letter, neighbors(other, layout), msg=(layout, letter, other))

    def test_known_neighbors(self):
        self.assertEqual(neighbors("q", AZERTY), set("sazw"))
        self.assertEqual(neighbors("a", AZERTY), set("zq"))
        self.assertEqual(neighbors("m", AZERTY), set("lp"))
        self.assertEqual(neighbors("z", QWERTZ), set("tugh"))
        self.assertEqual(neighbors("y", QWERTZ), set("xas"))
        self.assertEqual(neighbors("a", DVORAK), set("o"))
        self.assertEqual(neighbors("e", DVORAK), set("oupqj"))
        self.assertEqual(neighbors("r", COLEMAK), set("aswfzx"))

    def test_the_same_letter_has_different_neighbors_on_different_layouts(self):
        self.assertNotEqual(neighbors("a", QWERTY), neighbors("a", AZERTY))
        self.assertNotEqual(neighbors("e", QWERTY), neighbors("e", DVORAK))
        self.assertNotEqual(neighbors("s", QWERTY), neighbors("s", COLEMAK))

    def test_capitals_and_junk(self):
        self.assertEqual(neighbors("A", DVORAK), set("o"))
        for junk in ("1", " ", ".", "", "\u00e9", None, 5):
            self.assertEqual(APP["get_adjacent_letters"](junk, DVORAK), "", msg=repr(junk))

    def test_a_bad_layout_name_means_qwerty(self):
        self.assertEqual(neighbors("a", "Nonsense"), neighbors("a", QWERTY))


class FingerTests(unittest.TestCase):
    def finger(self, letter, layout):
        return APP["get_finger_for_letter"](letter, layout)

    def test_every_letter_has_a_finger_on_every_layout(self):
        for layout in LAYOUTS:
            for letter in string.ascii_lowercase:
                hand, finger = self.finger(letter, layout)
                self.assertIn(hand, ("left", "right"))
                self.assertIn(finger, ("pinky", "ring", "middle", "index"))

    def test_each_hand_has_about_half_the_letters(self):
        for layout in LAYOUTS:
            left = sum(1 for c in string.ascii_lowercase if self.finger(c, layout)[0] == "left")
            self.assertTrue(10 <= left <= 16, msg=(layout, left))

    def test_known_fingers(self):
        self.assertEqual(self.finger("a", AZERTY), ("left", "pinky"))
        self.assertEqual(self.finger("m", AZERTY), ("right", "pinky"))
        self.assertEqual(self.finger("z", QWERTZ), ("right", "index"))
        self.assertEqual(self.finger("y", QWERTZ), ("left", "pinky"))
        self.assertEqual(self.finger("e", DVORAK), ("left", "middle"))
        self.assertEqual(self.finger("h", DVORAK), ("right", "index"))
        self.assertEqual(self.finger("s", DVORAK), ("right", "pinky"))
        self.assertEqual(self.finger("e", COLEMAK), ("right", "middle"))
        self.assertEqual(self.finger("o", COLEMAK), ("right", "pinky"))

    def test_not_a_letter_has_no_finger(self):
        self.assertIsNone(self.finger("1", DVORAK))
        self.assertIsNone(self.finger(" ", DVORAK))


class TimingTests(unittest.TestCase):
    def setUp(self):
        APP["set_active_keyboard_layout"](QWERTY)

    def tearDown(self):
        APP["set_active_keyboard_layout"](QWERTY)

    def test_same_pair_changes_with_the_layout(self):
        multiplier = APP["get_finger_timing_multiplier"]
        # e and d share a finger on QWERTY, but are on different hands on Dvorak.
        self.assertEqual(multiplier("e", "d", QWERTY), APP["SAME_FINGER_MULTIPLIER"])
        self.assertEqual(multiplier("e", "d", DVORAK), APP["HAND_SWITCH_MULTIPLIER"])
        # e and j are on different hands on QWERTY, but share a finger on Dvorak.
        self.assertEqual(multiplier("e", "j", QWERTY), APP["HAND_SWITCH_MULTIPLIER"])
        self.assertEqual(multiplier("e", "j", DVORAK), APP["SAME_FINGER_MULTIPLIER"])

    def test_no_layout_given_uses_the_active_one(self):
        multiplier = APP["get_finger_timing_multiplier"]
        self.assertEqual(multiplier("e", "d"), APP["SAME_FINGER_MULTIPLIER"])
        APP["set_active_keyboard_layout"](DVORAK)
        self.assertEqual(multiplier("e", "d"), APP["HAND_SWITCH_MULTIPLIER"])

    def test_spaces_digits_and_repeats_stay_at_one(self):
        multiplier = APP["get_finger_timing_multiplier"]
        for layout in LAYOUTS:
            self.assertEqual(multiplier("a", " ", layout), 1.0)
            self.assertEqual(multiplier("1", "a", layout), 1.0)
            self.assertEqual(multiplier("l", "l", layout), 1.0)
            self.assertEqual(multiplier("", "a", layout), 1.0)

    def test_the_estimate_follows_the_layout(self):
        text = "The quick brown fox jumps over the lazy dog. " * 4
        qwerty = APP["estimate_finger_timing_shift"](text, QWERTY)
        dvorak = APP["estimate_finger_timing_shift"](text, DVORAK)
        self.assertNotEqual(qwerty, dvorak)
        self.assertEqual(APP["estimate_finger_timing_shift"](text), qwerty)
        APP["set_active_keyboard_layout"](DVORAK)
        self.assertEqual(APP["estimate_finger_timing_shift"](text), dvorak)

    def test_the_qwerty_estimate_is_the_same_as_before(self):
        fingers = APP["FINGER_FOR_LETTER"]
        common = APP["COMMON_PAIRS"]
        text = "the lazy dog jumped over a quick brown fox and rested"
        expected = 0.0
        for first, second in zip(text, text[1:]):
            if first + second in common:
                continue
            multiplier = APP["get_finger_timing_multiplier"](first, second, QWERTY)
            if first in fingers and second in fingers:
                expected += multiplier - 1.0
        self.assertAlmostEqual(APP["estimate_finger_timing_shift"](text, QWERTY), expected)

    def test_opposite_hand_letters_follow_the_layout(self):
        opposite = APP["are_opposite_hand_letters"]
        self.assertFalse(opposite("e", "d", QWERTY))
        self.assertTrue(opposite("e", "d", DVORAK))
        self.assertTrue(opposite("e", "j", QWERTY))
        self.assertFalse(opposite("e", "j", DVORAK))
        self.assertFalse(opposite("e", "1", DVORAK))
        self.assertFalse(opposite("e", None, DVORAK))


class TypoTests(unittest.TestCase):
    def setUp(self):
        APP["set_active_keyboard_layout"](QWERTY)

    def tearDown(self):
        APP["set_active_keyboard_layout"](QWERTY)

    def test_a_left_mistake_uses_a_neighbor_on_the_active_layout(self):
        build = APP["_build_left_mistake"]
        for layout in LAYOUTS:
            APP["set_active_keyboard_layout"](layout)
            for letter in string.ascii_lowercase:
                start, end, typed = build("x" + letter + "x", "wrong_letter", 1, random.Random(7))
                self.assertEqual((start, end), (1, 2))
                self.assertIn(typed, neighbors(letter, layout), msg=(layout, letter))

    def test_a_capital_letter_gets_a_capital_neighbor(self):
        APP["set_active_keyboard_layout"](DVORAK)
        _, _, typed = APP["_build_left_mistake"]("xAx", "wrong_letter", 1, random.Random(3))
        self.assertEqual(typed, "O")

    def test_typo_sorting_follows_the_layout(self):
        classify = APP["classify_typo_fix"]
        # s next to a is a next-door typo on QWERTY, but not on Dvorak.
        self.assertEqual(classify(["s"], ["a"], ""), "adjacent")
        APP["set_active_keyboard_layout"](DVORAK)
        self.assertEqual(classify(["s"], ["a"], ""), "other")
        # o next to a is the other way round.
        self.assertEqual(classify(["o"], ["a"], ""), "adjacent")

    def test_other_typo_kinds_do_not_depend_on_the_layout(self):
        classify = APP["classify_typo_fix"]
        for layout in LAYOUTS:
            APP["set_active_keyboard_layout"](layout)
            self.assertEqual(classify(["A"], ["a"], ""), "case")
            self.assertEqual(classify([" "], ["a"], ""), "space")
            self.assertEqual(classify(["t", "h"], ["h", "t"], ""), "swap")


class SettingTests(unittest.TestCase):
    def setUp(self):
        self._saved = APP["keyboard_layout_var"].get()
        APP["keyboard_layout_var"].set(QWERTY)
        MESSAGES.calls.clear()

    def tearDown(self):
        APP["keyboard_layout_var"].set(self._saved)
        APP["set_active_keyboard_layout"](QWERTY)

    def test_default_is_qwerty(self):
        self.assertEqual(APP["validate_settings"]()["keyboard_layout"], QWERTY)

    def test_a_chosen_layout_reaches_the_settings_and_becomes_active(self):
        for layout in LAYOUTS:
            APP["keyboard_layout_var"].set(layout)
            self.assertEqual(APP["validate_settings"]()["keyboard_layout"], layout)
            self.assertEqual(APP["active_keyboard_layout"], layout)

    def test_a_bad_value_is_put_back_to_qwerty(self):
        APP["keyboard_layout_var"].set("Klingon")
        settings = APP["validate_settings"]()
        self.assertEqual(settings["keyboard_layout"], QWERTY)
        self.assertEqual(APP["keyboard_layout_var"].get(), QWERTY)
        self.assertEqual(APP["active_keyboard_layout"], QWERTY)
        self.assertEqual(MESSAGES.calls, [])

    def test_the_layout_survives_saving_and_loading(self):
        APP["keyboard_layout_var"].set(COLEMAK)
        snapshot = APP["get_current_settings_snapshot"]()
        self.assertEqual(snapshot["keyboard_layout"], COLEMAK)
        APP["keyboard_layout_var"].set(QWERTY)
        APP["apply_settings_snapshot"](snapshot, include_files=True, rebind_hotkeys=False)
        self.assertEqual(APP["keyboard_layout_var"].get(), COLEMAK)

    def test_older_files_without_the_setting_load_as_qwerty(self):
        APP["keyboard_layout_var"].set(AZERTY)
        snapshot = dict(APP["get_current_settings_snapshot"]())
        del snapshot["keyboard_layout"]
        APP["apply_settings_snapshot"](snapshot, include_files=True, rebind_hotkeys=False)
        self.assertEqual(APP["keyboard_layout_var"].get(), QWERTY)

    def test_a_damaged_file_value_loads_as_qwerty(self):
        snapshot = dict(APP["get_current_settings_snapshot"]())
        for junk in ("Maltron", 7, None, ["Dvorak"], {"a": 1}):
            snapshot["keyboard_layout"] = junk
            APP["apply_settings_snapshot"](snapshot, include_files=True, rebind_hotkeys=False)
            self.assertEqual(APP["keyboard_layout_var"].get(), QWERTY, msg=repr(junk))

    def test_loading_a_profile_leaves_the_layout_alone(self):
        APP["keyboard_layout_var"].set(DVORAK)
        snapshot = dict(APP["get_current_settings_snapshot"]())
        snapshot["keyboard_layout"] = AZERTY
        APP["apply_settings_snapshot"](snapshot, include_files=False, rebind_hotkeys=False)
        self.assertEqual(APP["keyboard_layout_var"].get(), DVORAK)

    def test_shared_profiles_never_carry_the_layout(self):
        APP["keyboard_layout_var"].set(DVORAK)
        self.assertNotIn("keyboard_layout", APP["get_shareable_profile_data"]())
        self.assertIn("keyboard_layout", APP["PROFILE_LOCAL_ONLY_KEYS"])

    def test_auto_and_starter_profiles_leave_the_layout_alone(self):
        self.assertIn("keyboard_layout", APP["AUTO_PROFILE_KEEP_KEYS"])
        self.assertIn("keyboard_layout", APP["STARTER_PROFILE_KEEP_KEYS"])
        for name, profile in APP["STARTER_PROFILES"].items():
            self.assertNotIn("keyboard_layout", profile["settings"], msg=name)

    def test_reset_all_settings_leaves_the_layout_alone(self):
        for variables in APP["SECTION_RESET_VARS"].values():
            self.assertNotIn(APP["keyboard_layout_var"], variables)


if __name__ == "__main__":
    unittest.main()
