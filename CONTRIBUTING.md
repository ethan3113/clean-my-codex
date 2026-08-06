# Contributing to Clean My Codex

Contributions are welcome when they preserve the app's narrow purpose and reversible safety model.

## Before Opening a Change

1. Search existing issues and pull requests.
2. Keep the proposed change focused on Codex chat, path, file, or Trash Bin management.
3. Never attach real Codex sessions, databases, credentials, operation logs, or identifying workspace paths.
4. Use synthetic fixtures for every test.

## Development

The source launcher requires Python 3.10 or newer and uses no third-party runtime packages. Building the standalone macOS app additionally requires the pinned PyInstaller build dependency and Apple Command Line Tools.

```bash
python3 -m unittest discover -s tests
python3 -m py_compile clean_my_codex/*.py scripts/*.py script/*.py
node --check static/app.js
python3 scripts/build_release.py --output-dir release
```

For a standalone app build:

```bash
python3 -m venv build/pyinstaller-venv
build/pyinstaller-venv/bin/python -m pip install -r requirements-macos-build.txt
PYINSTALLER_PYTHON="$PWD/build/pyinstaller-venv/bin/python" ./script/build_macos_app.sh
```

## Pull Requests

- Explain the behavior and safety boundary being changed.
- Include focused tests for any data operation or parser change.
- Keep preview and apply paths separate.
- Preserve confirmation text for destructive actions.
- Do not weaken SQLite transactions, integrity checks, backups, or post-apply verification.
- Confirm that the release audit passes.
- Do not commit generated release archives.

## Interface Changes

- Keep controls compact and keyboard accessible.
- Preserve visible focus states and reduced-motion behavior.
- Use semantic icons in addition to color for status.
- Keep destructive red inside explicit destructive contexts.
- Verify desktop and narrow viewport layouts.

## Commit Scope

Use a small, descriptive commit and avoid unrelated formatting or generated-file churn.
