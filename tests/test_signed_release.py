import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_asset_verifier():
    path = ROOT / "scripts" / "verify_signed_release_assets.py"
    spec = importlib.util.spec_from_file_location("verify_signed_release_assets", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load signed release asset verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SignedReleaseTests(unittest.TestCase):
    def test_macos_release_script_fails_closed_and_notarizes(self):
        script = (ROOT / "script" / "sign_macos_release.sh").read_text(encoding="utf-8")
        self.assertIn("Developer ID Application:", script)
        self.assertIn("--options runtime --timestamp", script)
        self.assertIn("notarytool submit", script)
        self.assertIn('NOTARY_STATUS" != "Accepted"', script)
        self.assertIn("stapler staple", script)
        self.assertIn("stapler validate", script)
        self.assertIn("spctl --assess --type execute", script)
        self.assertIn("codesign --verify --deep --strict", script)
        self.assertNotIn("codesign --force --deep", script)

    def test_windows_release_script_signs_only_project_owned_files(self):
        script = (ROOT / "script" / "sign_windows_release.ps1").read_text(encoding="utf-8")
        self.assertIn('"Clean My Codex.exe"', script)
        self.assertIn('"Clean My Codex.dll"', script)
        self.assertIn('"server\\clean-my-codex-server.exe"', script)
        self.assertIn("1.3.6.1.5.5.7.3.3", script)
        self.assertIn("/fd SHA256", script)
        self.assertIn("/tr $TimestampUrl", script)
        self.assertIn("/td SHA256", script)
        self.assertIn("verify /pa /all /tw", script)
        self.assertIn("Get-AuthenticodeSignature", script)
        self.assertIn("TimeStamperCertificate", script)
        self.assertNotIn("CertificatePassword", script)
        self.assertNotIn("Get-ChildItem $PackageRoot", script)

    def test_signed_workflow_is_manual_guarded_and_fail_closed(self):
        workflow = (ROOT / ".github" / "workflows" / "signed-release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("pull_request_target:", workflow)
        self.assertNotIn("\n  push:", workflow)
        self.assertNotIn("\n  pull_request:", workflow)
        self.assertIn('GITHUB_REF" != "refs/heads/main"', workflow)
        self.assertIn("environment: release-signing", workflow)
        self.assertIn("default: false", workflow)
        self.assertIn("ref: ${{ github.sha }}", workflow)
        self.assertIn("ref: ${{ inputs.tag }}", workflow)
        self.assertIn("path: source", workflow)
        self.assertEqual(workflow.count("persist-credentials: false"), 6)
        self.assertIn("MACOS_CERTIFICATE_P12_BASE64", workflow)
        self.assertIn("APPLE_API_KEY_P8_BASE64", workflow)
        self.assertIn("WINDOWS_CERTIFICATE_PFX_BASE64", workflow)
        self.assertIn("gh release upload", workflow)
        self.assertIn("gh release edit", workflow)
        self.assertIn("actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c", workflow)

    def test_signed_asset_inventory_and_checksums(self):
        module = load_asset_verifier()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            packages = sorted(
                name for name in module.expected_asset_names("0.3.0") if name.endswith(".zip")
            )
            for index, name in enumerate(packages):
                payload = f"signed-package-{index}".encode("ascii")
                (root / name).write_bytes(payload)
                digest = hashlib.sha256(payload).hexdigest()
                (root / f"{name}.sha256").write_text(
                    f"{digest}  {name}\n", encoding="ascii"
                )

            verified = module.verify_assets(root, "0.3.0")
            self.assertEqual(set(verified), set(packages))

            (root / packages[0]).write_bytes(b"changed")
            with self.assertRaisesRegex(RuntimeError, "Checksum mismatch"):
                module.verify_assets(root, "0.3.0")

    def test_signed_asset_verifier_rejects_extra_files_and_unsafe_versions(self):
        module = load_asset_verifier()
        with self.assertRaises(ValueError):
            module.expected_asset_names("../0.3.0")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "unexpected.txt").write_text("unexpected", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "inventory mismatch"):
                module.verify_assets(root, "0.3.0")

    def test_signing_release_files_are_publicly_allowlisted(self):
        entries = {
            line.strip()
            for line in (ROOT / "PUBLIC_RELEASE_FILES.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        expected = {
            ".github/workflows/signed-release.yml",
            "SIGNING_AND_RELEASE.md",
            "script/sign_macos_release.sh",
            "script/sign_windows_release.ps1",
            "scripts/verify_signed_release_assets.py",
            "tests/test_signed_release.py",
        }
        self.assertTrue(expected.issubset(entries))


if __name__ == "__main__":
    unittest.main()
