"""Protected text ([[like this]]) must always come out exactly as written."""

import random
import unittest

from app_loader import get_app

APP = get_app()

ALL_FIXES_ON = {
    "enable_smart_fixes": True,
    "smart_fix_spacing": True,
    "smart_fix_punctuation": True,
    "smart_fix_capitalization": True,
    "smart_fix_repeated_words": True,
}


class ProtectedSpanTests(unittest.TestCase):
    def test_brackets_are_removed_and_range_points_at_inner_text(self):
        clean, ranges = APP["parse_protected_spans"]("Account [[AB-123]] ok")
        self.assertEqual(clean, "Account AB-123 ok")
        self.assertEqual(ranges, [(8, 14)])
        self.assertEqual(clean[8:14], "AB-123")

    def test_two_spans_and_no_spans(self):
        clean, ranges = APP["parse_protected_spans"]("[[a]] and [[bb]]")
        self.assertEqual(clean, "a and bb")
        self.assertEqual([clean[s:e] for s, e in ranges], ["a", "bb"])
        self.assertEqual(APP["parse_protected_spans"]("plain"), ("plain", []))

    def test_mask_marks_only_protected_characters(self):
        mask = APP["build_protection_mask"](6, [(2, 4)])
        self.assertEqual(mask, [False, False, True, True, False, False])

    def test_mask_ignores_range_past_the_end(self):
        mask = APP["build_protection_mask"](3, [(2, 10)])
        self.assertEqual(mask, [False, False, True])


class ProtectedTextSurvivesSmartFixesTests(unittest.TestCase):
    def prepare(self, raw):
        return APP["prepare_input_text"](raw, dict(ALL_FIXES_ON))

    def protected_pieces(self, prepared):
        return [prepared.clean_text[s:e] for s, e in prepared.protected_ranges]

    def test_messy_protected_text_is_never_cleaned_up(self):
        raw = "hello  there. [[the  the ,x  i]] and [[Acct 0012.9]] end"
        prepared = self.prepare(raw)
        self.assertEqual(self.protected_pieces(prepared), ["the  the ,x  i", "Acct 0012.9"])

    def test_text_around_protected_text_is_still_fixed(self):
        prepared = self.prepare("hello  there [[KEEP  THIS]] ok")
        self.assertTrue(prepared.clean_text.startswith("Hello there "))
        self.assertEqual(self.protected_pieces(prepared), ["KEEP  THIS"])

    def test_fuzz_protected_text_always_identical(self):
        rng = random.Random(1234)
        pieces = ["i ", "the the ", "  ", " ,", "word.Next ", "however I ", "..", "x"]
        for _ in range(300):
            before = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 5)))
            inner = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 4)))
            after = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 5)))
            prepared = self.prepare(f"{before}[[{inner}]]{after}")
            self.assertEqual(
                self.protected_pieces(prepared), [inner], msg=repr((before, inner, after))
            )


if __name__ == "__main__":
    unittest.main()
