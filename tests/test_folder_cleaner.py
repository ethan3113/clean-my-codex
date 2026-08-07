import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from clean_my_codex.core import CodexStore, create_state_db


class CodexFolderCleanerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.codex_home = self.root / ".codex"
        self.app_home = self.root / "app"
        self.codex_home.mkdir()
        (self.codex_home / "sqlite").mkdir()

        (self.codex_home / "config.toml").write_text("active = true\n", encoding="utf-8")
        (self.codex_home / "config.toml.before-path-relocation-20260614-221215").write_text(
            "active = false\n",
            encoding="utf-8",
        )

        create_state_db(self.codex_home / "state_5.sqlite")
        create_state_db(self.codex_home / "sqlite" / "state_5.sqlite")
        (self.codex_home / "state_5.sqlite.before-cwd-update-20260614").write_bytes(
            b"backup database"
        )
        (self.codex_home / "state_5.sqlite.before-path-relocation-20260614-221215").write_bytes(
            b"backup database"
        )
        (self.codex_home / "scratch.tmp").write_text("temp", encoding="utf-8")
        old_timestamp = time.time() - (48 * 60 * 60)
        os.utime(self.codex_home / "scratch.tmp", (old_timestamp, old_timestamp))
        (self.codex_home / "recent.tmp").write_text("still active", encoding="utf-8")
        (self.codex_home / "unknown.data").write_text("unknown", encoding="utf-8")
        (self.codex_home / "auth.json.bak").write_text("credential backup", encoding="utf-8")

        self.store = CodexStore(self.codex_home, self.app_home)

    def tearDown(self):
        self.tmp.cleanup()

    def _by_name(self):
        return {Path(item["path"]).name: item for item in self.store.scan_codex_folder()["items"]}

    def test_folder_cleaner_classifies_active_and_backup_files(self):
        items = self._by_name()

        self.assertEqual(items["config.toml"]["risk_level"], "active")
        self.assertFalse(items["config.toml"]["safe_to_archive"])
        self.assertEqual(
            items["config.toml.before-path-relocation-20260614-221215"]["risk_level"],
            "stale",
        )
        self.assertTrue(
            items["config.toml.before-path-relocation-20260614-221215"]["looks_like_backup"]
        )
        self.assertEqual(
            items["config.toml.before-path-relocation-20260614-221215"]["probable_original"],
            str(self.codex_home / "config.toml"),
        )
        self.assertIn(
            "path relocation",
            items["config.toml.before-path-relocation-20260614-221215"]["explanation"],
        )

    def test_folder_cleaner_classifies_sqlite_backup_and_temp_files(self):
        items = self._by_name()

        backup = items["state_5.sqlite.before-cwd-update-20260614"]
        self.assertEqual(backup["file_type"], "sqlite-backup")
        self.assertTrue(backup["looks_like_database_copy"])
        self.assertTrue(backup["safe_to_archive"])

        tmp = items["scratch.tmp"]
        self.assertEqual(tmp["risk_level"], "stale")
        self.assertTrue(tmp["looks_like_temporary"])

        recent = items["recent.tmp"]
        self.assertEqual(recent["risk_level"], "unknown")
        self.assertFalse(recent["safe_to_archive"])

        unknown = items["unknown.data"]
        self.assertEqual(unknown["risk_level"], "unknown")
        self.assertFalse(unknown["safe_to_archive"])

        auth_backup = items["auth.json.bak"]
        self.assertEqual(auth_backup["risk_level"], "danger")
        self.assertTrue(auth_backup["contains_sensitive_auth_data"])
        self.assertFalse(auth_backup["safe_to_archive"])

    def test_archive_dry_run_preserves_relative_paths_and_requires_confirmation(self):
        target = str(self.codex_home / "config.toml.before-path-relocation-20260614-221215")
        preview = self.store.preview_archive_codex_files([target])

        self.assertEqual(preview["status"], "preview_only")
        self.assertEqual(preview["total_files"], 1)
        self.assertIn("/.codex/config.toml.before-path-relocation-20260614-221215", preview["files"][0]["destination"])

        blocked = self.store.archive_codex_files([target], confirmation="")
        self.assertEqual(blocked["status"], "confirmation_required")
        self.assertTrue(Path(target).exists())

    def test_folder_cleaner_moves_safe_candidates_to_unified_trash_bin(self):
        target = str(self.codex_home / "config.toml.before-path-relocation-20260614-221215")
        result = self.store.move_codex_files_to_trash_bin(
            [target],
            confirmation="MOVE CODEX FILES TO TRASH",
        )

        self.assertEqual(result["status"], "trashed")
        self.assertFalse(Path(target).exists())
        trash_path = Path(result["moved_files"][0]["trash"])
        self.assertTrue(trash_path.exists())
        self.assertTrue(str(trash_path).startswith(str((self.app_home / "trash-bin").resolve())))
        self.assertTrue((self.app_home / "operation-logs" / "operations.jsonl").exists())

    def test_folder_cleaner_blocks_active_and_auth_files(self):
        target = str(self.codex_home / "config.toml")
        auth_backup = str(self.codex_home / "auth.json.bak")
        result = self.store.move_codex_files_to_trash_bin(
            [target, auth_backup],
            confirmation="MOVE CODEX FILES TO TRASH",
        )

        self.assertEqual(result["status"], "blocked")
        self.assertTrue(Path(target).exists())
        self.assertTrue(Path(auth_backup).exists())

    def test_folder_cleaner_trash_restore_round_trip(self):
        target = str(self.codex_home / "config.toml.before-path-relocation-20260614-221215")
        trashed = self.store.move_codex_files_to_trash_bin(
            [target],
            confirmation="MOVE CODEX FILES TO TRASH",
        )
        item_id = trashed["item_id"]

        preview = self.store.preview_restore_trash_bin_item(item_id)
        self.assertTrue(preview["safe_to_restore_metadata"])
        self.assertFalse(preview["conflicts"])

        restored = self.store.restore_trash_bin_item(
            item_id,
            confirmation="RESTORE FROM TRASH",
        )
        self.assertEqual(restored["status"], "restored")
        self.assertTrue(Path(target).exists())

    def test_permanent_delete_only_removes_unified_trash_item(self):
        target = str(self.codex_home / "config.toml.before-path-relocation-20260614-221215")
        trashed = self.store.move_codex_files_to_trash_bin(
            [target],
            confirmation="MOVE CODEX FILES TO TRASH",
        )
        item_id = trashed["item_id"]
        item_dir = Path(trashed["trash_dir"])

        blocked = self.store.permanently_delete_trash_bin_item(
            item_id,
            confirmation="",
        )
        self.assertEqual(blocked["status"], "confirmation_required")

        deleted = self.store.permanently_delete_trash_bin_item(
            item_id,
            confirmation="PERMANENT DELETE",
        )
        self.assertEqual(deleted["status"], "permanently_deleted")
        self.assertFalse(item_dir.exists())

    def test_folder_cleaner_rejects_symbolic_links_and_outside_paths(self):
        outside = self.root / "outside.tmp"
        outside.write_text("keep", encoding="utf-8")
        link = self.codex_home / "linked.tmp"
        try:
            link.symlink_to(outside)
        except (NotImplementedError, OSError):
            self.skipTest("Symbolic links are unavailable for this account")
        preview = self.store.preview_move_codex_files_to_trash_bin([str(link), str(outside)])
        self.assertEqual(len(preview["unsafe_files"]), 2)
        blocked = self.store.move_codex_files_to_trash_bin(
            [str(link)],
            confirmation="MOVE CODEX FILES TO TRASH",
        )
        self.assertEqual(blocked["status"], "blocked")
        self.assertTrue(outside.exists())

    def test_cleanliness_report_is_generated(self):
        report = self.store.generate_codex_cleanliness_report()
        report_path = Path(report["report_path"])

        self.assertTrue(report_path.exists())
        text = report_path.read_text(encoding="utf-8")
        self.assertIn("Codex Folder Cleanliness Report", text)
        self.assertIn("config.toml.before-path-relocation-20260614-221215", text)


if __name__ == "__main__":
    unittest.main()
