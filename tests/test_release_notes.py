"""The release notes builder: one update or several, and the wording checks."""

import importlib.util
import os
import unittest

_SCRIPT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "release_notes.py"
)
_spec = importlib.util.spec_from_file_location("release_notes", _SCRIPT_PATH)
release_notes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_notes)

Error = release_notes.ReleaseNotesError


def make_entry(version, title="A title", changes=None, date="2026-01-01"):
    return {
        "version": version,
        "date": date,
        "title": title,
        "changes": changes or [f"Added: something for {version}."],
    }


def make_history():
    return [make_entry("5.3.0"), make_entry("5.2.1"), make_entry("5.2.0"), make_entry("5.1.0")]


class VersionTests(unittest.TestCase):
    def test_versions_compare_as_numbers_not_text(self):
        self.assertLess(release_notes.parse_version("5.9.0"), release_notes.parse_version("5.10.0"))

    def test_leading_v_is_allowed(self):
        self.assertEqual(release_notes.parse_version("v5.37.1"), (5, 37, 1))

    def test_bad_versions_are_refused(self):
        for text in ("", "v", "5.x", "5.37.0-rc1", "five"):
            with self.assertRaises(Error, msg=text):
                release_notes.parse_version(text)


class EntryCheckTests(unittest.TestCase):
    def test_good_history_passes(self):
        self.assertEqual(len(release_notes.check_history(make_history())), 4)

    def test_empty_history_is_refused(self):
        with self.assertRaises(Error):
            release_notes.check_history([])

    def test_history_must_be_newest_first(self):
        with self.assertRaises(Error):
            release_notes.check_history([make_entry("5.1.0"), make_entry("5.2.0")])

    def test_entry_without_changes_is_refused(self):
        entry = make_entry("5.3.0")
        entry["changes"] = []
        with self.assertRaises(Error):
            release_notes.check_history([entry])

    def test_untested_wording_is_refused(self):
        for line in (
            "Notes: Not yet tried on a real screen.",
            "Notes: this is untested.",
            "Notes: open it and send back what happens.",
            "Notes: not yet tested on Linux.",
        ):
            entry = make_entry("5.3.0", changes=[line])
            with self.assertRaises(Error, msg=line):
                release_notes.check_history([entry])


class EntriesToReleaseTests(unittest.TestCase):
    def test_without_since_only_the_newest_entry(self):
        entries = release_notes.get_entries_to_release(make_history())
        self.assertEqual([e["version"] for e in entries], ["5.3.0"])

    def test_since_lists_everything_newer_newest_first(self):
        entries = release_notes.get_entries_to_release(make_history(), "v5.1.0")
        self.assertEqual([e["version"] for e in entries], ["5.3.0", "5.2.1", "5.2.0"])

    def test_since_version_itself_is_left_out(self):
        entries = release_notes.get_entries_to_release(make_history(), "5.2.1")
        self.assertEqual([e["version"] for e in entries], ["5.3.0"])

    def test_since_works_when_the_old_version_is_not_in_the_list(self):
        entries = release_notes.get_entries_to_release(make_history(), "v5.2.5")
        self.assertEqual([e["version"] for e in entries], ["5.3.0"])

    def test_since_must_be_older_than_the_newest(self):
        for since in ("v5.3.0", "v6.0.0"):
            with self.assertRaises(Error, msg=since):
                release_notes.get_entries_to_release(make_history(), since)

    def test_tag_must_match_the_newest_version(self):
        entry = make_history()[0]
        release_notes.check_tag_matches(entry, "v5.3.0")
        release_notes.check_tag_matches(entry, None)
        with self.assertRaises(Error):
            release_notes.check_tag_matches(entry, "v5.2.0")


class MarkdownTests(unittest.TestCase):
    def test_single_entry_keeps_the_original_layout(self):
        entry = make_entry("5.3.0", "Nice update", ["Added: a thing.", "Fixed: a bug.", "Notes: careful."])
        text = release_notes.build_markdown([entry])
        self.assertTrue(text.startswith("## Nice update\n"))
        self.assertIn("Released on 2026-01-01.", text)
        self.assertIn("### Added\n- A thing.", text)
        self.assertIn("### Fixed\n- A bug.", text)
        self.assertIn("### Notes\n- Careful.", text)
        self.assertIn(release_notes.VERIFY_FOOTER, text)

    def test_several_entries_each_get_a_version_heading(self):
        entries = release_notes.get_entries_to_release(make_history(), "v5.1.0")
        text = release_notes.build_markdown(entries)
        self.assertIn("This release includes 3 updates", text)
        self.assertLess(text.index("## v5.3.0: A title"), text.index("## v5.2.1: A title"))
        self.assertLess(text.index("## v5.2.1: A title"), text.index("## v5.2.0: A title"))
        self.assertEqual(text.count(release_notes.VERIFY_FOOTER), 1)

    def test_a_very_long_list_leaves_out_the_oldest_and_says_so(self):
        big_line = "Added: " + ("word " * 2000)
        history = [make_entry(f"5.{n}.0", changes=[big_line]) for n in range(60, 0, -1)]
        entries = release_notes.get_entries_to_release(history, "v5.0.0")
        text = release_notes.build_markdown(entries)
        self.assertLess(len(text), 125000)
        self.assertIn("## v5.60.0:", text)
        self.assertNotIn("## v5.1.0:", text)
        self.assertIn("oldest of them are not listed here", text)

    def test_the_newest_entry_is_kept_even_if_it_is_huge(self):
        huge = make_entry("5.3.0", changes=["Added: " + "x" * (release_notes.MAX_NOTES_CHARS + 10)])
        text = release_notes.build_markdown([huge, make_entry("5.2.0")])
        self.assertIn("## v5.3.0:", text)
        self.assertNotIn("## v5.2.0:", text)


class RealHistoryTests(unittest.TestCase):
    """The real VERSION_HISTORY in Amanuensis.py must always be buildable."""

    def test_every_real_entry_passes_and_all_of_it_can_be_combined(self):
        history = release_notes.find_version_history(release_notes.read_app_text(release_notes.REPO_ROOT))
        entries = release_notes.get_entries_to_release(history, "v0.0.1")
        self.assertEqual(len(entries), len(history))
        text = release_notes.build_markdown(entries)
        self.assertLess(len(text), 125000)


if __name__ == "__main__":
    unittest.main()
