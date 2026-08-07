#!/bin/zsh
set -euo pipefail

if [[ "$#" -ne 2 ]]; then
  echo "Usage: $0 <app-bundle> <output-archive>"
  exit 64
fi

APP_BUNDLE="$1"
OUTPUT_ARCHIVE="$2"
BUILD_ARCH="${CLEAN_MY_CODEX_BUILD_ARCH:-$(uname -m)}"

: "${CLEAN_MY_CODEX_SIGN_IDENTITY:?CLEAN_MY_CODEX_SIGN_IDENTITY is required}"
: "${APPLE_API_KEY_PATH:?APPLE_API_KEY_PATH is required}"
: "${APPLE_API_KEY_ID:?APPLE_API_KEY_ID is required}"
: "${APPLE_API_ISSUER_ID:?APPLE_API_ISSUER_ID is required}"

if [[ ! -d "$APP_BUNDLE" || "${APP_BUNDLE:t}" != *.app ]]; then
  echo "The app bundle does not exist: $APP_BUNDLE"
  exit 1
fi
if [[ ! -f "$APPLE_API_KEY_PATH" ]]; then
  echo "The App Store Connect API key file does not exist."
  exit 1
fi
if [[ "$CLEAN_MY_CODEX_SIGN_IDENTITY" != "Developer ID Application:"* ]]; then
  echo "A Developer ID Application identity is required for public distribution."
  exit 1
fi

for required_command in codesign security xcrun ditto shasum plutil file find; do
  if ! command -v "$required_command" >/dev/null 2>&1; then
    echo "Required command is unavailable: $required_command"
    exit 1
  fi
done

if ! security find-identity -v -p codesigning | grep -F "\"$CLEAN_MY_CODEX_SIGN_IDENTITY\"" >/dev/null; then
  echo "The requested Developer ID identity is not available in the active keychains."
  exit 1
fi

BUILD_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/clean-my-codex-notary.XXXXXX")"
SUBMISSION_ARCHIVE="$BUILD_ROOT/notarization-submission.zip"
NOTARY_RESULT="$BUILD_ROOT/notary-result.json"

cleanup() {
  rm -rf "$BUILD_ROOT"
}
trap cleanup EXIT

xattr -cr "$APP_BUNDLE"

typeset -a macho_files
while IFS= read -r -d '' candidate; do
  if [[ "$(file -b "$candidate")" == *"Mach-O"* ]]; then
    macho_files+=("$candidate")
  fi
done < <(find "$APP_BUNDLE/Contents" -type f -print0)

if (( ${#macho_files[@]} == 0 )); then
  echo "No Mach-O code was found in the app bundle."
  exit 1
fi

for candidate in "${macho_files[@]}"; do
  codesign --force --options runtime --timestamp \
    --sign "$CLEAN_MY_CODEX_SIGN_IDENTITY" "$candidate"
done

# Sign nested code containers after their contents, then seal the outer app.
while IFS= read -r -d '' nested_bundle; do
  codesign --force --options runtime --timestamp \
    --sign "$CLEAN_MY_CODEX_SIGN_IDENTITY" "$nested_bundle"
done < <(
  find "$APP_BUNDLE/Contents" -depth -type d \
    \( -name '*.framework' -o -name '*.app' -o -name '*.xpc' -o -name '*.appex' -o -name '*.plugin' \) \
    -print0
)

codesign --force --options runtime --timestamp \
  --sign "$CLEAN_MY_CODEX_SIGN_IDENTITY" "$APP_BUNDLE"
codesign --verify --deep --strict --verbose=2 "$APP_BUNDLE"

SIGNING_DETAILS="$(codesign -dvv "$APP_BUNDLE" 2>&1)"
TEAM_IDENTIFIER="$(printf '%s\n' "$SIGNING_DETAILS" | sed -n 's/^TeamIdentifier=//p' | head -n 1)"
if [[ "$SIGNING_DETAILS" != *"Authority=Developer ID Application:"* ]]; then
  echo "The final app is not signed with a Developer ID Application certificate."
  exit 1
fi
if [[ -z "$TEAM_IDENTIFIER" || "$TEAM_IDENTIFIER" == "not set" ]]; then
  echo "The final app does not contain an Apple TeamIdentifier."
  exit 1
fi
if [[ "$SIGNING_DETAILS" != *"runtime"* || "$SIGNING_DETAILS" != *"Timestamp="* ]]; then
  echo "The final app is missing hardened runtime or a secure timestamp."
  exit 1
fi

ditto -c -k --sequesterRsrc --keepParent "$APP_BUNDLE" "$SUBMISSION_ARCHIVE"
xcrun notarytool submit "$SUBMISSION_ARCHIVE" \
  --key "$APPLE_API_KEY_PATH" \
  --key-id "$APPLE_API_KEY_ID" \
  --issuer "$APPLE_API_ISSUER_ID" \
  --wait \
  --output-format json > "$NOTARY_RESULT"

NOTARY_STATUS="$(plutil -extract status raw -o - "$NOTARY_RESULT")"
if [[ "$NOTARY_STATUS" != "Accepted" ]]; then
  echo "Apple notarization did not return Accepted."
  exit 1
fi

xcrun stapler staple "$APP_BUNDLE"
xcrun stapler validate "$APP_BUNDLE"
codesign --verify --deep --strict --verbose=2 "$APP_BUNDLE"
spctl --assess --type execute --verbose=4 "$APP_BUNDLE"

mkdir -p "${OUTPUT_ARCHIVE:h}"
rm -f "$OUTPUT_ARCHIVE" "$OUTPUT_ARCHIVE.sha256"
ditto -c -k --sequesterRsrc --keepParent "$APP_BUNDLE" "$OUTPUT_ARCHIVE"
ARCHIVE_HASH="$(shasum -a 256 "$OUTPUT_ARCHIVE" | awk '{print $1}')"
printf '%s  %s\n' "$ARCHIVE_HASH" "${OUTPUT_ARCHIVE:t}" > "$OUTPUT_ARCHIVE.sha256"

echo "Signed and notarized macOS package: $OUTPUT_ARCHIVE"
echo "Architecture: $BUILD_ARCH"
echo "SHA-256: $ARCHIVE_HASH"
