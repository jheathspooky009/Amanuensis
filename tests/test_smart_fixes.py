"""Smart Text Fixes: each rule, the cases it must leave alone, and the safety rules."""

import random
import unittest

from app_loader import get_app

APP = get_app()

ALL_ON = {"spacing": True, "punctuation": True, "capitalization": True, "repeated_words": True}


def fixed(text, options=None, protected=()):
    mask = APP["build_protection_mask"](len(text), list(protected))
    fixes = APP["find_smart_fixes"](text, mask, options or ALL_ON)
    return APP["apply_smart_fixes"](text, fixes)[0]


class OptionTests(unittest.TestCase):
    def test_off_by_default(self):
        self.assertIsNone(APP["get_smart_fix_options"]({}))

    def test_master_switch_needed_unless_ignored(self):
        settings = {"enable_smart_fixes": False}
        self.assertIsNone(APP["get_smart_fix_options"](settings))
        self.assertIsNotNone(APP["get_smart_fix_options"](settings, ignore_master=True))

    def test_all_groups_off_means_none(self):
        settings = {
            "enable_smart_fixes": True, "smart_fix_spacing": False, "smart_fix_punctuation": False,
            "smart_fix_capitalization": False, "smart_fix_repeated_words": False,
        }
        self.assertIsNone(APP["get_smart_fix_options"](settings))

    def test_nothing_changes_when_smart_fixes_are_off(self):
        prepared = APP["prepare_input_text"]("hello  world", {"enable_smart_fixes": False})
        self.assertEqual(prepared.clean_text, "hello  world")
        self.assertEqual(prepared.fixes, [])


class RuleTests(unittest.TestCase):
    def check(self, before, after):
        self.assertEqual(fixed(before), after, msg=repr(before))

    def test_extra_spaces(self):
        self.check("Hello  world.", "Hello world.")

    def test_space_before_punctuation(self):
        self.check("Hello world .", "Hello world.")

    def test_missing_space_after_period(self):
        self.check("It is done.Then more.", "It is done. Then more.")

    def test_double_punctuation(self):
        self.check("Wait,, what.", "Wait, what.")

    def test_intro_comma(self):
        self.check("However I tried it today.", "However, I tried it today.")

    def test_intro_word_without_comma_need_is_left_alone(self):
        self.check("However hard I try, it is fine.", "However hard I try, it is fine.")

    def test_lowercase_sentence_start_and_lone_i(self):
        self.check("i think so. then we left.", "I think so. Then we left.")

    def test_repeated_word_removed(self):
        self.check("See the the cat.", "See the cat.")

    def test_allowed_repeats_are_kept(self):
        self.check("She had had a dream.", "She had had a dream.")

    def test_web_addresses_are_left_alone(self):
        self.check("Visit www.site.com now.", "Visit www.site.com now.")

    def test_abbreviations_and_list_markers_are_left_alone(self):
        text = "Dr. smith went home.\nii. second point."
        self.assertEqual(fixed(text, {"capitalization": True}), text)

    def test_clean_text_is_unchanged(self):
        text = "This is a clean sentence. It has nothing to fix."
        self.check(text, text)

    def test_empty_text(self):
        self.check("", "")


class SafetyRuleTests(unittest.TestCase):
    def test_fixes_are_sorted_and_never_overlap(self):
        rng = random.Random(99)
        pieces = ["i ", "the the ", "  ", " ,", "word.Next ", "however I ", "..", "x", "\n", ". "]
        for _ in range(300):
            text = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 12)))
            mask = APP["build_protection_mask"](len(text), [])
            fixes = APP["find_smart_fixes"](text, mask, ALL_ON)
            for earlier, later in zip(fixes, fixes[1:]):
                self.assertLessEqual(earlier.end, later.start, msg=repr(text))

    def test_no_fix_touches_protected_characters(self):
        rng = random.Random(7)
        pieces = ["i ", "the the ", "  ", " ,", "word.Next ", "however I ", "..", "x", ". "]
        for _ in range(300):
            text = "".join(rng.choice(pieces) for _ in range(rng.randint(2, 12)))
            start = rng.randint(0, len(text) - 1)
            end = rng.randint(start + 1, len(text))
            mask = APP["build_protection_mask"](len(text), [(start, end)])
            for fix in APP["find_smart_fixes"](text, mask, ALL_ON):
                if fix.start == fix.end:
                    self.assertFalse(fix.start > 0 and mask[fix.start - 1], msg=repr(text))
                    self.assertFalse(fix.start < len(mask) and mask[fix.start], msg=repr(text))
                else:
                    self.assertFalse(any(mask[fix.start:fix.end]), msg=repr(text))

    def test_fixing_twice_changes_nothing_more(self):
        text = "hello  world .this is the the test.Then i left however I tried."
        once = fixed(text)
        self.assertEqual(fixed(once), once)

    def test_position_mapping_through_fixes(self):
        text = "ab  cd"
        mask = APP["build_protection_mask"](len(text), [])
        fixes = APP["find_smart_fixes"](text, mask, {"spacing": True})
        new_text, _starts = APP["apply_smart_fixes"](text, fixes)
        self.assertEqual(new_text, "ab cd")
        self.assertEqual(APP["map_position_through_fixes"](4, fixes), 3)
        self.assertEqual(new_text[APP["map_position_through_fixes"](4, fixes)], "c")

    def test_summary_has_no_text_from_the_document(self):
        text = "secret  words"
        mask = APP["build_protection_mask"](len(text), [])
        fixes = APP["find_smart_fixes"](text, mask, ALL_ON)
        summary = APP["summarize_smart_fixes"](fixes)
        self.assertNotIn("secret", summary)
        self.assertEqual(APP["summarize_smart_fixes"]([]), "no fixes")


if __name__ == "__main__":
    unittest.main()
