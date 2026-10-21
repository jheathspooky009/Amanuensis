# Amanuensis

A Python desktop tool that simulates human-like typing — built for accessibility users, QA/software testers, and restricted VDI environments where copy-paste isn't an option.

Amanuensis reads text from a file and types it into whatever window currently has focus, with randomized speed, natural pauses, occasional realistic mistakes, and configurable "research breaks" — instead of an instant, obviously-automated paste.

**Open source and publicly auditable.** Amanuensis is released under the MIT license. Every line of code and every build script is in this repository for anyone to read, check, and improve. It runs entirely on your computer, with no account and no telemetry. See [SECURITY.md](SECURITY.md) for how it is built to be safe and how to report a problem.

> **Always check a new release before you install it.** Download only from this repository's [Releases](../../releases) page, then verify the checksum, build attestation, or GPG signature using the steps in [Verifying a release](#verifying-a-release). If a check fails, do not run the file.

## Who this is for

- **Accessibility & motor-impairment support** — people with RSI, carpal tunnel, arthritis, or similar conditions can prepare text with speech-to-text tools and have it typed in hands-free, with the countdown and controls giving time to position the cursor without physical strain.
- **QA & software testing** — testers can see how text editors, form fields, and collaborative tools handle realistic, non-instant keystroke input instead of an instantaneous string injection.
- **Restricted / VDI environments** — for enterprise remote desktops and web portals that disable copy-paste, Amanuensis simulates raw keyboard input instead, with an instant ESC/Stop kill-switch for safety.

## Key features

- **Human-like speed and rhythm** — randomized delay per character, plus optional touches like finger timing, mood swings between fast and hesitant, typing in bursts, and a pace that changes from the opening to the closing of an essay
- **Natural pauses and breaks** — thinking pauses at paragraph and sentence ends, longer research breaks every few paragraphs, and an optional target time that spreads long session breaks through the run
- **Realistic mistakes that always end correct** — adjacent-key typos, sentence reworks, and revision passes are caught and fixed, so the final text is exactly your file
- **Protected text** — wrap exact values in `[[double brackets]]` (e.g. `[[ACC-4471-2290]]`) so they are typed with no typos or reworks, for account numbers, URLs, and anything else that must be exact
- **Bold and italic** — `[[b]]bold[[/b]]` and `[[i]]italic[[/i]]` markers press Ctrl+B and Ctrl+I for you in Google Docs or Word
- **Safe to run** — lock typing to one target window (it auto-pauses if focus moves), and stop at any time with the Stop button or your own hotkeys
- **Resume where you left off** — stopping partway saves a checkpoint you can pick up from later
- **Plan ahead** — preview the word count and a rough duration before you start, set a start countdown, or schedule a start time
- **Profiles and starter profiles** — your last setup is restored automatically, you can save named profiles, and four built-in starters (Careful student, Fast typist, Tired late night, Hands-free dictation) give new people a good start
- **Leave Mistakes For Me and Smart Text Fixes** — optional modes that either leave a few mistakes for you to find, or clean up common slips before typing
- **Easy to read and keep current** — an app-wide text size setting (up to 200%), a Compact Mode for watching progress, and an optional update check that sends only a version number

This is the short list. See the **[full feature list](docs/FEATURES.md)** for every setting and how it works.

## Requirements

- Python 3.14+
- [`uv`](https://docs.astral.sh/uv/) (this project's dependencies and virtual environment are managed with it)
- Windows (the window-lock feature depends on `pygetwindow`, which is Windows-oriented; core typing works cross-platform)

## Installation

```
uv sync
```

This installs `pyautogui` and `keyboard` as declared in `pyproject.toml`.

**Optional — target window lock:** this feature needs `pygetwindow`, which isn't a required dependency. Install it with:

```
uv add pygetwindow
```

If it's not installed, Amanuensis still runs fine — the window-lock section just shows an inline note and stays disabled.

## Usage

1. Run the app:
   ```
   uv run python Amanuensis.py
   ```
2. In the **Setup** tab, pick your input `.txt` file (or use the default `input.txt`), and adjust typing speed, pauses, typos, and reworks to taste.
3. Optionally click **Preview Time Estimate** to see how long a run will take.
4. Click **Start Typing**, then within the countdown (10 seconds by default, and you can change it from 0 to 60 seconds on the **Window** tab), click into the window you want text typed into.
5. Use **Pause/Resume** or **Stop** (or your configured hotkeys) at any time. If you stop partway through, you'll be offered the chance to resume from that point next time.

Hotkeys, profiles, and session logging live in the **Hotkeys & Data** tab.

## Building a standalone .exe

This project ships a PyInstaller spec file. To build a fresh executable:

```
uv run pyinstaller amanuensis.spec
```

The output goes to `dist/amanuensis.exe`. Note: `dist/` and `build/` are regenerated by this command and are excluded from version control via `.gitignore`.

**Do not publish an `.exe` you built yourself.** Official releases are built only by the GitHub Actions Release workflow from a pushed tag, so people can verify where the file came from.

## Verifying a release

Every release lists `amanuensis.exe` and `SHA256SUMS.txt`, and has a build attestation. A `SHA256SUMS.txt.asc` GPG signature is added when the maintainer's signing key is set up.

1. **Checksum (PowerShell):** `Get-FileHash .\amanuensis.exe -Algorithm SHA256` must match the line in `SHA256SUMS.txt`.
2. **Build attestation (needs the [GitHub CLI](https://cli.github.com/)):** `gh attestation verify .\amanuensis.exe --repo jheathspooky009/Amanuensis`
3. **GPG signature (when present):** `gpg --import maintainer-public-key.asc`, then `gpg --verify SHA256SUMS.txt.asc SHA256SUMS.txt`, and compare the fingerprint with the one in [SECURITY.md](SECURITY.md).

The `.exe` is not code-signed, so Windows SmartScreen may warn. That is why the checks above matter.

## Privacy and your data

- Your text stays on your computer. The only network request is the optional update check (a version number from GitHub, with no text and no settings). Turn it off on the Data tab.
- The app saves `last_settings.json`, `resume_state.json`, `profiles/`, and optional session logs on your computer. These can hold folder names, window titles, or pieces of your text, so check them before sharing them. Saved profiles never include folder paths.

## Notes & limitations

- Protected-text markers (`[[...]]`) don't support nesting — the first `]]` found closes the marker, so avoid putting `[[` or `]]` inside protected text itself.
- Formatting markers work by pressing Ctrl+B and Ctrl+I, which switch a style on and off in Google Docs and Word, so the target needs to understand those shortcuts (in a browser form, Ctrl+B may do something else, so turn the setting off there). `[[b]]`, `[[/b]]`, `[[i]]`, and `[[/i]]` (any capitals) are reserved, so protected text cannot be exactly `b` or `i`. While a style is on, the paragraph-ending paraphrase draft is skipped and a rework never deletes the first formatted word. Preview does not press shortcuts. When you resume a stopped run, click right after the last typed character so the formatting carries on.
- Smart Text Fixes follow English punctuation and spacing rules and are deliberately cautious, so some slips are left alone rather than risk a wrong change (for example a lowercase word after an ellipsis, or `word.next` that could be a web address). If you turn Smart Text Fixes on or off, or change which groups are on, a saved resume point may not match anymore (if a fix changes text that was already typed) and you will be asked to start over.
- Leave Mistakes For Me lists only the mistakes typed in the current session, so if you resume a partly typed file, mistakes left in an earlier session are not in the list. The typer never backspaces or retypes a left mistake, and it skips sentence reworks for the words around one.
- Update checks need a public GitHub release. If the repository is private, the check reports that no public release was found. The Update and Restart and Download buttons only appear in the packaged `.exe`; running from source only offers the GitHub page.
- The `keyboard` library's global hotkeys may require running with elevated/administrator privileges on some systems.
- Ctrl+Backspace word-deletion behavior (used for sentence reworks) varies slightly by target application; exact spacing after a rework depends on how the target app handles it.
- This tool sends real keystrokes via `pyautogui`/`keyboard` — always keep the Stop hotkey/button accessible while a session is running.

## Contributing and security

Contributors must run the security checks in [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

## License

MIT — see [LICENSE](LICENSE).
