#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_BUNDLE="$ROOT/dist/Clean My Codex.app"
VERIFY="${1:-}"

if pgrep -x "Clean My Codex" >/dev/null 2>&1; then
  echo "Clean My Codex is already open. Quit it normally, then run this command again."
  exit 1
fi
"$ROOT/script/build_macos_app.sh"
open -n "$APP_BUNDLE"

if [[ "$VERIFY" == "--verify" ]]; then
  for _ in {1..50}; do
    if pgrep -x "Clean My Codex" >/dev/null 2>&1; then
      echo "Clean My Codex.app is running."
      exit 0
    fi
    sleep 0.1
  done
  echo "Clean My Codex.app did not stay running."
  exit 1
fi
