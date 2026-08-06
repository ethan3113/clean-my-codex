import tempfile
import unittest
import zipfile
import re
from pathlib import Path

from scripts.audit_release import (
    MAX_PUBLIC_FILE_SIZE,
    _is_identifying_account_name,
    audit_publication_tree,
    audit_tree,
)
from scripts.build_release import ROOT, _capture_stage, _copy_allowlisted, app_version, build_release, load_allowlist


class ReleasePackagingTests(unittest.TestCase):
    def test_generic_ci_accounts_do_not_trigger_identity_scans(self):
        self.assertFalse(_is_identifying_account_name("runner"))
        self.assertFalse(_is_identifying_account_name("ubuntu"))
        self.assertTrue(_is_identifying_account_name("specific-account"))

    def test_allowlist_excludes_every_runtime_area(self):
        entries = {path.as_posix() for path in load_allowlist()}
        forbidden_roots = {
            "backups",
            "codex-archive",
            "operation-logs",
            "purge-backups",
            "trash",
            "trash-bin",
        }
        self.assertFalse({path.split("/", 1)[0] for path in entries}.intersection(forbidden_roots))
        self.assertNotIn("CODEX_DATA_STRUCTURE_REPORT.md", entries)
        self.assertNotIn("CODEX_FOLDER_CLEANLINESS_REPORT.md", entries)
        self.assertIn("clean_my_codex/core.py", entries)
        self.assertIn("LICENSE", entries)

    def test_build_release_contains_only_allowlisted_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = build_release(ROOT, Path(tmp))
            stage = Path(result["stage"])
            expected = {path.as_posix() for path in load_allowlist()}
            actual = {
                path.relative_to(stage).as_posix()
                for path in stage.rglob("*")
                if path.is_file()
            }
            self.assertEqual(actual, expected)
            self.assertTrue(Path(result["archive"]).is_file())
            self.assertTrue(Path(result["checksum"]).is_file())
            with zipfile.ZipFile(result["archive"]) as archive:
                members = {Path(name).relative_to(result["name"]).as_posix() for name in archive.namelist()}
            self.assertEqual(members, expected)

    def test_audit_detects_identifying_paths_and_runtime_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            identifying_path = "/" + "Users" + "/sample/Documents/project"
            email = "name" + "@" + "example.com"
            (root / "notes.txt").write_text(f"{identifying_path}\n{email}\n", encoding="utf-8")
            (root / ("session" + ".jsonl")).write_text("{}\n", encoding="utf-8")
            findings = audit_tree(root)
            reasons = {finding.reason for finding in findings}
            self.assertIn("macOS home path", reasons)
            self.assertIn("email address", reasons)
            self.assertIn("forbidden runtime format: .jsonl", reasons)

    def test_audit_detects_extended_credentials_and_home_path_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = "/" + "Users" + "/sample"
            fine_grained = "github" + "_pat_" + "A" * 40
            aws_key = "AK" + "IA" + "B" * 16
            unquoted = "api" + "_key=" + "sensitivevalue123"
            (root / "notes.txt").write_text(
                "\n".join([home, fine_grained, aws_key, unquoted]),
                encoding="utf-8",
            )
            reasons = {finding.reason for finding in audit_tree(root)}
            self.assertIn("macOS home path", reasons)
            self.assertIn("GitHub fine-grained token", reasons)
            self.assertIn("AWS access key", reasons)
            self.assertIn("unquoted credential assignment", reasons)

    def test_audit_detects_common_service_tokens_and_client_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stripe = "sk_" + "live_" + "A" * 24
            slack = "xox" + "b-" + "1" * 12 + "-" + "A" * 24
            npm_token = "npm" + "_" + "B" * 24
            client_secret = "client" + "_secret=" + "sensitivevalue123"
            (root / "notes.txt").write_text(
                "\n".join([stripe, slack, npm_token, client_secret]),
                encoding="utf-8",
            )
            reasons = {finding.reason for finding in audit_tree(root)}
            self.assertIn("Stripe live secret", reasons)
            self.assertIn("Slack bot token", reasons)
            self.assertIn("npm access token", reasons)
            self.assertIn("unquoted credential assignment", reasons)

    def test_audit_blocks_environment_files_generic_credentials_and_binary_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env.local").write_text("placeholder=true\n", encoding="utf-8")
            database_name = "DATABASE" + "_" + "URL"
            database_value = "post" + "gresql://account:secret@127.0.0.1/app"
            generic_name = "TO" + "KEN"
            generic_value = "example-sensitive-value"
            classic_github = "gh" + "p_" + "A" * 36
            openai_key = "sk" + "-" + "B" * 32
            google_key = "AI" + "za" + "C" * 35
            bearer = "Bear" + "er " + "D" * 32
            (root / "credentials.txt").write_text(
                "\n".join(
                    [
                        f'{database_name}="{database_value}"',
                        f'{generic_name}="{generic_value}"',
                        classic_github,
                        openai_key,
                        google_key,
                        bearer,
                    ]
                ),
                encoding="utf-8",
            )
            (root / "binary.dat").write_bytes(b"safe\x00binary")
            (root / "non-utf8.dat").write_bytes(b"\xff\xfe")
            (root / "oversized.txt").write_bytes(b"x" * (MAX_PUBLIC_FILE_SIZE + 1))

            reasons = {finding.reason for finding in audit_tree(root)}

            self.assertIn("forbidden environment filename", reasons)
            self.assertIn("generic credential assignment", reasons)
            self.assertIn("credential-bearing URL", reasons)
            self.assertIn("GitHub token", reasons)
            self.assertIn("OpenAI-style token", reasons)
            self.assertIn("Google API key", reasons)
            self.assertIn("bearer token", reasons)
            self.assertIn("binary content is not allowlisted", reasons)
            self.assertIn("non-UTF-8 content is not allowlisted", reasons)
            self.assertIn("file exceeds the public size limit", reasons)

    def test_publication_audit_rejects_files_outside_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("safe\n", encoding="utf-8")
            (root / "debug.log").write_text("unexpected\n", encoding="utf-8")
            allowlist = root / "PUBLIC_RELEASE_FILES.txt"
            allowlist.write_text("README.md\nPUBLIC_RELEASE_FILES.txt\n", encoding="utf-8")
            findings = audit_publication_tree(root, allowlist)
            self.assertIn(
                ("debug.log", "file is not listed for public release"),
                {(finding.path, finding.reason) for finding in findings},
            )

    def test_release_version_and_symlink_boundaries_are_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "clean_my_codex"
            package.mkdir()
            (package / "__init__.py").write_text(
                'APP_VERSION = "../../escape"\n',
                encoding="utf-8",
            )
            with self.assertRaises(RuntimeError):
                app_version(root)

            real = root / "real"
            real.mkdir()
            (real / "file.txt").write_text("safe", encoding="utf-8")
            (root / "linked").symlink_to(real, target_is_directory=True)
            stage = root / "stage"
            stage.mkdir()
            with self.assertRaises(RuntimeError):
                _copy_allowlisted(root, stage, [Path("linked/file.txt")])

            allowlist = root / "allowlist.txt"
            allowlist.write_text("..\\..\\payload\n", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_allowlist(allowlist)

    def test_stage_capture_rejects_unlisted_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp)
            (stage / "README.md").write_text("safe\n", encoding="utf-8")
            (stage / "unexpected.txt").write_text("not allowlisted\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "inventory changed"):
                _capture_stage(stage, [Path("README.md")])

    def test_release_builder_refuses_overwrite_and_source_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "release"
            build_release(ROOT, output)
            with self.assertRaises(RuntimeError):
                build_release(ROOT, output)
            rebuilt = build_release(ROOT, output, replace_existing=True)
            self.assertTrue(Path(rebuilt["archive"]).is_file())

            fake_root = Path(tmp) / "clean-my-codex-v0.1.0"
            package = fake_root / "clean_my_codex"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text('APP_VERSION = "0.1.0"\n', encoding="utf-8")
            (fake_root / "PUBLIC_RELEASE_FILES.txt").write_text(
                "PUBLIC_RELEASE_FILES.txt\nclean_my_codex/__init__.py\n",
                encoding="utf-8",
            )
            with self.assertRaises(RuntimeError):
                build_release(fake_root, fake_root.parent)

    def test_github_actions_are_pinned_to_full_commits(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        uses = re.findall(r"^\s*- uses:\s*[^@\s]+@([^\s]+)", workflow, re.MULTILINE)
        self.assertTrue(uses)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{40}", value) for value in uses))
        self.assertIn("--allowlist PUBLIC_RELEASE_FILES.txt", workflow)


if __name__ == "__main__":
    unittest.main()
