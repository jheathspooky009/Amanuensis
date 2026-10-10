## What does this change?

(Describe it in a sentence or two.)

## Security checklist (required)

- [ ] I ran `uv run python scripts/security_check.py` and it passed
- [ ] I ran `uv run pyflakes Amanuensis.py scripts/security_check.py` and `uvx bandit@1.9.4 -q -ll -r Amanuensis.py scripts` with no problems
- [ ] My change adds no personal or machine-specific details (folder paths, user names, emails, IP addresses) and no secrets
- [ ] I did not commit settings, profiles, logs, resume files, or a built `.exe`
- [ ] If I added or changed a dependency, I explained why and ran `uvx pip-audit@2.10.1` on the updated lock file
- [ ] If I touched `.github/workflows/`, I kept `permissions:` as small as possible
- [ ] If I touched the update checker, it still uses HTTPS and the approved GitHub hosts only
- [ ] I updated the version history in `Amanuensis.py` (comment block and `VERSION_HISTORY`)
