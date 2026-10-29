"""Learn My Speed measuring, and that the test loader found the app's code."""

import unittest

from app_loader import get_app

APP = get_app()


class MeasureSpeedTests(unittest.TestCase):
    measure = staticmethod(lambda gaps: APP["measure_typing_speed"](gaps))

    def test_not_enough_key_presses_gives_no_answer(self):
        self.assertIsNone(self.measure([0.2] * (APP["LEARN_SPEED_MIN_KEYS"] - 1)))
        self.assertIsNone(self.measure([]))

    def test_steady_typing_converts_with_five_characters_per_word(self):
        self.assertEqual(self.measure([0.2] * 40), 60)
        self.assertEqual(self.measure([0.1] * 40), 120)

    def test_long_thinking_gaps_are_left_out(self):
        self.assertEqual(self.measure([0.2] * 40 + [5.0, 30.0]), 60)

    def test_answer_stays_between_one_and_four_hundred(self):
        self.assertEqual(self.measure([0.02] * 40), 400)
        self.assertGreaterEqual(self.measure([1.9] * 40), 1)


class LoaderTests(unittest.TestCase):
    def test_main_pieces_were_found(self):
        for name in ("prepare_input_text", "validate_settings", "find_smart_fixes",
                     "compare_typed_text", "strip_format_markers", "min_delay_var"):
            self.assertIn(name, APP)

    def test_only_expected_pieces_were_skipped(self):
        # Pieces that need a real window or a GUI package are skipped on purpose.
        # A skipped definition of any other kind means the loader needs attention.
        for name, _problem in APP["__skipped__"]:
            self.assertTrue(
                name.startswith(("Assign(", "Modern", "_Safe")), msg=f"unexpected skip: {name}"
            )


if __name__ == "__main__":
    unittest.main()
