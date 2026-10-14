# Security Policy

Amanuensis is open source and publicly auditable. Anyone can read every line of the code and every build script in this repository. If you find a security problem, please tell us privately first so it can be fixed before it is public.

## Reporting a vulnerability

Please do **not** open a public issue for a security problem.

1. Go to the **Security** tab of this repository.
2. Click **Report a vulnerability** (GitHub private vulnerability reporting).
3. Describe what you found, how to reproduce it, and what you think the impact is.

What to expect (this is a one-person project, so these are goals, not guarantees):

- A first reply within 7 days.
- A fix or a clear plan for serious problems as soon as possible, and a new release when the fix is ready.
- Credit in the release notes if you want it. Tell us if you would rather stay anonymous.
- Please give us up to 90 days to fix a problem before you share it publicly.

Never include real passwords, tokens, or personal data in a report.

## Supported versions

Only the newest release gets security fixes. Please update before reporting a problem.

## What counts as a security problem

In scope:

- The update checker or downloader accepting a bad file, a bad address, or a redirect it should refuse.
- Typing text or running anything the person did not choose.
- Settings, profile, resume, or log files that crash the app, expose personal data, or cause harm when opened.
- A weakness in the build or release process (for example a workflow that could be abused).
- A dependency with a known vulnerability that affects this app.

Out of scope:

- Anything that needs the attacker to already control the person's computer or Windows account.
- The app typing into the wrong window because the person moved focus (use the Target Window lock and the Stop hotkey).
- Windows SmartScreen warnings. The `.exe` is not code-signed (see "Known limits").

## How the app is built to be safe

- **Runs locally.** There is no account, no server, and no telemetry. Your text is never uploaded.
- **One network request.** The optional update check asks GitHub for the newest release number. It uses HTTPS to a short list of GitHub hosts only, refuses redirects anywhere else, and sends no text and no settings. You can turn it off on the Data tab.
- **Safe updates.** In the packaged `.exe`, "Update and Restart" downloads the new file next to the app, checks its size and SHA-256 against what GitHub publishes, and only then swaps it in by renaming (the running file becomes `.old`). If any step fails, the old file is put back. It never installs during a typing run, and it is only offered when GitHub published a checksum and the app's folder can be written to. In every other case an update is saved as a separate file and nothing is replaced.
- **No secrets.** The app has no keys, passwords, or tokens.
- **Shareable files.** Saved profiles contain settings only, never folder paths. Session logs record the input file's name, not its folder, and cells that a spreadsheet could run as a formula are neutralized.

### What the app writes on your computer

| File | What it holds | Where |
|---|---|---|
| `last_settings.json` | Your settings and last input file path | Next to the app |
| `resume_state.json` | Where each file stopped, plus a hash of the typed text | Next to the app |
| `profiles/` | Saved settings presets (no folder paths) | Next to the app |
| Session log (`.csv`, optional, off by default) | Events with window titles and short pieces of typed text | Next to the input file |
| `_pasted_input.txt` | Text you pasted into the app | Next to the app |
| Mistake list (`.txt`, only if you save it) | Mistakes left on purpose | Where you choose |

These can contain folder names, window titles, or pieces of your text. Do not post them publicly or attach them to issues without checking them first.

## Checking a download before you install it

Always check a new release before you run it. Download only from this repository's **Releases** page, never from a link someone sends you.

Every release has `amanuensis.exe`, `SHA256SUMS.txt`, and a build attestation. It also has `SHA256SUMS.txt.asc` when a GPG signing key is set up.

**1. Check the SHA-256 checksum (PowerShell):**

```
Get-FileHash .\amanuensis.exe -Algorithm SHA256
```

The hash must match the line in `SHA256SUMS.txt`.

**2. Check the build attestation (needs the [GitHub CLI](https://cli.github.com/)):**

```
gh attestation verify .\amanuensis.exe --repo jheathspooky009/Amanuensis
```

This proves the file was built by this repository's release workflow on GitHub's machines.

**3. Check the GPG signature (when `SHA256SUMS.txt.asc` is present):**

```
gpg --import maintainer-public-key.asc
gpg --verify SHA256SUMS.txt.asc SHA256SUMS.txt
```

Then compare the fingerprint that GPG prints with the one below.

Signing key fingerprint: published here when the maintainer's signing key is set up. If a release has a `.asc` file but this line is empty, do not trust the signature.

If any check fails, do not run the file. Report it using the steps above.

## How every update is checked

Releases are built only by GitHub Actions, from a pushed tag, never from a personal computer. Before a release builds, and on every push and pull request, these checks run:

- Syntax check and `pyflakes` lint.
- `bandit` code security scan (medium and high findings fail the build).
- `scripts/security_check.py`: finds personal paths, emails, IP addresses, secrets, files that must not be committed, weak workflow settings, an unapproved update host, and a version mismatch with the release tag.
- `pip-audit` against the locked dependency list.
- CodeQL code scanning (also weekly) and dependency review on pull requests.
- Dependabot pull requests for dependency and GitHub Action updates.

Contributors must run the same checks before opening a pull request. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Known limits

- The `.exe` is not code-signed, so Windows SmartScreen may warn. Verify the checksum or attestation instead.
- The checksum shown in the update prompt comes from GitHub. The attestation and GPG signature give stronger proof than the checksum alone.
- Update and Restart trusts the same GitHub checksum and does not check the attestation or GPG signature, so use the manual checks above if you want the stronger proof. It also writes `amanuensis.exe.new` and `amanuensis.exe.old` next to the app for a short time.
- The `keyboard` library uses global hotkeys and may need administrator rights on some systems.
- Amanuensis types into whatever window has focus. Keep the Stop hotkey ready, use the Target Window lock, and do not type secrets into a window you cannot see.
