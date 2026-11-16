"""High-contrast theme: the theme choices, the three palettes, the contrast of the colors, and how the setting is saved."""

import unittest

from app_loader import get_app

APP = get_app()
MESSAGES = APP["__messagebox__"]

LIGHT = APP["LIGHT_PALETTE"]
DARK = APP["DARK_PALETTE"]
HIGH = APP["HIGH_CONTRAST_PALETTE"]


def luminance(color):
    """WCAG relative luminance of a color like '#ffe600'."""
    digits = color.lstrip("#")
    channels = [int(digits[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(first, second):
    """WCAG contrast ratio between two colors (1 to 21)."""
    lighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


class NormalizeThemeTests(unittest.TestCase):
    def test_the_three_themes_are_known(self):
        self.assertEqual(APP["THEME_CHOICES"], ("light", "dark", "high_contrast"))
        self.assertEqual(set(APP["THEME_LABELS"]), set(APP["THEME_CHOICES"]))

    def test_any_capitals_and_spacing_are_accepted(self):
        normalize = APP["normalize_theme"]
        self.assertEqual(normalize("DARK"), "dark")
        self.assertEqual(normalize("  Light "), "light")
        self.assertEqual(normalize("High Contrast"), "high_contrast")
        self.assertEqual(normalize("high-contrast"), "high_contrast")
        self.assertEqual(normalize("HIGH_CONTRAST"), "high_contrast")

    def test_bad_values_use_the_fallback(self):
        normalize = APP["normalize_theme"]
        for junk in (None, 5, [], "", "purple", "high contrast!"):
            self.assertEqual(normalize(junk), "light", msg=repr(junk))
            self.assertIsNone(normalize(junk, fallback=None), msg=repr(junk))


class CycleTests(unittest.TestCase):
    def test_the_button_goes_light_dark_high_contrast_light(self):
        next_theme = APP["next_theme"]
        self.assertEqual(next_theme("light"), "dark")
        self.assertEqual(next_theme("dark"), "high_contrast")
        self.assertEqual(next_theme("high_contrast"), "light")

    def test_an_unknown_theme_counts_as_light(self):
        self.assertEqual(APP["next_theme"]("nonsense"), "dark")
        self.assertEqual(APP["next_theme"](None), "dark")

    def test_three_presses_come_back_to_the_start(self):
        theme = "dark"
        for _ in range(3):
            theme = APP["next_theme"](theme)
        self.assertEqual(theme, "dark")

    def test_button_words_and_width(self):
        self.assertEqual(APP["theme_button_text"]("light"), "Theme: Light")
        self.assertEqual(APP["theme_button_text"]("dark"), "Theme: Dark")
        self.assertEqual(APP["theme_button_text"]("high_contrast"), "Theme: High Contrast")
        self.assertEqual(APP["theme_button_text"]("nonsense"), "Theme: Light")
        self.assertEqual(APP["theme_button_width"]("light"), 112)
        self.assertEqual(APP["theme_button_width"]("dark"), 112)
        self.assertGreater(APP["theme_button_width"]("high_contrast"), 112)


class PaletteShapeTests(unittest.TestCase):
    def test_every_palette_has_the_same_keys(self):
        self.assertEqual(set(LIGHT), set(DARK))
        self.assertEqual(set(LIGHT), set(HIGH))

    def test_every_color_is_a_plain_hex_color(self):
        for name, palette in (("light", LIGHT), ("dark", DARK), ("high", HIGH)):
            for key, value in palette.items():
                self.assertRegex(value, r"^#[0-9a-f]{6}$", msg=f"{name} {key}")

    def test_each_theme_has_a_palette(self):
        self.assertIs(APP["THEME_PALETTES"]["light"], LIGHT)
        self.assertIs(APP["THEME_PALETTES"]["dark"], DARK)
        self.assertIs(APP["THEME_PALETTES"]["high_contrast"], HIGH)

    def test_light_and_dark_keep_the_colors_those_parts_always_used(self):
        for palette in (LIGHT, DARK):
            self.assertEqual(palette["button_text"], "#ffffff")
            self.assertEqual(palette["focus"], palette["accent"])
            self.assertEqual(palette["hover_bg"], palette["bg"])
            self.assertEqual(palette["tab_selected"], palette["accent"])
            self.assertEqual(palette["tab_selected_hover"], palette["accent_dark"])
            self.assertEqual(palette["combo_button"], palette["accent"])
            self.assertEqual(palette["combo_button_hover"], palette["accent_dark"])


class HighContrastColorTests(unittest.TestCase):
    def test_it_is_black_and_yellow(self):
        self.assertEqual(HIGH["bg"], "#000000")
        self.assertEqual(HIGH["accent"], "#ffe600")
        self.assertEqual(HIGH["button_text"], "#000000")

    def test_words_on_their_backgrounds_are_at_least_7_to_1(self):
        pairs = [
            ("text", "bg"), ("text", "panel_bg"), ("text", "input_bg"),
            ("muted", "bg"), ("muted", "panel_bg"), ("muted", "input_bg"),
            ("accent_dark", "panel_bg"), ("brick", "panel_bg"),
            ("header_text", "header_bg"), ("header_muted", "header_bg"),
            ("header_text", "header_hover"), ("header_muted", "header_hover"),
            ("text", "hover_bg"), ("muted", "disabled_bg"),
        ]
        for text_key, back_key in pairs:
            ratio = contrast(HIGH[text_key], HIGH[back_key])
            self.assertGreaterEqual(ratio, 7.0, msg=f"{text_key} on {back_key}: {ratio:.1f}")

    def test_words_on_colored_fills_are_at_least_7_to_1(self):
        for fill in ("accent", "accent_dark", "amber", "amber_hover", "brick", "brick_hover"):
            ratio = contrast(HIGH["button_text"], HIGH[fill])
            self.assertGreaterEqual(ratio, 7.0, msg=f"button_text on {fill}: {ratio:.1f}")

    def test_the_open_tab_and_dropdown_button_keep_readable_words(self):
        for fill in ("tab_selected", "combo_button"):
            self.assertGreaterEqual(contrast(HIGH["header_text"], HIGH[fill]), 7.0, msg=fill)
        for fill in ("tab_selected_hover", "combo_button_hover"):
            self.assertGreaterEqual(contrast(HIGH["header_text"], HIGH[fill]), 4.5, msg=fill)
        # A dropdown arrow is drawn in the text color on the button.
        for fill in ("combo_button", "combo_button_hover"):
            self.assertGreaterEqual(contrast(HIGH["text"], HIGH[fill]), 4.5, msg=fill)

    def test_outlines_and_the_focus_outline_stand_out(self):
        for key in ("border", "border_strong", "focus", "header_border"):
            ratio = contrast(HIGH[key], HIGH["panel_bg"])
            self.assertGreaterEqual(ratio, 3.0, msg=f"{key}: {ratio:.1f}")
        # The focus outline must not look like the normal outline.
        self.assertNotEqual(HIGH["focus"], HIGH["border_strong"])

    def test_the_progress_bar_trough_is_not_the_same_as_its_fill(self):
        self.assertNotEqual(HIGH["border"], HIGH["accent"])
        self.assertGreaterEqual(contrast(HIGH["border"], HIGH["accent"]), 1.5)

    def test_a_hover_color_shows_against_the_resting_color(self):
        self.assertNotEqual(HIGH["hover_bg"], HIGH["panel_bg"])
        self.assertNotEqual(HIGH["header_hover"], HIGH["header_bg"])


class ThemeSettingTests(unittest.TestCase):
    def setUp(self):
        self._saved = APP["theme_var"].get()
        MESSAGES.calls.clear()

    def tearDown(self):
        APP["theme_var"].set(self._saved)

    def test_high_contrast_passes_validation(self):
        APP["theme_var"].set("high_contrast")
        self.assertEqual(APP["validate_settings"]()["theme"], "high_contrast")
        self.assertEqual(MESSAGES.calls, [])

    def test_a_messy_value_is_cleaned_and_a_bad_one_goes_to_light(self):
        APP["theme_var"].set(" High Contrast ")
        self.assertEqual(APP["validate_settings"]()["theme"], "high_contrast")
        APP["theme_var"].set("neon")
        self.assertEqual(APP["validate_settings"]()["theme"], "light")

    def test_it_survives_saving_and_loading(self):
        APP["theme_var"].set("high_contrast")
        snapshot = APP["get_current_settings_snapshot"]()
        self.assertEqual(snapshot["theme"], "high_contrast")
        APP["theme_var"].set("dark")
        APP["apply_settings_snapshot"](snapshot, include_files=True, rebind_hotkeys=False)
        self.assertEqual(APP["theme_var"].get(), "high_contrast")

    def test_a_settings_file_with_a_bad_or_missing_theme_keeps_the_current_one(self):
        APP["theme_var"].set("dark")
        for bad in ("neon", 7, None):
            snapshot = dict(APP["get_current_settings_snapshot"]())
            snapshot["theme"] = bad
            APP["apply_settings_snapshot"](snapshot, include_files=True, rebind_hotkeys=False)
            self.assertEqual(APP["theme_var"].get(), "dark", msg=repr(bad))
        snapshot = dict(APP["get_current_settings_snapshot"]())
        del snapshot["theme"]
        APP["apply_settings_snapshot"](snapshot, include_files=True, rebind_hotkeys=False)
        self.assertEqual(APP["theme_var"].get(), "dark")


class ProfilesLeaveTheThemeAloneTests(unittest.TestCase):
    def setUp(self):
        self._saved = APP["theme_var"].get()

    def tearDown(self):
        APP["theme_var"].set(self._saved)

    def test_shared_profiles_do_not_carry_the_theme(self):
        self.assertIn("theme", APP["PROFILE_LOCAL_ONLY_KEYS"])
        APP["theme_var"].set("high_contrast")
        self.assertNotIn("theme", APP["get_shareable_profile_data"]())

    def test_loading_a_profile_never_changes_the_theme(self):
        APP["theme_var"].set("high_contrast")
        snapshot = dict(APP["get_current_settings_snapshot"]())
        for other in ("light", "dark"):
            snapshot["theme"] = other
            APP["apply_settings_snapshot"](snapshot, include_files=False, rebind_hotkeys=False)
            self.assertEqual(APP["theme_var"].get(), "high_contrast", msg=other)

    def test_a_profile_with_no_theme_leaves_it_alone(self):
        APP["theme_var"].set("high_contrast")
        profile = APP["get_shareable_profile_data"]()
        APP["apply_settings_snapshot"](profile, include_files=False, rebind_hotkeys=False)
        self.assertEqual(APP["theme_var"].get(), "high_contrast")

    def test_auto_and_starter_profiles_keep_the_theme(self):
        self.assertIn("theme", APP["AUTO_PROFILE_KEEP_KEYS"])
        self.assertIn("theme", APP["STARTER_PROFILE_KEEP_KEYS"])
        for name, profile in APP["STARTER_PROFILES"].items():
            self.assertNotIn("theme", profile["settings"], msg=name)


if __name__ == "__main__":
    unittest.main()
