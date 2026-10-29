# Contributing to Amanuensis

Thanks for helping. Amanuensis is open source (MIT) and anyone can read and audit it. Because many people run it and it sends real keystrokes, every change is checked for security.

## Before you start

- Open an issue to talk about a feature before you build it.
- Report security problems privately. See [SECURITY.md](SECURITY.md). Do not use public issues for them.
- Be kind and keep discussions on topic.

## Set up

```
uv sync
uv run python Amanuensis.py
```

## Required security checks (before every pull request)

Run all of these. A pull request that fails them will not be merged.

```
uv run python -m py_compile Amanuensis.py scripts/security_check.py scripts/release_notes.py tests/app_loader.py tests/test_protected_text.py tests/test_format_markers.py tests/test_smart_fixes.py tests/test_settings.py tests/test_typed_text_check.py tests/test_speed_and_loading.py
uv run pyflakes Amanuensis.py scripts/security_check.py scripts/release_notes.py tests
uvx bandit@1.9.4 -q -ll -r Amanuensis.py scripts
uv run python scripts/security_check.py
uv run python -m unittest discover -s tests -v
uv export --locked --no-emit-project -o requirements-audit.txt
uvx pip-audit@2.10.1 -r requirements-audit.txt --no-deps --disable-pip
```

`scripts/security_check.py` looks for personal details and secrets. To make it also catch your own user name and real name, set this first (the words stay on your computer):

```
$env:AMANUENSIS_PRIVATE_TERMS = "your-windows-username,your full name"
```

Never write those words into any file in the repository. The same checks run automatically in GitHub Actions, and your pull request template has a checklist.

## Rules for every change

1. **No personal or machine data.** No folder paths, user names, emails, IP addresses, saved settings, profiles, logs, resume files, or `.exe` files. Use placeholders like `C:\Users\<you>\` in examples.
2. **No secrets.** The app needs none. Never commit tokens, keys, or passwords.
3. **Keep the update checker strict.** HTTPS only, the approved GitHub hosts only, no sending the person's text or settings, never replace or restart the running app, no dialogs during a run.
4. **New dependencies need a reason.** Prefer the standard library. Explain any new package in the pull request and run `pip-audit` on the new lock file.
5. **Treat files as untrusted.** Settings, profiles, resume files, and input text may come from someone else. Validate them and never crash on bad data.
6. **Workflows stay locked down.** Every workflow needs a top-level `permissions:` block, no `pull_request_target`, and no secrets in logs.
7. **Never publish a locally built `.exe`.** Releases are built only by the Release workflow from a pushed tag.
8. **Follow the existing code patterns** (CustomTkinter `modern_*` helpers, `run_on_main_thread()` for background threads, new settings need a variable, validation, the settings dict, and both snapshot functions).
9. **Update the version history** in `Amanuensis.py` (the comment block and `VERSION_HISTORY`) and keep `pyproject.toml` on the same version.
10. **Add a test when you fix a bug or add logic that does not need a window.** Tests live in `tests/` and use Python's built-in `unittest`. Because `Amanuensis.py` builds its window when it loads, the tests read it through `tests/app_loader.py`, which runs only its functions and plain settings. Use `from app_loader import get_app` and call the function with `APP["function_name"]`.

## For maintainers: making a release

1. Make sure CI is green on `main`.
2. Make sure `pyproject.toml`, `uv.lock`, and the newest `VERSION_HISTORY` entry all show the same version. The newest entry also becomes the release description on GitHub (`scripts/release_notes.py` builds it), so fill in its title and change lines before you tag. Lines that start with `Added:`, `Modified:`, `Fixed:`, `Removed:`, or `Notes:` are grouped under headings. To preview it, run `uv run python scripts/release_notes.py --output release_notes.md`.
3. Create and push a tag such as `v5.0.0`. The Release workflow checks the tag against the app version, scans dependencies, builds the `.exe`, writes `SHA256SUMS.txt`, creates the build attestation, signs the checksums when a GPG key is configured, writes the release description from the newest `VERSION_HISTORY` entry, and publishes the release.
4. Download the release and check it yourself using the steps in [SECURITY.md](SECURITY.md).

## License

By contributing, you agree your work is released under the MIT license in [LICENSE](LICENSE).
