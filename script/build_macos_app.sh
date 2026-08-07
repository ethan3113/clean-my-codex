#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYINSTALLER_PYTHON:-python3}"
BUILD_ROOT="$ROOT/build/macos"
DIST_ROOT="$ROOT/dist"
APP_BUNDLE="$DIST_ROOT/Clean My Codex.app"
CONTENTS="$APP_BUNDLE/Contents"
MACOS_DIR="$CONTENTS/MacOS"
RESOURCES_DIR="$CONTENTS/Resources"
EXPECTED_ARCH="${CLEAN_MY_CODEX_BUILD_ARCH:-$(uname -m)}"

case "$EXPECTED_ARCH" in
  arm64|x86_64) ;;
  aarch64) EXPECTED_ARCH="arm64" ;;
  amd64) EXPECTED_ARCH="x86_64" ;;
  *) echo "Unsupported macOS build architecture: $EXPECTED_ARCH"; exit 1 ;;
esac

if ! "$PYTHON" -c 'import PyInstaller' >/dev/null 2>&1; then
  echo "PyInstaller is required to build the standalone macOS application."
  echo "Install requirements-macos-build.txt into a build-only virtual environment."
  exit 1
fi

VERSION="$($PYTHON -c 'from clean_my_codex import APP_VERSION; print(APP_VERSION)')"
PYTHON_ARCH="$($PYTHON -c 'import platform; print(platform.machine())')"
if [[ "$PYTHON_ARCH" != "$EXPECTED_ARCH" ]]; then
  echo "Python architecture $PYTHON_ARCH does not match requested app architecture $EXPECTED_ARCH."
  exit 1
fi

rm -rf "$BUILD_ROOT" "$APP_BUNDLE"
mkdir -p "$BUILD_ROOT/server-spec" "$DIST_ROOT" "$MACOS_DIR" "$RESOURCES_DIR/app"
export PYINSTALLER_CONFIG_DIR="$BUILD_ROOT/pyinstaller-config"

"$PYTHON" -m PyInstaller "$ROOT/run.py" \
  --noconfirm \
  --clean \
  --onedir \
  --console \
  --target-architecture "$EXPECTED_ARCH" \
  --name clean-my-codex-server \
  --distpath "$BUILD_ROOT/server-dist" \
  --workpath "$BUILD_ROOT/server-work" \
  --specpath "$BUILD_ROOT/server-spec"

mkdir -p "$BUILD_ROOT/clang-module-cache"
xcrun --sdk macosx clang \
  -fobjc-arc \
  -fmodules \
  -fmodules-cache-path="$BUILD_ROOT/clang-module-cache" \
  -mmacosx-version-min=13.0 \
  -arch "$EXPECTED_ARCH" \
  -framework Cocoa \
  -framework Security \
  -framework WebKit \
  "$ROOT/macos/CleanMyCodexApp/main.m" \
  -o "$MACOS_DIR/Clean My Codex"
ditto "$BUILD_ROOT/server-dist/clean-my-codex-server" "$RESOURCES_DIR/server"
ditto "$ROOT/static" "$RESOURCES_DIR/app/static"
mkdir -p "$RESOURCES_DIR/Documentation"
cp "$ROOT/LICENSE" "$ROOT/README.md" "$ROOT/THIRD_PARTY_NOTICES.md" "$RESOURCES_DIR/Documentation/"
PYINSTALLER_LICENSE="$($PYTHON -c 'from importlib.metadata import distribution; d=distribution("pyinstaller"); print(next(p.locate() for p in d.files if p.name == "COPYING.txt"))')"
PYTHON_LICENSE="$($PYTHON "$ROOT/script/cpython_runtime_license.py" "$ROOT")"
cp "$PYINSTALLER_LICENSE" "$RESOURCES_DIR/Documentation/PYINSTALLER_COPYING.txt"
cp "$PYTHON_LICENSE" "$RESOURCES_DIR/Documentation/PYTHON_LICENSE.txt"
cp "$ROOT/macos/Info.plist" "$CONTENTS/Info.plist"
plutil -replace CFBundleShortVersionString -string "$VERSION" "$CONTENTS/Info.plist"
plutil -replace CFBundleVersion -string "$VERSION" "$CONTENTS/Info.plist"

BASE_ICON="$BUILD_ROOT/AppIcon-1024.png"
ICONSET="$BUILD_ROOT/AppIcon.iconset"
xcrun --sdk macosx clang \
  -fobjc-arc \
  -fmodules \
  -fmodules-cache-path="$BUILD_ROOT/clang-module-cache" \
  -mmacosx-version-min=13.0 \
  -arch "$EXPECTED_ARCH" \
  -framework Cocoa \
  "$ROOT/macos/AppIconGenerator.m" \
  -o "$BUILD_ROOT/AppIconGenerator"
"$BUILD_ROOT/AppIconGenerator" "$BASE_ICON"
mkdir -p "$ICONSET"
RETINA_SUFFIX=$'\x402x'
for entry in \
  "16 icon_16x16.png" \
  "32 icon_16x16${RETINA_SUFFIX}.png" \
  "32 icon_32x32.png" \
  "64 icon_32x32${RETINA_SUFFIX}.png" \
  "128 icon_128x128.png" \
  "256 icon_128x128${RETINA_SUFFIX}.png" \
  "256 icon_256x256.png" \
  "512 icon_256x256${RETINA_SUFFIX}.png" \
  "512 icon_512x512.png" \
  "1024 icon_512x512${RETINA_SUFFIX}.png"; do
  size="${entry%% *}"
  name="${entry#* }"
  sips -z "$size" "$size" "$BASE_ICON" --out "$ICONSET/$name" >/dev/null
done
"$PYTHON" "$ROOT/script/package_icns.py" "$ICONSET" "$RESOURCES_DIR/AppIcon.icns"

DECLARED_MINIMUM="$(plutil -extract LSMinimumSystemVersion raw "$CONTENTS/Info.plist")"
DETECTED_MINIMUM="$($PYTHON "$ROOT/script/macos_bundle_minimum.py" "$APP_BUNDLE" --floor "$DECLARED_MINIMUM")"
plutil -replace LSMinimumSystemVersion -string "$DETECTED_MINIMUM" "$CONTENTS/Info.plist"
MACHO_COUNT="$($PYTHON "$ROOT/script/macos_bundle_architecture.py" "$APP_BUNDLE" --expected "$EXPECTED_ARCH")"

xattr -cr "$APP_BUNDLE"
codesign --force --deep --sign - "$APP_BUNDLE"
plutil -lint "$CONTENTS/Info.plist"
codesign --verify --deep --strict --verbose=2 "$APP_BUNDLE"

echo "Minimum macOS: $DETECTED_MINIMUM"
echo "Architecture: $EXPECTED_ARCH ($MACHO_COUNT Mach-O files verified)"
echo "$APP_BUNDLE"
