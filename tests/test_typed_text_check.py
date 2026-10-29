"""The typed text check: finds real differences, ignores harmless ones."""

import unittest

from app_loader import get_app

APP = get_app()
compare = APP["compare_typed_text"]


class MatchingTextTests(unittest.TestCase):
    def test_identical_text_matches(self):
        result = compare("Hello world\nsecond line", "Hello world\nsecond line")
        self.assertTrue(result["matched"])
        self.assertEqual(result["difference_count"], 0)
        self.assertEqual(result["differences"], [])

    def test_earlier_text_in_the_document_is_ignored(self):
        self.assertTrue(compare("Hello world", "Earlier text. Hello world")["matched"])
        self.assertTrue(compare("one\ntwo", "old line\nsome one\ntwo")["matched"])

    def test_curly_quotes_and_non_breaking_spaces_count_as_same(self):
        self.assertTrue(compare("it\u2019s \u201cok\u201d", "it's \"ok\"")["matched"])
        self.assertTrue(compare("a b", "a\u00a0b")["matched"])

    def test_line_endings_and_end_spaces_do_not_matter(self):
        self.assertTrue(compare("a\nb", "a\r\nb  \n")["matched"])

    def test_empty_expected_text(self):
        self.assertTrue(compare("", "anything")["matched"])


class DifferenceTests(unittest.TestCase):
    def test_wrong_letter_is_found_with_line_number(self):
        result = compare("a\nb\nc", "a\nx\nc")
        self.assertFalse(result["matched"])
        self.assertEqual(result["difference_count"], 1)
        difference = result["differences"][0]
        self.assertEqual(difference["line"], 2)
        self.assertEqual(difference["kind"], "wrong")
        self.assertIn("x", difference["on_screen"])
        self.assertIn("b", difference["should_be"])

    def test_missing_letter(self):
        result = compare("Hello world", "Hello wrld")
        self.assertFalse(result["matched"])
        self.assertEqual(result["differences"][0]["kind"], "missing")

    def test_extra_text(self):
        result = compare("Hello world", "Hello world, extra")
        self.assertFalse(result["matched"])
        self.assertEqual(result["differences"][0]["kind"], "extra")

    def test_missing_last_line(self):
        result = compare("a\nb", "a")
        self.assertFalse(result["matched"])
        self.assertEqual(result["differences"][0]["line"], 2)

    def test_leftover_double_space_is_a_difference(self):
        self.assertFalse(compare("one two", "one  two")["matched"])

    def test_report_is_limited_but_count_is_complete(self):
        expected = "\n".join(f"line {n}" for n in range(60))
        found = "\n".join(f"lin {n}" for n in range(60))
        result = compare(expected, found)
        self.assertEqual(result["difference_count"], 60)
        self.assertLessEqual(len(result["differences"]), APP["CHECK_MAX_DIFFERENCES_SHOWN"])


class ExpectedTextTests(unittest.TestCase):
    build = staticmethod(lambda *args: APP["build_expected_typed_text"](*args))

    def test_whole_text_when_nothing_was_left(self):
        self.assertEqual(self.build("abc def", 0, {}), "abc def")

    def test_starts_at_the_resume_point(self):
        self.assertEqual(self.build("abcdef", 3, {}), "def")

    def test_left_mistake_replaces_the_correct_text(self):
        self.assertEqual(self.build("abcdef gh", 2, {3: {"typed": "XY", "end": 5}}), "cXYf gh")

    def test_left_mistake_before_the_resume_point_is_ignored(self):
        self.assertEqual(self.build("abcdef", 4, {1: {"typed": "ZZ", "end": 3}}), "ef")

    def test_normalizing_is_stable(self):
        once = APP["normalize_for_check"]("a\u00a0b \n")
        self.assertEqual(once, "a b")
        self.assertEqual(APP["normalize_for_check"](once), once)


if __name__ == "__main__":
    unittest.main()
