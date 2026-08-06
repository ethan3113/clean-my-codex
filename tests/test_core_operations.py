import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from clean_my_codex.core import (
    CodexStore,
    atomic_write_text,
    create_state_db,
    managed_item_path,
)


OLD_PATH = "/workspaces/example/old-folder"
NEW_PATH = "/workspaces/example/new-folder"


class CleanMyCodexCoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.codex_home = self.root / ".codex"
        self.app_home = self.root / "app"
        self.codex_home.mkdir()
        (self.codex_home / "sqlite").mkdir()
        (self.codex_home / "sessions" / "2026" / "06" / "14").mkdir(parents=True)

        self.session_path = (
            self.codex_home
            / "sessions"
            / "2026"
            / "06"
            / "14"
            / "rollout-2026-06-14T10-00-00-thread-1.jsonl"
        )
        self.session_path.write_text(
            json.dumps(
                {
                    "timestamp": "2026-06-14T10:00:00Z",
                    "type": "session_meta",
                    "payload": {"id": "thread-1", "cwd": OLD_PATH},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self.sidecar_path = self.session_path.with_name(self.session_path.name + ".meta.json")
        self.sidecar_path.write_text(
            json.dumps({"thread_id": "thread-1", "kind": "sidecar"}),
            encoding="utf-8",
        )

        for db_path in [
            self.codex_home / "state_5.sqlite",
            self.codex_home / "sqlite" / "state_5.sqlite",
        ]:
            create_state_db(db_path)
            con = sqlite3.connect(db_path)
            con.execute(
                """
                INSERT INTO threads (
                    id, rollout_path, created_at, updated_at, source, model_provider,
                    cwd, title, sandbox_policy, approval_mode, tokens_used,
                    has_user_event, archived, first_user_message, preview
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "thread-1",
                    str(self.session_path),
                    1000,
                    2000,
                    "app",
                    "openai",
                    OLD_PATH,
                    "Old Project Chat",
                    "{}",
                    "never",
                    123,
                    1,
                    0,
                    "first message",
                    "preview text",
                ),
            )
            con.execute(
                """
                INSERT INTO thread_dynamic_tools (
                    thread_id, position, name, description, input_schema
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                ("thread-1", 0, "fixture_tool", "fixture", "{}"),
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS thread_spawn_edges (
                    parent_thread_id TEXT NOT NULL,
                    child_thread_id TEXT NOT NULL,
                    status TEXT NOT NULL
                )
                """
            )
            con.execute(
                """
                INSERT INTO thread_spawn_edges (parent_thread_id, child_thread_id, status)
                VALUES (?, ?, ?)
                """,
                ("thread-1", "child-thread", "done"),
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_job_items (
                    job_id TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    assigned_thread_id TEXT,
                    status TEXT NOT NULL
                )
                """
            )
            con.execute(
                """
                INSERT INTO agent_job_items (job_id, item_id, assigned_thread_id, status)
                VALUES (?, ?, ?, ?)
                """,
                ("job-1", "item-1", "thread-1", "done"),
            )
            con.commit()
            con.close()

        (self.codex_home / "config.toml").write_text(
            f'[projects."{OLD_PATH}"]\ntrust_level = "trusted"\n',
            encoding="utf-8",
        )
        (self.codex_home / ".codex-global-state.json").write_text(
            json.dumps(
                {
                    "electron-saved-workspace-roots": [OLD_PATH],
                    "project-order": [OLD_PATH],
                    "active-workspace-roots": [OLD_PATH],
                    "electron-workspace-root-labels": {OLD_PATH: "old-folder"},
                    "projectless-thread-ids": ["thread-1", "thread-keep"],
                    "thread-workspace-root-hints": {
                        "thread-1": "/tmp/old",
                        "thread-keep": "/tmp/keep",
                    },
                    "thread-projectless-output-directories": {
                        "thread-1": "/tmp/old/outputs",
                        "thread-keep": "/tmp/keep/outputs",
                    },
                    "electron-persisted-atom-state": {
                        "prompt-history": {
                            "thread-1": [f"historical {OLD_PATH}"],
                            "thread-keep": ["keep me", "mentions thread-1 but is unrelated"],
                        },
                        "heartbeat-thread-permissions-by-id": {
                            "thread-1": {"approvalPolicy": "never"},
                            "thread-keep": {"approvalPolicy": "never"},
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        (self.codex_home / "session_index.jsonl").write_text(
            json.dumps(
                {
                    "id": "thread-1",
                    "thread_name": "Indexed Old Project Chat",
                    "updated_at": "2026-06-14T10:00:00Z",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        with (self.codex_home / "session_index.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "id": "thread-keep",
                        "thread_name": "Keep Chat",
                        "updated_at": "2026-06-14T11:00:00Z",
                    }
                )
                + "\n"
            )

        self.keep_session_path = (
            self.codex_home
            / "sessions"
            / "2026"
            / "06"
            / "14"
            / "rollout-2026-06-14T11-00-00-thread-keep.jsonl"
        )
        self.keep_session_path.write_text(
            json.dumps(
                {
                    "timestamp": "2026-06-14T11:00:00Z",
                    "type": "session_meta",
                    "payload": {"id": "thread-keep", "cwd": NEW_PATH},
                }
            )
            + "\n",
            encoding="utf-8",
        )

        self.store = CodexStore(self.codex_home, self.app_home)

    def tearDown(self):
        self.tmp.cleanup()

    def test_managed_item_path_rejects_directory_traversal(self):
        root = self.app_home / "trash-bin"
        root.mkdir(parents=True, exist_ok=True)
        self.assertEqual(managed_item_path(root, "item-1", "item"), root / "item-1")
        for value in ["", ".", "..", "../outside", "nested/item"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                managed_item_path(root, value, "item")

    def _add_thread(self, thread_id: str, cwd: str, title: str) -> Path:
        session_path = (
            self.codex_home
            / "sessions"
            / "2026"
            / "06"
            / "14"
            / f"rollout-2026-06-14T12-00-00-{thread_id}.jsonl"
        )
        session_path.write_text(
            json.dumps(
                {
                    "timestamp": "2026-06-14T12:00:00Z",
                    "type": "session_meta",
                    "payload": {"id": thread_id, "cwd": cwd},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        for db_path in [
            self.codex_home / "state_5.sqlite",
            self.codex_home / "sqlite" / "state_5.sqlite",
        ]:
            con = sqlite3.connect(db_path)
            con.execute(
                """
                INSERT INTO threads (
                    id, rollout_path, created_at, updated_at, source, model_provider,
                    cwd, title, sandbox_policy, approval_mode, tokens_used,
                    has_user_event, archived, first_user_message, preview
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    str(session_path),
                    1200,
                    2200,
                    "app",
                    "openai",
                    cwd,
                    title,
                    "{}",
                    "never",
                    456,
                    1,
                    0,
                    "another first message",
                    "another preview",
                ),
            )
            con.commit()
            con.close()
        with (self.codex_home / "session_index.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "id": thread_id,
                        "thread_name": title,
                        "updated_at": "2026-06-14T12:00:00Z",
                    }
                )
                + "\n"
            )
        return session_path

    def test_scan_chats_reads_thread_metadata_without_raw_body(self):
        chats = self.store.scan_chats()
        self.assertEqual(len(chats), 1)
        self.assertEqual(chats[0]["id"], "thread-1")
        self.assertEqual(chats[0]["title"], "Old Project Chat")
        self.assertEqual(chats[0]["indexed_title"], "Indexed Old Project Chat")
        self.assertEqual(chats[0]["display_title"], "Indexed Old Project Chat")
        self.assertEqual(chats[0]["cwd"], OLD_PATH)
        self.assertTrue(chats[0]["session_exists"])

    def test_relocation_preview_reports_exact_active_targets_only(self):
        preview = self.store.preview_relocation(OLD_PATH, NEW_PATH)
        self.assertEqual(preview["old_path"], OLD_PATH)
        self.assertEqual(preview["new_path"], NEW_PATH)
        self.assertEqual(preview["sqlite_updates"]["state_5.sqlite"], 1)
        self.assertEqual(preview["sqlite_updates"]["sqlite/state_5.sqlite"], 1)
        self.assertEqual(preview["config_toml_matches"], 1)
        self.assertEqual(preview["global_state_matches"]["active-workspace-roots"], 1)
        self.assertEqual(preview["historical_references_not_modified"], True)

    def test_relocation_preserves_prefix_siblings_and_rejects_relative_paths(self):
        sibling = f"{OLD_PATH}-archive"
        with (self.codex_home / "config.toml").open("a", encoding="utf-8") as handle:
            handle.write(f'\n[projects."{sibling}"]\ntrust_level = "trusted"\n')
            handle.write(f'# historical note: {OLD_PATH}\n')

        preview = self.store.preview_relocation(OLD_PATH, NEW_PATH)
        self.assertEqual(preview["config_toml_matches"], 1)
        self.store.apply_relocation(OLD_PATH, NEW_PATH, confirm=True)
        config_text = (self.codex_home / "config.toml").read_text(encoding="utf-8")
        self.assertIn(f'[projects."{sibling}"]', config_text)
        self.assertIn(f'# historical note: {OLD_PATH}', config_text)

        with self.assertRaises(ValueError):
            self.store.preview_relocation("relative/old", NEW_PATH)

    def test_relocation_rejects_unsafe_toml_path_characters(self):
        for value in [f'{NEW_PATH}"quoted', f"{NEW_PATH}\\escaped", f"{NEW_PATH}\nline"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.preview_relocation(OLD_PATH, value)

    def test_relocation_does_not_create_a_workspace_label_without_a_source_label(self):
        state_path = self.codex_home / ".codex-global-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["electron-workspace-root-labels"] = {}
        state_path.write_text(json.dumps(state), encoding="utf-8")

        preview = self.store.preview_relocation(OLD_PATH, NEW_PATH)
        result = self.store.apply_relocation(OLD_PATH, NEW_PATH, confirm=True, preview=preview)

        self.assertEqual(result["status"], "applied")
        updated = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertNotIn(NEW_PATH, updated["electron-workspace-root-labels"])

    def test_relocation_blocks_existing_destination_metadata(self):
        self._add_thread("thread-destination", NEW_PATH, "Existing Destination")
        preview = self.store.preview_relocation(OLD_PATH, NEW_PATH)
        self.assertGreater(preview["destination_reference_count"], 0)
        self.assertFalse(preview["safe_to_apply"])

        result = self.store.apply_relocation(
            OLD_PATH,
            NEW_PATH,
            confirm=True,
            preview=preview,
        )
        self.assertEqual(result["status"], "blocked")
        self.assertIn(
            OLD_PATH,
            (self.codex_home / "config.toml").read_text(encoding="utf-8"),
        )

    def test_relocation_rolls_back_all_active_files_on_late_failure(self):
        preview = self.store.preview_relocation(OLD_PATH, NEW_PATH)
        original_update = self.store._update_thread_cwd

        def fail_second_database(
            db_path,
            old_path,
            new_path,
            expected_digest,
            target_digest=None,
        ):
            if db_path.parent.name == "sqlite" and target_digest is None:
                raise RuntimeError("forced second database failure")
            return original_update(
                db_path,
                old_path,
                new_path,
                expected_digest,
                target_digest=target_digest,
            )

        self.store._update_thread_cwd = fail_second_database
        with self.assertRaisesRegex(RuntimeError, "changes were rolled back"):
            self.store.apply_relocation(
                OLD_PATH,
                NEW_PATH,
                confirm=True,
                preview=preview,
            )

        self.assertIn(
            OLD_PATH,
            (self.codex_home / "config.toml").read_text(encoding="utf-8"),
        )
        state = json.loads(
            (self.codex_home / ".codex-global-state.json").read_text(encoding="utf-8")
        )
        self.assertIn(OLD_PATH, state["active-workspace-roots"])
        for db_path in self.store.state_db_paths():
            con = sqlite3.connect(db_path)
            cwd = con.execute("SELECT cwd FROM threads WHERE id='thread-1'").fetchone()[0]
            con.close()
            self.assertEqual(cwd, OLD_PATH)

    def test_apply_relocation_creates_backup_and_updates_active_state(self):
        result = self.store.apply_relocation(OLD_PATH, NEW_PATH, confirm=True)
        self.assertEqual(result["status"], "applied")
        self.assertTrue(Path(result["backup_dir"]).exists())
        self.assertTrue((self.app_home / "operation-logs" / "operations.jsonl").exists())
        self.assertEqual(self.store.list_logs()[0]["status"], "applied")

        config_text = (self.codex_home / "config.toml").read_text(encoding="utf-8")
        self.assertIn(NEW_PATH, config_text)
        self.assertNotIn(OLD_PATH, config_text)

        state = json.loads(
            (self.codex_home / ".codex-global-state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state["active-workspace-roots"], [NEW_PATH])
        self.assertEqual(state["electron-workspace-root-labels"][NEW_PATH], "new-folder")
        self.assertNotIn(OLD_PATH, state["electron-workspace-root-labels"])
        self.assertIn(OLD_PATH, state["electron-persisted-atom-state"]["prompt-history"]["thread-1"][0])

        for db_path in [
            self.codex_home / "state_5.sqlite",
            self.codex_home / "sqlite" / "state_5.sqlite",
        ]:
            con = sqlite3.connect(db_path)
            cwd = con.execute("SELECT cwd FROM threads WHERE id='thread-1'").fetchone()[0]
            con.close()
            self.assertEqual(cwd, NEW_PATH)

    def test_legacy_trash_apply_is_disabled(self):
        trash = self.store.move_chats_to_trash(["thread-1"], confirm=True)
        self.assertEqual(trash["status"], "manual-review-needed")
        self.assertTrue(self.session_path.exists())

        con = sqlite3.connect(self.codex_home / "state_5.sqlite")
        archived = con.execute("SELECT archived FROM threads WHERE id='thread-1'").fetchone()[0]
        con.close()
        self.assertEqual(archived, 0)

    def test_clean_trash_preview_detects_related_files_and_metadata_references(self):
        preview = self.store.preview_clean_trash(["thread-1"])

        self.assertEqual(preview["operation"], "clean_move_to_trash")
        self.assertTrue(preview["dry_run"])
        self.assertEqual(preview["thread_ids"], ["thread-1"])
        self.assertEqual(preview["cleanup_confidence"], "safe")
        self.assertIn(str(self.session_path), preview["files_to_move"])
        self.assertIn(str(self.sidecar_path), preview["files_to_move"])
        self.assertEqual(preview["session_index_lines_to_remove"], [1])
        self.assertIn("projectless-thread-ids[0]", preview["global_state_paths_to_remove"])
        self.assertEqual(preview["sqlite"]["state_5.sqlite"]["rows_to_delete"]["threads"], 1)
        self.assertEqual(
            preview["sqlite"]["state_5.sqlite"]["rows_to_delete"]["thread_dynamic_tools"],
            1,
        )
        self.assertIn("restore", preview["restore_plan"].lower())

    def test_related_file_detection_rejects_superstrings_and_non_session_files(self):
        superstring = self.session_path.with_name(
            "rollout-2026-06-14T10-00-00-thread-10.jsonl"
        )
        superstring.write_text("keep", encoding="utf-8")
        unrelated_root_file = self.codex_home / "notes-thread-1.txt"
        unrelated_root_file.write_text("keep", encoding="utf-8")

        preview = self.store.preview_delete_chat_to_trash_bin(["thread-1"])

        self.assertNotIn(str(superstring), preview["files_to_move"])
        self.assertNotIn(str(unrelated_root_file), preview["files_to_move"])
        result = self.store.delete_chat_to_trash_bin(
            ["thread-1"],
            confirmation="DELETE CHAT",
            preview=preview,
        )
        self.assertEqual(result["status"], "trashed")
        self.assertTrue(superstring.exists())
        self.assertTrue(unrelated_root_file.exists())

    def test_clean_trash_apply_requires_exact_confirmation(self):
        result = self.store.clean_move_chats_to_trash(["thread-1"], confirmation="")

        self.assertEqual(result["status"], "confirmation_required")
        self.assertTrue(self.session_path.exists())
        con = sqlite3.connect(self.codex_home / "state_5.sqlite")
        row_count = con.execute("SELECT COUNT(*) FROM threads WHERE id='thread-1'").fetchone()[0]
        con.close()
        self.assertEqual(row_count, 1)

    def test_legacy_clean_trash_apply_is_disabled(self):
        result = self.store.clean_move_chats_to_trash(
            ["thread-1"],
            confirmation="CLEAN MOVE TO TRASH",
        )

        self.assertEqual(result["status"], "manual-review-needed")
        self.assertTrue(self.session_path.exists())
        self.assertTrue(self.sidecar_path.exists())
        con = sqlite3.connect(self.codex_home / "state_5.sqlite")
        active_count = con.execute(
            "SELECT COUNT(*) FROM threads WHERE id='thread-1'"
        ).fetchone()[0]
        con.close()
        self.assertEqual(active_count, 1)

    def test_clean_trash_marks_unknown_sqlite_thread_references_for_manual_review(self):
        db_path = self.codex_home / "state_5.sqlite"
        con = sqlite3.connect(db_path)
        con.execute(
            "CREATE TABLE thread_notes (id TEXT PRIMARY KEY, thread_reference TEXT NOT NULL)"
        )
        con.execute(
            "INSERT INTO thread_notes (id, thread_reference) VALUES (?, ?)",
            ("note-1", "thread-1"),
        )
        con.commit()
        con.close()

        preview = self.store.preview_clean_trash(["thread-1"])
        self.assertEqual(preview["cleanup_confidence"], "manual-review")
        self.assertIn("state_5.sqlite:thread_notes", preview["unsafe_or_unknown_references"])

        result = self.store.clean_move_chats_to_trash(
            ["thread-1"],
            confirmation="CLEAN MOVE TO TRASH",
        )
        self.assertEqual(result["status"], "manual-review-needed")
        con = sqlite3.connect(db_path)
        untouched = con.execute(
            "SELECT COUNT(*) FROM thread_notes WHERE thread_reference='thread-1'"
        ).fetchone()[0]
        con.close()
        self.assertEqual(untouched, 1)

    def test_ambiguous_sqlite_thread_column_is_manual_review_only(self):
        db_path = self.codex_home / "state_5.sqlite"
        con = sqlite3.connect(db_path)
        con.execute("CREATE TABLE ambiguous_links (id TEXT PRIMARY KEY, thread TEXT NOT NULL)")
        con.execute("INSERT INTO ambiguous_links VALUES (?, ?)", ("link-1", "thread-1"))
        con.commit()
        con.close()

        preview = self.store.preview_delete_chat_to_trash_bin(["thread-1"])

        self.assertFalse(preview["safe_to_delete"])
        self.assertIn(
            "ambiguous_links",
            preview["sqlite"]["state_5.sqlite"]["manual_review_tables"],
        )

    def test_missing_path_scan_reports_related_chats_and_metadata(self):
        scan = self.store.scan_missing_paths()
        row = next(item for item in scan["missing_paths"] if item["path"] == OLD_PATH)

        self.assertEqual(row["label"], "old-folder")
        self.assertEqual(row["chat_count"], 1)
        self.assertEqual(row["thread_ids"], ["thread-1"])
        self.assertIn(str(self.session_path), row["session_files"])
        self.assertIn("state_5.sqlite", row["sqlite"])
        self.assertEqual(row["sqlite"]["state_5.sqlite"]["rows_to_delete"]["threads"], 1)
        self.assertEqual(row["session_index_lines_to_remove"], [1])
        self.assertTrue(row["unrecoverable"])
        self.assertTrue(row["safe_to_delete"])

    def test_delete_chat_to_trash_bin_requires_confirmation(self):
        result = self.store.delete_chat_to_trash_bin(["thread-1"], confirmation="")

        self.assertEqual(result["status"], "confirmation_required")
        self.assertTrue(self.session_path.exists())
        self.assertEqual(self.store.list_trash_bin(), [])
        con = sqlite3.connect(self.codex_home / "state_5.sqlite")
        row_count = con.execute("SELECT COUNT(*) FROM threads WHERE id='thread-1'").fetchone()[0]
        con.close()
        self.assertEqual(row_count, 1)

    def test_delete_chat_to_trash_bin_moves_metadata_and_restores(self):
        os.chmod(self.session_path, 0o644)
        result = self.store.delete_chat_to_trash_bin(["thread-1"], confirmation="DELETE CHAT")

        self.assertEqual(result["status"], "trashed")
        self.assertFalse(self.session_path.exists())
        self.assertFalse(self.sidecar_path.exists())
        trash_dir = Path(result["trash_dir"])
        self.assertEqual(trash_dir.parent, self.app_home / "trash-bin")
        self.assertTrue((trash_dir / "manifest.json").exists())
        self.assertTrue((trash_dir / "RESTORE_REPORT.md").exists())
        self.assertTrue((trash_dir / "sqlite_rows.json").exists())
        self.assertTrue(
            all(
                path.stat().st_mode & 0o777 == 0o600
                for path in trash_dir.rglob("*")
                if path.is_file()
            )
        )
        manifest = json.loads((trash_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["item_type"], "chat")
        self.assertEqual(manifest["confirmation_required"], "RESTORE FROM TRASH")
        self.assertIn("thread-1", manifest["thread_ids"])

        for db_path in [
            self.codex_home / "state_5.sqlite",
            self.codex_home / "sqlite" / "state_5.sqlite",
        ]:
            con = sqlite3.connect(db_path)
            thread_count = con.execute(
                "SELECT COUNT(*) FROM threads WHERE id='thread-1'"
            ).fetchone()[0]
            con.close()
            self.assertEqual(thread_count, 0)
        self.assertNotIn(
            "thread-1",
            (self.codex_home / "session_index.jsonl").read_text(encoding="utf-8"),
        )
        self.assertEqual(result["verification"]["result"], "clean")
        self.assertEqual(self.store.list_trash_bin()[0]["item_id"], result["item_id"])

        restored = self.store.restore_trash_bin_item(
            result["item_id"],
            confirmation="RESTORE FROM TRASH",
        )
        self.assertEqual(restored["status"], "restored")
        self.assertIn("sqlite-rows", {row["kind"] for row in restored["restored_metadata"]})
        self.assertTrue(self.session_path.exists())
        self.assertEqual(self.session_path.stat().st_mode & 0o777, 0o644)
        self.assertTrue(self.sidecar_path.exists())
        con = sqlite3.connect(self.codex_home / "state_5.sqlite")
        restored_count = con.execute(
            "SELECT COUNT(*) FROM threads WHERE id='thread-1'"
        ).fetchone()[0]
        con.close()
        self.assertEqual(restored_count, 1)
        self.assertIn(
            "thread-1",
            (self.codex_home / "session_index.jsonl").read_text(encoding="utf-8"),
        )

    def test_delete_chat_uses_exact_index_and_global_state_references(self):
        with (self.codex_home / "session_index.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "id": "thread-10",
                        "thread_name": "Title mentions thread-1",
                        "updated_at": "2026-06-14T12:30:00Z",
                    }
                )
                + "\n"
            )
        result = self.store.delete_chat_to_trash_bin(["thread-1"], confirmation="DELETE CHAT")
        self.assertEqual(result["status"], "trashed")
        index_text = (self.codex_home / "session_index.jsonl").read_text(encoding="utf-8")
        self.assertIn('"id": "thread-10"', index_text)
        self.assertIn("Title mentions thread-1", index_text)
        state_text = (self.codex_home / ".codex-global-state.json").read_text(encoding="utf-8")
        self.assertIn("mentions thread-1 but is unrelated", state_text)

    def test_trash_bin_restore_blocks_when_active_metadata_changed(self):
        result = self.store.delete_chat_to_trash_bin(["thread-1"], confirmation="DELETE CHAT")
        new_row = json.dumps({"id": "thread-new", "thread_name": "Created later"}) + "\n"
        with (self.codex_home / "session_index.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(new_row)

        preview = self.store.preview_restore_trash_bin_item(result["item_id"])
        self.assertFalse(preview["safe_to_restore_metadata"])
        restored = self.store.restore_trash_bin_item(
            result["item_id"],
            confirmation="RESTORE FROM TRASH",
            overwrite=True,
        )
        self.assertEqual(restored["status"], "manual-review-needed")
        self.assertIn(
            "thread-new",
            (self.codex_home / "session_index.jsonl").read_text(encoding="utf-8"),
        )
        self.assertFalse(self.session_path.exists())

    def test_trash_bin_restore_blocks_sqlite_wal_changes(self):
        result = self.store.delete_chat_to_trash_bin(["thread-1"], confirmation="DELETE CHAT")
        db_path = self.codex_home / "state_5.sqlite"
        con = sqlite3.connect(db_path)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA wal_autocheckpoint=0")
        con.execute("CREATE TABLE later_metadata (value TEXT)")
        con.execute("INSERT INTO later_metadata VALUES ('keep')")
        con.commit()
        self.assertTrue(Path(str(db_path) + "-wal").exists())

        preview = self.store.preview_restore_trash_bin_item(result["item_id"])
        self.assertFalse(preview["safe_to_restore_metadata"])
        restored = self.store.restore_trash_bin_item(
            result["item_id"],
            confirmation="RESTORE FROM TRASH",
            overwrite=True,
        )
        self.assertEqual(restored["status"], "manual-review-needed")
        self.assertEqual(con.execute("SELECT value FROM later_metadata").fetchone()[0], "keep")
        con.close()

    def test_trash_bin_restore_rejects_symlink_destination(self):
        result = self.store.delete_chat_to_trash_bin(["thread-1"], confirmation="DELETE CHAT")
        config_before = (self.codex_home / "config.toml").read_text(encoding="utf-8")
        self.session_path.symlink_to(self.codex_home / "config.toml")

        with self.assertRaises(ValueError):
            self.store.restore_trash_bin_item(
                result["item_id"],
                confirmation="RESTORE FROM TRASH",
                overwrite=True,
            )
        self.assertTrue(self.session_path.is_symlink())
        self.assertEqual(
            (self.codex_home / "config.toml").read_text(encoding="utf-8"),
            config_before,
        )

    def test_failed_restore_rolls_rows_and_files_back_to_trash(self):
        deleted = self.store.delete_chat_to_trash_bin(["thread-1"], confirmation="DELETE CHAT")
        original_verify = self.store._verify_trash_bin_restore

        def fail_verification(manifest):
            raise RuntimeError("forced restore verification failure")

        self.store._verify_trash_bin_restore = fail_verification
        restored = self.store.restore_trash_bin_item(
            deleted["item_id"],
            confirmation="RESTORE FROM TRASH",
        )
        self.store._verify_trash_bin_restore = original_verify

        self.assertEqual(restored["status"], "failed-rollback-completed")
        self.assertFalse(self.session_path.exists())
        con = sqlite3.connect(self.codex_home / "state_5.sqlite")
        row_count = con.execute("SELECT COUNT(*) FROM threads WHERE id='thread-1'").fetchone()[0]
        con.close()
        self.assertEqual(row_count, 0)

    def test_delete_rejects_stale_preview_before_any_active_change(self):
        preview = self.store.preview_delete_chat_to_trash_bin(["thread-1"])
        new_line = json.dumps({"id": "thread-new", "thread_name": "Created later"}) + "\n"
        with (self.codex_home / "session_index.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(new_line)

        with self.assertRaisesRegex(RuntimeError, "changed after preview"):
            self.store.delete_chat_to_trash_bin(
                ["thread-1"],
                confirmation="DELETE CHAT",
                preview=preview,
            )
        self.assertTrue(self.session_path.exists())
        self.assertIn(
            "thread-new",
            (self.codex_home / "session_index.jsonl").read_text(encoding="utf-8"),
        )
        self.assertEqual(self.store.list_trash_bin(), [])

    def test_delete_rejects_change_during_snapshot_without_losing_new_data(self):
        preview = self.store.preview_delete_chat_to_trash_bin(["thread-1"])
        original_copy = self.store._copy_trash_bin_metadata_snapshots

        def copy_then_change(item_dir, frozen_preview):
            rows = original_copy(item_dir, frozen_preview)
            with (self.codex_home / "session_index.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"id": "thread-race", "thread_name": "Keep"}) + "\n")
            return rows

        self.store._copy_trash_bin_metadata_snapshots = copy_then_change
        with self.assertRaisesRegex(RuntimeError, "snapshots were being created"):
            self.store.delete_chat_to_trash_bin(
                ["thread-1"],
                confirmation="DELETE CHAT",
                preview=preview,
            )
        self.assertTrue(self.session_path.exists())
        self.assertIn(
            "thread-race",
            (self.codex_home / "session_index.jsonl").read_text(encoding="utf-8"),
        )
        self.assertEqual(self.store.list_trash_bin(), [])

    def test_delete_project_to_trash_bin_deletes_related_chats_and_path_references(self):
        second_session = self._add_thread("thread-2", OLD_PATH, "Second Old Project Chat")

        result = self.store.delete_project_from_codex_to_trash_bin(
            OLD_PATH,
            confirmation="DELETE PROJECT FROM CODEX",
        )

        self.assertEqual(result["status"], "trashed")
        self.assertEqual(result["item_type"], "project")
        self.assertFalse(self.session_path.exists())
        self.assertFalse(second_session.exists())
        self.assertEqual(sorted(result["thread_ids"]), ["thread-1", "thread-2"])
        config_text = (self.codex_home / "config.toml").read_text(encoding="utf-8")
        self.assertNotIn(OLD_PATH, config_text)
        state_text = (self.codex_home / ".codex-global-state.json").read_text(encoding="utf-8")
        self.assertNotIn(OLD_PATH, state_text)
        state = json.loads(state_text)
        self.assertNotIn("thread-1", state["projectless-thread-ids"])
        self.assertIn("mentions thread-1 but is unrelated", state_text)
        index_text = (self.codex_home / "session_index.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("thread-1", index_text)
        self.assertNotIn("thread-2", index_text)
        self.assertEqual(result["verification"]["result"], "clean")

    def test_move_missing_path_to_trash_bin_and_batch_unrecoverable(self):
        preview = self.store.preview_move_missing_path_to_trash_bin(OLD_PATH)
        self.assertEqual(preview["item_type"], "missing-path")
        self.assertEqual(preview["path"], OLD_PATH)
        self.assertEqual(preview["thread_ids"], ["thread-1"])

        result = self.store.move_missing_path_to_trash_bin(
            OLD_PATH,
            confirmation="MOVE MISSING PATHS TO TRASH",
        )
        self.assertEqual(result["status"], "trashed")
        self.assertEqual(result["item_type"], "missing-path")
        self.assertFalse(self.session_path.exists())

        self.tearDown()
        self.setUp()
        batch_preview = self.store.preview_move_all_unrecoverable_missing_paths_to_trash_bin()
        self.assertIn(OLD_PATH, batch_preview["paths"])
        batch = self.store.move_all_unrecoverable_missing_paths_to_trash_bin(
            confirmation="MOVE MISSING PATHS TO TRASH",
        )
        self.assertEqual(batch["status"], "trashed")
        self.assertEqual(batch["item_type"], "missing-path")
        self.assertFalse(self.session_path.exists())

    def test_missing_path_trash_is_blocked_when_the_path_exists(self):
        existing_path = self.root / "restored-project"
        restored_session = self._add_thread(
            "thread-restored",
            str(existing_path),
            "Restored Project",
        )
        preview = self.store.preview_move_missing_path_to_trash_bin(str(existing_path))
        self.assertTrue(preview["safe_to_delete"])

        existing_path.mkdir()
        result = self.store.move_missing_path_to_trash_bin(
            str(existing_path),
            confirmation="MOVE MISSING PATHS TO TRASH",
            preview=preview,
        )

        self.assertEqual(result["status"], "manual-review-needed")
        self.assertFalse(result["preview"]["safe_to_delete"])
        self.assertIn("Path now exists on disk", " ".join(result["preview"]["manual_review"]))
        self.assertTrue(restored_session.exists())
        self.assertEqual(self.store.list_trash_bin(), [])

    def test_trash_bin_permanent_delete_only_removes_trash_item(self):
        result = self.store.delete_chat_to_trash_bin(["thread-1"], confirmation="DELETE CHAT")
        trash_dir = Path(result["trash_dir"])

        blocked = self.store.permanently_delete_trash_bin_item(result["item_id"], confirmation="")
        self.assertEqual(blocked["status"], "confirmation_required")
        self.assertTrue(trash_dir.exists())

        deleted = self.store.permanently_delete_trash_bin_item(
            result["item_id"],
            confirmation="PERMANENT DELETE",
        )
        self.assertEqual(deleted["status"], "permanently_deleted")
        self.assertFalse(trash_dir.exists())
        self.assertEqual(self.store.list_trash_bin(), [])

    def test_trash_bin_delete_rolls_back_when_sqlite_integrity_fails(self):
        original_integrity_check = self.store._integrity_check_connection

        def failing_integrity_check(con, db_path):
            if db_path.name == "state_5.sqlite":
                raise RuntimeError("forced integrity failure")
            return original_integrity_check(con, db_path)

        self.store._integrity_check_connection = failing_integrity_check
        result = self.store.delete_chat_to_trash_bin(["thread-1"], confirmation="DELETE CHAT")

        self.assertEqual(result["status"], "failed-rollback-completed")
        self.assertTrue(self.session_path.exists())
        con = sqlite3.connect(self.codex_home / "state_5.sqlite")
        row_count = con.execute("SELECT COUNT(*) FROM threads WHERE id='thread-1'").fetchone()[0]
        con.close()
        self.assertEqual(row_count, 1)

    def test_failed_delete_preserves_concurrent_metadata_and_rolls_back_moved_file(self):
        original_delete = self.store._delete_sqlite_threads_rows

        def change_index_then_fail(db_path, threads, expected_digest):
            with (self.codex_home / "session_index.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"id": "thread-race", "thread_name": "Keep"}) + "\n")
            raise RuntimeError("forced concurrent change")

        self.store._delete_sqlite_threads_rows = change_index_then_fail
        result = self.store.delete_chat_to_trash_bin(["thread-1"], confirmation="DELETE CHAT")
        self.store._delete_sqlite_threads_rows = original_delete

        self.assertEqual(result["status"], "failed-rollback-completed")
        self.assertTrue(self.session_path.exists())
        self.assertIn(
            "thread-race",
            (self.codex_home / "session_index.jsonl").read_text(encoding="utf-8"),
        )

    def test_sqlite_row_export_round_trips_blob_values_during_rollback(self):
        for db_path in self.store.state_db_paths():
            con = sqlite3.connect(db_path)
            con.execute(
                "CREATE TABLE binary_thread_notes (thread_id TEXT PRIMARY KEY, payload BLOB)"
            )
            con.execute(
                "INSERT INTO binary_thread_notes VALUES (?, ?)",
                ("thread-1", sqlite3.Binary(b"\x00\x01fixture")),
            )
            con.commit()
            con.close()

        original_purge = self.store._purge_session_index_refs

        def fail_after_sqlite(*args, **kwargs):
            raise RuntimeError("force row rollback")

        self.store._purge_session_index_refs = fail_after_sqlite
        result = self.store.delete_chat_to_trash_bin(["thread-1"], confirmation="DELETE CHAT")
        self.store._purge_session_index_refs = original_purge

        self.assertEqual(result["status"], "failed-rollback-completed")
        for db_path in self.store.state_db_paths():
            con = sqlite3.connect(db_path)
            payload = con.execute(
                "SELECT payload FROM binary_thread_notes WHERE thread_id = ?",
                ("thread-1",),
            ).fetchone()[0]
            con.close()
            self.assertEqual(payload, b"\x00\x01fixture")

    def test_runtime_outputs_are_private_and_atomic_writes_preserve_active_mode(self):
        original_umask = os.umask(0o022)
        try:
            private_path = self.root / "new-runtime.json"
            atomic_write_text(private_path, "{}\n")
            self.assertEqual(private_path.stat().st_mode & 0o777, 0o600)

            active_path = self.root / "existing-active.json"
            active_path.write_text("before\n", encoding="utf-8")
            os.chmod(active_path, 0o644)
            atomic_write_text(active_path, "after\n")
            self.assertEqual(active_path.stat().st_mode & 0o777, 0o644)

            self.store.backup.log_operation({"operation_type": "permission-test"})
            self.assertEqual((self.app_home / "backups").stat().st_mode & 0o777, 0o700)
            self.assertEqual((self.app_home / "operation-logs").stat().st_mode & 0o777, 0o700)
            self.assertEqual((self.app_home / "trash-bin").stat().st_mode & 0o777, 0o700)
            self.assertEqual(
                (self.app_home / "operation-logs" / "operations.jsonl").stat().st_mode & 0o777,
                0o600,
            )
        finally:
            os.umask(original_umask)

    def test_interrupted_delete_leaves_a_reviewable_provisional_manifest(self):
        preview = self.store.preview_delete_chat_to_trash_bin(["thread-1"])
        with mock.patch("clean_my_codex.core.shutil.move", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.store.delete_chat_to_trash_bin(
                    ["thread-1"],
                    confirmation="DELETE CHAT",
                    preview=preview,
                )

        items = self.store.list_trash_bin()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["status"], "applying")
        manifest = json.loads(
            (self.app_home / "trash-bin" / items[0]["item_id"] / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["verification"]["result"], "manual-review-needed")
        self.assertIn(str(self.session_path), manifest["planned_files_to_move"])

    def test_legacy_whole_file_backup_restore_is_disabled(self):
        result = self.store.apply_relocation(OLD_PATH, NEW_PATH, confirm=True)
        restored = self.store.restore_backup(result["operation_id"], confirm=True)
        self.assertEqual(restored["status"], "manual-review-needed")

        config_text = (self.codex_home / "config.toml").read_text(encoding="utf-8")
        self.assertNotIn(OLD_PATH, config_text)
        self.assertIn(NEW_PATH, config_text)

        con = sqlite3.connect(self.codex_home / "state_5.sqlite")
        cwd = con.execute("SELECT cwd FROM threads WHERE id='thread-1'").fetchone()[0]
        con.close()
        self.assertEqual(cwd, NEW_PATH)

    def test_full_purge_preview_blocks_active_chat_before_trash(self):
        preview = self.store.preview_full_purge("thread-1")
        self.assertFalse(preview["eligible"])
        self.assertEqual(preview["cleanup_confidence"], "manual-review")
        self.assertIn("move it to trash first", preview["blocked_reason"])

    def test_sqlite_foreign_key_relationship_is_detected_and_removed(self):
        db_path = self.codex_home / "state_5.sqlite"
        con = sqlite3.connect(db_path)
        con.execute(
            "CREATE TABLE notes (id TEXT PRIMARY KEY, owner_id TEXT REFERENCES threads(id))"
        )
        con.execute("INSERT INTO notes VALUES (?, ?)", ("note-1", "thread-1"))
        con.commit()
        con.close()

        preview = self.store.preview_delete_chat_to_trash_bin(["thread-1"])
        self.assertEqual(preview["sqlite"]["state_5.sqlite"]["rows_to_delete"]["notes"], 1)
        self.assertTrue(preview["safe_to_delete"])
        result = self.store.delete_chat_to_trash_bin(
            ["thread-1"],
            confirmation="DELETE CHAT",
            preview=preview,
        )
        self.assertEqual(result["status"], "trashed")
        con = sqlite3.connect(db_path)
        note_count = con.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        con.close()
        self.assertEqual(note_count, 0)

    def test_trash_relative_rejects_escape_and_symbolic_links(self):
        outside = self.root / "outside.jsonl"
        outside.write_text("{}\n", encoding="utf-8")
        escaped = self.codex_home / "sessions" / ".." / ".." / "outside.jsonl"
        with self.assertRaises(ValueError):
            self.store._trash_relative(escaped)

        link = self.codex_home / "sessions" / "linked.jsonl"
        link.symlink_to(self.session_path)
        with self.assertRaises(ValueError):
            self.store._trash_relative(link)

    def test_legacy_trash_restore_is_disabled(self):
        restored = self.store.restore_trash_item("legacy-item", confirm=True)
        self.assertEqual(restored["status"], "manual-review-needed")
        self.assertTrue(self.session_path.exists())

    def test_legacy_full_purge_and_restore_are_disabled(self):
        result = self.store.full_purge_chat("thread-1", confirmation="FULL PURGE CODEX CHAT")
        self.assertEqual(result["status"], "manual-review-needed")
        restored = self.store.restore_purge_backup(
            "legacy-purge",
            confirmation="RESTORE FULL PURGE",
        )
        self.assertEqual(restored["status"], "manual-review-needed")

        con = sqlite3.connect(self.codex_home / "state_5.sqlite")
        row_count = con.execute("SELECT COUNT(*) FROM threads WHERE id='thread-1'").fetchone()[0]
        con.close()
        self.assertEqual(row_count, 1)


if __name__ == "__main__":
    unittest.main()
