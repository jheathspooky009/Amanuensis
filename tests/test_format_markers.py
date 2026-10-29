"""[[b]] and [[i]] formatting markers, pause markers, and Markdown input."""

import unittest

from app_loader import get_app

APP = get_app()


def settings(**changes):
    base = {"enable_pause_markers": True, "enable_format_markers": True}
    base.update(changes)
    return base


class StripFormatMarkerTests(unittest.TestCase):
    def test_markers_removed_and_positions_recorded(self):
        text, positions, spans, problems = APP["strip_format_markers"]("a [[b]]bold[[/b]] z")
        self.assertEqual(text, "a bold z")
        self.assertEqual(positions, {2: ["bold"], 6: ["bold"]})
        self.assertEqual(problems, [])
        self.assertEqual(len(spans), 2)

    def test_capital_letters_in_markers_work(self):
        text, positions, _spans, _problems = APP["strip_format_markers"]("[[I]]x[[/I]]")
        self.assertEqual(text, "x")
        self.assertEqual(positions, {0: ["italic"], 1: ["italic"]})

    def test_closing_without_opening_is_ignored_and_reported(self):
        text, positions, _spans, problems = APP["strip_format_markers"]("a[[/b]]b")
        self.assertEqual(text, "ab")
        self.assertEqual(positions, {})
        self.assertEqual(len(problems), 1)

    def test_unclosed_style_is_closed_after_the_last_character(self):
        text, positions, _spans, problems = APP["strip_format_markers"]("[[b]]open")
        self.assertEqual(text, "open")
        self.assertEqual(positions, {0: ["bold"], 4: ["bold"]})
        self.assertEqual(len(problems), 1)

    def test_every_style_that_is_turned_on_is_turned_off(self):
        raw = "[[b]]one [[i]]two[[/b]] three [[b]]four"
        _text, positions, _spans, _problems = APP["strip_format_markers"](raw)
        counts = {"bold": 0, "italic": 0}
        for kinds in positions.values():
            for kind in kinds:
                counts[kind] += 1
        self.assertEqual(counts["bold"] % 2, 0)
        self.assertEqual(counts["italic"] % 2, 0)

    def test_same_style_toggled_twice_at_one_spot_cancels(self):
        self.assertEqual(APP["normalize_format_map"]({3: ["bold", "bold"], 5: ["bold"]}), {5: ["bold"]})


class PrepareWithMarkersTests(unittest.TestCase):
    def test_format_map_lines_up_with_clean_text(self):
        prepared = APP["prepare_input_text"]("Hi [[b]]there[[/b]] you", settings())
        self.assertEqual(prepared.clean_text, "Hi there you")
        self.assertEqual(prepared.format_map, {3: ["bold"], 8: ["bold"]})

    def test_markers_still_removed_when_pressing_shortcuts_is_off(self):
        prepared = APP["prepare_input_text"]("[[b]]Hi[[/b]] there", settings(enable_format_markers=False))
        self.assertEqual(prepared.clean_text, "Hi there")
        self.assertEqual(prepared.format_map, {})

    def test_protected_text_inside_bold_keeps_correct_range(self):
        prepared = APP["prepare_input_text"]("[[b]]Hi [[AB]] x[[/b]]", settings())
        self.assertEqual(prepared.clean_text, "Hi AB x")
        start, end = prepared.protected_ranges[0]
        self.assertEqual(prepared.clean_text[start:end], "AB")

    def test_pause_marker_position_and_length(self):
        prepared = APP["prepare_input_text"]("ab{{pause:2}}cd", settings())
        self.assertEqual(prepared.clean_text, "abcd")
        self.assertEqual(prepared.pause_map, {2: 2.0})

    def test_pause_markers_stay_as_text_when_turned_off(self):
        prepared = APP["prepare_input_text"]("ab{{pause:2}}cd", settings(enable_pause_markers=False))
        self.assertEqual(prepared.clean_text, "ab{{pause:2}}cd")
        self.assertEqual(prepared.pause_map, {})

    def test_pause_is_capped(self):
        _text, pauses, _invalid, clamped = APP["strip_pause_markers"]("a{{pause:99999}}b")
        self.assertLessEqual(max(pauses.values()), 600.0)
        self.assertEqual(len(clamped), 1)


class MarkdownTests(unittest.TestCase):
    convert = staticmethod(lambda text: APP["convert_markdown_emphasis"](text))

    def test_bold_italic_and_both(self):
        self.assertEqual(self.convert("**a** *b* ***c***"), "[[b]]a[[/b]] [[i]]b[[/i]] [[b]][[i]]c[[/i]][[/b]]")

    def test_underscore_forms(self):
        self.assertEqual(self.convert("__a__ _b_"), "[[b]]a[[/b]] [[i]]b[[/i]]")

    def test_math_and_snake_case_are_left_alone(self):
        text = "2 * 3 * 4 and snake_case_word"
        self.assertEqual(self.convert(text), text)

    def test_protected_text_is_left_alone(self):
        text = "[[**keep**]]"
        self.assertEqual(self.convert(text), text)


if __name__ == "__main__":
    unittest.main()
