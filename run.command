#!/bin/zsh
set -u
unsetopt BG_NICE

APP_DIR="$(/usr/bin/python3 -c 'import os, sys; print(os.path.dirname(os.path.realpath(sys.argv[1])))' "$0")" || exit 1
HOST="127.0.0.1"
PORT="${CLEAN_MY_CODEX_PORT:-8765}"
CODEX_HOME="${CLEAN_MY_CODEX_HOME:-${HOME}/.codex}"
URL="http://${HOST}:${PORT}"
SESSION_FILE="${APP_DIR}/.clean-my-codex-session"
BOOTSTRAP_FILE=""

cd "$APP_DIR" || exit 1

if ! /usr/bin/python3 -c 'import os, stat, sys; value = os.stat(sys.argv[1]); raise SystemExit(0 if value.st_uid == os.getuid() and not (stat.S_IMODE(value.st_mode) & 0o022) else 1)' "$APP_DIR"; then
  echo "Clean My Codex must run from a directory owned by the current account and not writable by other accounts."
  exit 1
fi

if [[ ! "$PORT" =~ ^[0-9]{1,5}$ ]] || (( PORT < 1 || PORT > 65535 )); then
  echo "CLEAN_MY_CODEX_PORT must be a number from 1 to 65535."
  exit 1
fi

open_protected_session() {
  local temp_root="${TMPDIR:-/tmp}"
  BOOTSTRAP_FILE="$(umask 077; /usr/bin/python3 -c 'import os, sys, tempfile; fd, path = tempfile.mkstemp(prefix="clean-my-codex-launch.", suffix=".html", dir=sys.argv[1]); os.close(fd); print(path)' "$temp_root")" || return 1
  printf '<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer"><script>location.replace("%s/#token=%s")</script>\n' "$URL" "$TOKEN" > "$BOOTSTRAP_FILE"
  /bin/chmod 600 "$BOOTSTRAP_FILE"
  /usr/bin/open "$BOOTSTRAP_FILE"
  (
    sleep 10
    /bin/rm -f "$BOOTSTRAP_FILE"
  ) &
}

if /usr/bin/curl -fsS "${URL}/api/health" >/dev/null 2>&1; then
  if [[ -s "$SESSION_FILE" && -f "$SESSION_FILE" && ! -L "$SESSION_FILE" && "$(/usr/bin/stat -f '%Lp' "$SESSION_FILE")" == "600" && "$(/usr/bin/stat -f '%u' "$SESSION_FILE")" == "$EUID" ]]; then
    TOKEN="$(/bin/cat "$SESSION_FILE")"
    if (( ${#TOKEN} < 32 )); then
      echo "Clean My Codex found an invalid app session."
      echo "Stop that process, then launch this command again."
      exit 1
    fi
    echo "Clean My Codex is already running:"
    echo "$URL"
    umask 077
    open_protected_session
    exit 0
  fi
  echo "Clean My Codex is running without a readable app session."
  echo "Stop that process, then launch this command again."
  exit 1
fi

TOKEN="$(/usr/bin/python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
if (( ${#TOKEN} < 32 )); then
  echo "Clean My Codex could not create a valid app session."
  exit 1
fi
umask 077
/bin/rm -f "$SESSION_FILE"
if ! print -r -- "$TOKEN" | /usr/bin/python3 -c 'import os, sys; path = sys.argv[1]; flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0); data = sys.stdin.buffer.read(); descriptor = os.open(path, flags, 0o600); os.write(descriptor, data); os.fsync(descriptor); os.close(descriptor)' "$SESSION_FILE"; then
  echo "Clean My Codex could not create a protected app session."
  exit 1
fi
cleanup() {
  /bin/rm -f "$SESSION_FILE"
  if [[ -n "$BOOTSTRAP_FILE" ]]; then
    /bin/rm -f "$BOOTSTRAP_FILE"
  fi
}
trap cleanup EXIT INT TERM

echo "Starting Clean My Codex..."
echo "$URL"

(
  for _ in {1..40}; do
    if /usr/bin/curl -fsS "${URL}/api/health" >/dev/null 2>&1; then
      open_protected_session
      exit 0
    fi
    sleep 0.25
  done
) &

/usr/bin/python3 "$APP_DIR/run.py" \
  --host "$HOST" \
  --port "$PORT" \
  --codex-home "$CODEX_HOME" \
  --token-file "$SESSION_FILE"
