import json
import importlib.util
import struct
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]


def load_icon_module():
    path = ROOT / "script" / "package_windows_icon.py"
    spec = importlib.util.spec_from_file_location("package_windows_icon", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load Windows icon packager")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WindowsPackagingTests(unittest.TestCase):
    def test_wpf_project_is_pinned_and_targets_windows_x64(self):
        project = ROOT / "windows" / "CleanMyCodexApp" / "CleanMyCodexApp.csproj"
        root = ElementTree.parse(project).getroot()
        values = {element.tag: (element.text or "") for element in root.iter()}
        self.assertEqual(values["TargetFramework"], "net8.0-windows10.0.17763.0")
        self.assertEqual(values["RuntimeIdentifier"], "win-x64")
        self.assertEqual(values["PlatformTarget"], "x64")
        package = root.find(".//PackageReference")
        self.assertIsNotNone(package)
        self.assertEqual(package.attrib["Include"], "Microsoft.Web.WebView2")
        self.assertEqual(package.attrib["Version"], "1.0.4078.44")

    def test_windows_shell_uses_loopback_capability_and_graceful_shutdown(self):
        session = (ROOT / "windows" / "CleanMyCodexApp" / "ServerSession.cs").read_text(
            encoding="utf-8"
        )
        window = (ROOT / "windows" / "CleanMyCodexApp" / "MainWindow.xaml.cs").read_text(
            encoding="utf-8"
        )
        self.assertIn('"--host", "127.0.0.1", "--port", "0"', session)
        self.assertIn("using System.IO;", session)
        self.assertIn("using System.IO;", window)
        self.assertIn('"X-Clean-My-Codex-Token"', session)
        self.assertIn('"/api/lifecycle/shutdown"', session)
        self.assertIn("safe_to_terminate", session)
        self.assertIn("RandomNumberGenerator.GetBytes(32)", session)
        self.assertIn("CancellationToken cancellationToken", session)
        self.assertIn('Path.Combine(profile, ".codex")', session)
        self.assertIn("CoreWebView2Environment.CreateAsync", window)
        self.assertIn("_startupCancellation.Cancel()", window)
        self.assertIn("await _startupTask", window)
        self.assertIn("args.State = CoreWebView2PermissionState.Deny", window)
        self.assertIn('path.StartsWith("/ethan3113/clean-my-codex/"', window)

    def test_windows_build_creates_a_self_contained_smoke_tested_package(self):
        build = (ROOT / "script" / "build_windows_app.ps1").read_text(encoding="utf-8")
        self.assertIn("--self-contained true", build)
        self.assertIn("dotnet restore $Project --runtime win-x64", build)
        self.assertIn("requirements-windows-build.txt", build)
        self.assertIn("--smoke-test", build)
        self.assertIn("WEBVIEW2_LICENSE.txt", build)
        self.assertIn("cpython_runtime_license.py", build)
        self.assertIn("Compress-Archive", build)

        sdk = json.loads((ROOT / "global.json").read_text(encoding="utf-8"))["sdk"]
        self.assertEqual(sdk["version"], "8.0.100")
        self.assertEqual(sdk["rollForward"], "latestFeature")
        self.assertFalse(sdk["allowPrerelease"])

    def test_generated_windows_icon_is_a_valid_png_backed_ico(self):
        module = load_icon_module()
        png = module.png_payload(module.render_rgba())
        payload = module.icon_payload(png)
        self.assertEqual(payload[:6], struct.pack("<HHH", 0, 1, 1))
        self.assertEqual(payload[22:30], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack("<I", payload[14:18])[0], len(png))

    def test_windows_sources_are_in_the_public_allowlist(self):
        entries = {
            line.strip()
            for line in (ROOT / "PUBLIC_RELEASE_FILES.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        expected = {
            "requirements-windows-build.txt",
            "global.json",
            "script/build_windows_app.ps1",
            "script/package_windows_icon.py",
            "windows/CleanMyCodexApp/App.xaml",
            "windows/CleanMyCodexApp/App.xaml.cs",
            "windows/CleanMyCodexApp/CleanMyCodexApp.csproj",
            "windows/CleanMyCodexApp/MainWindow.xaml",
            "windows/CleanMyCodexApp/MainWindow.xaml.cs",
            "windows/CleanMyCodexApp/ServerSession.cs",
            "tests/test_windows_packaging.py",
        }
        self.assertTrue(expected.issubset(entries))


if __name__ == "__main__":
    unittest.main()
