"""Amanuensis repository security check.

Run this before you open a pull request, and before every release:

    uv run python scripts/security_check.py

It uses only the Python standard library, reads files but never changes
them, and sends nothing over the network. It looks for:

  1. Files that must never be committed (your saved settings, resume
     checkpoints, logs, profiles, built .exe files, key files).
  2. Personal or machine-specific details inside files: user folder paths,
     email addresses, IP addresses, and any private words you list in the
     AMANUENSIS_PRIVATE_TERMS environment variable (comma separated, for
     example your Windows user name).
  3. Secrets: private keys and common access-token formats.
  4. Weak spots in GitHub workflow files and in the update checker's list of
     allowed hosts.
  5. A version mismatch between the app and pyproject.toml (and, with --tag,
     the release tag).

The output names the file, the line number, and the kind of problem. It never
prints the value it found, because CI logs on a public repository are public.

Exit code 0 means every check passed. Exit code 1 means something needs fixing.
"""

import argparse
import fnmatch
import os
import re
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_FILE = "Amanuensis.py"
PYPROJECT_FILE = "pyproject.toml"

# The update checker may only talk to these hosts (see UPDATE_ALLOWED_HOSTS in
# Amanuensis.py). Adding a host there means adding it here on purpose.
APPROVED_UPDATE_HOSTS = {
    "api.github.com",
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}

# Files that are created by the app (or by a build) on one person's computer.
FORBIDDEN_FILE_PATTERNS = [
    "last_settings.json",
    "resume_state.json",
    "resume_state.json.tmp",
    "profiles/*",
    "input.txt",
    "_pasted_input.txt",
    "*.csv",
    "*.log",
    "*.exe",
    "*.part",
    "build/*",
    "dist/*",
    ".idea/*",
    ".vscode/*",
    ".venv/*",
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
]

SKIP_CONTENT_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".zip", ".pyc", ".exe", ".dll",
    ".pdf", ".woff", ".woff2", ".ttf",
}
MAX_SCAN_BYTES = 2 * 1024 * 1024

# Words that mean "this is an example, not a real person".
PLACEHOLDER_USERS = {
    "you", "user", "users", "username", "yourname", "your-name", "your_name",
    "name", "me", "runner", "public", "default", "all users", "example",
    "someone", "person",
}
SAFE_EMAIL_DOMAINS = {"example.com", "example.org", "example.net", "github.com"}
SAFE_IP_PREFIXES = ("127.", "0.0.0.0", "255.", "192.0.2.", "198.51.100.", "203.0.113.")  # nosec B104 (a list of ignored text, not a network bind)

WINDOWS_PATH_PATTERN = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+([^\\/\s\"'<>|:*?]+)", re.IGNORECASE)
UNIX_HOME_PATTERN = re.compile(r"(?:^|[\s\"'=(])/(?:home|Users)/([^/\s\"'<>]+)")
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")
IPV4_PATTERN = re.compile(r"(?<![\w.])(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})(?![\w.])")

SECRET_PATTERNS = [
    ("private key block", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY")),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b")),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("API secret key", re.compile(r"\bsk-[A-Za-z0-9_-]{32,}\b")),
]


class Findings:
    """Collects problems so they can be printed together at the end."""

    def __init__(self):
        self.items = []

    def add(self, where, kind):
        self.items.append((where, kind))

    def __len__(self):
        return len(self.items)


# --------------------------------------------------------------------------
# Listing the files that would be published
# --------------------------------------------------------------------------

def read_ignore_rules(root):
    """Reads .gitignore into a simple list of (pattern, is_negated)."""
    rules = []
    ignore_path = os.path.join(root, ".gitignore")
    try:
        with open(ignore_path, "r", encoding="utf-8-sig") as ignore_file:
            lines = ignore_file.read().splitlines()
    except OSError:
        return rules
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        if negated:
            line = line[1:]
        rules.append((line, negated))
    return rules


def matches_ignore_pattern(rel_path, pattern):
    """A small approximation of how .gitignore patterns match a path."""
    parts = rel_path.split("/")
    if pattern.endswith("/"):
        folder_name = pattern.rstrip("/")
        return folder_name in parts[:-1]
    name_pattern = pattern.lstrip("/")
    if "/" in name_pattern:
        return fnmatch.fnmatch(rel_path, name_pattern)
    return any(fnmatch.fnmatch(part, name_pattern) for part in parts)


def is_ignored(rel_path, rules):
    ignored = False
    for pattern, negated in rules:
        if matches_ignore_pattern(rel_path, pattern):
            ignored = not negated
    return ignored


def list_files_by_folder_scan(root):
    """Fallback for when git is not installed: walk the folder and skip
    whatever .gitignore skips."""
    rules = read_ignore_rules(root)
    found = []
    for folder, subfolders, filenames in os.walk(root):
        subfolders[:] = [name for name in subfolders if name != ".git"]
        for filename in filenames:
            full_path = os.path.join(folder, filename)
            rel_path = os.path.relpath(full_path, root).replace(os.sep, "/")
            if not is_ignored(rel_path, rules):
                found.append(rel_path)
    return sorted(found)


def list_repo_files(root):
    """Returns (files, how). With git, this is every tracked file plus every
    new file that is not ignored, which is exactly what a commit could add."""
    command = ["git", "-C", root, "ls-files", "--cached", "--others", "--exclude-standard", "-z"]
    try:
        result = subprocess.run(command, capture_output=True, check=True, timeout=60)  # nosec B603
        names = [name for name in result.stdout.decode("utf-8", "replace").split("\0") if name]
        existing = [name for name in names if os.path.isfile(os.path.join(root, name))]
        return sorted(set(existing)), "git"
    except (OSError, subprocess.SubprocessError):
        return list_files_by_folder_scan(root), "folder scan (git was not found)"


# --------------------------------------------------------------------------
# The checks
# --------------------------------------------------------------------------

def check_forbidden_files(files, findings):
    for rel_path in files:
        base_name = rel_path.rsplit("/", 1)[-1]
        for pattern in FORBIDDEN_FILE_PATTERNS:
            if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(base_name, pattern):
                findings.add(rel_path, "file must not be committed (local data, build output, or a key file)")
                break


def looks_like_placeholder(name):
    cleaned = name.strip().lower()
    if cleaned in PLACEHOLDER_USERS:
        return True
    return cleaned[:1] in ("<", "{", "%", "$", "*", "[") or cleaned.endswith(">")


def read_text_for_scan(root, rel_path):
    """Returns the file's text, or None when it is not a text file we scan."""
    if os.path.splitext(rel_path)[1].lower() in SKIP_CONTENT_EXTENSIONS:
        return None
    full_path = os.path.join(root, rel_path)
    try:
        if os.path.getsize(full_path) > MAX_SCAN_BYTES:
            return None
        with open(full_path, "rb") as scan_file:
            raw = scan_file.read()
    except OSError:
        return None
    if b"\0" in raw[:8000]:
        return None
    return raw.decode("utf-8-sig", "replace")


def load_private_terms():
    raw_value = os.environ.get("AMANUENSIS_PRIVATE_TERMS", "")
    return [term.strip().lower() for term in raw_value.split(",") if term.strip()]


def scan_line_for_problems(line, rel_path, private_terms):
    """Returns a list of problem kinds found on one line."""
    problems = []
    for match in WINDOWS_PATH_PATTERN.finditer(line):
        if not looks_like_placeholder(match.group(1)):
            problems.append("a Windows user folder path")
    for match in UNIX_HOME_PATTERN.finditer(line):
        if not looks_like_placeholder(match.group(1)):
            problems.append("a user home folder path")
    for match in EMAIL_PATTERN.finditer(line):
        domain = match.group(1).lower()
        if domain in SAFE_EMAIL_DOMAINS or domain == "noreply.github.com" or domain.endswith(".noreply.github.com"):
            continue
        problems.append("an email address")
    if rel_path != "uv.lock":
        for match in IPV4_PATTERN.finditer(line):
            address = match.group(1)
            octets = address.split(".")
            if all(octet.isdigit() and int(octet) <= 255 for octet in octets):
                if not address.startswith(SAFE_IP_PREFIXES):
                    problems.append("an IP address")
    for label, pattern in SECRET_PATTERNS:
        if pattern.search(line):
            problems.append(f"a secret ({label})")
    lowered_line = line.lower()
    for term in private_terms:
        if term in lowered_line:
            problems.append("one of your private terms (AMANUENSIS_PRIVATE_TERMS)")
    return problems


def check_file_contents(root, files, findings):
    private_terms = load_private_terms()
    scanned = 0
    for rel_path in files:
        text = read_text_for_scan(root, rel_path)
        if text is None:
            continue
        scanned += 1
        for line_number, line in enumerate(text.splitlines(), start=1):
            for kind in sorted(set(scan_line_for_problems(line, rel_path, private_terms))):
                findings.add(f"{rel_path}:{line_number}", f"contains {kind}")
    return scanned


def check_workflows(root, files, findings):
    for rel_path in files:
        if not (rel_path.startswith(".github/workflows/") and rel_path.endswith((".yml", ".yaml"))):
            continue
        text = read_text_for_scan(root, rel_path) or ""
        if not re.search(r"^permissions:", text, re.MULTILINE):
            findings.add(rel_path, "workflow has no top-level 'permissions:' block (it would get broad default rights)")
        if "pull_request_target" in text:
            findings.add(rel_path, "workflow uses pull_request_target (it can run untrusted code with secrets)")


def check_update_hosts(root, findings):
    app_path = os.path.join(root, APP_FILE)
    try:
        with open(app_path, "r", encoding="utf-8") as app_file:
            app_text = app_file.read()
    except OSError:
        findings.add(APP_FILE, "could not be read")
        return
    block = re.search(r"UPDATE_ALLOWED_HOSTS\s*=\s*\((.*?)\)", app_text, re.DOTALL)
    if block is None:
        findings.add(APP_FILE, "UPDATE_ALLOWED_HOSTS list was not found")
        return
    hosts = set(re.findall(r"\"([^\"]+)\"", block.group(1)))
    unexpected = hosts - APPROVED_UPDATE_HOSTS
    if unexpected:
        findings.add(APP_FILE, "UPDATE_ALLOWED_HOSTS has a host that is not on the approved list in this script")
    for line_number, line in enumerate(app_text.splitlines(), start=1):
        if re.search(r"http://(?!localhost|127\.0\.0\.1)", line):
            findings.add(f"{APP_FILE}:{line_number}", "uses a plain http:// address (use https://)")


def read_app_version(root):
    try:
        with open(os.path.join(root, APP_FILE), "r", encoding="utf-8") as app_file:
            app_text = app_file.read()
    except OSError:
        return None
    match = re.search(r"VERSION_HISTORY\s*=\s*\[\s*\{\s*\"version\":\s*\"([^\"]+)\"", app_text)
    return match.group(1) if match else None


def read_pyproject_version(root):
    try:
        with open(os.path.join(root, PYPROJECT_FILE), "r", encoding="utf-8") as project_file:
            project_text = project_file.read()
    except OSError:
        return None
    match = re.search(r"^version\s*=\s*\"([^\"]+)\"", project_text, re.MULTILINE)
    return match.group(1) if match else None


def check_versions(root, tag, findings):
    app_version = read_app_version(root)
    project_version = read_pyproject_version(root)
    if app_version is None:
        findings.add(APP_FILE, "newest VERSION_HISTORY entry was not found")
        return
    if project_version != app_version:
        findings.add(PYPROJECT_FILE, "version does not match the newest version in VERSION_HISTORY")
    if tag is not None and tag.lstrip("vV") != app_version:
        findings.add("release tag", "the tag does not match the version inside the app")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def safe_kind_for_output(kind):
    """Redact sensitive finding details before printing."""
    if "secret" in kind.lower():
        return "contains sensitive data"
    return kind


def main():
    parser = argparse.ArgumentParser(description="Amanuensis repository security check")
    parser.add_argument("--tag", help="a release tag such as v5.0.0, checked against the app version")
    args = parser.parse_args()

    files, how = list_repo_files(REPO_ROOT)
    print(f"Amanuensis security check ({len(files)} files listed by {how})")

    findings = Findings()
    check_forbidden_files(files, findings)
    scanned = check_file_contents(REPO_ROOT, files, findings)
    check_workflows(REPO_ROOT, files, findings)
    check_update_hosts(REPO_ROOT, findings)
    check_versions(REPO_ROOT, args.tag, findings)
    print(f"Scanned the text of {scanned} files.")

    if not os.environ.get("AMANUENSIS_PRIVATE_TERMS"):
        print("Tip: set AMANUENSIS_PRIVATE_TERMS to your user name and real name (comma separated) "
              "to also catch them. Never write those words into a file in this repository.")

    if len(findings) == 0:
        print("PASS: no problems found.")
        return 0

    print(f"FAIL: {len(findings)} problem(s) found. Values are hidden on purpose.")
    for where, kind in findings.items:
        print(f"  - {where}: {safe_kind_for_output(kind)}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
