"""Learn my real typing (Update #60): pair speed, sentence pauses, typo fixes."""

import unittest

from app_loader import get_app

APP = get_app()


def type_text(text, gap=0.2, start=0.0, sentence_wait=None):
    """Makes key events for typing text at a steady gap. sentence_wait adds
    extra seconds before the first key after a sentence ending."""
    events = []
    now = start
    previous = ""
    for character in text:
        extra = 0.0
        if sentence_wait and previous == " " and events and events[-2][1] in ".!?":
            extra = sentence_wait
        now += gap + extra
        events.append(("char", character, now))
        previous = character
    return events


def back(events, count=1, gap=0.2):
    now = events[-1][2]
    for _ in range(count):
        now += gap
        events.append(("back", "", now))
    return events


def more(events, text, gap=0.2):
    return events + type_text(text, gap, start=events[-1][2])


class GapTests(unittest.TestCase):
    def test_gaps_skip_backspace_and_breaks(self):
        events = type_text("ab", 0.2)
        events.append(("back", "", 1.0))
        events += [("char", "c", 2.0), ("char", "d", 2.3), ("break", "", 3.0), ("char", "e", 4.0)]
        gaps = APP["gaps_from_events"](events)
        self.assertEqual(len(gaps), 2)
        self.assertAlmostEqual(gaps[1], 0.3)


class PairTests(unittest.TestCase):
    def test_only_common_pairs_are_measured(self):
        events = type_text("the cat", 0.1)
        gaps = APP["find_common_pair_gaps"](events)
        self.assertEqual(len(gaps), 2)  # th and he

    def test_pair_delays_need_enough_gaps(self):
        learned = APP["learned_pair_delays"]
        self.assertIsNone(learned([0.1] * 5))
        low, high = learned([0.1] * 6)
        self.assertLess(low, high)

    def test_pair_delays_stay_in_limits(self):
        low, high = APP["learned_pair_delays"]([0.0] * 6)
        self.assertGreaterEqual(low, 0.01)
        self.assertLessEqual(high, 1.0)


class SentencePauseTests(unittest.TestCase):
    def test_wait_after_sentence_is_found(self):
        events = type_text("Hi there. Next one. Last bit.", 0.2, sentence_wait=1.0)
        pauses = APP["find_sentence_pauses"](events, 0.2)
        self.assertEqual(len(pauses), 2)
        for pause in pauses:
            self.assertAlmostEqual(pause, 1.0, places=1)

    def test_steady_typing_has_no_pause(self):
        events = type_text("Hi there. Next one. Last bit.", 0.2)
        self.assertEqual(APP["find_sentence_pauses"](events, 0.2), [])

    def test_too_few_waits_learn_nothing(self):
        self.assertIsNone(APP["learned_sentence_pauses"]([1.0]))
        low, high = APP["learned_sentence_pauses"]([1.0, 1.0])
        self.assertLess(low, high)


class TypoTests(unittest.TestCase):
    def fix(self, wrong_text, right_text, backspaces):
        events = type_text("so " + wrong_text)
        events = back(events, backspaces)
        return more(events, right_text)

    def test_neighbor_key_typo(self):
        letters, fixes, kinds = APP["find_typo_corrections"](self.fix("wo", "i", 1))
        self.assertEqual(fixes, 1)
        self.assertEqual(kinds["adjacent"], 1)  # o and i are next-door keys

    def test_capital_slip(self):
        _l, fixes, kinds = APP["find_typo_corrections"](self.fix("T", "t", 1))
        self.assertEqual(kinds["case"], 1)

    def test_swapped_letters(self):
        _l, fixes, kinds = APP["find_typo_corrections"](self.fix("ht", "th", 2))
        self.assertEqual(kinds["swap"], 1)

    def test_doubled_letter(self):
        _l, fixes, kinds = APP["find_typo_corrections"](self.fix("allll", "o", 1))
        self.assertEqual(kinds["double"], 1)

    def test_long_deletion_is_a_rewrite_not_a_typo(self):
        events = back(type_text("a long phrase"), 8)
        events = more(events, "different")
        _l, fixes, _k = APP["find_typo_corrections"](events)
        self.assertEqual(fixes, 0)

    def test_backspace_after_a_break_is_ignored(self):
        events = type_text("hello")
        events.append(("break", "", 5.0))
        events.append(("back", "", 5.2))
        events += [("char", "x", 5.4)]
        _l, fixes, _k = APP["find_typo_corrections"](events)
        self.assertEqual(fixes, 0)


class LearnAllTests(unittest.TestCase):
    SAMPLE = APP["LEARN_SPEED_SAMPLE"]

    def learn(self, events):
        return APP["learn_real_typing"](events)

    def test_short_typing_learns_nothing(self):
        learned = self.learn(type_text("the end", 0.2))
        self.assertIsNone(learned["speed"])
        self.assertEqual(learned["settings"], {})
        self.assertEqual(APP["describe_learned_typing"](learned), [])

    def test_clean_sample_learns_speed_pairs_and_pauses_only(self):
        events = type_text(self.SAMPLE, 0.2, sentence_wait=1.0)
        learned = self.learn(events)
        settings = learned["settings"]
        self.assertEqual(learned["speed"], 60)
        self.assertIn("pair_delay_min", settings)
        self.assertIn("sentence_pause_min", settings)
        self.assertNotIn("typo_chance", settings)
        self.assertNotIn("use_extra_typos", settings)

    def test_learned_settings_pass_validation(self):
        events = type_text(self.SAMPLE, 0.2, sentence_wait=1.0)
        learned = self.learn(events)
        boxes = {
            "min_delay_var": "min_delay", "max_delay_var": "max_delay",
            "pair_delay_min_var": "pair_delay_min", "pair_delay_max_var": "pair_delay_max",
            "sentence_pause_min_var": "sentence_pause_min", "sentence_pause_max_var": "sentence_pause_max",
        }
        saved = {name: APP[name].get() for name in boxes}
        try:
            for name, key in boxes.items():
                APP[name].set(learned["settings"][key])
            APP["__messagebox__"].calls.clear()
            result = APP["validate_settings"]()
            self.assertIsInstance(result, dict)
            self.assertLessEqual(result["pair_delay_min"], result["pair_delay_max"])
        finally:
            for name, value in saved.items():
                APP[name].set(value)

    def test_fixed_typos_set_the_typo_chance(self):
        events = type_text(self.SAMPLE, 0.2, sentence_wait=1.0)
        for _ in range(4):  # four capital slips fixed
            events = more(back(events + [("char", "Q", events[-1][2] + 0.2)], 1), "q")
        learned = self.learn(events)
        settings = learned["settings"]
        self.assertTrue(settings["enable_typos"])
        self.assertGreater(float(settings["typo_chance"]), 0)
        self.assertTrue(settings["enable_capital_slips"])
        self.assertFalse(settings["use_extra_typos"])  # 4 fixes, none were extra kinds

    def test_the_typed_letters_are_never_in_the_result(self):
        events = type_text(self.SAMPLE, 0.2, sentence_wait=1.0)
        text = repr(self.learn(events))
        self.assertNotIn("library", text)

    def test_delays_for_wpm_matches_apply_wpm_rule(self):
        self.assertEqual(APP["delays_for_wpm"](60), ("0.100", "0.300"))


class SettingTests(unittest.TestCase):
    def test_defaults_match_the_old_fixed_delay(self):
        self.assertEqual(APP["pair_delay_min_var"].get(), "0.02")
        self.assertEqual(APP["pair_delay_max_var"].get(), "0.05")

    def test_new_settings_are_saved_and_restored(self):
        snapshot = APP["get_current_settings_snapshot"]()
        self.assertIn("pair_delay_min", snapshot)
        self.assertIn("pair_delay_max", snapshot)
        old = dict(snapshot)
        old.pop("pair_delay_min")
        old.pop("pair_delay_max")
        APP["apply_settings_snapshot"](old, include_files=False, rebind_hotkeys=False)
        self.assertEqual(APP["pair_delay_min_var"].get(), "0.02")

    def test_bad_pair_delays_are_refused(self):
        messages = APP["__messagebox__"]
        saved = (APP["pair_delay_min_var"].get(), APP["pair_delay_max_var"].get())
        try:
            for low, high in (("abc", "0.05"), ("-1", "0.05"), ("0.5", "0.1"), ("nan", "0.05")):
                APP["pair_delay_min_var"].set(low)
                APP["pair_delay_max_var"].set(high)
                messages.calls.clear()
                self.assertIsNone(APP["validate_settings"]())
                self.assertTrue(messages.calls)
        finally:
            APP["pair_delay_min_var"].set(saved[0])
            APP["pair_delay_max_var"].set(saved[1])

    def test_shareable_profile_leaves_out_personal_settings(self):
        data = APP["get_shareable_profile_data"]()
        for key in APP["PROFILE_LOCAL_ONLY_KEYS"]:
            self.assertNotIn(key, data)
        self.assertIn("pair_delay_min", data)


if __name__ == "__main__":
    unittest.main()
