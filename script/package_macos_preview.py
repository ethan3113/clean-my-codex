from __future__ import annotations

import argparse
import hashlib
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION_PATTERN = re.compile(r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$")
SUPPORTED_ARCHITECTURES = {"arm64", "x86_64"}


def preview_asset_name(version: str, architecture: str) -> str:
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"Unsafe preview version: {version!r}")
    if architecture not in SUPPORTED_ARCHITECTURES:
        raise ValueError(f"Unsupported macOS preview architecture: {architecture!r}")
    return f"Clean-My-Codex-macOS-{architecture}-v{version}-unsigned-preview.dmg"


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, check=True, text=True, capture_output=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_preview(
    app_bundle: Path,
    output_directory: Path,
    version: str,
    architecture: str,
) -> dict[str, str]:
    if sys.platform != "darwin":
        raise RuntimeError("macOS preview images must be built on macOS")
    for command in ("codesign", "ditto", "hdiutil"):
        if shutil.which(command) is None:
            raise RuntimeError(f"Required macOS packaging command is unavailable: {command}")

    app_bundle = app_bundle.expanduser().resolve()
    output_directory = output_directory.expanduser().resolve()
    if not app_bundle.is_dir() or app_bundle.suffix != ".app":
        raise RuntimeError(f"The app bundle does not exist: {app_bundle}")

    info_path = app_bundle / "Contents" / "Info.plist"
    with info_path.open("rb") as handle:
        info = plistlib.load(handle)
    if info.get("CFBundleShortVersionString") != version:
        raise RuntimeError("The app bundle version does not match the requested preview version")

    _run(
        sys.executable,
        str(ROOT / "script" / "macos_bundle_architecture.py"),
        str(app_bundle),
        "--expected",
        architecture,
    )
    _run("codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app_bundle))
    signing = _run("codesign", "-dvv", str(app_bundle))
    signing_details = signing.stdout + signing.stderr
    if "Signature=adhoc" not in signing_details:
        raise RuntimeError("Preview packaging accepts only an explicitly ad hoc signed app")

    output_directory.mkdir(parents=True, exist_ok=True)
    image = output_directory / preview_asset_name(version, architecture)
    checksum = Path(f"{image}.sha256")
    if image.is_symlink() or checksum.is_symlink():
        raise RuntimeError("Preview outputs cannot be symbolic links")

    with tempfile.TemporaryDirectory(prefix="clean-my-codex-dmg-") as temporary:
        staging = Path(temporary) / "Clean My Codex"
        staging.mkdir()
        staged_app = staging / app_bundle.name
        _run("ditto", str(app_bundle), str(staged_app))
        (staging / "Applications").symlink_to("/Applications", target_is_directory=True)
        (staging / "README.txt").write_text(
            "Clean My Codex open-source preview\n\n"
            "Open Clean My Codex.app to use the Codex data stored under the signed-in "
            "macOS account. No separate Clean My Codex, ChatGPT, or API-key sign-in is required.\n\n"
            "This preview is ad hoc signed and not Apple notarized. macOS may require "
            "Control-click > Open for the first launch.\n",
            encoding="utf-8",
        )
        image.unlink(missing_ok=True)
        _run(
            "hdiutil",
            "create",
            "-volname",
            "Clean My Codex",
            "-srcfolder",
            str(staging),
            "-format",
            "UDZO",
            "-ov",
            str(image),
        )

    _run("hdiutil", "verify", str(image))
    digest = _sha256(image)
    checksum.write_text(f"{digest}  {image.name}\n", encoding="ascii")
    return {"image": str(image), "checksum": str(checksum), "sha256": digest}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Package an unsigned macOS preview DMG")
    parser.add_argument("--app", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--arch", required=True, choices=sorted(SUPPORTED_ARCHITECTURES))
    args = parser.parse_args(argv)
    result = package_preview(args.app, args.output_dir, args.version, args.arch)
    print(f"Preview: {result['image']}")
    print(f"SHA-256: {result['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
