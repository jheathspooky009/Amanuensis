"""Amanuensis release notes builder.

The Release workflow runs this to write the description that appears on the
GitHub release page. It reads the VERSION_HISTORY list in Amanuensis.py, which
is the same list the app's History tab shows, so the code, the app, and the
release page always say the same thing.

    uv run python scripts/release_notes.py --tag v5.0.2 --output release_notes.md
    uv run python scripts/release_notes.py --tag v5.40.0 --since v5.37.0 --output release_notes.md

Without --since, the notes cover only the newest entry. With --since (the tag of
the previous release), the notes cover every entry newer than that version, up
to and including the newest one, so a release that holds several updates lists
all of them. The Release workflow finds the previous release tag by itself.

It uses only the Python standard library, never runs Amanuensis.py (it only
reads the list as plain data), reads files but changes only the output file,
and sends nothing over the network.

Exit code 0 means the notes were written. Exit code 1 means something needs
fixing, and no notes file is written.
"""

import argparse
import ast
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_FILE = "Amanuensis.py"
HISTORY_NAME = "VERSION_HISTORY"
DEFAULT_OUTPUT = "release_notes.md"

# GitHub stops accepting a release description at 125,000 characters. When
# several updates are combined, older ones are left out once this many
# characters are used, and a line says so. The newest entry is always kept.
MAX_NOTES_CHARS = 100000

# The label at the start of each change line, and the heading it gets on the
# release page. The order here is the order on the page. The "Next Steps"
# idea from the code comment block is private, so it has no place here.
GROUP_HEADINGS = [
    ("added", "Added"),
    ("modified", "Changed"),
    ("fixed", "Fixed"),
    ("removed", "Removed"),
    ("notes", "Notes"),
]
OTHER_HEADING = "Other"

# Release notes are public, so they never say what has not been tried yet and
# never ask the maintainer to send anything back. A change line with this
# wording stops the notes from being built until it is rewritten.
FORBIDDEN_WORDING = re.compile(
    r"not\s+yet\s+(tried|tested|seen|checked)|untested|send\s+back", re.IGNORECASE
)

VERSION_PATTERN = re.compile(r"\d+(\.\d+)*", re.ASCII)

VERIFY_FOOTER = (
    "To check your download, compare its SHA-256 checksum with the one in "
    "`SHA256SUMS.txt`. The steps are in `SECURITY.md` in the repository."
)


class ReleaseNotesError(Exception):
    """A problem the person running the script can fix."""


def read_app_text(root):
    app_path = os.path.join(root, APP_FILE)
    try:
        with open(app_path, "r", encoding="utf-8") as app_file:
            return app_file.read()
    except OSError as error:
        raise ReleaseNotesError(f"{APP_FILE} could not be read ({error.__class__.__name__}).")


def find_version_history(app_text):
    """Finds the VERSION_HISTORY list and returns it as plain Python data.

    The file is only parsed, never run, and only plain text, numbers, lists,
    and dictionaries are accepted inside the list."""
    try:
        tree = ast.parse(app_text)
    except SyntaxError:
        raise ReleaseNotesError(f"{APP_FILE} could not be parsed as Python.")
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == HISTORY_NAME:
                try:
                    return ast.literal_eval(node.value)
                except ValueError:
                    raise ReleaseNotesError(f"{HISTORY_NAME} holds something other than plain data.")
    raise ReleaseNotesError(f"{HISTORY_NAME} was not found in {APP_FILE}.")


def parse_version(text):
    """Turns '5.37.0' or 'v5.37.0' into (5, 37, 0) so versions can be compared."""
    cleaned = text.strip().lstrip("vV")
    if not VERSION_PATTERN.fullmatch(cleaned):
        raise ReleaseNotesError(f"'{text}' is not a version number like 5.37.0.")
    return tuple(int(part) for part in cleaned.split("."))


def check_entry(entry, label):
    """Makes sure one history entry can be turned into release notes."""
    if not isinstance(entry, dict):
        raise ReleaseNotesError(f"{label} is not a dictionary.")
    version = entry.get("version")
    title = entry.get("title")
    changes = entry.get("changes")
    if not isinstance(version, str) or not version:
        raise ReleaseNotesError(f"{label} has no version text.")
    parse_version(version)
    if not isinstance(title, str) or not title:
        raise ReleaseNotesError(f"{label} has no title.")
    if not isinstance(changes, list) or not changes:
        raise ReleaseNotesError(f"{label} has no change lines.")
    for line in changes:
        if not isinstance(line, str) or not line.strip():
            raise ReleaseNotesError(f"{label} has a change line that is not text.")
        if FORBIDDEN_WORDING.search(line):
            raise ReleaseNotesError(
                f"{label} (version {version}) says something has not been tried yet, "
                "or asks for something to be sent back. Release notes are public, "
                "so rewrite that line."
            )


def check_history(history):
    """Checks every entry, and that the list is newest first. Returns the list."""
    if not isinstance(history, list) or not history:
        raise ReleaseNotesError(f"{HISTORY_NAME} is empty.")
    for position, entry in enumerate(history):
        label = "The newest entry" if position == 0 else f"Entry number {position + 1}"
        check_entry(entry, label)
    versions = [parse_version(entry["version"]) for entry in history]
    if versions[0] != max(versions):
        raise ReleaseNotesError(f"{HISTORY_NAME} must list the newest version first.")
    return history


def get_newest_entry(history):
    return check_history(history)[0]


def get_entries_to_release(history, since=None):
    """Returns the entries for the release, newest first.

    With no 'since', only the newest entry. With 'since' (the previous release's
    tag or version), every entry newer than that version, up to the newest."""
    checked = check_history(history)
    newest = checked[0]
    if since is None:
        return [newest]
    since_version = parse_version(since)
    newest_version = parse_version(newest["version"])
    if since_version >= newest_version:
        raise ReleaseNotesError("The previous release is not older than the newest version in the app.")
    chosen = [
        entry for entry in checked
        if since_version < parse_version(entry["version"]) <= newest_version
    ]
    chosen.sort(key=lambda entry: parse_version(entry["version"]), reverse=True)
    return chosen


def check_tag_matches(entry, tag):
    if tag is None:
        return
    if tag.lstrip("vV") != entry["version"]:
        raise ReleaseNotesError("The release tag does not match the newest version in the app.")


def split_change_line(line):
    """Splits 'Added: something' into ('added', 'Something').
    A line with no known label goes under 'Other'."""
    text = line.strip()
    if ":" in text:
        label_text, rest = text.split(":", 1)
        label = label_text.strip().lower()
        for known_label, _heading in GROUP_HEADINGS:
            if label == known_label and rest.strip():
                return known_label, capitalize_first(rest.strip())
    return None, capitalize_first(text)


def capitalize_first(text):
    return text[:1].upper() + text[1:]


def group_changes(changes):
    """Returns a dictionary of heading -> list of change texts."""
    heading_for_label = dict(GROUP_HEADINGS)
    groups = {}
    for line in changes:
        label, text = split_change_line(line)
        heading = heading_for_label.get(label, OTHER_HEADING)
        groups.setdefault(heading, []).append(text)
    return groups


def render_entry(entry, heading_line):
    """Returns the lines for one entry: its heading, date, and change groups."""
    lines = [heading_line, ""]
    date_text = entry.get("date")
    if isinstance(date_text, str) and date_text:
        lines.extend([f"Released on {date_text}.", ""])

    groups = group_changes(entry["changes"])
    heading_order = [heading for _label, heading in GROUP_HEADINGS] + [OTHER_HEADING]
    for heading in heading_order:
        items = groups.get(heading)
        if not items:
            continue
        lines.append(f"### {heading}")
        for item in items:
            lines.append(f"- {item}")
        lines.append("")
    return lines


def build_markdown(entries):
    """Builds the release description from one entry, or from several (newest first)."""
    if isinstance(entries, dict):
        entries = [entries]
    footer = ["---", "", VERIFY_FOOTER, ""]

    if len(entries) == 1:
        entry = entries[0]
        return "\n".join(render_entry(entry, f"## {entry['title']}") + footer)

    blocks = []
    used = 0
    for entry in entries:
        version_text = "v" + entry["version"].lstrip("vV")
        block = render_entry(entry, f"## {version_text}: {entry['title']}")
        size = len("\n".join(block))
        if blocks and used + size > MAX_NOTES_CHARS:
            break
        blocks.append(block)
        used += size

    lines = [f"This release includes {len(entries)} updates since the last release, newest first.", ""]
    omitted = len(entries) - len(blocks)
    if omitted:
        lines.extend([
            f"The {omitted} oldest of them are not listed here, because GitHub limits how long "
            "release notes can be. Every update is listed in the History tab of the app.",
            "",
        ])
    for block in blocks:
        lines.extend(block)
    lines.extend(footer)
    return "\n".join(lines)


def write_notes(output_path, markdown_text):
    try:
        with open(output_path, "w", encoding="utf-8", newline="\n") as notes_file:
            notes_file.write(markdown_text)
    except OSError as error:
        raise ReleaseNotesError(f"The notes file could not be written ({error.__class__.__name__}).")


def main():
    parser = argparse.ArgumentParser(description="Amanuensis release notes builder")
    parser.add_argument("--tag", help="a release tag such as v5.0.2, checked against the newest app version")
    parser.add_argument(
        "--since",
        help="the previous release's tag, such as v5.37.0: lists every update newer than it "
             "(default: only the newest update)",
    )
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="where to write the notes (default: release_notes.md)")
    args = parser.parse_args()

    try:
        history = find_version_history(read_app_text(REPO_ROOT))
        entries = get_entries_to_release(history, args.since)
        check_tag_matches(entries[0], args.tag)
        markdown_text = build_markdown(entries)
        write_notes(args.output, markdown_text)
    except ReleaseNotesError as error:
        print(f"FAIL: {error}")
        return 1

    count_text = "1 update" if len(entries) == 1 else f"{len(entries)} updates"
    print(f"Wrote release notes for version {entries[0]['version']} ({count_text}) to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
