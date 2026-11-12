# Project conventions

- Use **WagFlow** in prose, `wagflow` in commands, paths and markers, and `WAGFLOW` or `WAGFLOW_*` for environment variables.
- WagTerm integration uses `wagtermctl`, the `wagterm` control pipe and `WAGTERM_*` variables. Do not fall back to upstream terminal identities.
- The terminal transport module is `lib/wag.py`. The mailbox CLI is `lib/wagmsg.py`.
- Diff sizing uses `origin/HEAD` without guessing a branch name. When the default branch is unknown, the diff size is unknown.
- `lib/wagflow.ps1` is the launcher. `lib/WagFlow.Common.ps1` contains shared helpers. Keep their names distinct on case-insensitive filesystems.
- Run `python -m unittest discover -s tests`. PowerShell integration tests require `pwsh` on PATH and use temporary configurations and terminal stubs.
- Resolve Git Bash's own Git executable instead of hard-coding `/mingw64/bin/git`. MSYS2 installations may use `/ucrt64/bin/git`.
- `tests/frames.py` and files under `tests/fixtures/` ending in `.txt` contain captured terminal output. Preserve historical paths and branding in those recordings.
