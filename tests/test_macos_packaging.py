import importlib.util
import plistlib
import struct
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_icns_module():
    path = ROOT / "script" / "package_icns.py"
    spec = importlib.util.spec_from_file_location("package_icns", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load ICNS packager")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_minimum_version_module():
    path = ROOT / "script" / "macos_bundle_minimum.py"
    spec = importlib.util.spec_from_file_location("macos_bundle_minimum", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load macOS minimum-version validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_architecture_module():
    path = ROOT / "script" / "macos_bundle_architecture.py"
    spec = importlib.util.spec_from_file_location("macos_bundle_architecture", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load macOS architecture validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MacOSPackagingTests(unittest.TestCase):
    def test_native_app_metadata_and_launch_contract(self):
        with (ROOT / "macos" / "Info.plist").open("rb") as handle:
            info = plistlib.load(handle)
        self.assertEqual(info["CFBundleName"], "Clean My Codex")
        self.assertEqual(info["CFBundleExecutable"], "Clean My Codex")
        self.assertEqual(info["CFBundleIdentifier"], "studio.envocs.cleanmycodex")
        self.assertEqual(info["LSMinimumSystemVersion"], "13.0")
        self.assertTrue(info["LSMultipleInstancesProhibited"])

        source = (ROOT / "macos" / "CleanMyCodexApp" / "main.m").read_text(encoding="utf-8")
        self.assertIn('@"--host", CMCLoopbackHost', source)
        self.assertIn('@"--port", @"0"', source)
        self.assertIn("WKWebsiteDataStore.nonPersistentDataStore", source)
        self.assertIn("[self stopServer]", source)
        self.assertIn('CMCShutdownPath = @"/api/lifecycle/shutdown"', source)
        self.assertIn("NSTerminateLater", source)
        self.assertIn("replyToApplicationShouldTerminate:YES", source)
        self.assertIn("replyToApplicationShouldTerminate:NO", source)
        self.assertIn("URLByAppendingPathComponent:@\".codex\"", source)
        self.assertIn('@"github.com"', source)
        self.assertIn('isEqualToString:@"/ethan3113/clean-my-codex"', source)
        self.assertIn('hasPrefix:@"/ethan3113/clean-my-codex/"', source)
        self.assertNotIn('hasPrefix:@"/ethan3113/clean-my-codex"]', source)
        self.assertNotIn('isEqualToString:@"mailto"', source)

    def test_build_dependency_and_signing_are_explicit(self):
        requirements = (ROOT / "requirements-macos-build.txt").read_text(encoding="utf-8")
        self.assertEqual(requirements.strip(), "pyinstaller==6.21.0")

        build = (ROOT / "script" / "build_macos_app.sh").read_text(encoding="utf-8")
        self.assertIn('PYINSTALLER_CONFIG_DIR="$BUILD_ROOT/pyinstaller-config"', build)
        self.assertIn("PYINSTALLER_COPYING.txt", build)
        self.assertIn("PYTHON_LICENSE.txt", build)
        self.assertIn("macos_bundle_minimum.py", build)
        self.assertIn("macos_bundle_architecture.py", build)
        self.assertIn('--target-architecture "$EXPECTED_ARCH"', build)
        self.assertIn('-arch "$EXPECTED_ARCH"', build)
        self.assertIn("codesign --verify --deep --strict", build)

        build_and_run = (ROOT / "script" / "build_and_run.sh").read_text(encoding="utf-8")
        self.assertNotIn("pkill", build_and_run)
        self.assertIn('pgrep -x "Clean My Codex"', build_and_run)

    def test_macos_minimum_version_uses_the_highest_macho_requirement(self):
        module = load_minimum_version_module()
        output = """
            Load command 10
                cmd LC_BUILD_VERSION
                minos 13.0
                tool LD
                version 1266.8
            Load command 11
                cmd LC_VERSION_MIN_MACOSX
                version 10.9
        """
        self.assertEqual(module.parse_minos(output), ["13.0", "10.9"])
        self.assertEqual(module.maximum_version(module.parse_minos(output), "12.0"), "13.0")
        self.assertEqual(module.maximum_version(["11.0"], "13.0"), "13.0")

    def test_macos_minimum_version_fails_closed_for_unreadable_macho(self):
        module = load_minimum_version_module()
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "Test.app"
            bundle.mkdir()
            binary = bundle / "binary"
            binary.write_bytes(b"\xcf\xfa\xed\xfe" + (b"\0" * 32))
            failed = module.subprocess.CompletedProcess(
                args=["vtool"],
                returncode=1,
                stdout="",
                stderr="invalid Mach-O",
            )
            with (
                mock.patch.object(module.shutil, "which", return_value="/usr/bin/vtool"),
                mock.patch.object(module.subprocess, "run", return_value=failed),
            ):
                with self.assertRaisesRegex(RuntimeError, "Unable to inspect Mach-O"):
                    module.bundle_minimum(bundle, "13.0")

    def test_macos_architecture_parser_and_mismatch_fail_closed(self):
        module = load_architecture_module()
        self.assertEqual(module.parse_architectures("arm64\n"), {"arm64"})
        self.assertEqual(
            module.parse_architectures("Architectures in the fat file: app are: x86_64 arm64"),
            {"x86_64", "arm64"},
        )
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "Test.app"
            bundle.mkdir()
            binary = bundle / "binary"
            binary.write_bytes(b"\xcf\xfa\xed\xfe" + (b"\0" * 32))
            result = module.subprocess.CompletedProcess(
                args=["lipo"], returncode=0, stdout="x86_64\n", stderr=""
            )
            with (
                mock.patch.object(module.shutil, "which", return_value="/usr/bin/lipo"),
                mock.patch.object(module.subprocess, "run", return_value=result),
            ):
                with self.assertRaisesRegex(RuntimeError, "architecture mismatch"):
                    module.validate_bundle_architecture(bundle, "arm64")

    def test_icns_packager_writes_standard_container(self):
        module = load_icns_module()
        png = b"\x89PNG\r\n\x1a\nsynthetic-test-payload"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            iconset = root / "AppIcon.iconset"
            iconset.mkdir()
            for _, filename in module.ICON_CHUNKS:
                (iconset / filename).write_bytes(png)
            output = root / "AppIcon.icns"

            module.package_iconset(iconset, output)

            payload = output.read_bytes()
            self.assertEqual(payload[:4], b"icns")
            self.assertEqual(struct.unpack(">I", payload[4:8])[0], len(payload))
            self.assertEqual(payload.count(png), len(module.ICON_CHUNKS))

    def test_macos_sources_are_in_the_public_allowlist(self):
        entries = {
            line.strip()
            for line in (ROOT / "PUBLIC_RELEASE_FILES.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        expected = {
            "requirements-macos-build.txt",
            "macos/AppIconGenerator.m",
            "macos/CleanMyCodexApp/main.m",
            "macos/Info.plist",
            "script/build_and_run.sh",
            "script/build_macos_app.sh",
            "script/macos_bundle_architecture.py",
            "script/macos_bundle_minimum.py",
            "script/package_icns.py",
            "tests/test_macos_packaging.py",
        }
        self.assertTrue(expected.issubset(entries))


if __name__ == "__main__":
    unittest.main()
