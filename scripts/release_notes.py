"""Amanuensis release notes builder.

The Release workflow runs this to write the description that appears on the
GitHub release page. It reads the NEWEST entry of the VERSION_HISTORY list in
Amanuensis.py, which is the same list the app's History tab shows, so the code,
the app, and the release page always say the same thing.

    uv run python scripts/release_notes.py --tag v5.0.2 --output release_notes.md

It uses only the Python standard library, never runs Amanuensis.py (it only
reads the list as plain data), reads files but changes only the output file,
and sends nothing over the network.

Exit code 0 means the notes were written. Exit code 1 means something needs
fixing, and no notes file is written.
"""

import argparse
import ast
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_FILE = "Amanuensis.py"
HISTORY_NAME = "VERSION_HISTORY"
DEFAULT_OUTPUT = "release_notes.md"

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


def get_newest_entry(history):
    if not isinstance(history, list) or not history:
        raise ReleaseNotesError(f"{HISTORY_NAME} is empty.")
    entry = history[0]
    if not isinstance(entry, dict):
        raise ReleaseNotesError(f"The newest {HISTORY_NAME} entry is not a dictionary.")
    version = entry.get("version")
    title = entry.get("title")
    changes = entry.get("changes")
    if not isinstance(version, str) or not version:
        raise ReleaseNotesError("The newest entry has no version text.")
    if not isinstance(title, str) or not title:
        raise ReleaseNotesError("The newest entry has no title.")
    if not isinstance(changes, list) or not changes:
        raise ReleaseNotesError("The newest entry has no change lines.")
    for line in changes:
        if not isinstance(line, str) or not line.strip():
            raise ReleaseNotesError("The newest entry has a change line that is not text.")
    return entry


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


def build_markdown(entry):
    lines = [f"## {entry['title']}", ""]
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

    lines.extend(["---", "", VERIFY_FOOTER, ""])
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
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="where to write the notes (default: release_notes.md)")
    args = parser.parse_args()

    try:
        history = find_version_history(read_app_text(REPO_ROOT))
        entry = get_newest_entry(history)
        check_tag_matches(entry, args.tag)
        markdown_text = build_markdown(entry)
        write_notes(args.output, markdown_text)
    except ReleaseNotesError as error:
        print(f"FAIL: {error}")
        return 1

    print(f"Wrote release notes for version {entry['version']} to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
