# Amanuensis

A Python desktop tool that simulates human-like typing — built for accessibility users, QA/software testers, and restricted VDI environments where copy-paste isn't an option.

Amanuensis reads text from a file and types it into whatever window currently has focus, with randomized speed, natural pauses, occasional realistic mistakes, and configurable "research breaks" — instead of an instant, obviously-automated paste.

**Open source and publicly auditable.** Amanuensis is released under the MIT license. Every line of code and every build script is in this repository for anyone to read, check, and improve. It runs entirely on your computer, with no account and no telemetry. See [SECURITY.md](SECURITY.md) for how it is built to be safe and how to report a problem.

> **Always check a new release before you install it.** Download only from this repository's [Releases](../../releases) page, then verify the checksum, build attestation, or GPG signature using the steps in [Verifying a release](#verifying-a-release). If a check fails, do not run the file.

## Who this is for

- **Accessibility & motor-impairment support** — people with RSI, carpal tunnel, arthritis, or similar conditions can prepare text with speech-to-text tools and have it typed in hands-free, with the countdown and controls giving time to position the cursor without physical strain.
- **QA & software testing** — testers can see how text editors, form fields, and collaborative tools handle realistic, non-instant keystroke input instead of an instantaneous string injection.
- **Restricted / VDI environments** — for enterprise remote desktops and web portals that disable copy-paste, Amanuensis simulates raw keyboard input instead, with an instant ESC/Stop kill-switch for safety.

## Features

- **Configurable typing speed** — min/max delay per character, with a slight speed-up on common letter pairs (`th`, `he`, `an`, etc.)
- **Finger and hand timing** — an optional setting (on by default) that follows a QWERTY touch-typing finger map. Two letters typed with opposite hands come a little sooner, and two different letters on the same finger (like `ed` or `lo`) come a little later. Only letters are affected
- **Flow and hesitation** — an optional setting (on by default) that gives the typist moods. Amanuensis drifts between normal, flow (about 15% faster), and hesitant (about 25% slower) stretches that last a few dozen to a few hundred characters, and the speed eases in and out so it never jumps. A Mood Strength setting (0 to 200%) scales the swings. Only speed changes, and a long break puts the typist back in the normal state
- **Natural pauses** — a short "thinking" pause at every paragraph break, and a longer pause after sentence-ending punctuation, independent of any research break
- **Typing bursts**: an optional setting (on by default) where the typist writes a phrase, stops for a moment, then carries on. Bursts are about 8 to 16 words in plain, familiar wording and about 3 to 7 words in a sentence with a hard word, an argument word like "because", or many commas, and a burst often ends at a comma. A Pause Strength setting (0 to 300%) scales the pauses, and protected text never gets one
- **Decision pauses**: an optional setting (on by default) where the typist stops a little longer at the spots where a writer has to decide something: before a new paragraph, before a sentence that states a claim (like "I believe" or "this shows"), and before a transition (like "however" or "for example"). A Pause Strength setting (0 to 300%) scales the pauses, and protected text never gets one
- **Uneven rhythm**: an optional setting (on by default) so the typist does not keep one steady beat. Everyday words and familiar phrases (like "of the" or "a lot of") come out about 15% quicker. Names in the middle of a sentence, hard or technical words, numbers, punctuation marks, and hedged claims (like "perhaps" or "tends to") come out about 20 to 30% slower. A Rhythm Strength setting (0 to 200%) scales the changes. Only speed changes, and protected text always types at the normal speed
- **Sentence reworks** — occasionally pauses, deletes the last few words just typed, and retypes them, simulating real hesitation (final text always stays exactly correct)
- **Adjacent-key typos** — a configurable per-letter chance of a realistic, physically-nearby keyboard typo that gets caught and corrected
- **Research breaks** — configurable frequency and duration for longer pauses every few paragraphs
- **Second revision pass** — an optional setting (off by default) where the typist rereads a paragraph and goes back to change an earlier word. One word is first typed as a close synonym. At the end of the paragraph, Amanuensis presses the Left arrow key to go back, deletes the word, types the real one, and presses the Right arrow key the same number of times to return, so the final text is always exactly your file. It only uses plain words 30 to 250 characters from the end of the paragraph and skips paragraphs with protected text, bold or italic markers, purposely left mistakes, or symbols an editor might change. It needs an editor where the arrow keys move the cursor (like Google Docs or Word). If you press Stop before a word is fixed, a message tells you what to change by hand. Preview shows the change without pressing any keys
- **Select and retype** — an optional setting (off by default) where the typist decides a word they just typed was wrong. One word in a paragraph is first typed as a close synonym. Then Amanuensis holds Shift and taps the Left arrow key once per letter to highlight the word, and types the real word over it, so the final text is always exactly your file. It only uses plain words and skips words touched by protected text, bold or italic markers, or purposely left mistakes. It needs an editor where Shift+Left highlights text (like Google Docs or Word). If you press Stop in the middle, a message tells you what to change by hand. Preview shows the change without pressing any keys
- **Overshoot** — a setting (on by default) where, after a typo is backspaced away, the typist sometimes presses Backspace one time too many, pauses, and types that letter again. It only uses the Backspace key, only happens when character typos are on, and the letter is always put back
- **Revision clusters**: an optional setting (on by default) so mistakes bunch together like real revising. Typos, sentence reworks, drafted paraphrases, and second revision or select and retype words land in short stretches (about 70 to 220 characters, starting at the beginning of a sentence), with calmer typing in between. A Cluster Strength setting (0 to 200%) scales it, and a revision style (Mixed, Edit as I go, or Draft, then revise) chooses where the stretches go. You get about the same number of mistakes overall, so the time estimate does not change, and protected text is never touched
- **Target time till completion** — an optional setting (off by default) for how long the whole run should take, in hours and minutes. The spare time becomes long session breaks at paragraph or sentence starts, never inside `[[protected text]]`. The longer the target, the longer and more spread out the breaks (a 7-hour target can have breaks of an hour or more). Break lengths adjust as the run goes so it finishes close to the target, and time you spend paused is not counted. It never types faster than your speed settings, so a target that is too short cannot be reached (you get a warning first). Preview ignores it
- **Target window lock** — pick a specific window from a list; typing auto-pauses if focus moves elsewhere
- **Resume where you left off** — stopping partway through a file saves a checkpoint; next time, choose to resume or start over
- **Pre-start time estimate** — preview word/character count and a rough total duration before clicking Start
- **Session logging** — optional CSV log of mistakes, typos, breaks, and focus changes for QA review
- **Auto-saved settings** — your last-used setup is restored automatically; named profiles are also available for switching between distinct presets
- **Configurable hotkeys** — remap Start/Pause/Stop; ESC-style safety is always available via the Stop hotkey
- **Live stats** — WPM, ETA, progress, and countdowns to the next mistake/break, all visible while running
- **Protected text** — wrap exact text in `[[double brackets]]` (e.g. `[[ACC-4471-2290]]`) to type it with no typos or reworks applied, for account numbers, URLs, or other values that must be exact
- **Formatting markers** — wrap text in `[[b]]bold[[/b]]` or `[[i]]italic[[/i]]` (use both at once if you like) and Amanuensis presses Ctrl+B or Ctrl+I at those spots, so the text comes out bold or italic in Google Docs or Word. The markers are never typed. An optional setting (on by default) can be turned off to remove the markers without pressing any shortcut, which suits plain web forms. You get a warning first if a marker has no partner, and a style left open is closed after the last character. Typos, reworks, and left mistakes are arranged so they can never lose or move a formatting change
- **Smart Text Fixes** — an optional, built-in proofreading pass that cleans up common slips before typing: extra, trailing, and missing spaces, spaces before punctuation, missing commas after openers like "However", missing periods at the end of a paragraph, doubled punctuation, sentence capitalization, a lowercase "i", and repeated words ("the the"). Four on/off groups, off by default. It uses plain fixed rules (no AI, no internet), never touches `[[protected text]]`, and skips web addresses, emails, file paths, code, and abbreviations. A "Show fixes being noticed" setting makes some fixes visible on screen (a quick slip, a pause, a backspace), and **Review Fixes** lists every planned change before you type
- **Leave Mistakes For Me** — an optional mode (off by default) that purposely leaves a few mistakes in the typed text so you can find and fix them yourself: typos (wrong, swapped, doubled, or missing letters, and joined words), missing commas, and missing punctuation (periods, `?`, `!`, apostrophes). Choose how many per file and which groups to use. Mistakes are spread across the text and never touch `[[protected text]]`, web addresses, emails, paths, or numbers. When the run ends, a **Mistakes Left For You** window lists every one by line number, showing what is on screen and what it should be, with Copy and Save buttons
- **Completion notifications** — independent toggles for an on-screen notification and/or a sound when a run finishes naturally (not on a manual Stop)
- **Update prompts** — an optional check (on by default, easy to turn off) that asks GitHub for the newest release a few seconds after the app starts, plus a **Check for Updates Now** button on the Data tab. When a newer version exists you can update, visit the GitHub page, skip that version, or wait. In the packaged `.exe`, **Update and Restart** downloads the new version next to the app, checks it against GitHub's published SHA-256, swaps it in for the running file, and opens it, so your settings, profiles, and saved progress carry over. If any step fails, your current version is kept. It never installs while a run is active. If the app runs from source, its folder cannot be written to, or a release has no checksum, a plain **Download** saves a separate `.exe` instead and does not replace anything. Only a version number is requested; nothing from your text or settings is sent, and no pop-up appears while a run is active
- **Compact Mode** — a header button that shrinks the window to just a status view (live stats + controls) for keeping an eye on progress without the full settings panel on screen

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
4. Click **Start Typing**, then within the 10-second countdown, click into the window you want text typed into.
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
