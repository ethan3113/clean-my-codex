from __future__ import annotations

import base64
import json
import hashlib
import os
import re
import shutil
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .platform_security import file_mode, is_link_like, set_private_mode


PRIVATE_FILE_MODE = 0o600
PRIVATE_DIR_MODE = 0o700
TEMP_FILE_STALE_SECONDS = 24 * 60 * 60


THREAD_COLUMNS_SQL = """
CREATE TABLE IF NOT EXISTS threads (
    id TEXT PRIMARY KEY,
    rollout_path TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    source TEXT NOT NULL,
    model_provider TEXT NOT NULL,
    cwd TEXT NOT NULL,
    title TEXT NOT NULL,
    sandbox_policy TEXT NOT NULL,
    approval_mode TEXT NOT NULL,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    has_user_event INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    archived_at INTEGER,
    git_sha TEXT,
    git_branch TEXT,
    git_origin_url TEXT,
    cli_version TEXT NOT NULL DEFAULT '',
    first_user_message TEXT NOT NULL DEFAULT '',
    agent_nickname TEXT,
    agent_role TEXT,
    memory_mode TEXT NOT NULL DEFAULT 'enabled',
    model TEXT,
    reasoning_effort TEXT,
    agent_path TEXT,
    created_at_ms INTEGER,
    updated_at_ms INTEGER,
    thread_source TEXT,
    preview TEXT NOT NULL DEFAULT ''
)
"""


def create_state_db(db_path: Path) -> None:
    """Create the subset of Codex state schema used by tests and fixtures."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute(THREAD_COLUMNS_SQL)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS thread_dynamic_tools (
            thread_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            input_schema TEXT NOT NULL,
            defer_loading INTEGER NOT NULL DEFAULT 0,
            namespace TEXT,
            PRIMARY KEY(thread_id, position)
        )
        """
    )
    con.commit()
    con.close()


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def now_ms() -> int:
    return int(time.time() * 1000)


def basename_label(path: str) -> str:
    return Path(path).name or path


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def read_text_exact(path: Path) -> str:
    """Read UTF-8 text without normalizing platform line endings."""
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def ensure_private_dir(path: Path, parents: bool = True, exist_ok: bool = True) -> None:
    path = Path(path)
    if is_link_like(path):
        raise ValueError(f"Private runtime directory cannot be a link or junction: {path}")
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    path.mkdir(mode=PRIVATE_DIR_MODE, parents=parents, exist_ok=exist_ok)
    for directory in reversed(missing):
        if is_link_like(directory) or not directory.is_dir():
            raise ValueError(f"Private runtime directory is unsafe: {directory}")
        set_private_mode(directory, PRIVATE_DIR_MODE)
    if is_link_like(path) or not path.is_dir():
        raise ValueError(f"Private runtime directory is unsafe: {path}")
    set_private_mode(path, PRIVATE_DIR_MODE)


def _atomic_text_mode(path: Path) -> int:
    if not path.exists():
        return PRIVATE_FILE_MODE
    if is_link_like(path) or not path.is_file():
        raise ValueError(f"Atomic write target is unsafe: {path}")
    return file_mode(path, PRIVATE_FILE_MODE)


def _write_atomic_temp(path: Path, text: str) -> Path:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    mode = _atomic_text_mode(path)
    descriptor = os.open(tmp, flags, mode)
    try:
        set_private_mode(tmp, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            descriptor = -1
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return tmp


def atomic_write_text(path: Path, text: str) -> None:
    tmp: Path | None = None
    try:
        tmp = _write_atomic_temp(path, text)
        os.replace(tmp, path)
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink()


def atomic_write_text_if_unchanged(path: Path, text: str, expected_digest: str) -> str:
    tmp: Path | None = None
    try:
        tmp = _write_atomic_temp(path, text)
        if not path.is_file() or is_link_like(path) or file_sha256(path) != expected_digest:
            raise RuntimeError(f"Active metadata changed while preparing an update: {path}")
        os.replace(tmp, path)
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink()
    return file_sha256(path)


def managed_item_path(root: Path, item_id: str, label: str) -> Path:
    value = str(item_id or "").strip()
    if not value or value in {".", ".."} or Path(value).name != value:
        raise ValueError(f"Invalid {label} identifier")
    root_resolved = root.resolve()
    lexical_candidate = root / value
    if is_link_like(lexical_candidate):
        raise ValueError(f"Invalid {label} identifier")
    candidate = lexical_candidate.resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"{label} must stay inside {root.name}") from exc
    if candidate == root_resolved:
        raise ValueError(f"Invalid {label} identifier")
    return root / value


def confined_path(root: Path, candidate: Path, label: str) -> Path:
    root_resolved = root.resolve()
    lexical = candidate.expanduser()
    if not lexical.is_absolute():
        lexical = root_resolved / lexical
    lexical = Path(os.path.abspath(lexical))
    try:
        relative = lexical.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"{label} must stay inside {root.name}") from exc
    if lexical == root_resolved:
        raise ValueError(f"{label} cannot be the managed root")
    current = root_resolved
    for part in relative.parts:
        current = current / part
        if is_link_like(current):
            raise ValueError(f"{label} cannot contain links or junctions")
    try:
        lexical.resolve(strict=False).relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"{label} must stay inside {root.name}") from exc
    return lexical


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sqlite_connection_sha256(con: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    for statement in con.iterdump():
        digest.update(statement.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def sqlite_content_sha256(path: Path) -> str:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity check failed for {path}: {integrity}")
        return sqlite_connection_sha256(con)
    finally:
        con.close()


def metadata_file_digest(path: Path) -> str:
    return sqlite_content_sha256(path) if path.suffix == ".sqlite" else file_sha256(path)


def dedupe_keep_order(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


_RemovedJsonRef = object()


@dataclass(frozen=True)
class OperationBackup:
    operation_id: str
    backup_dir: Path


class BackupManager:
    def __init__(self, codex_home: Path, app_home: Path):
        self.codex_home = codex_home.expanduser().resolve()
        self.app_home = app_home.expanduser().resolve()
        self.backups_dir = app_home / "backups"
        self.logs_dir = app_home / "operation-logs"
        ensure_private_dir(self.backups_dir)
        ensure_private_dir(self.logs_dir)

    def create_backup(
        self,
        operation_type: str,
        affected_paths: list[Path],
        preview: dict[str, Any],
    ) -> OperationBackup:
        operation_id = f"{utc_stamp()}-{operation_type}-{uuid.uuid4().hex[:8]}"
        backup_dir = self.backups_dir / operation_id
        files_dir = backup_dir / "files"
        ensure_private_dir(files_dir, exist_ok=False)

        copied: list[dict[str, str]] = []
        for source in affected_paths:
            if not source.exists() or not source.is_file():
                continue
            relative = self._safe_relative(source)
            destination = files_dir / relative
            ensure_private_dir(destination.parent)
            if source.suffix == ".sqlite":
                self._sqlite_backup(source, destination)
            else:
                shutil.copy2(source, destination)
                set_private_mode(destination, PRIVATE_FILE_MODE)
            copied.append({"source": str(source), "backup": str(destination)})

        manifest = {
            "operation_id": operation_id,
            "operation_type": operation_type,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "codex_home": str(self.codex_home),
            "copied": copied,
            "preview": preview,
        }
        write_json(backup_dir / "manifest.json", manifest)
        write_json(backup_dir / "preview.json", preview)
        return OperationBackup(operation_id=operation_id, backup_dir=backup_dir)

    def log_operation(self, payload: dict[str, Any]) -> None:
        line = json.dumps(
            {"logged_at": datetime.now(timezone.utc).isoformat(), **payload},
            ensure_ascii=False,
        )
        log_path = self.logs_dir / "operations.jsonl"
        if is_link_like(log_path):
            raise ValueError("Operation log cannot be a link or junction")
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(log_path, flags, PRIVATE_FILE_MODE)
        try:
            set_private_mode(log_path, PRIVATE_FILE_MODE)
            os.write(descriptor, (line + "\n").encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def list_backups(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for manifest_path in sorted(self.backups_dir.glob("*/manifest.json"), reverse=True):
            try:
                manifest = read_json(manifest_path, {})
            except Exception:
                continue
            rows.append(
                {
                    "operation_id": manifest.get("operation_id") or manifest_path.parent.name,
                    "operation_type": manifest.get("operation_type"),
                    "created_at": manifest.get("created_at"),
                    "backup_dir": str(manifest_path.parent),
                    "file_count": len(manifest.get("copied") or []),
                }
            )
        return rows

    def backup_manifest(self, operation_id: str) -> dict[str, Any]:
        manifest_path = managed_item_path(self.backups_dir, operation_id, "backup") / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"Backup manifest not found: {operation_id}")
        return read_json(manifest_path, {})

    def restore_backup(self, operation_id: str, confirm: bool = False) -> dict[str, Any]:
        if confirm:
            return {
                "status": "manual-review-needed",
                "operation_id": operation_id,
                "message": "Legacy whole-file backup restore is disabled; use Trash Bin restore data instead.",
            }
        manifest = self.backup_manifest(operation_id)
        backup_dir = managed_item_path(self.backups_dir, operation_id, "backup")
        copied = manifest.get("copied") or []
        preview = {
            "operation": "restore_backup",
            "operation_id": operation_id,
            "backup_created_at": manifest.get("created_at"),
            "files_to_restore": copied,
            "dry_run": True,
        }
        if not confirm:
            return {"status": "preview_only", "preview": preview}

        validated: list[tuple[Path, Path]] = []
        for row in copied:
            source_text = row.get("source")
            backup_text = row.get("backup")
            if not source_text or not backup_text:
                raise ValueError("Backup restore mapping is incomplete")
            source = confined_path(self.codex_home, Path(source_text), "backup restore target")
            backup = confined_path(backup_dir, Path(backup_text), "backup restore source")
            if not backup.is_file() or is_link_like(backup):
                raise ValueError(f"Backup restore source is missing or unsafe: {backup}")
            validated.append((source, backup))

        affected_paths = [source for source, _ in validated if source.exists()]
        rollback_guard = self.create_backup("rollback-guard", affected_paths, preview)

        restored: list[dict[str, str]] = []
        for source, backup in validated:
            source.parent.mkdir(parents=True, exist_ok=True)
            if source.suffix == ".sqlite":
                self._sqlite_backup(backup, source)
                self._sqlite_integrity_check(source)
            else:
                shutil.copy2(backup, source)
            restored.append({"source": str(source), "backup": str(backup)})

        result = {
            "status": "restored_backup",
            "operation_id": operation_id,
            "guard_backup_dir": str(rollback_guard.backup_dir),
            "restored": restored,
        }
        self.log_operation(result)
        return result

    def _safe_relative(self, source: Path) -> Path:
        if is_link_like(source):
            raise ValueError(f"Links and junctions are not supported: {source}")
        try:
            relative = source.resolve().relative_to(self.codex_home.resolve())
        except ValueError as exc:
            raise ValueError(f"Backup source is outside the Codex data directory: {source}") from exc
        return Path(".codex") / relative

    @staticmethod
    def _sqlite_backup(source: Path, destination: Path) -> None:
        src = sqlite3.connect(source)
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        set_private_mode(destination, PRIVATE_FILE_MODE)

    @staticmethod
    def _sqlite_integrity_check(db_path: Path) -> None:
        con = sqlite3.connect(db_path)
        try:
            result = con.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise RuntimeError(f"SQLite integrity check failed for {db_path}: {result}")
        finally:
            con.close()


class CodexStore:
    def __init__(self, codex_home: Path | str, app_home: Path | str):
        self.codex_home = Path(codex_home).expanduser().resolve()
        self.app_home = Path(app_home).expanduser().resolve()
        self.backup = BackupManager(self.codex_home, self.app_home)
        self.trash_dir = self.app_home / "trash"
        self.purge_backups_dir = self.app_home / "purge-backups"
        self.codex_archive_dir = self.app_home / "codex-archive"
        self.trash_bin_dir = self.app_home / "trash-bin"
        ensure_private_dir(self.trash_bin_dir)

    @property
    def config_path(self) -> Path:
        return self.codex_home / "config.toml"

    @property
    def global_state_path(self) -> Path:
        return self.codex_home / ".codex-global-state.json"

    @property
    def session_index_path(self) -> Path:
        return self.codex_home / "session_index.jsonl"

    def state_db_paths(self) -> list[Path]:
        return [
            self.codex_home / "state_5.sqlite",
            self.codex_home / "sqlite" / "state_5.sqlite",
        ]

    def managed_active_paths(self) -> list[Path]:
        paths = [self.config_path, self.global_state_path]
        paths.extend(self.state_db_paths())
        return [p for p in paths if p.exists()]

    def scan_chats(self) -> list[dict[str, Any]]:
        indexed_titles = self._load_session_index_titles()
        merged: dict[str, dict[str, Any]] = {}
        for db_path in self.state_db_paths():
            if not db_path.exists():
                continue
            source_name = self._db_label(db_path)
            for row in self._fetch_threads(db_path):
                thread_id = row["id"]
                if thread_id not in merged:
                    rollout_path = row["rollout_path"] or ""
                    session_path = Path(rollout_path) if rollout_path else None
                    merged[thread_id] = {
                        "id": thread_id,
                        "title": row["title"] or indexed_titles.get(thread_id) or thread_id,
                        "indexed_title": indexed_titles.get(thread_id, ""),
                        "display_title": indexed_titles.get(thread_id, ""),
                        "cwd": row["cwd"] or "",
                        "cwd_exists": Path(row["cwd"]).exists() if row["cwd"] else False,
                        "rollout_path": rollout_path,
                        "session_exists": session_path.exists() if session_path else False,
                        "session_size": session_path.stat().st_size
                        if session_path and session_path.exists()
                        else 0,
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                        "archived": bool(row["archived"]),
                        "tokens_used": row["tokens_used"],
                        "preview": (row["preview"] or row["first_user_message"] or "")[:240],
                        "sources": [source_name],
                    }
                    merged[thread_id]["badges"] = self._chat_badges(merged[thread_id])
                else:
                    merged[thread_id]["sources"].append(source_name)
                    merged[thread_id]["badges"] = self._chat_badges(merged[thread_id])
        return sorted(merged.values(), key=lambda item: item.get("updated_at") or 0, reverse=True)

    def scan_paths(self) -> dict[str, Any]:
        cwd_counts: dict[str, int] = {}
        for db_path in self.state_db_paths():
            if not db_path.exists():
                continue
            con = sqlite3.connect(db_path)
            try:
                for cwd, count in con.execute(
                    "SELECT cwd, COUNT(*) FROM threads GROUP BY cwd ORDER BY COUNT(*) DESC"
                ):
                    if cwd:
                        cwd_counts[cwd] = max(cwd_counts.get(cwd, 0), int(count))
            finally:
                con.close()

        missing = [
            {"path": path, "thread_count": count}
            for path, count in sorted(cwd_counts.items(), key=lambda item: (-item[1], item[0]))
            if Path(path).is_absolute() and not Path(path).exists()
        ]
        global_state = read_json(self.global_state_path, {})
        return {
            "cwd_paths": [
                {
                    "path": path,
                    "thread_count": count,
                    "exists": Path(path).exists() if Path(path).is_absolute() else None,
                }
                for path, count in sorted(cwd_counts.items(), key=lambda item: (-item[1], item[0]))
            ],
            "missing_paths": missing,
            "saved_workspace_roots": global_state.get("electron-saved-workspace-roots", []),
            "project_order": global_state.get("project-order", []),
            "active_workspace_roots": global_state.get("active-workspace-roots", []),
            "workspace_labels": global_state.get("electron-workspace-root-labels", {}),
        }

    def preview_relocation(self, old_path: str, new_path: str) -> dict[str, Any]:
        old_path, new_path = self._validate_relocation_paths(old_path, new_path)
        sqlite_updates: dict[str, int] = {}
        sqlite_destination_rows: dict[str, int] = {}
        for db_path in self.state_db_paths():
            if db_path.exists():
                sqlite_updates[self._db_label(db_path)] = self._count_thread_cwd(db_path, old_path)
                sqlite_destination_rows[self._db_label(db_path)] = self._count_thread_cwd(db_path, new_path)

        config_matches = 0
        config_destination_matches = 0
        if self.config_path.exists():
            old_header = self._config_project_header(old_path)
            new_header = self._config_project_header(new_path)
            config_lines = self.config_path.read_text(
                encoding="utf-8",
                errors="replace",
            ).splitlines()
            config_matches = sum(
                line.strip() == old_header
                for line in config_lines
            )
            config_destination_matches = sum(line.strip() == new_header for line in config_lines)

        global_matches = {
            "electron-saved-workspace-roots": 0,
            "project-order": 0,
            "active-workspace-roots": 0,
            "electron-workspace-root-labels": 0,
        }
        global_destination_matches = {key: 0 for key in global_matches}
        if self.global_state_path.exists():
            state = read_json(self.global_state_path, {})
            for key in [
                "electron-saved-workspace-roots",
                "project-order",
                "active-workspace-roots",
            ]:
                values = state.get(key, [])
                global_matches[key] = len([value for value in values if value == old_path])
                global_destination_matches[key] = len([value for value in values if value == new_path])
            labels = state.get("electron-workspace-root-labels", {})
            global_matches["electron-workspace-root-labels"] = 1 if old_path in labels else 0
            global_destination_matches["electron-workspace-root-labels"] = 1 if new_path in labels else 0

        destination_reference_count = (
            sum(sqlite_destination_rows.values())
            + config_destination_matches
            + sum(global_destination_matches.values())
        )
        source_reference_count = sum(sqlite_updates.values()) + config_matches + sum(global_matches.values())
        affected_paths = self.managed_active_paths()

        return {
            "operation": "relocation",
            "old_path": old_path,
            "new_path": new_path,
            "new_path_exists": Path(new_path).exists(),
            "sqlite_updates": sqlite_updates,
            "sqlite_destination_rows": sqlite_destination_rows,
            "config_toml_matches": config_matches,
            "config_destination_matches": config_destination_matches,
            "global_state_matches": global_matches,
            "global_state_destination_matches": global_destination_matches,
            "source_reference_count": source_reference_count,
            "destination_reference_count": destination_reference_count,
            "safe_to_apply": source_reference_count > 0 and destination_reference_count == 0,
            "active_metadata_digests": self._metadata_source_digests(
                [{"source": str(path)} for path in affected_paths]
            ),
            "metadata_digest_algorithm": "sqlite-logical-v1+sha256-v1",
            "historical_references_not_modified": True,
            "affected_files": [str(path) for path in affected_paths],
            "dry_run": True,
        }

    def apply_relocation(
        self,
        old_path: str,
        new_path: str,
        confirm: bool = False,
        preview: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        preview = preview or self.preview_relocation(old_path, new_path)
        if not confirm:
            return {"status": "preview_only", "preview": preview}
        if not preview.get("safe_to_apply"):
            return {
                "status": "blocked",
                "preview": preview,
                "message": "Relocation requires source references and no existing destination metadata.",
            }

        active_paths = self.managed_active_paths()
        pre_digests = preview.get("active_metadata_digests") or {}
        self._assert_metadata_digests(
            pre_digests,
            "Active Codex metadata changed after relocation preview.",
            active_paths,
        )
        backup = self.backup.create_backup("relocation", active_paths, preview)
        original_text: dict[str, str] = {}
        mutation_post_digests: dict[str, str] = {}

        try:
            if self.config_path.exists():
                source_key = str(self.config_path.resolve())
                current_digest = file_sha256(self.config_path)
                original_text[source_key] = read_text_exact(self.config_path)
                old_header = self._config_project_header(old_path)
                new_header = self._config_project_header(new_path)
                lines = original_text[source_key].splitlines(keepends=True)
                updated = [
                    line.replace(old_header, new_header, 1) if line.strip() == old_header else line
                    for line in lines
                ]
                if updated != lines:
                    mutation_post_digests[source_key] = atomic_write_text_if_unchanged(
                        self.config_path,
                        "".join(updated),
                        current_digest,
                    )

            if self.global_state_path.exists():
                source_key = str(self.global_state_path.resolve())
                current_digest = file_sha256(self.global_state_path)
                original_text[source_key] = read_text_exact(self.global_state_path)
                state = read_json(self.global_state_path, {})
                original_state = json.loads(json.dumps(state, ensure_ascii=False))
                for key in [
                    "electron-saved-workspace-roots",
                    "project-order",
                    "active-workspace-roots",
                ]:
                    values = state.get(key, [])
                    if isinstance(values, list):
                        state[key] = dedupe_keep_order(
                            [new_path if value == old_path else value for value in values]
                        )
                labels = state.get("electron-workspace-root-labels", {})
                if isinstance(labels, dict) and old_path in labels:
                    label = labels.pop(old_path)
                    labels[new_path] = basename_label(new_path) if label == basename_label(old_path) else label
                    state["electron-workspace-root-labels"] = labels
                if state != original_state:
                    mutation_post_digests[source_key] = atomic_write_text_if_unchanged(
                        self.global_state_path,
                        json.dumps(state, indent=2, ensure_ascii=False) + "\n",
                        current_digest,
                    )

            for db_path in self.state_db_paths():
                if db_path.exists():
                    source_key = str(db_path.resolve())
                    mutation_post_digests[source_key] = self._update_thread_cwd(
                        db_path,
                        old_path,
                        new_path,
                        pre_digests[source_key],
                    )
        except Exception as exc:
            rollback_errors = self._rollback_relocation_changes(
                old_path,
                new_path,
                pre_digests,
                mutation_post_digests,
                original_text,
            )
            if rollback_errors:
                raise RuntimeError(
                    "Relocation failed and automatic rollback stopped because active metadata changed. "
                    f"Review backup {backup.backup_dir}. Conflicts: {'; '.join(rollback_errors)}"
                ) from exc
            raise RuntimeError("Relocation failed; this operation's changes were rolled back") from exc

        result = {
            "status": "applied",
            "operation_id": backup.operation_id,
            "backup_dir": str(backup.backup_dir),
            "preview": preview,
        }
        self.backup.log_operation(result)
        return result

    def preview_trash(self, thread_ids: list[str]) -> dict[str, Any]:
        threads = self._threads_by_id(thread_ids)
        affected_files: list[str] = [str(path) for path in self.state_db_paths() if path.exists()]
        for thread in threads:
            rollout_path = thread.get("rollout_path")
            if rollout_path and Path(rollout_path).exists():
                affected_files.append(rollout_path)
        return {
            "operation": "trash_chats",
            "thread_ids": thread_ids,
            "threads": threads,
            "affected_files": dedupe_keep_order(affected_files),
            "dry_run": True,
        }

    def move_chats_to_trash(self, thread_ids: list[str], confirm: bool = False) -> dict[str, Any]:
        preview = self.preview_trash(thread_ids)
        if not confirm:
            return {"status": "preview_only", "preview": preview}
        return {
            "status": "manual-review-needed",
            "preview": preview,
            "message": "Legacy Trash mutation is disabled; use Delete Chat and the unified Trash Bin.",
        }

        affected_paths = [Path(path) for path in preview["affected_files"]]
        backup = self.backup.create_backup("trash-chats", affected_paths, preview)
        trash_id = f"{utc_stamp()}-trash-{uuid.uuid4().hex[:8]}"
        trash_item_dir = self.trash_dir / trash_id
        trashed_files_dir = trash_item_dir / "files"
        trashed_files_dir.mkdir(parents=True, exist_ok=False)

        moved_files: list[dict[str, Any]] = []
        for thread in preview["threads"]:
            rollout_path = thread.get("rollout_path")
            if not rollout_path:
                continue
            source = Path(rollout_path)
            if not source.exists():
                continue
            relative = self._trash_relative(source)
            destination = trashed_files_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            moved_files.append({"source": str(source), "trash": str(destination)})

        for db_path in self.state_db_paths():
            if db_path.exists():
                self._set_archived(db_path, thread_ids, archived=True)
                self._integrity_check(db_path)

        manifest = {
            "trash_id": trash_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "thread_ids": thread_ids,
            "backup_dir": str(backup.backup_dir),
            "moved_files": moved_files,
            "threads": preview["threads"],
            "status": "trashed",
        }
        write_json(trash_item_dir / "manifest.json", manifest)
        result = {
            "status": "trashed",
            "trash_id": trash_id,
            "trash_dir": str(trash_item_dir),
            "backup_dir": str(backup.backup_dir),
            "moved_files": moved_files,
        }
        self.backup.log_operation(result)
        return result

    def preview_clean_trash(self, thread_ids: list[str]) -> dict[str, Any]:
        threads = self._threads_by_id(thread_ids)
        refs: list[str] = []
        files_to_move: list[str] = []
        thread_reports: list[dict[str, Any]] = []

        for thread in threads:
            thread_id = thread["id"]
            rollout_path = thread.get("rollout_path") or ""
            refs.append(thread_id)
            if rollout_path:
                refs.append(rollout_path)
            related_files = self._related_files_for_thread(thread)
            related_file_paths = [str(path) for path in related_files]
            files_to_move.extend(related_file_paths)
            thread_reports.append(
                {
                    "id": thread_id,
                    "title": thread.get("display_title") or f"Untitled chat - {thread_id[:8]}",
                    "rollout_path": rollout_path,
                    "session_exists": bool(rollout_path and Path(rollout_path).exists()),
                    "archived": bool(thread.get("archived")),
                    "related_files": related_file_paths,
                }
            )

        refs = dedupe_keep_order([ref for ref in refs if ref])
        sqlite_preview: dict[str, Any] = {}
        unsafe_or_unknown: list[str] = []
        for db_path in self.state_db_paths():
            if not db_path.exists():
                continue
            label = self._db_label(db_path)
            aggregate_rows: dict[str, int] = {}
            manual_review_tables: list[str] = []
            for thread in threads:
                preview = self._preview_sqlite_thread_rows(
                    db_path,
                    thread["id"],
                    thread.get("rollout_path") or "",
                )
                for table_name, count in preview.get("rows_to_delete", {}).items():
                    aggregate_rows[table_name] = aggregate_rows.get(table_name, 0) + int(count)
                manual_review_tables.extend(preview.get("manual_review_tables") or [])
            manual_review_tables = sorted(set(manual_review_tables))
            sqlite_preview[label] = {
                "database": str(db_path),
                "rows_to_delete": aggregate_rows,
                "manual_review_tables": manual_review_tables,
            }
            unsafe_or_unknown.extend([f"{label}:{table}" for table in manual_review_tables])

        index_lines = self._session_index_lines_to_remove(refs)
        global_state_paths = self._global_state_paths_to_remove(refs)
        metadata_files: list[str] = []
        for db_path in self.state_db_paths():
            label = self._db_label(db_path)
            db_preview = sqlite_preview.get(label) or {}
            if sum((db_preview.get("rows_to_delete") or {}).values()) > 0:
                metadata_files.append(str(db_path))
        if index_lines:
            metadata_files.append(str(self.session_index_path))
        if global_state_paths:
            metadata_files.append(str(self.global_state_path))

        known_reference_count = (
            sum(sum((db.get("rows_to_delete") or {}).values()) for db in sqlite_preview.values())
            + len(index_lines)
            + len(global_state_paths)
        )
        if unsafe_or_unknown:
            confidence = "manual-review"
        elif files_to_move or known_reference_count:
            confidence = "safe"
        else:
            confidence = "partial"

        affected_files = dedupe_keep_order(files_to_move + metadata_files)
        return {
            "operation": "clean_move_to_trash",
            "thread_ids": thread_ids,
            "threads": thread_reports,
            "files_to_move": dedupe_keep_order(files_to_move),
            "metadata_files_to_modify": dedupe_keep_order(metadata_files),
            "affected_files": affected_files,
            "sqlite": sqlite_preview,
            "session_index_lines_to_remove": index_lines,
            "global_state_paths_to_remove": global_state_paths,
            "unsafe_or_unknown_references": dedupe_keep_order(unsafe_or_unknown),
            "items_not_touched": dedupe_keep_order(unsafe_or_unknown),
            "cleanup_confidence": confidence,
            "restore_plan": (
                "Restore moved files from the trash item and restore pre-clean copies of "
                "SQLite/index/global-state files from the linked backup manifest."
            ),
            "dry_run": True,
        }

    def clean_move_chats_to_trash(
        self,
        thread_ids: list[str],
        confirmation: str = "",
    ) -> dict[str, Any]:
        preview = self.preview_clean_trash(thread_ids)
        if confirmation != "CLEAN MOVE TO TRASH":
            return {"status": "confirmation_required", "preview": preview}
        return {
            "status": "manual-review-needed",
            "preview": preview,
            "message": "Legacy clean-trash mutation is disabled; use Delete Chat and the unified Trash Bin.",
        }

        affected_paths = [Path(path) for path in preview["affected_files"]]
        backup = self.backup.create_backup("clean-trash", affected_paths, preview)
        trash_id = f"{utc_stamp()}-clean-trash-{uuid.uuid4().hex[:8]}"
        trash_item_dir = self.trash_dir / trash_id
        trashed_files_dir = trash_item_dir / "files"
        trashed_files_dir.mkdir(parents=True, exist_ok=False)

        moved_files: list[dict[str, str]] = []
        sqlite_results: dict[str, Any] = {}
        jsonl_result: dict[str, Any] = {}
        global_result: dict[str, Any] = {}
        modified_files: list[str] = []
        try:
            for source_text in preview.get("files_to_move") or []:
                source = Path(source_text)
                if not source.exists() or not source.is_file():
                    continue
                relative = self._trash_relative(source)
                destination = trashed_files_dir / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))
                moved_files.append({"source": str(source), "trash": str(destination)})

            for db_path in self.state_db_paths():
                if not db_path.exists():
                    continue
                label = self._db_label(db_path)
                db_deleted: dict[str, int] = {}
                for thread in preview["threads"]:
                    result = self._delete_sqlite_thread_rows(
                        db_path,
                        thread["id"],
                        thread.get("rollout_path") or "",
                    )
                    for table_name, count in result.get("deleted_rows", {}).items():
                        db_deleted[table_name] = db_deleted.get(table_name, 0) + int(count)
                sqlite_results[label] = {"deleted_rows": db_deleted}
                if sum(db_deleted.values()) > 0:
                    modified_files.append(str(db_path))

            jsonl_result = self._purge_session_index_refs(preview["thread_ids"], preview["threads"])
            if jsonl_result.get("removed_count", 0) > 0:
                modified_files.append(str(self.session_index_path))

            global_result = self._purge_global_state_refs_for_preview(preview)
            if global_result.get("removed_paths"):
                modified_files.append(str(self.global_state_path))

            verification = self.verify_clean_trash(preview, moved_files, backup.backup_dir)
            status = verification["result"]
        except Exception as exc:
            self._restore_backup_copies(backup.backup_dir)
            verification = {"result": "failed-rollback-completed", "error": str(exc)}
            status = "failed-rollback-completed"
            sqlite_results.setdefault("error", str(exc))

        manifest = {
            "trash_id": trash_id,
            "operation_type": "clean-move-to-trash",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "thread_ids": thread_ids,
            "backup_dir": str(backup.backup_dir),
            "moved_files": moved_files,
            "threads": preview["threads"],
            "preview": preview,
            "modified_files": dedupe_keep_order(modified_files),
            "sqlite_tables_affected": sqlite_results,
            "session_index": jsonl_result,
            "global_state": global_result,
            "verification": verification,
            "status": "trashed" if status in {"clean", "partial", "manual-review-needed"} else status,
            "cleanup_result": status,
            "restore_confirmation": "RESTORE CHAT",
        }
        write_json(trash_item_dir / "manifest.json", manifest)
        result = {
            "status": status,
            "trash_id": trash_id,
            "trash_dir": str(trash_item_dir),
            "backup_dir": str(backup.backup_dir),
            "thread_ids": thread_ids,
            "moved_files": moved_files,
            "files_modified": dedupe_keep_order(modified_files),
            "references_removed": {
                "session_index": jsonl_result,
                "global_state": global_result,
            },
            "sqlite_rows_affected": sqlite_results,
            "verification": verification,
            "restore_instructions": f"Restore trash item {trash_id} with confirmation RESTORE CHAT.",
        }
        self.backup.log_operation(
            {
                **result,
                "operation_type": "clean-move-to-trash",
                "dry_run": False,
                "selected_thread_ids": thread_ids,
                "original_paths_removed": [row["source"] for row in moved_files],
                "backup_folder_path": str(backup.backup_dir),
            }
        )
        return result

    def scan_missing_paths(self) -> dict[str, Any]:
        paths = self.scan_paths()
        global_state = read_json(self.global_state_path, {})
        labels = global_state.get("electron-workspace-root-labels", {})
        rows: list[dict[str, Any]] = []
        for item in paths.get("missing_paths") or []:
            path = item["path"]
            preview = self._preview_trash_bin_delete(
                item_type="missing-path",
                path=path,
                title=path,
                threads=[chat for chat in self.scan_chats() if chat.get("cwd") == path],
                scan_related_files=False,
            )
            rows.append(
                {
                    "path": path,
                    "label": labels.get(path, basename_label(path)) if isinstance(labels, dict) else basename_label(path),
                    "chat_count": len(preview["thread_ids"]),
                    "thread_ids": preview["thread_ids"],
                    "session_files": preview["session_files"],
                    "sqlite": preview["sqlite"],
                    "session_index_lines_to_remove": preview["session_index_lines_to_remove"],
                    "global_state_paths_to_remove": preview["global_state_paths_to_remove"],
                    "can_restore_or_relink": False,
                    "unrecoverable": not Path(path).exists(),
                    "safe_to_delete": bool(
                        preview.get("safe_to_delete")
                        and (
                            preview["thread_ids"]
                            or preview["global_state_paths_to_remove"]
                            or preview["config_project_blocks_to_remove"]
                        )
                    ),
                    "preview": preview,
                }
            )
        return {"missing_paths": rows, "count": len(rows)}

    def preview_delete_chat_to_trash_bin(self, thread_ids: list[str]) -> dict[str, Any]:
        threads = self._threads_by_id(thread_ids)
        return self._preview_trash_bin_delete(
            item_type="chat",
            title=", ".join(
                thread.get("display_title") or f"Untitled chat - {thread['id'][:8]}"
                for thread in threads
            ),
            threads=threads,
        )

    def delete_chat_to_trash_bin(
        self,
        thread_ids: list[str],
        confirmation: str = "",
        preview: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        preview = preview or self.preview_delete_chat_to_trash_bin(thread_ids)
        if confirmation != "DELETE CHAT":
            return {"status": "confirmation_required", "preview": preview}
        if not preview.get("safe_to_delete"):
            return {"status": "manual-review-needed", "preview": preview}
        return self._apply_trash_bin_delete(preview)

    def preview_delete_project_from_codex_to_trash_bin(self, path: str) -> dict[str, Any]:
        return self._preview_trash_bin_delete(
            item_type="project",
            path=path,
            title=path,
            threads=[chat for chat in self.scan_chats() if chat.get("cwd") == path],
        )

    def delete_project_from_codex_to_trash_bin(
        self,
        path: str,
        confirmation: str = "",
        preview: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        preview = preview or self.preview_delete_project_from_codex_to_trash_bin(path)
        if confirmation != "DELETE PROJECT FROM CODEX":
            return {"status": "confirmation_required", "preview": preview}
        if not preview.get("safe_to_delete"):
            return {"status": "manual-review-needed", "preview": preview}
        return self._apply_trash_bin_delete(preview)

    def preview_move_missing_path_to_trash_bin(self, path: str) -> dict[str, Any]:
        preview = self._preview_trash_bin_delete(
            item_type="missing-path",
            path=path,
            title=path,
            threads=[chat for chat in self.scan_chats() if chat.get("cwd") == path],
        )
        if Path(path).exists():
            preview["manual_review"] = dedupe_keep_order(
                list(preview.get("manual_review", []))
                + ["Path exists on disk; use project delete or relocation instead."]
            )
            preview["safe_to_delete"] = False
        return preview

    def move_missing_path_to_trash_bin(
        self,
        path: str,
        confirmation: str = "",
        preview: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        preview = preview or self.preview_move_missing_path_to_trash_bin(path)
        if confirmation != "MOVE MISSING PATHS TO TRASH":
            return {"status": "confirmation_required", "preview": preview}
        if not preview.get("safe_to_delete"):
            return {"status": "manual-review-needed", "preview": preview}
        return self._apply_trash_bin_delete(preview)

    def preview_move_all_unrecoverable_missing_paths_to_trash_bin(self) -> dict[str, Any]:
        scan = self.scan_missing_paths()
        paths = [row["path"] for row in scan["missing_paths"] if row.get("unrecoverable")]
        threads = [chat for chat in self.scan_chats() if chat.get("cwd") in set(paths)]
        return self._preview_trash_bin_delete(
            item_type="missing-path",
            paths=paths,
            title=f"{len(paths)} missing path(s)",
            threads=threads,
        )

    def move_all_unrecoverable_missing_paths_to_trash_bin(
        self,
        confirmation: str = "",
        preview: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        preview = preview or self.preview_move_all_unrecoverable_missing_paths_to_trash_bin()
        if confirmation != "MOVE MISSING PATHS TO TRASH":
            return {"status": "confirmation_required", "preview": preview}
        if not preview.get("safe_to_delete"):
            return {"status": "manual-review-needed", "preview": preview}
        return self._apply_trash_bin_delete(preview)

    def preview_move_codex_files_to_trash_bin(self, paths: list[str]) -> dict[str, Any]:
        safe_files: list[str] = []
        unsafe_files: list[dict[str, str]] = []
        for raw_path in paths:
            try:
                source = confined_path(self.codex_home, Path(raw_path), "Folder Cleaner selection")
            except (OSError, ValueError) as exc:
                unsafe_files.append({"path": raw_path, "reason": str(exc)})
                continue
            if not source.exists() or not source.is_file() or is_link_like(source):
                unsafe_files.append({"path": raw_path, "reason": "Not an existing file"})
                continue
            item = self._describe_codex_item(source, source.stat())
            if not item.get("safe_to_archive"):
                unsafe_files.append({"path": raw_path, "reason": "Not classified as a safe stale/backup candidate"})
                continue
            safe_files.append(str(source))
        preview = self._preview_trash_bin_delete(
            item_type="stale-file",
            title=f"{len(safe_files)} Codex file(s)",
            threads=[],
        )
        preview["files_to_move"] = dedupe_keep_order(safe_files)
        preview["session_files"] = []
        preview["unsafe_files"] = unsafe_files
        preview["safe_to_delete"] = bool(safe_files) and not unsafe_files
        return preview

    def move_codex_files_to_trash_bin(
        self,
        paths: list[str],
        confirmation: str = "",
        preview: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        preview = preview or self.preview_move_codex_files_to_trash_bin(paths)
        if confirmation != "MOVE CODEX FILES TO TRASH":
            return {"status": "confirmation_required", "preview": preview}
        if preview.get("unsafe_files") or not preview.get("safe_to_delete"):
            return {"status": "blocked", "preview": preview}
        return self._apply_trash_bin_delete(preview)

    def list_trash_bin(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for manifest_path in sorted(self.trash_bin_dir.glob("*/manifest.json"), reverse=True):
            try:
                manifest = read_json(manifest_path, {})
            except Exception:
                continue
            items.append(
                {
                    "item_id": manifest.get("item_id") or manifest_path.parent.name,
                    "item_type": manifest.get("item_type"),
                    "title": manifest.get("title") or manifest.get("path") or manifest_path.parent.name,
                    "path": manifest.get("path"),
                    "paths": manifest.get("paths") or [],
                    "deleted_at": manifest.get("deleted_at"),
                    "status": manifest.get("status"),
                    "thread_ids": manifest.get("thread_ids") or [],
                    "session_files": manifest.get("session_files") or [],
                    "size": self._folder_size(manifest_path.parent),
                    "trash_dir": str(manifest_path.parent),
                }
            )
        return items

    def preview_restore_trash_bin_item(self, item_id: str) -> dict[str, Any]:
        item_dir = managed_item_path(self.trash_bin_dir, item_id, "Trash Bin item")
        manifest = self._read_trash_bin_manifest(item_id)
        conflicts = [
            moved["source"]
            for moved in manifest.get("moved_files") or []
            if moved.get("source") and Path(moved["source"]).exists()
        ]
        metadata_conflicts = self._trash_bin_metadata_restore_conflicts(manifest, item_dir)
        if str(manifest.get("status") or "").startswith("failed"):
            metadata_conflicts.append(
                {"path": str(item_dir), "reason": "The delete operation rolled back; there is nothing to restore."}
            )
        if manifest.get("status") == "applying":
            metadata_conflicts.append(
                {
                    "path": str(item_dir),
                    "reason": "This Trash Bin item records an interrupted operation and requires review.",
                }
            )
        return {
            "operation": "restore_from_trash_bin",
            "item_id": item_id,
            "item_type": manifest.get("item_type"),
            "title": manifest.get("title"),
            "thread_ids": manifest.get("thread_ids") or [],
            "session_files": manifest.get("session_files") or [],
            "conflicts": conflicts,
            "metadata_conflicts": metadata_conflicts,
            "safe_to_restore_metadata": not metadata_conflicts,
            "dry_run": True,
        }

    def restore_trash_bin_item(
        self,
        item_id: str,
        confirmation: str = "",
        overwrite: bool = False,
    ) -> dict[str, Any]:
        if confirmation != "RESTORE FROM TRASH":
            return {"status": "confirmation_required", "item_id": item_id}
        manifest_path = managed_item_path(self.trash_bin_dir, item_id, "Trash Bin item") / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"Trash Bin manifest not found: {item_id}")
        manifest = read_json(manifest_path, {})
        preview = self.preview_restore_trash_bin_item(item_id)
        if preview["metadata_conflicts"]:
            return {
                "status": "manual-review-needed",
                "item_id": item_id,
                "metadata_conflicts": preview["metadata_conflicts"],
                "message": "Active metadata changed after deletion; automatic snapshot restore was blocked.",
            }
        if preview["conflicts"] and not overwrite:
            return {
                "status": "restore_conflict",
                "item_id": item_id,
                "conflicts": preview["conflicts"],
            }

        item_dir = manifest_path.parent
        metadata_rows = self._validated_trash_bin_metadata_rows(manifest, item_dir)
        guard_rows = self._create_trash_bin_restore_guard(item_dir, metadata_rows, manifest)
        self._assert_metadata_digests(
            manifest.get("post_delete_metadata_digests") or {},
            "Active Codex metadata changed while the restore guard was created.",
            [Path(row["source"]) for row in metadata_rows],
        )
        restored_metadata: list[dict[str, str]] = []
        restored_files: list[dict[str, str]] = []
        try:
            restored_metadata = self._restore_trash_bin_metadata_for_item(
                manifest,
                item_dir,
                metadata_rows,
            )
            for moved in manifest.get("moved_files") or []:
                source = confined_path(
                    self.codex_home,
                    Path(moved["source"]),
                    "Trash Bin restore destination",
                )
                trash = confined_path(
                    item_dir,
                    Path(moved["trash"]),
                    "Trash Bin restore source",
                )
                if not trash.exists():
                    continue
                source.parent.mkdir(parents=True, exist_ok=True)
                if source.exists() and overwrite:
                    if not source.is_file() or is_link_like(source):
                        raise ValueError(f"Unsafe restore conflict: {source}")
                    source.unlink()
                shutil.move(str(trash), str(source))
                original_mode = int(moved.get("original_mode", PRIVATE_FILE_MODE))
                if original_mode < 0 or original_mode > 0o777:
                    raise ValueError(f"Invalid restore file mode: {source}")
                set_private_mode(source, original_mode)
                restored_files.append(
                    {
                        "trash": str(trash),
                        "restored": str(source),
                        "restored_digest": file_sha256(source),
                    }
                )

            verification = self._verify_trash_bin_restore(manifest)
        except Exception as exc:
            rollback = self._rollback_failed_trash_bin_restore(
                manifest,
                guard_rows,
                restored_metadata,
                restored_files,
            )
            status = (
                "failed-manual-restore-needed"
                if rollback["conflicts"]
                else "failed-rollback-completed"
            )
            manifest["status"] = status
            manifest["restore_failure"] = {"error": str(exc), "rollback": rollback}
            write_json(manifest_path, manifest)
            result = {
                "status": status,
                "item_id": item_id,
                "error": str(exc),
                "rollback": rollback,
            }
            self.backup.log_operation(
                {**result, "operation_type": "restore-from-trash-bin", "dry_run": False}
            )
            return result

        manifest["status"] = "restored"
        manifest["restored_at"] = datetime.now(timezone.utc).isoformat()
        manifest["restore_verification"] = verification
        manifest["last_restore_guard"] = guard_rows[0].get("guard_dir", "") if guard_rows else ""
        write_json(manifest_path, manifest)
        result = {
            "status": "restored" if verification.get("restored") else "partial",
            "item_id": item_id,
            "restored_files": restored_files,
            "restored_metadata": restored_metadata,
            "restore_guard": guard_rows[0].get("guard_dir", "") if guard_rows else "",
            "verification": verification,
        }
        self.backup.log_operation({**result, "operation_type": "restore-from-trash-bin", "dry_run": False})
        return result

    def preview_permanently_delete_trash_bin_item(self, item_id: str) -> dict[str, Any]:
        item_dir = managed_item_path(self.trash_bin_dir, item_id, "Trash Bin item")
        manifest_path = item_dir / "manifest.json"
        if not manifest_path.is_file() or is_link_like(manifest_path):
            raise FileNotFoundError(f"Trash Bin item not found: {item_id}")
        manifest = read_json(manifest_path, {})
        return {
            "operation": "permanent-delete-trash-bin-item",
            "item_id": item_id,
            "item_type": manifest.get("item_type"),
            "title": manifest.get("title") or manifest.get("path") or item_id,
            "thread_ids": manifest.get("thread_ids") or [],
            "size": self._folder_size(item_dir),
            "manifest_digest": file_sha256(manifest_path),
            "deletes_active_codex_data": False,
            "dry_run": True,
        }

    def permanently_delete_trash_bin_item(self, item_id: str, confirmation: str = "") -> dict[str, Any]:
        if confirmation != "PERMANENT DELETE":
            return {"status": "confirmation_required", "item_id": item_id}
        item_dir = managed_item_path(self.trash_bin_dir, item_id, "Trash Bin item")
        if not item_dir.exists():
            raise FileNotFoundError(f"Trash Bin item not found: {item_id}")
        shutil.rmtree(item_dir)
        result = {"status": "permanently_deleted", "item_id": item_id, "trash_dir": str(item_dir)}
        self.backup.log_operation({**result, "operation_type": "permanent-delete-trash-bin", "dry_run": False})
        return result

    def list_trash(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for manifest_path in sorted(self.trash_dir.glob("*/manifest.json"), reverse=True):
            try:
                manifest = read_json(manifest_path, {})
            except Exception:
                continue
            items.append(
                {
                    "trash_id": manifest.get("trash_id") or manifest_path.parent.name,
                    "created_at": manifest.get("created_at"),
                    "status": manifest.get("status"),
                    "operation_type": manifest.get("operation_type", "legacy-trash"),
                    "cleanup_result": manifest.get("cleanup_result", ""),
                    "thread_ids": manifest.get("thread_ids") or [],
                    "title": ", ".join(
                        [thread.get("title") or thread.get("id") for thread in manifest.get("threads", [])]
                    ),
                    "trash_dir": str(manifest_path.parent),
                    "file_count": len(manifest.get("moved_files") or []),
                }
            )
        return items

    def restore_trash_item(self, trash_id: str, confirm: bool = False) -> dict[str, Any]:
        if confirm:
            return {
                "status": "manual-review-needed",
                "trash_id": trash_id,
                "message": "Legacy Trash restore is disabled; migrate the item to the unified Trash Bin.",
            }
        trash_item_dir = managed_item_path(self.trash_dir, trash_id, "legacy Trash item")
        manifest_path = trash_item_dir / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"Trash manifest not found: {trash_id}")
        manifest = read_json(manifest_path, {})
        if not confirm:
            return {"status": "preview_only", "trash": manifest}
        if manifest.get("operation_type") == "clean-move-to-trash":
            return self._restore_clean_trash_item(trash_id, manifest_path, manifest)

        affected_paths = [Path(path) for path in self.state_db_paths() if path.exists()]
        backup = self.backup.create_backup("restore-trash", affected_paths, manifest)
        restored_files: list[dict[str, str]] = []
        for moved in manifest.get("moved_files") or []:
            source = Path(moved["trash"])
            destination = Path(moved["source"])
            if not source.exists():
                continue
            source = confined_path(trash_item_dir, source, "legacy Trash restore source")
            destination = confined_path(
                self.codex_home,
                destination,
                "legacy Trash restore destination",
            )
            if destination.exists():
                return {
                    "status": "restore_conflict",
                    "trash_id": trash_id,
                    "conflicts": [str(destination)],
                }
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            restored_files.append({"trash": str(source), "restored": str(destination)})

        thread_ids = manifest.get("thread_ids") or []
        for db_path in self.state_db_paths():
            if db_path.exists():
                self._set_archived(db_path, thread_ids, archived=False)
                self._integrity_check(db_path)

        manifest["status"] = "restored"
        manifest["restored_at"] = datetime.now(timezone.utc).isoformat()
        write_json(manifest_path, manifest)
        result = {
            "status": "restored",
            "trash_id": trash_id,
            "backup_dir": str(backup.backup_dir),
            "restored_files": restored_files,
        }
        self.backup.log_operation(result)
        return result

    def _restore_clean_trash_item(
        self,
        trash_id: str,
        manifest_path: Path,
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "status": "manual-review-needed",
            "trash_id": trash_id,
            "message": "Legacy clean-trash metadata replacement is disabled.",
        }

        # Retained below only for reading historical manifests during migration.
        backup_dir = Path(manifest.get("backup_dir") or "")
        backup_manifest_path = backup_dir / "manifest.json"
        if not backup_manifest_path.exists():
            raise FileNotFoundError(f"Clean trash backup manifest not found: {backup_dir}")
        backup_manifest = read_json(backup_manifest_path, {})
        copied = backup_manifest.get("copied") or []
        moved_files = manifest.get("moved_files") or []
        moved_sources = {row.get("source") for row in moved_files if row.get("source")}
        conflicts = [source for source in moved_sources if source and Path(source).exists()]
        if conflicts:
            return {
                "status": "restore_conflict",
                "trash_id": trash_id,
                "conflicts": sorted(conflicts),
                "warning": "Restore would overwrite active files. Move or review conflicts first.",
            }

        metadata_sources = [
            Path(row["source"])
            for row in copied
            if row.get("source") and row.get("source") not in moved_sources and Path(row["source"]).exists()
        ]
        guard = self.backup.create_backup("restore-clean-trash", metadata_sources, manifest)

        restored_metadata: list[dict[str, str]] = []
        for row in copied:
            source_text = row.get("source")
            backup_text = row.get("backup")
            if not source_text or not backup_text or source_text in moved_sources:
                continue
            source = Path(source_text)
            backup = Path(backup_text)
            if not backup.exists():
                continue
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, source)
            if source.suffix == ".sqlite":
                self._integrity_check(source)
            restored_metadata.append({"source": str(source), "backup": str(backup)})

        backup_by_source = {
            row.get("source"): row.get("backup")
            for row in copied
            if row.get("source") and row.get("backup")
        }
        restored_files: list[dict[str, str]] = []
        for moved in moved_files:
            source = Path(moved["source"])
            trash = Path(moved["trash"])
            source.parent.mkdir(parents=True, exist_ok=True)
            if trash.exists():
                shutil.move(str(trash), str(source))
                restored_files.append({"trash": str(trash), "restored": str(source)})
                continue
            backup_text = backup_by_source.get(moved.get("source"))
            backup = Path(backup_text) if backup_text else None
            if backup and backup.exists():
                shutil.copy2(backup, source)
                restored_files.append({"backup": str(backup), "restored": str(source)})

        manifest["status"] = "restored"
        manifest["restored_at"] = datetime.now(timezone.utc).isoformat()
        write_json(manifest_path, manifest)
        result = {
            "status": "restored",
            "trash_id": trash_id,
            "guard_backup_dir": str(guard.backup_dir),
            "restored_files": restored_files,
            "restored_metadata": restored_metadata,
            "warning": "Clean-trash restore replaces active metadata files with their pre-clean backup copies.",
        }
        self.backup.log_operation(
            {
                **result,
                "operation_type": "restore-clean-trash",
                "dry_run": False,
            }
        )
        return result

    def purge_trash_item(
        self,
        trash_id: str,
        confirmation: str,
        remove_metadata: bool = False,
    ) -> dict[str, Any]:
        return {
            "status": "manual-review-needed",
            "trash_id": trash_id,
            "message": "Legacy purge is disabled; permanent deletion is available only in Trash Bin.",
        }

        if confirmation != "PERMANENT DELETE":
            raise ValueError("Permanent purge requires confirmation text: PERMANENT DELETE")
        trash_item_dir = managed_item_path(self.trash_dir, trash_id, "legacy Trash item")
        manifest_path = trash_item_dir / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"Trash manifest not found: {trash_id}")
        manifest = read_json(manifest_path, {})
        affected_paths = [Path(path) for path in self.state_db_paths() if path.exists()]
        backup = self.backup.create_backup("purge-trash", affected_paths, manifest)

        if remove_metadata:
            thread_ids = manifest.get("thread_ids") or []
            for db_path in self.state_db_paths():
                if db_path.exists():
                    self._delete_threads(db_path, thread_ids)
                    self._integrity_check(db_path)

        shutil.rmtree(trash_item_dir)
        result = {
            "status": "purged",
            "trash_id": trash_id,
            "backup_dir": str(backup.backup_dir),
            "remove_metadata": remove_metadata,
        }
        self.backup.log_operation(result)
        return result

    def scan_codex_folder(self) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for path in sorted(self.codex_home.rglob("*")):
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            items.append(self._describe_codex_item(path, stat))
        summary = self._folder_cleanliness_summary(items)
        result = {
            "codex_home": str(self.codex_home),
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "items": items,
            "summary": summary,
        }
        self.backup.log_operation(
            {
                "operation_type": "folder-cleaner-scan",
                "dry_run": True,
                "files_affected": [],
                "result": {
                    "total_items": summary["total_items"],
                    "cleanliness_score": summary["cleanliness_score"],
                },
            }
        )
        return result

    def preview_archive_codex_files(self, paths: list[str]) -> dict[str, Any]:
        archive_id = f"{utc_stamp()}-archive-{uuid.uuid4().hex[:8]}"
        files: list[dict[str, Any]] = []
        total_size = 0
        blocked: list[dict[str, Any]] = []
        for raw_path in paths:
            source = Path(raw_path).expanduser()
            item = self._describe_codex_item(source, source.stat()) if source.exists() else {
                "path": str(source),
                "risk_level": "unknown",
                "safe_to_archive": False,
                "size": 0,
                "explanation": "File does not exist.",
            }
            try:
                destination = self._archive_destination(archive_id, source)
            except ValueError as exc:
                destination = managed_item_path(self.codex_archive_dir, archive_id, "archive") / "blocked"
                item = {
                    **item,
                    "safe_to_archive": False,
                    "risk_level": "danger",
                    "explanation": str(exc),
                }
            row = {
                "source": str(source),
                "destination": str(destination),
                "size": item.get("size", 0),
                "risk_level": item.get("risk_level", "unknown"),
                "safe_to_archive": bool(item.get("safe_to_archive")),
                "explanation": item.get("explanation", ""),
            }
            files.append(row)
            total_size += int(item.get("size") or 0)
            if not row["safe_to_archive"]:
                blocked.append(row)
        return {
            "status": "preview_only",
            "operation": "archive_codex_files",
            "archive_id": archive_id,
            "archive_dir": str(self.codex_archive_dir / archive_id),
            "files": files,
            "total_files": len(files),
            "total_size": total_size,
            "blocked_files": blocked,
            "dry_run": True,
        }

    def archive_codex_files(self, paths: list[str], confirmation: str = "") -> dict[str, Any]:
        preview = self.preview_archive_codex_files(paths)
        if confirmation != "ARCHIVE CODEX FILES":
            return {"status": "confirmation_required", "preview": preview}
        return {
            "status": "manual-review-needed",
            "preview": preview,
            "message": "Legacy archive mutation is disabled; use Folder Cleaner and the unified Trash Bin.",
        }
        if preview["blocked_files"]:
            result = {"status": "blocked", "preview": preview}
            self.backup.log_operation(
                {
                    "operation_type": "archive-codex-files",
                    "dry_run": False,
                    "files_affected": paths,
                    "result": result["status"],
                    "errors": ["One or more files are not safe archive candidates."],
                }
            )
            return result

        archive_dir = managed_item_path(
            self.codex_archive_dir,
            preview["archive_id"],
            "archive",
        )
        archive_dir.mkdir(parents=True, exist_ok=False)
        moved: list[dict[str, Any]] = []
        errors: list[str] = []
        for row in preview["files"]:
            source = Path(row["source"])
            destination = Path(row["destination"])
            try:
                if not self._is_inside_codex_home(source):
                    raise ValueError("Source must be inside codex_home")
                if is_link_like(source):
                    raise ValueError("Links and junctions are not archive candidates")
                destination = confined_path(archive_dir, destination, "archive destination")
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))
                moved.append({**row, "result": "archived"})
            except Exception as exc:
                errors.append(f"{source}: {exc}")
        manifest = {
            "archive_id": preview["archive_id"],
            "operation_type": "archive-codex-files",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "codex_home": str(self.codex_home),
            "files": moved,
            "errors": errors,
            "restore_confirmation": "RESTORE CODEX ARCHIVE",
            "delete_confirmation": "PERMANENT DELETE ARCHIVE FILES",
        }
        write_json(archive_dir / "archive_manifest.json", manifest)
        result = {
            "status": "archived" if moved and not errors else "partial",
            "archive_id": preview["archive_id"],
            "archive_dir": str(archive_dir),
            "files": moved,
            "errors": errors,
        }
        self.backup.log_operation(
            {
                "operation_type": "archive-codex-files",
                "dry_run": False,
                "files_affected": [row["source"] for row in moved],
                "destination_paths": [row["destination"] for row in moved],
                "result": result["status"],
                "errors": errors,
            }
        )
        return result

    def list_codex_archives(self) -> list[dict[str, Any]]:
        archives: list[dict[str, Any]] = []
        for manifest_path in sorted(self.codex_archive_dir.glob("*/archive_manifest.json"), reverse=True):
            try:
                manifest = read_json(manifest_path, {})
            except Exception:
                continue
            archives.append(
                {
                    "archive_id": manifest.get("archive_id") or manifest_path.parent.name,
                    "created_at": manifest.get("created_at"),
                    "archive_dir": str(manifest_path.parent),
                    "file_count": len(manifest.get("files") or []),
                    "files": manifest.get("files") or [],
                    "errors": manifest.get("errors") or [],
                }
            )
        return archives

    def preview_restore_codex_archive(self, archive_id: str) -> dict[str, Any]:
        manifest = self._read_archive_manifest(archive_id)
        files = []
        for row in manifest.get("files") or []:
            destination = Path(row["source"])
            files.append(
                {
                    "source": row["destination"],
                    "destination": row["source"],
                    "destination_exists": destination.exists(),
                    "size": row.get("size", 0),
                }
            )
        return {
            "status": "preview_only",
            "archive_id": archive_id,
            "files": files,
            "dry_run": True,
        }

    def restore_codex_archive(
        self,
        archive_id: str,
        confirmation: str = "",
        overwrite: bool = False,
    ) -> dict[str, Any]:
        return {
            "status": "manual-review-needed",
            "archive_id": archive_id,
            "message": "Legacy archive restore is disabled; use the unified Trash Bin.",
        }

        preview = self.preview_restore_codex_archive(archive_id)
        if confirmation != "RESTORE CODEX ARCHIVE":
            return {"status": "confirmation_required", "preview": preview}
        conflicts = [row for row in preview["files"] if row["destination_exists"] and not overwrite]
        if conflicts:
            return {"status": "blocked", "conflicts": conflicts, "preview": preview}
        restored: list[dict[str, Any]] = []
        errors: list[str] = []
        archive_dir = managed_item_path(self.codex_archive_dir, archive_id, "archive")
        for row in preview["files"]:
            source = Path(row["source"])
            destination = Path(row["destination"])
            try:
                source = confined_path(archive_dir, source, "archive restore source")
                destination = confined_path(
                    self.codex_home,
                    destination,
                    "archive restore destination",
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))
                restored.append({**row, "result": "restored"})
            except Exception as exc:
                errors.append(f"{source}: {exc}")
        result = {
            "status": "restored" if restored and not errors else "partial",
            "archive_id": archive_id,
            "files": restored,
            "errors": errors,
        }
        self.backup.log_operation(
            {
                "operation_type": "restore-codex-archive",
                "dry_run": False,
                "files_affected": [row["destination"] for row in restored],
                "original_paths": [row["destination"] for row in restored],
                "source_paths": [row["source"] for row in restored],
                "result": result["status"],
                "errors": errors,
            }
        )
        return result

    def permanent_delete_archive_files(
        self,
        archive_id: str,
        paths: list[str],
        confirmation: str = "",
    ) -> dict[str, Any]:
        return {
            "status": "manual-review-needed",
            "archive_id": archive_id,
            "message": "Legacy archive deletion is disabled; permanent deletion is available only in Trash Bin.",
        }

        if confirmation != "PERMANENT DELETE ARCHIVE FILES":
            return {"status": "confirmation_required", "archive_id": archive_id}
        archive_dir = managed_item_path(self.codex_archive_dir, archive_id, "archive")
        manifest = self._read_archive_manifest(archive_id)
        allowed = {
            confined_path(archive_dir, Path(row["destination"]), "archived file")
            for row in manifest.get("files") or []
            if row.get("destination")
        }
        deleted: list[str] = []
        errors: list[str] = []
        for raw_path in paths:
            path = Path(raw_path)
            try:
                path = confined_path(archive_dir, path, "archived file")
                if path not in allowed:
                    raise ValueError("File does not belong to the selected archive")
                if path.exists() and path.is_file():
                    path.unlink()
                    deleted.append(str(path))
            except Exception as exc:
                errors.append(f"{path}: {exc}")
        result = {
            "status": "deleted" if deleted and not errors else "partial",
            "archive_id": archive_id,
            "deleted": deleted,
            "errors": errors,
        }
        self.backup.log_operation(
            {
                "operation_type": "delete-codex-archive-files",
                "dry_run": False,
                "files_affected": deleted,
                "result": result["status"],
                "errors": errors,
            }
        )
        return result

    def generate_codex_cleanliness_report(self) -> dict[str, Any]:
        scan = self.scan_codex_folder()
        report_path = self.app_home / "CODEX_FOLDER_CLEANLINESS_REPORT.md"
        atomic_write_text(report_path, self._render_cleanliness_report(scan))
        result = {
            "status": "generated",
            "report_path": str(report_path),
            "summary": scan["summary"],
        }
        self.backup.log_operation(
            {
                "operation_type": "folder-cleaner-report",
                "dry_run": True,
                "files_affected": [str(report_path)],
                "result": "generated",
            }
        )
        return result

    def preview_full_purge(self, thread_id: str) -> dict[str, Any]:
        trash_manifest_path, trash_manifest = self._find_trash_manifest_for_thread(thread_id)
        backup_manifest_path, backup_manifest = self._find_backup_manifest_for_thread(thread_id)
        thread = self._thread_for_purge_preview(thread_id, trash_manifest)
        rollout_path = self._rollout_path_for_purge(thread, trash_manifest, backup_manifest)
        title = (
            thread.get("display_title")
            or self._title_from_trash(trash_manifest, thread_id)
            or f"Untitled chat - {thread_id[:8]}"
        )
        original_session = Path(rollout_path) if rollout_path else None
        original_session_exists = bool(original_session and original_session.exists())
        refs = [thread_id]
        if rollout_path:
            refs.append(rollout_path)

        sqlite_preview: dict[str, Any] = {}
        manual_review: list[str] = []
        for db_path in self.state_db_paths():
            if not db_path.exists():
                continue
            label = self._db_label(db_path)
            sqlite_preview[label] = self._preview_sqlite_thread_rows(db_path, thread_id, rollout_path)
            manual_review.extend(
                [f"{label}:{table}" for table in sqlite_preview[label].get("manual_review_tables", [])]
            )

        index_lines = self._session_index_lines_to_remove(refs)
        global_state_paths = self._global_state_paths_to_remove(refs)
        has_reference_manifest = bool(trash_manifest_path or backup_manifest_path)
        has_trash_copy = self._trash_or_backup_session_exists(trash_manifest, backup_manifest, rollout_path)
        is_archived = bool(thread.get("archived")) if thread else bool(trash_manifest)
        eligible = bool(
            has_reference_manifest
            and (is_archived or not original_session_exists)
            and (has_trash_copy or not original_session_exists)
            and not manual_review
        )
        blocked_reason = ""
        if not eligible:
            if manual_review:
                blocked_reason = "Unknown SQLite relationships require manual review; no purge was applied."
            elif original_session_exists and not is_archived:
                blocked_reason = "Chat is still active; move it to trash first."
            elif not has_reference_manifest:
                blocked_reason = "No trash or backup manifest exists for this thread."
            elif not has_trash_copy and not original_session_exists:
                blocked_reason = "Session is missing and no trash or backup copy was found."
            else:
                blocked_reason = "Full purge eligibility could not be proven safely."

        references_found = (
            sum(sum(db.get("rows_to_delete", {}).values()) for db in sqlite_preview.values())
            + len(index_lines)
            + len(global_state_paths)
        )
        if not eligible or manual_review:
            confidence = "manual-review"
        elif references_found == 0:
            confidence = "partial"
        else:
            confidence = "safe"

        return {
            "operation": "full_purge_from_codex",
            "thread_id": thread_id,
            "title": title,
            "eligible": eligible,
            "blocked_reason": blocked_reason,
            "trash_manifest_path": str(trash_manifest_path) if trash_manifest_path else "",
            "backup_manifest_path": str(backup_manifest_path) if backup_manifest_path else "",
            "original_session_path": rollout_path,
            "original_session_exists": original_session_exists,
            "trash_or_backup_copy_exists": has_trash_copy,
            "sqlite": sqlite_preview,
            "session_index_lines_to_remove": index_lines,
            "global_state_paths_to_remove": global_state_paths,
            "unknown_references_skipped": manual_review,
            "cleanup_confidence": confidence,
            "dry_run": True,
        }

    def full_purge_chat(self, thread_id: str, confirmation: str = "") -> dict[str, Any]:
        return {
            "status": "manual-review-needed",
            "thread_id": thread_id,
            "message": "Full Purge is disabled; use Delete Chat and the unified Trash Bin.",
        }

        preview = self.preview_full_purge(thread_id)
        if confirmation != "FULL PURGE CODEX CHAT":
            return {"status": "confirmation_required", "preview": preview}
        if not preview["eligible"]:
            return {"status": "blocked", "preview": preview}

        purge_id = f"{utc_stamp()}-purge-{thread_id[:8]}"
        purge_dir = managed_item_path(self.purge_backups_dir, purge_id, "purge backup")
        purge_dir.mkdir(parents=True, exist_ok=False)
        copied = self._copy_purge_backup_files(purge_dir, preview)

        modified_files: list[str] = []
        sqlite_results: dict[str, Any] = {}
        jsonl_result: dict[str, Any] = {}
        global_result: dict[str, Any] = {}
        moved_session: dict[str, str] | None = None
        try:
            moved_session = self._move_session_to_existing_trash_if_needed(preview)
            for db_path in self.state_db_paths():
                if not db_path.exists():
                    continue
                label = self._db_label(db_path)
                sqlite_results[label] = self._delete_sqlite_thread_rows(
                    db_path,
                    thread_id,
                    preview.get("original_session_path") or "",
                )
                if sum(sqlite_results[label].get("deleted_rows", {}).values()) > 0:
                    modified_files.append(str(db_path))

            jsonl_result = self._purge_session_index_lines(
                thread_id,
                preview.get("original_session_path") or "",
            )
            if jsonl_result.get("removed_count", 0) > 0:
                modified_files.append(str(self.session_index_path))

            global_result = self._purge_global_state_refs(
                thread_id,
                preview.get("original_session_path") or "",
            )
            if global_result.get("removed_paths"):
                modified_files.append(str(self.global_state_path))

            verification = self.verify_full_purge(thread_id, preview.get("original_session_path") or "")
            status = self._purge_status_from_verification(verification)
        except Exception as exc:
            try:
                self._restore_files_from_purge_backup(copied)
                status = "failed-rollback-completed"
            except Exception as restore_exc:
                status = "failed-manual-restore-needed"
                exc = RuntimeError(f"{exc}; rollback failed: {restore_exc}")
            verification = {"error": str(exc)}
            sqlite_results.setdefault("error", str(exc))

        manifest = {
            "purge_id": purge_id,
            "operation_type": "full-purge-codex-chat",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "thread_id": thread_id,
            "title": preview.get("title"),
            "preview": preview,
            "copied": copied,
            "moved_session": moved_session,
            "modified_files": dedupe_keep_order(modified_files),
            "sqlite_tables_affected": sqlite_results,
            "session_index": jsonl_result,
            "global_state": global_result,
            "verification": verification,
            "status": status,
            "restore_confirmation": "RESTORE FULL PURGE",
        }
        write_json(purge_dir / "purge_manifest.json", manifest)
        atomic_write_text(
            purge_dir / "PURGE_REPORT.md",
            self._render_purge_report(manifest),
        )

        result = {
            "status": status,
            "purge_id": purge_id,
            "purge_backup_dir": str(purge_dir),
            "thread_id": thread_id,
            "title": preview.get("title"),
            "files_modified": dedupe_keep_order(modified_files),
            "sqlite_tables_affected": sqlite_results,
            "jsonl_lines_removed": jsonl_result.get("removed_count", 0),
            "global_state_paths_removed": global_result.get("removed_paths", []),
            "verification": verification,
            "restore_instructions": f"Restore with purge id {purge_id} and confirmation RESTORE FULL PURGE.",
        }
        self.backup.log_operation(
            {
                **result,
                "dry_run": False,
                "backup_folder_path": str(purge_dir),
            }
        )
        return result

    def list_purge_backups(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for manifest_path in sorted(self.purge_backups_dir.glob("*/purge_manifest.json"), reverse=True):
            try:
                manifest = read_json(manifest_path, {})
            except Exception:
                continue
            rows.append(
                {
                    "purge_id": manifest.get("purge_id") or manifest_path.parent.name,
                    "thread_id": manifest.get("thread_id"),
                    "title": manifest.get("title"),
                    "created_at": manifest.get("created_at"),
                    "status": manifest.get("status"),
                    "purge_backup_dir": str(manifest_path.parent),
                }
            )
        return rows

    def restore_purge_backup(self, purge_id: str, confirmation: str = "") -> dict[str, Any]:
        return {
            "status": "manual-review-needed",
            "purge_id": purge_id,
            "message": "Legacy purge restore is disabled; review the historical backup manually.",
        }

        if confirmation != "RESTORE FULL PURGE":
            return {"status": "confirmation_required", "purge_id": purge_id}
        purge_dir = managed_item_path(self.purge_backups_dir, purge_id, "purge backup")
        manifest_path = purge_dir / "purge_manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"Purge manifest not found: {purge_id}")
        manifest = read_json(manifest_path, {})
        copied = manifest.get("copied") or []
        affected_paths = [Path(row["source"]) for row in copied if row.get("source")]
        guard = self.backup.create_backup("restore-purge-guard", affected_paths, manifest)
        self._restore_files_from_purge_backup(copied)

        preview = manifest.get("preview") or {}
        session_path = preview.get("original_session_path") or ""
        restored_session = self._restore_session_from_trash_or_backup(
            session_path,
            manifest,
        )
        for db_path in self.state_db_paths():
            if db_path.exists():
                self._integrity_check(db_path)
        result = {
            "status": "restored_purge_backup",
            "purge_id": purge_id,
            "guard_backup_dir": str(guard.backup_dir),
            "restored_files": copied,
            "restored_session": restored_session,
            "warning": "Restoring a full purge replaces active files with the purge backup versions.",
        }
        self.backup.log_operation(result)
        return result

    def list_backups(self) -> list[dict[str, Any]]:
        return self.backup.list_backups()

    def preview_backup_restore(self, operation_id: str) -> dict[str, Any]:
        return self.backup.restore_backup(operation_id, confirm=False)

    def restore_backup(self, operation_id: str, confirm: bool = False) -> dict[str, Any]:
        return self.backup.restore_backup(operation_id, confirm=confirm)

    def list_logs(self, limit: int = 250) -> list[dict[str, Any]]:
        log_path = self.backup.logs_dir / "operations.jsonl"
        if not log_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with log_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
        return rows[-limit:][::-1]

    def verify_full_purge(self, thread_id: str, rollout_path: str) -> dict[str, Any]:
        sqlite_clear: dict[str, bool] = {}
        sqlite_integrity: dict[str, str] = {}
        manual_review: list[str] = []
        for db_path in self.state_db_paths():
            if not db_path.exists():
                continue
            label = self._db_label(db_path)
            preview = self._preview_sqlite_thread_rows(db_path, thread_id, rollout_path)
            sqlite_clear[label] = sum(preview.get("rows_to_delete", {}).values()) == 0
            manual_review.extend(
                f"{label}:{table}" for table in preview.get("manual_review_tables") or []
            )
            try:
                self._integrity_check(db_path)
                sqlite_integrity[label] = "ok"
            except Exception as exc:
                sqlite_integrity[label] = str(exc)

        thread_refs = [thread_id]
        rollout_refs = [rollout_path] if rollout_path else []
        session_path = Path(rollout_path) if rollout_path else None
        trash_manifest_path, trash_manifest = self._find_trash_manifest_for_thread(thread_id)
        backup_manifest_path, backup_manifest = self._find_backup_manifest_for_thread(thread_id)
        return {
            "sqlite_thread_absent": all(sqlite_clear.values()) if sqlite_clear else True,
            "sqlite_integrity": sqlite_integrity,
            "session_index_thread_absent": not self._session_index_lines_to_remove(thread_refs),
            "session_index_rollout_absent": not self._session_index_lines_to_remove(rollout_refs),
            "global_state_thread_absent": not self._global_state_paths_to_remove(thread_refs),
            "global_state_rollout_absent": not self._global_state_paths_to_remove(rollout_refs),
            "original_session_missing": not session_path or not session_path.exists(),
            "trash_or_backup_copy_exists": self._trash_or_backup_session_exists(
                trash_manifest,
                backup_manifest,
                rollout_path,
            ),
            "trash_manifest_path": str(trash_manifest_path) if trash_manifest_path else "",
            "backup_manifest_path": str(backup_manifest_path) if backup_manifest_path else "",
            "manual_review": dedupe_keep_order(manual_review),
        }

    def verify_clean_trash(
        self,
        preview: dict[str, Any],
        moved_files: list[dict[str, str]],
        backup_dir: Path,
    ) -> dict[str, Any]:
        refs = self._refs_from_clean_preview(preview)
        sqlite_clear: dict[str, bool] = {}
        sqlite_integrity: dict[str, str] = {}
        sqlite_manual_review: list[str] = []
        for db_path in self.state_db_paths():
            if not db_path.exists():
                continue
            label = self._db_label(db_path)
            rows_left = 0
            manual_tables: list[str] = []
            for thread in preview.get("threads") or []:
                db_preview = self._preview_sqlite_thread_rows(
                    db_path,
                    thread["id"],
                    thread.get("rollout_path") or "",
                )
                rows_left += sum((db_preview.get("rows_to_delete") or {}).values())
                manual_tables.extend(db_preview.get("manual_review_tables") or [])
            manual_tables = sorted(set(manual_tables))
            sqlite_clear[label] = rows_left == 0
            sqlite_manual_review.extend([f"{label}:{table}" for table in manual_tables])
            try:
                self._integrity_check(db_path)
                sqlite_integrity[label] = "ok"
            except Exception as exc:
                sqlite_integrity[label] = str(exc)

        session_refs_absent = not self._session_index_lines_to_remove(refs)
        global_refs_absent = not self._global_state_paths_to_remove(refs)
        source_paths_missing = {
            row["source"]: not Path(row["source"]).exists()
            for row in moved_files
            if row.get("source")
        }
        trash_copies_exist = {
            row["trash"]: Path(row["trash"]).exists()
            for row in moved_files
            if row.get("trash")
        }
        backup_manifest = read_json(backup_dir / "manifest.json", {})
        backup_copied_sources = {
            row.get("source")
            for row in backup_manifest.get("copied") or []
            if row.get("source") and Path(row.get("backup", "")).exists()
        }
        affected_sources = set(preview.get("affected_files") or [])
        backup_contains_restore_data = affected_sources.issubset(backup_copied_sources)
        integrity_ok = all(value == "ok" for value in sqlite_integrity.values())
        known_refs_absent = all(
            [
                all(sqlite_clear.values()) if sqlite_clear else True,
                session_refs_absent,
                global_refs_absent,
            ]
        )
        files_moved = all(source_paths_missing.values()) and all(trash_copies_exist.values())
        manual = dedupe_keep_order(sqlite_manual_review or preview.get("unsafe_or_unknown_references") or [])
        if manual:
            result = "manual-review-needed"
        elif known_refs_absent and files_moved and backup_contains_restore_data and integrity_ok:
            result = "clean"
        else:
            result = "partial"
        return {
            "result": result,
            "source_paths_missing": source_paths_missing,
            "trash_copies_exist": trash_copies_exist,
            "backup_contains_restore_data": backup_contains_restore_data,
            "known_refs_absent": known_refs_absent,
            "sqlite_thread_absent": all(sqlite_clear.values()) if sqlite_clear else True,
            "sqlite_integrity": sqlite_integrity,
            "session_index_refs_absent": session_refs_absent,
            "global_state_refs_absent": global_refs_absent,
            "manual_review_references": manual,
        }

    def _purge_status_from_verification(self, verification: dict[str, Any]) -> str:
        if verification.get("manual_review"):
            return "manual-review-needed"
        integrity_ok = all(value == "ok" for value in verification.get("sqlite_integrity", {}).values())
        required = [
            verification.get("sqlite_thread_absent"),
            verification.get("session_index_thread_absent"),
            verification.get("session_index_rollout_absent"),
            verification.get("global_state_thread_absent"),
            verification.get("global_state_rollout_absent"),
            verification.get("original_session_missing"),
            verification.get("trash_or_backup_copy_exists"),
            integrity_ok,
        ]
        return "fully-purged" if all(required) else "partially-purged"

    def _describe_codex_item(self, path: Path, stat: os.stat_result) -> dict[str, Any]:
        relative = self._relative_to_codex(path)
        name = path.name
        is_dir = path.is_dir()
        file_type = self._file_type_for_path(path, relative, is_dir)
        backup_info = self._backup_info_for_path(path)
        looks_like_backup = backup_info["looks_like_backup"]
        looks_like_temporary = self._looks_like_temporary(name)
        looks_like_database_copy = bool(backup_info["looks_like_database_copy"])
        active_kind = self._active_kind_for_relative(relative)
        probable_original = backup_info["probable_original"]
        active_equivalent_exists = bool(probable_original and Path(probable_original).exists())
        active_equivalent_newer = False
        if probable_original and Path(probable_original).exists():
            active_equivalent_newer = Path(probable_original).stat().st_mtime >= stat.st_mtime
        sensitive_auth_artifact = bool(
            re.fullmatch(
                r"(?:auth|credentials?|tokens?|cookies?)(?:\.json)?(?:[._-].*)?",
                name.lower(),
            )
        )

        if sensitive_auth_artifact:
            risk_level = "danger"
            safe_to_archive = False
            explanation = "Credential-related Codex data. This tool will not move, archive, or delete it."
        elif active_kind:
            risk_level = "active" if active_kind.startswith("active") else "important"
            safe_to_archive = False
            explanation = f"Active Codex {active_kind}. This should not be touched by Folder Cleaner."
        elif looks_like_temporary:
            temporary_age_seconds = max(0.0, time.time() - stat.st_mtime)
            if temporary_age_seconds >= TEMP_FILE_STALE_SECONDS:
                risk_level = "stale"
                safe_to_archive = True
                explanation = "Temporary-looking file older than 24 hours. It is not recognized as active Codex state and is a conservative Trash Bin candidate."
            else:
                risk_level = "unknown"
                safe_to_archive = False
                explanation = "Recent temporary-looking file. It may still be in use, so leave it in place and review later."
        elif looks_like_backup:
            risk_level = "stale" if ".before-" in name else "backup"
            safe_to_archive = True
            explanation = self._backup_explanation(path, backup_info, active_equivalent_exists, active_equivalent_newer)
        elif is_dir:
            risk_level = "important" if relative.split("/", 1)[0] in self._important_top_dirs() else "unknown"
            safe_to_archive = False
            explanation = "Directory. Folder Cleaner only archives selected files, not whole directories."
        else:
            risk_level = "unknown"
            safe_to_archive = False
            explanation = "Unrecognized file. Manual review required; not recommended for automatic archive."

        return {
            "name": name,
            "path": str(path),
            "relative_path": relative,
            "file_type": file_type,
            "size": stat.st_size if not is_dir else 0,
            "created_at": self._iso_from_timestamp(getattr(stat, "st_birthtime", stat.st_ctime)),
            "modified_at": self._iso_from_timestamp(stat.st_mtime),
            "accessed_at": self._iso_from_timestamp(stat.st_atime),
            "is_directory": is_dir,
            "likely_active_codex_data": bool(active_kind),
            "looks_like_backup": looks_like_backup,
            "looks_like_temporary": looks_like_temporary,
            "looks_like_stale_relocation_backup": bool(backup_info["looks_like_stale_relocation_backup"]),
            "looks_like_database_copy": looks_like_database_copy,
            "contains_sensitive_auth_data": sensitive_auth_artifact,
            "safe_to_archive": safe_to_archive,
            "risk_level": risk_level,
            "probable_original": probable_original,
            "active_equivalent_exists": active_equivalent_exists,
            "active_equivalent_newer": active_equivalent_newer,
            "explanation": explanation,
        }

    def _folder_cleanliness_summary(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        counts = {
            "total_items": len(items),
            "total_files": len([item for item in items if not item["is_directory"]]),
            "active_files": len([item for item in items if item["risk_level"] == "active"]),
            "important_files": len([item for item in items if item["risk_level"] == "important"]),
            "backup_files": len([item for item in items if item["risk_level"] == "backup"]),
            "stale_candidates": len([item for item in items if item["risk_level"] == "stale"]),
            "unknown_files": len([item for item in items if item["risk_level"] == "unknown" and not item["is_directory"]]),
            "danger_files": len([item for item in items if item["risk_level"] == "danger"]),
            "archive_candidate_size": sum(item["size"] for item in items if item["safe_to_archive"]),
        }
        if counts["danger_files"] > 0 and counts["unknown_files"] > 10:
            score = "Risky"
        elif counts["stale_candidates"] > 3 or counts["unknown_files"] > 0:
            score = "Needs review"
        elif counts["backup_files"] > 0 or counts["stale_candidates"] > 0:
            score = "Mostly clean"
        else:
            score = "Clean"
        return {**counts, "cleanliness_score": score}

    def _backup_info_for_path(self, path: Path) -> dict[str, Any]:
        name = path.name
        lower = name.lower()
        backup_patterns = [
            ".bak",
            ".backup",
            ".old",
            ".copy",
            ".before-",
            "-backup-",
        ]
        looks_like_backup = any(pattern in lower or lower.endswith(pattern) for pattern in backup_patterns)
        looks_like_backup = looks_like_backup or bool(re.search(r"[-_.]20\d{6}(?:[-_]\d{6})?$", name))
        looks_like_database_copy = ".sqlite.before-" in lower or (".sqlite" in lower and looks_like_backup)
        looks_like_stale_relocation_backup = (
            "relocat" in lower or "cwd-update" in lower or "working-directory" in lower
        )
        probable_original = ""
        if ".before-" in name:
            probable_original = str(path.with_name(name.split(".before-", 1)[0]))
        elif lower.endswith(".bak"):
            probable_original = str(path.with_name(name[:-4]))
        elif lower.endswith(".backup"):
            probable_original = str(path.with_name(name[:-7]))
        elif lower.endswith(".old"):
            probable_original = str(path.with_name(name[:-4]))
        elif lower.endswith(".copy"):
            probable_original = str(path.with_name(name[:-5]))
        return {
            "looks_like_backup": looks_like_backup,
            "looks_like_database_copy": looks_like_database_copy,
            "looks_like_stale_relocation_backup": looks_like_stale_relocation_backup,
            "probable_original": probable_original,
        }

    @staticmethod
    def _looks_like_temporary(name: str) -> bool:
        lower = name.lower()
        return lower.endswith(".tmp") or lower.endswith(".temp") or lower.startswith(".tmp") or lower.endswith("~")

    def _active_kind_for_relative(self, relative: str) -> str:
        active_exact = {
            "config.toml": "active config",
            "state_5.sqlite": "active database/state",
            "sqlite/state_5.sqlite": "active database/state",
            "session_index.jsonl": "active session index",
            ".codex-global-state.json": "active global state",
            "logs_2.sqlite": "important logs database",
            "sqlite/logs_2.sqlite": "important logs database",
        }
        if relative in active_exact:
            return active_exact[relative]
        if relative.startswith("sessions/") and relative.endswith(".jsonl"):
            return "important chat session log"
        if relative.startswith("attachments/"):
            return "important attachment data"
        if relative.startswith("browser/"):
            return "important browser/plugin state"
        if relative.startswith("plugins/") or relative.startswith("cache/"):
            return "important runtime/plugin cache"
        if relative.startswith("memories/"):
            return "important memory data"
        return ""

    @staticmethod
    def _important_top_dirs() -> set[str]:
        return {"sessions", "attachments", "browser", "plugins", "cache", "memories", "sqlite"}

    def _file_type_for_path(self, path: Path, relative: str, is_dir: bool) -> str:
        if is_dir:
            return "directory"
        name = path.name.lower()
        if ".sqlite.before-" in name:
            return "sqlite-backup"
        if name.endswith(".sqlite"):
            return "sqlite"
        if name.endswith(".jsonl"):
            return "jsonl"
        if name.endswith(".json"):
            return "json"
        if ".toml.before-" in name:
            return "toml-backup"
        if name.endswith(".toml"):
            return "toml"
        if self._looks_like_temporary(name):
            return "temporary"
        return path.suffix.lower().lstrip(".") or "unknown"

    def _backup_explanation(
        self,
        path: Path,
        backup_info: dict[str, Any],
        active_equivalent_exists: bool,
        active_equivalent_newer: bool,
    ) -> str:
        name = path.name
        original_name = Path(backup_info["probable_original"]).name if backup_info["probable_original"] else "an active file"
        reason = "a previous operation"
        if "relocat" in name.lower():
            reason = "a path relocation operation"
        elif "cwd" in name.lower() or "working-directory" in name.lower():
            reason = "a working-directory update operation"
        timestamp = self._timestamp_from_backup_name(name)
        parts = [f"Likely a backup copy of {original_name} created before {reason}"]
        if timestamp:
            parts[0] += f" on {timestamp}"
        parts[0] += "."
        if active_equivalent_exists:
            parts.append("The active equivalent file still exists.")
            if active_equivalent_newer:
                parts.append("The active equivalent is newer or same-aged.")
        else:
            parts.append("No active equivalent file was found, so review before archiving.")
        parts.append("Codex probably does not load this backup directly; archive instead of deleting.")
        return " ".join(parts)

    @staticmethod
    def _timestamp_from_backup_name(name: str) -> str:
        match = re.search(r"(20\d{6})(?:[-_](\d{6}))?", name)
        if not match:
            return ""
        date = match.group(1)
        time_part = match.group(2)
        if not time_part:
            return f"{date[:4]}-{date[4:6]}-{date[6:]}"
        return f"{date[:4]}-{date[4:6]}-{date[6:]} {time_part[:2]}:{time_part[2:4]}:{time_part[4:]}"

    def _archive_destination(self, archive_id: str, source: Path) -> Path:
        archive_dir = managed_item_path(self.codex_archive_dir, archive_id, "archive")
        return confined_path(
            archive_dir,
            archive_dir / self._trash_relative(source),
            "archive destination",
        )

    def _read_archive_manifest(self, archive_id: str) -> dict[str, Any]:
        manifest_path = managed_item_path(self.codex_archive_dir, archive_id, "archive") / "archive_manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"Archive manifest not found: {archive_id}")
        return read_json(manifest_path, {})

    def _is_inside_codex_home(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.codex_home.resolve())
            return True
        except ValueError:
            return False

    def _is_inside_archive(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.codex_archive_dir.resolve())
            return True
        except ValueError:
            return False

    def _relative_to_codex(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.codex_home))
        except ValueError:
            return str(path)

    @staticmethod
    def _iso_from_timestamp(value: float) -> str:
        return datetime.fromtimestamp(value, timezone.utc).isoformat()

    def _render_cleanliness_report(self, scan: dict[str, Any]) -> str:
        summary = scan["summary"]
        items = scan["items"]
        suspicious = [
            item
            for item in items
            if item["looks_like_backup"] or item["looks_like_temporary"] or item["risk_level"] == "unknown"
        ]
        recommended = [item for item in items if item["safe_to_archive"]]
        never_touch = [item for item in items if item["risk_level"] in {"active", "danger"}]
        unknown = [item for item in items if item["risk_level"] == "unknown" and not item["is_directory"]]
        lines = [
            "# Codex Folder Cleanliness Report",
            "",
            f"Generated: {datetime.now(timezone.utc).isoformat()}",
            f"Codex home: `{self.codex_home}`",
            "",
            "## Summary",
            "",
            f"- Cleanliness score: {summary['cleanliness_score']}",
            f"- Total items scanned: {summary['total_items']}",
            f"- Total files scanned: {summary['total_files']}",
            f"- Active files: {summary['active_files']}",
            f"- Important files: {summary['important_files']}",
            f"- Backup files: {summary['backup_files']}",
            f"- Stale candidates: {summary['stale_candidates']}",
            f"- Unknown files: {summary['unknown_files']}",
            f"- Archive candidate size: {summary['archive_candidate_size']} bytes",
            "",
            "## Suspicious / Backup-Looking Files",
            "",
        ]
        lines.extend(
            f"- `{item['relative_path']}` — {item['risk_level']}: {item['explanation']}"
            for item in suspicious[:200]
        )
        if not suspicious:
            lines.append("- None detected.")
        lines.extend(["", "## Recommended For Archive", ""])
        lines.extend(
            f"- `{item['relative_path']}` ({item['size']} bytes)"
            for item in recommended[:200]
        )
        if not recommended:
            lines.append("- None.")
        lines.extend(["", "## Must Not Touch", ""])
        lines.extend(
            f"- `{item['relative_path']}` — {item['risk_level']}"
            for item in never_touch[:200]
        )
        lines.extend(["", "## Unknown Files Requiring Manual Review", ""])
        lines.extend(f"- `{item['relative_path']}`" for item in unknown[:200])
        if not unknown:
            lines.append("- None.")
        lines.extend(
            [
                "",
                "## Cleanup Recommendation",
                "",
                "Move only files marked safe-to-archive into `trash-bin`. Do not delete anything directly from `.codex`; permanent deletion is available only for Trash Bin items after a separate confirmation.",
            ]
        )
        return "\n".join(lines) + "\n"

    def _preview_trash_bin_delete(
        self,
        item_type: str,
        title: str,
        threads: list[dict[str, Any]],
        path: str = "",
        paths: list[str] | None = None,
        scan_related_files: bool = True,
    ) -> dict[str, Any]:
        paths = paths or ([path] if path else [])
        thread_ids = [thread["id"] for thread in threads]
        refs: list[str] = []
        refs.extend(thread_ids)
        refs.extend([thread.get("rollout_path") for thread in threads if thread.get("rollout_path")])
        refs.extend(paths)
        refs = dedupe_keep_order([ref for ref in refs if ref])

        files_to_move: list[str] = []
        session_files: list[str] = []
        for thread in threads:
            if thread.get("rollout_path"):
                session_files.append(thread["rollout_path"])
            if scan_related_files:
                files_to_move.extend([str(path) for path in self._related_files_for_thread(thread)])
            elif thread.get("rollout_path") and Path(thread["rollout_path"]).exists():
                files_to_move.append(thread["rollout_path"])

        sqlite_preview: dict[str, Any] = {}
        manual_review: list[str] = []
        for db_path in self.state_db_paths():
            if not db_path.exists():
                continue
            label = self._db_label(db_path)
            rows_to_delete: dict[str, int] = {}
            manual_tables: list[str] = []
            for thread in threads:
                preview = self._preview_sqlite_thread_rows(
                    db_path,
                    thread["id"],
                    thread.get("rollout_path") or "",
                )
                for table_name, count in preview.get("rows_to_delete", {}).items():
                    rows_to_delete[table_name] = rows_to_delete.get(table_name, 0) + int(count)
                manual_tables.extend(preview.get("manual_review_tables") or [])
            manual_tables = sorted(set(manual_tables))
            sqlite_preview[label] = {
                "database": str(db_path),
                "rows_to_delete": rows_to_delete,
                "manual_review_tables": manual_tables,
            }
            manual_review.extend([f"{label}:{table}" for table in manual_tables])

        session_index_lines = self._session_index_lines_to_remove(refs)
        global_state_paths = self._global_state_paths_to_remove(refs)
        config_blocks = [value for value in paths if self._config_has_project_block(value)]
        return {
            "operation": "trash_bin_delete",
            "item_type": item_type,
            "title": title,
            "path": path,
            "paths": paths,
            "thread_ids": thread_ids,
            "threads": threads,
            "session_files": dedupe_keep_order(session_files),
            "files_to_move": dedupe_keep_order(files_to_move),
            "sqlite": sqlite_preview,
            "session_index_lines_to_remove": session_index_lines,
            "removed_session_index_lines": self._session_index_line_exports(refs),
            "global_state_paths_to_remove": global_state_paths,
            "config_project_blocks_to_remove": config_blocks,
            "metadata_snapshot_files": [
                str(source) for source in self._trash_bin_metadata_sources(include_config=bool(paths))
            ],
            "active_metadata_digests": self._metadata_source_digests(
                [
                    {"source": str(source)}
                    for source in self._trash_bin_metadata_sources(include_config=bool(paths))
                ]
            ),
            "metadata_digest_algorithm": "sqlite-logical-v1+sha256-v1",
            "manual_review": dedupe_keep_order(manual_review),
            "safe_to_delete": not manual_review,
            "dry_run": True,
        }

    def _reappeared_missing_paths(self, preview: dict[str, Any]) -> list[str]:
        if preview.get("item_type") != "missing-path":
            return []
        paths = preview.get("paths") or [preview.get("path")]
        return [str(path) for path in paths if path and Path(str(path)).exists()]

    def _block_reappeared_missing_paths(
        self,
        preview: dict[str, Any],
        restored_paths: list[str],
    ) -> dict[str, Any]:
        preview["manual_review"] = dedupe_keep_order(
            list(preview.get("manual_review", []))
            + [f"Path now exists on disk and was not changed: {path}" for path in restored_paths]
        )
        preview["safe_to_delete"] = False
        return {"status": "manual-review-needed", "preview": preview}

    def _apply_trash_bin_delete(self, preview: dict[str, Any]) -> dict[str, Any]:
        if not preview.get("safe_to_delete"):
            return {"status": "manual-review-needed", "preview": preview}
        restored_paths = self._reappeared_missing_paths(preview)
        if restored_paths:
            return self._block_reappeared_missing_paths(preview, restored_paths)
        pre_delete_metadata_digests = preview.get("active_metadata_digests") or {}
        if not pre_delete_metadata_digests:
            raise RuntimeError("A current metadata fingerprint is required before deletion")
        self._assert_metadata_digests(
            pre_delete_metadata_digests,
            "Active Codex metadata changed after preview; run the preview again.",
            self._trash_bin_metadata_sources(include_config=bool(preview.get("paths"))),
        )
        short_id = (preview.get("thread_ids") or preview.get("paths") or [uuid.uuid4().hex])[0]
        safe_short = re.sub(r"[^A-Za-z0-9_-]+", "-", str(short_id))[:12] or uuid.uuid4().hex[:8]
        item_id = f"{utc_stamp()}-{preview['item_type']}-{safe_short}"
        item_dir = managed_item_path(self.trash_bin_dir, item_id, "Trash Bin item")
        ensure_private_dir(item_dir, exist_ok=False)
        moved_dir = item_dir / "moved"
        ensure_private_dir(moved_dir)

        try:
            sqlite_rows = self._export_sqlite_rows_for_preview(preview)
            write_json(item_dir / "sqlite_rows.json", sqlite_rows)
            metadata_snapshots = self._copy_trash_bin_metadata_snapshots(item_dir, preview)
            for row in metadata_snapshots:
                source_key = str(Path(row["source"]).resolve())
                expected_digest = pre_delete_metadata_digests.get(source_key)
                if not expected_digest or metadata_file_digest(Path(row["snapshot"])) != expected_digest:
                    raise RuntimeError(f"Metadata snapshot changed while it was being created: {source_key}")
            self._assert_metadata_digests(
                pre_delete_metadata_digests,
                "Active Codex metadata changed while snapshots were being created.",
                self._trash_bin_metadata_sources(include_config=bool(preview.get("paths"))),
            )
            provisional_manifest = {
                "item_id": item_id,
                "item_type": preview["item_type"],
                "title": preview.get("title"),
                "path": preview.get("path"),
                "paths": preview.get("paths") or [],
                "deleted_at": datetime.now(timezone.utc).isoformat(),
                "thread_ids": preview.get("thread_ids") or [],
                "session_files": preview.get("session_files") or [],
                "moved_files": [],
                "planned_files_to_move": preview.get("files_to_move") or [],
                "metadata_snapshots": metadata_snapshots,
                "pre_delete_metadata_digests": pre_delete_metadata_digests,
                "post_delete_metadata_digests": pre_delete_metadata_digests,
                "metadata_digest_algorithm": "sqlite-logical-v1+sha256-v1",
                "sqlite_rows_file": str(item_dir / "sqlite_rows.json"),
                "removed_session_index_lines": preview.get("removed_session_index_lines") or [],
                "removed_global_state_paths": preview.get("global_state_paths_to_remove") or [],
                "removed_config_project_blocks": preview.get("config_project_blocks_to_remove") or [],
                "preview": preview,
                "verification": {
                    "result": "manual-review-needed",
                    "message": "The operation was prepared but has not recorded a completed verification.",
                },
                "status": "applying",
                "confirmation_required": "RESTORE FROM TRASH",
            }
            write_json(item_dir / "manifest.json", provisional_manifest)
            atomic_write_text(
                item_dir / "RESTORE_REPORT.md",
                self._render_trash_bin_restore_report(provisional_manifest),
            )
        except Exception:
            shutil.rmtree(item_dir)
            raise
        restored_paths = self._reappeared_missing_paths(preview)
        if restored_paths:
            shutil.rmtree(item_dir)
            return self._block_reappeared_missing_paths(preview, restored_paths)
        moved_files: list[dict[str, str]] = []
        sqlite_results: dict[str, Any] = {}
        session_index_result: dict[str, Any] = {}
        global_state_result: dict[str, Any] = {}
        config_result: dict[str, Any] = {}
        status = "trashed"
        verification: dict[str, Any] = {}
        post_delete_metadata_digests = dict(pre_delete_metadata_digests)
        mutation_post_digests: dict[str, str] = {}

        try:
            for source_text in preview.get("files_to_move") or []:
                source = Path(source_text)
                if not source.exists() or not source.is_file():
                    continue
                if is_link_like(source):
                    raise ValueError(f"Refusing to move a link or junction: {source}")
                original_mode = file_mode(source, PRIVATE_FILE_MODE)
                destination = confined_path(
                    moved_dir,
                    moved_dir / self._trash_relative(source),
                    "Trash Bin destination",
                )
                ensure_private_dir(destination.parent)
                shutil.move(str(source), str(destination))
                set_private_mode(destination, PRIVATE_FILE_MODE)
                moved_files.append(
                    {
                        "source": str(source),
                        "trash": str(destination),
                        "original_mode": original_mode,
                    }
                )

            for db_path in self.state_db_paths():
                if not db_path.exists():
                    continue
                label = self._db_label(db_path)
                source_key = str(db_path.resolve())
                result = self._delete_sqlite_threads_rows(
                    db_path,
                    preview.get("threads") or [],
                    pre_delete_metadata_digests[source_key],
                )
                sqlite_results[label] = {"deleted_rows": result.get("deleted_rows", {})}
                post_delete_metadata_digests[source_key] = result["post_digest"]
                if result["post_digest"] != pre_delete_metadata_digests[source_key]:
                    mutation_post_digests[source_key] = result["post_digest"]

            session_index_result = self._purge_session_index_refs(
                preview.get("thread_ids") or [],
                preview.get("threads") or [],
                pre_delete_metadata_digests.get(str(self.session_index_path.resolve())),
            )
            if session_index_result.get("post_digest"):
                source_key = str(self.session_index_path.resolve())
                post_delete_metadata_digests[source_key] = session_index_result["post_digest"]
                if session_index_result["post_digest"] != pre_delete_metadata_digests.get(source_key):
                    mutation_post_digests[source_key] = session_index_result["post_digest"]
            global_state_result = self._purge_global_state_refs_for_preview(
                preview,
                pre_delete_metadata_digests.get(str(self.global_state_path.resolve())),
            )
            if global_state_result.get("post_digest"):
                source_key = str(self.global_state_path.resolve())
                post_delete_metadata_digests[source_key] = global_state_result["post_digest"]
                if global_state_result["post_digest"] != pre_delete_metadata_digests.get(source_key):
                    mutation_post_digests[source_key] = global_state_result["post_digest"]
            config_result = self._remove_config_project_blocks(
                preview.get("paths") or [],
                pre_delete_metadata_digests.get(str(self.config_path.resolve())),
            )
            if config_result.get("post_digest"):
                source_key = str(self.config_path.resolve())
                post_delete_metadata_digests[source_key] = config_result["post_digest"]
                if config_result["post_digest"] != pre_delete_metadata_digests.get(source_key):
                    mutation_post_digests[source_key] = config_result["post_digest"]
            verification = self._verify_trash_bin_delete(preview, moved_files, item_dir)
            if verification["result"] == "manual-review-needed":
                status = "manual-review-needed"
            elif verification["result"] != "clean":
                status = "partial"
        except Exception as exc:
            metadata_rollback = self._rollback_trash_bin_metadata_changes(
                metadata_snapshots,
                sqlite_rows,
                preview,
                pre_delete_metadata_digests,
                mutation_post_digests,
            )
            file_rollback = self._rollback_moved_trash_bin_files(moved_files)
            rollback_conflicts = metadata_rollback["conflicts"] + file_rollback["conflicts"]
            rollback_result = (
                "failed-manual-restore-needed" if rollback_conflicts else "failed-rollback-completed"
            )
            verification = {
                "result": rollback_result,
                "error": str(exc),
                "metadata_rollback": metadata_rollback,
                "file_rollback": file_rollback,
            }
            status = rollback_result
            post_delete_metadata_digests = self._metadata_source_digests(metadata_snapshots)

        manifest = {
            "item_id": item_id,
            "item_type": preview["item_type"],
            "title": preview.get("title"),
            "path": preview.get("path"),
            "paths": preview.get("paths") or [],
            "deleted_at": datetime.now(timezone.utc).isoformat(),
            "thread_ids": preview.get("thread_ids") or [],
            "session_files": preview.get("session_files") or [],
            "moved_files": moved_files,
            "metadata_snapshots": metadata_snapshots,
            "pre_delete_metadata_digests": pre_delete_metadata_digests,
            "post_delete_metadata_digests": post_delete_metadata_digests,
            "metadata_digest_algorithm": "sqlite-logical-v1+sha256-v1",
            "sqlite_rows_file": str(item_dir / "sqlite_rows.json"),
            "removed_session_index_lines": preview.get("removed_session_index_lines") or [],
            "removed_global_state_paths": preview.get("global_state_paths_to_remove") or [],
            "removed_config_project_blocks": preview.get("config_project_blocks_to_remove") or [],
            "sqlite_tables_affected": sqlite_results,
            "session_index": session_index_result,
            "global_state": global_state_result,
            "config_toml": config_result,
            "preview": preview,
            "verification": verification,
            "status": status,
            "confirmation_required": "RESTORE FROM TRASH",
        }
        write_json(item_dir / "manifest.json", manifest)
        atomic_write_text(
            item_dir / "RESTORE_REPORT.md",
            self._render_trash_bin_restore_report(manifest),
        )
        result = {
            "status": status,
            "item_id": item_id,
            "item_type": preview["item_type"],
            "trash_dir": str(item_dir),
            "title": preview.get("title"),
            "path": preview.get("path"),
            "paths": preview.get("paths") or [],
            "thread_ids": preview.get("thread_ids") or [],
            "moved_files": moved_files,
            "sqlite_rows_affected": sqlite_results,
            "session_index": session_index_result,
            "global_state": global_state_result,
            "config_toml": config_result,
            "verification": verification,
        }
        self.backup.log_operation({**result, "operation_type": "trash-bin-delete", "dry_run": False})
        return result

    def _trash_bin_metadata_sources(self, include_config: bool) -> list[Path]:
        sources = [path for path in self.state_db_paths() if path.exists()]
        for path in [self.session_index_path, self.global_state_path]:
            if path.exists():
                sources.append(path)
        if include_config and self.config_path.exists():
            sources.append(self.config_path)
        return sources

    def _copy_trash_bin_metadata_snapshots(
        self,
        item_dir: Path,
        preview: dict[str, Any],
    ) -> list[dict[str, str]]:
        copied: list[dict[str, str]] = []
        snapshots_dir = item_dir / "metadata-snapshots"
        for source in self._trash_bin_metadata_sources(include_config=bool(preview.get("paths"))):
            if is_link_like(source):
                raise ValueError(f"Refusing to snapshot a link or junction: {source}")
            destination = confined_path(
                snapshots_dir,
                snapshots_dir / self._trash_relative(source),
                "metadata snapshot destination",
            )
            ensure_private_dir(destination.parent)
            if source.suffix == ".sqlite":
                BackupManager._sqlite_backup(source, destination)
            else:
                shutil.copy2(source, destination)
                set_private_mode(destination, PRIVATE_FILE_MODE)
            copied.append({"source": str(source), "snapshot": str(destination)})
        return copied

    def _restore_trash_bin_metadata_snapshots_from_rows(self, rows: list[dict[str, str]]) -> list[dict[str, str]]:
        restored: list[dict[str, str]] = []
        for row in rows:
            source = confined_path(
                self.codex_home,
                Path(row["source"]),
                "metadata restore target",
            )
            snapshot = Path(row["snapshot"])
            if not snapshot.is_file() or is_link_like(snapshot):
                continue
            source.parent.mkdir(parents=True, exist_ok=True)
            if source.suffix == ".sqlite":
                BackupManager._sqlite_backup(snapshot, source)
                BackupManager._sqlite_integrity_check(source)
            else:
                shutil.copy2(snapshot, source)
            restored.append({"source": str(source), "snapshot": str(snapshot)})
        return restored

    def _restore_trash_bin_metadata_snapshots(self, manifest: dict[str, Any]) -> list[dict[str, str]]:
        return self._restore_trash_bin_metadata_snapshots_from_rows(manifest.get("metadata_snapshots") or [])

    @staticmethod
    def _metadata_source_digests(rows: list[dict[str, str]]) -> dict[str, str]:
        digests: dict[str, str] = {}
        for row in rows:
            source_text = row.get("source")
            if not source_text:
                continue
            source = Path(source_text)
            if source.exists() and source.is_file() and not is_link_like(source):
                digests[str(source.resolve())] = metadata_file_digest(source)
        return digests

    def _assert_metadata_digests(
        self,
        expected: dict[str, str],
        reason: str,
        sources: list[Path] | None = None,
    ) -> None:
        current = self._metadata_source_digests(
            [
                {"source": str(source)}
                for source in (sources or [Path(source) for source in expected])
            ]
        )
        if current != expected:
            raise RuntimeError(reason)

    def _validated_trash_bin_metadata_rows(
        self,
        manifest: dict[str, Any],
        item_dir: Path,
    ) -> list[dict[str, str]]:
        expected_sources = {
            Path(os.path.abspath(path))
            for path in [
                self.config_path,
                self.global_state_path,
                self.session_index_path,
                *self.state_db_paths(),
            ]
        }
        validated: list[dict[str, str]] = []
        for row in manifest.get("metadata_snapshots") or []:
            source_text = row.get("source")
            snapshot_text = row.get("snapshot")
            if not source_text or not snapshot_text:
                raise ValueError("Trash Bin metadata snapshot mapping is incomplete")
            source = confined_path(
                self.codex_home,
                Path(source_text),
                "Trash Bin metadata restore target",
            )
            if source not in expected_sources:
                raise ValueError(f"Unexpected metadata restore target: {source}")
            snapshot = confined_path(
                item_dir,
                Path(snapshot_text),
                "Trash Bin metadata snapshot",
            )
            if not snapshot.is_file() or is_link_like(snapshot):
                raise ValueError(f"Metadata snapshot is missing or unsafe: {snapshot}")
            validated.append({"source": str(source), "snapshot": str(snapshot)})
        return validated

    def _trash_bin_metadata_restore_conflicts(
        self,
        manifest: dict[str, Any],
        item_dir: Path,
    ) -> list[dict[str, str]]:
        try:
            rows = self._validated_trash_bin_metadata_rows(manifest, item_dir)
        except (ValueError, OSError) as exc:
            return [{"path": str(item_dir), "reason": str(exc)}]
        expected = manifest.get("post_delete_metadata_digests") or {}
        conflicts: list[dict[str, str]] = []
        if manifest.get("metadata_digest_algorithm") != "sqlite-logical-v1+sha256-v1":
            return [
                {
                    "path": str(item_dir),
                    "reason": "This Trash item predates concurrency-safe metadata fingerprints.",
                }
            ]
        for row in rows:
            source = Path(row["source"])
            expected_digest = expected.get(str(source))
            if not expected_digest:
                conflicts.append(
                    {
                        "path": str(source),
                        "reason": "No post-delete fingerprint is available for this Trash item.",
                    }
                )
            elif not source.is_file() or is_link_like(source):
                conflicts.append(
                    {"path": str(source), "reason": "The active metadata file is missing or unsafe."}
                )
            elif metadata_file_digest(source) != expected_digest:
                conflicts.append(
                    {"path": str(source), "reason": "The active metadata changed after deletion."}
                )
        return conflicts

    def _create_trash_bin_restore_guard(
        self,
        item_dir: Path,
        metadata_rows: list[dict[str, str]],
        manifest: dict[str, Any],
    ) -> list[dict[str, str]]:
        guard_id = f"{utc_stamp()}-{uuid.uuid4().hex[:8]}"
        guards_root = item_dir / "restore-guards"
        guard_dir = confined_path(
            guards_root,
            guards_root / guard_id,
            "Trash Bin restore guard",
        )
        ensure_private_dir(guard_dir)
        guard_rows: list[dict[str, str]] = []
        sources = [Path(row["source"]) for row in metadata_rows]
        sources.extend(
            Path(row["source"])
            for row in manifest.get("moved_files") or []
            if row.get("source") and Path(row["source"]).exists()
        )
        for source in dedupe_keep_order(sources):
            source = confined_path(self.codex_home, source, "restore guard source")
            if not source.is_file() or is_link_like(source):
                raise ValueError(f"Unsafe restore guard source: {source}")
            destination = confined_path(
                guard_dir,
                guard_dir / self._trash_relative(source),
                "restore guard destination",
            )
            ensure_private_dir(destination.parent)
            if source.suffix == ".sqlite":
                BackupManager._sqlite_backup(source, destination)
            else:
                shutil.copy2(source, destination)
                set_private_mode(destination, PRIVATE_FILE_MODE)
            guard_rows.append(
                {
                    "source": str(source),
                    "snapshot": str(destination),
                    "guard_dir": str(guard_dir),
                }
            )
        return guard_rows

    def _restore_trash_bin_metadata_for_item(
        self,
        manifest: dict[str, Any],
        item_dir: Path,
        metadata_rows: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        pre_digests = manifest.get("pre_delete_metadata_digests") or {}
        post_digests = manifest.get("post_delete_metadata_digests") or {}
        sqlite_export_path = confined_path(
            item_dir,
            item_dir / "sqlite_rows.json",
            "Trash Bin SQLite row export",
        )
        sqlite_exports = read_json(sqlite_export_path, {})
        restored: list[dict[str, str]] = []
        for row in metadata_rows:
            source = Path(row["source"])
            source_key = str(source.resolve())
            before_digest = post_digests.get(source_key)
            target_digest = pre_digests.get(source_key)
            if not before_digest or not target_digest:
                raise RuntimeError(f"Trash Bin metadata fingerprints are incomplete: {source}")
            if before_digest == target_digest:
                continue
            snapshot = confined_path(
                item_dir,
                Path(row["snapshot"]),
                "Trash Bin metadata snapshot",
            )
            if not snapshot.is_file() or is_link_like(snapshot):
                raise ValueError(f"Metadata snapshot is missing or unsafe: {snapshot}")
            if metadata_file_digest(snapshot) != target_digest:
                raise RuntimeError(f"Metadata snapshot fingerprint does not match: {snapshot}")

            if source.suffix == ".sqlite":
                restored_digest = self._restore_sqlite_export_rows(
                    source,
                    sqlite_exports.get(self._db_label(source)) or {},
                    expected_digest=before_digest,
                    target_digest=target_digest,
                )
                restore_kind = "sqlite-rows"
            else:
                restored_digest = atomic_write_text_if_unchanged(
                    source,
                    read_text_exact(snapshot),
                    before_digest,
                )
                if restored_digest != target_digest:
                    raise RuntimeError(f"Metadata restore fingerprint mismatch: {source}")
                restore_kind = "text-snapshot"
            restored.append(
                {
                    "source": str(source),
                    "kind": restore_kind,
                    "before_digest": before_digest,
                    "restored_digest": restored_digest,
                }
            )
        return restored

    def _rollback_failed_trash_bin_restore(
        self,
        manifest: dict[str, Any],
        guard_rows: list[dict[str, str]],
        restored_metadata: list[dict[str, str]],
        restored_files: list[dict[str, str]],
    ) -> dict[str, Any]:
        restored_to_trash: list[str] = []
        metadata_reverted: list[str] = []
        conflicts: list[str] = []

        for row in reversed(restored_files):
            try:
                restored = confined_path(
                    self.codex_home,
                    Path(row["restored"]),
                    "failed restore file",
                )
                trash = confined_path(
                    self.trash_bin_dir,
                    Path(row["trash"]),
                    "failed restore Trash destination",
                )
                if trash.exists() or is_link_like(trash):
                    raise RuntimeError("Trash destination already exists")
                if (
                    not restored.is_file()
                    or is_link_like(restored)
                    or file_sha256(restored) != row.get("restored_digest")
                ):
                    raise RuntimeError("restored file changed before rollback")
                trash.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(restored), str(trash))
                set_private_mode(trash, PRIVATE_FILE_MODE)
                restored_to_trash.append(str(trash))
            except Exception as exc:
                conflicts.append(f"{row.get('restored')}: {exc}")

        guards = {str(Path(row["source"]).resolve()): row for row in guard_rows}
        threads = (manifest.get("preview") or {}).get("threads") or []
        for row in reversed(restored_metadata):
            source_key = str(Path(row["source"]).resolve())
            try:
                source = confined_path(
                    self.codex_home,
                    Path(row["source"]),
                    "failed metadata restore target",
                )
                guard = guards.get(source_key)
                if not guard:
                    raise RuntimeError("restore guard is missing")
                guard_snapshot = confined_path(
                    self.trash_bin_dir,
                    Path(guard["snapshot"]),
                    "restore guard snapshot",
                )
                if metadata_file_digest(guard_snapshot) != row["before_digest"]:
                    raise RuntimeError("restore guard fingerprint mismatch")
                if row["kind"] == "sqlite-rows":
                    result = self._delete_sqlite_threads_rows(
                        source,
                        threads,
                        row["restored_digest"],
                        target_digest=row["before_digest"],
                    )
                    if result["post_digest"] != row["before_digest"]:
                        raise RuntimeError("SQLite restore rollback fingerprint mismatch")
                else:
                    reverted = atomic_write_text_if_unchanged(
                        source,
                        read_text_exact(guard_snapshot),
                        row["restored_digest"],
                    )
                    if reverted != row["before_digest"]:
                        raise RuntimeError("text restore rollback fingerprint mismatch")
                metadata_reverted.append(str(source))
            except Exception as exc:
                conflicts.append(f"{source_key}: {exc}")

        return {
            "files_returned_to_trash": restored_to_trash,
            "metadata_reverted": metadata_reverted,
            "conflicts": conflicts,
        }

    def _rollback_trash_bin_metadata_changes(
        self,
        metadata_rows: list[dict[str, str]],
        sqlite_rows: dict[str, Any],
        preview: dict[str, Any],
        pre_digests: dict[str, str],
        mutation_post_digests: dict[str, str],
    ) -> dict[str, Any]:
        snapshots = {str(Path(row["source"]).resolve()): row for row in metadata_rows}
        restored: list[str] = []
        conflicts: list[str] = []
        for source_key, expected_post in reversed(list(mutation_post_digests.items())):
            expected_pre = pre_digests.get(source_key)
            row = snapshots.get(source_key)
            if not expected_pre or not row:
                conflicts.append(f"{source_key}: missing rollback evidence")
                continue
            try:
                source = confined_path(
                    self.codex_home,
                    Path(row["source"]),
                    "Trash Bin metadata rollback target",
                )
                snapshot = confined_path(
                    self.trash_bin_dir,
                    Path(row["snapshot"]),
                    "Trash Bin metadata rollback snapshot",
                )
                if not snapshot.is_file() or is_link_like(snapshot):
                    raise ValueError("rollback snapshot is missing or unsafe")
                if metadata_file_digest(snapshot) != expected_pre:
                    raise RuntimeError("rollback snapshot does not match the preview fingerprint")
                if source.suffix == ".sqlite":
                    export = sqlite_rows.get(self._db_label(source)) or {}
                    self._restore_sqlite_export_rows(
                        source,
                        export,
                        expected_digest=expected_post,
                        target_digest=expected_pre,
                    )
                else:
                    if not source.is_file() or is_link_like(source):
                        raise ValueError("active metadata target is missing or unsafe")
                    restored_digest = atomic_write_text_if_unchanged(
                        source,
                        read_text_exact(snapshot),
                        expected_post,
                    )
                    if restored_digest != expected_pre:
                        raise RuntimeError("rollback did not restore the preview fingerprint")
                restored.append(str(source))
            except Exception as exc:
                conflicts.append(f"{source_key}: {exc}")
        return {
            "restored": restored,
            "conflicts": conflicts,
            "thread_ids": preview.get("thread_ids") or [],
        }

    def _rollback_moved_trash_bin_files(self, moved_files: list[dict[str, Any]]) -> dict[str, Any]:
        restored: list[dict[str, str]] = []
        conflicts: list[str] = []
        for moved in reversed(moved_files):
            source = confined_path(
                self.codex_home,
                Path(moved["source"]),
                "Trash Bin rollback destination",
            )
            trash = confined_path(
                self.trash_bin_dir,
                Path(moved["trash"]),
                "Trash Bin rollback source",
            )
            if not trash.exists():
                continue
            if source.exists() or is_link_like(source):
                conflicts.append(str(source))
                continue
            original_mode = int(moved.get("original_mode", PRIVATE_FILE_MODE))
            if original_mode < 0 or original_mode > 0o777:
                conflicts.append(f"{source}: invalid original file mode")
                continue
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(trash), str(source))
            set_private_mode(source, original_mode)
            restored.append({"trash": str(trash), "restored": str(source)})
        return {"restored": restored, "conflicts": conflicts}

    def _export_sqlite_rows_for_preview(self, preview: dict[str, Any]) -> dict[str, Any]:
        exported: dict[str, Any] = {}
        for db_path in self.state_db_paths():
            if not db_path.exists():
                continue
            label = self._db_label(db_path)
            db_rows: dict[str, list[dict[str, Any]]] = {}
            con = sqlite3.connect(db_path)
            con.row_factory = sqlite3.Row
            try:
                for thread in preview.get("threads") or []:
                    thread_export = self._export_sqlite_thread_rows(
                        con,
                        thread["id"],
                        thread.get("rollout_path") or "",
                    )
                    for table_name, rows in thread_export.items():
                        db_rows.setdefault(table_name, [])
                        for row in rows:
                            if row not in db_rows[table_name]:
                                db_rows[table_name].append(row)
            finally:
                con.close()
            exported[label] = {"database": str(db_path), "tables": db_rows}
        return exported

    def _export_sqlite_thread_rows(
        self,
        con: sqlite3.Connection,
        thread_id: str,
        rollout_path: str,
    ) -> dict[str, list[dict[str, Any]]]:
        exported: dict[str, list[dict[str, Any]]] = {}
        for (table_name,) in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ):
            columns = [row[1] for row in con.execute(f"PRAGMA table_info({self._quote_sqlite_ident(table_name)})")]
            foreign_key_columns = self._sqlite_thread_foreign_key_columns(con, table_name)
            conditions, params = self._sqlite_safe_thread_conditions(
                table_name,
                columns,
                thread_id,
                rollout_path,
                foreign_key_columns,
            )
            if not conditions:
                continue
            rows = [
                {
                    key: self._encode_sqlite_value(value)
                    for key, value in dict(row).items()
                }
                for row in con.execute(
                    f"SELECT * FROM {self._quote_sqlite_ident(table_name)} WHERE {' OR '.join(conditions)}",
                    params,
                )
            ]
            if rows:
                exported[table_name] = rows
        return exported

    @staticmethod
    def _encode_sqlite_value(value: Any) -> Any:
        if isinstance(value, (bytes, bytearray, memoryview)):
            return {
                "__clean_my_codex_type__": "bytes",
                "base64": base64.b64encode(bytes(value)).decode("ascii"),
            }
        return value

    @staticmethod
    def _decode_sqlite_value(value: Any) -> Any:
        if (
            isinstance(value, dict)
            and value.get("__clean_my_codex_type__") == "bytes"
            and set(value) == {"__clean_my_codex_type__", "base64"}
        ):
            return base64.b64decode(str(value["base64"]), validate=True)
        return value

    def _restore_sqlite_export_rows(
        self,
        db_path: Path,
        export: dict[str, Any],
        expected_digest: str,
        target_digest: str,
    ) -> str:
        exported_database = export.get("database")
        tables = export.get("tables")
        if not exported_database or Path(os.path.abspath(exported_database)) != db_path:
            raise ValueError(f"SQLite row export does not match {db_path}")
        if not isinstance(tables, dict) or not tables:
            raise ValueError(f"SQLite row export is empty for {db_path}")

        con = sqlite3.connect(db_path)
        try:
            con.execute("PRAGMA foreign_keys = ON")
            con.execute("BEGIN IMMEDIATE")
            if sqlite_connection_sha256(con) != expected_digest:
                raise RuntimeError(f"Active SQLite metadata changed before rollback: {db_path}")
            table_names = sorted(tables, key=lambda value: (value != "threads", value))
            for table_name in table_names:
                rows = tables[table_name]
                if not isinstance(rows, list):
                    raise ValueError(f"Invalid SQLite row export for {table_name}")
                actual_columns = {
                    row[1]
                    for row in con.execute(
                        f"PRAGMA table_info({self._quote_sqlite_ident(table_name)})"
                    )
                }
                if not actual_columns:
                    raise ValueError(f"SQLite table no longer exists: {table_name}")
                for row in rows:
                    if not isinstance(row, dict) or not row:
                        raise ValueError(f"Invalid SQLite row for {table_name}")
                    columns = list(row)
                    if not set(columns).issubset(actual_columns):
                        raise ValueError(f"SQLite schema changed for {table_name}")
                    quoted_columns = ", ".join(self._quote_sqlite_ident(column) for column in columns)
                    placeholders = ", ".join("?" for _ in columns)
                    con.execute(
                        f"INSERT INTO {self._quote_sqlite_ident(table_name)} "
                        f"({quoted_columns}) VALUES ({placeholders})",
                        [self._decode_sqlite_value(row[column]) for column in columns],
                    )
            self._integrity_check_connection(con, db_path)
            if con.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError(f"SQLite foreign-key check failed for {db_path}")
            post_digest = sqlite_connection_sha256(con)
            if post_digest != target_digest:
                raise RuntimeError(f"SQLite rollback did not restore the preview fingerprint: {db_path}")
            con.commit()
            return post_digest
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def _session_index_line_exports(self, refs: list[str]) -> list[dict[str, Any]]:
        if not self.session_index_path.exists():
            return []
        lines = self.session_index_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        return [
            {"line_number": index, "line": line}
            for index, line in enumerate(lines, start=1)
            if self._session_index_line_has_exact_ref(line, refs)
        ]

    @staticmethod
    def _session_index_line_has_exact_ref(line: str, refs: list[str]) -> bool:
        wanted = {str(ref) for ref in refs if ref}
        if not wanted:
            return False
        try:
            payload = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            return False

        identity_keys = {
            "id",
            "thread_id",
            "threadid",
            "session_id",
            "sessionid",
            "rollout_path",
            "rolloutpath",
            "session_path",
            "sessionpath",
        }

        def contains(value: Any) -> bool:
            if isinstance(value, dict):
                for key, child in value.items():
                    normalized = str(key).lower().replace("-", "_")
                    if normalized in identity_keys and isinstance(child, str) and child in wanted:
                        return True
                    if isinstance(child, (dict, list)) and contains(child):
                        return True
            elif isinstance(value, list):
                return any(contains(child) for child in value)
            return False

        return contains(payload)

    def _config_has_project_block(self, path: str) -> bool:
        if not path or not self.config_path.exists():
            return False
        return self._config_project_header(path) in self.config_path.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def _config_project_header(path: str) -> str:
        escaped = path.replace("\\", "\\\\").replace(chr(34), chr(92) + chr(34))
        return f'[projects."{escaped}"]'

    @staticmethod
    def _validate_relocation_paths(old_path: str, new_path: str) -> tuple[str, str]:
        old_value = str(old_path or "").strip()
        new_value = str(new_path or "").strip()
        if not old_value or not new_value:
            raise ValueError("Old and new workspace paths are required")
        if not Path(old_value).is_absolute() or not Path(new_value).is_absolute():
            raise ValueError("Workspace paths must be absolute")
        if old_value == new_value:
            raise ValueError("Old and new workspace paths must be different")
        for value in (old_value, new_value):
            if '"' in value or any(ord(character) < 32 or ord(character) == 127 for character in value):
                raise ValueError("Workspace paths cannot contain quotes or control characters")
            if os.name != "nt" and "\\" in value:
                raise ValueError("Workspace paths cannot contain backslashes on this platform")
        return old_value, new_value

    def _remove_config_project_blocks(
        self,
        paths: list[str],
        expected_digest: str | None = None,
    ) -> dict[str, Any]:
        if not paths or not self.config_path.exists():
            return {"removed_paths": []}
        current_digest = file_sha256(self.config_path)
        if expected_digest and current_digest != expected_digest:
            raise RuntimeError("Active config changed after preview")
        original = self.config_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        headers = {self._config_project_header(path): path for path in paths}
        kept: list[str] = []
        removed_paths: list[str] = []
        skipping = ""
        index = 0
        while index < len(original):
            line = original[index]
            stripped = line.strip()
            if stripped in headers:
                skipping = headers[stripped]
                removed_paths.append(skipping)
                index += 1
                while index < len(original):
                    next_line = original[index]
                    next_stripped = next_line.strip()
                    if next_stripped.startswith("[") and next_stripped.endswith("]"):
                        break
                    index += 1
                skipping = ""
                continue
            kept.append(line)
            index += 1
        if removed_paths:
            post_digest = atomic_write_text_if_unchanged(
                self.config_path,
                "".join(kept),
                current_digest,
            )
        else:
            post_digest = current_digest
        return {
            "removed_paths": dedupe_keep_order(removed_paths),
            "post_digest": post_digest,
        }

    def _verify_trash_bin_delete(
        self,
        preview: dict[str, Any],
        moved_files: list[dict[str, str]],
        item_dir: Path,
    ) -> dict[str, Any]:
        refs = self._refs_from_clean_preview(preview)
        refs.extend(preview.get("paths") or [])
        refs = dedupe_keep_order([ref for ref in refs if ref])
        sqlite_clear: dict[str, bool] = {}
        sqlite_integrity: dict[str, str] = {}
        manual_review: list[str] = list(preview.get("manual_review") or [])
        for db_path in self.state_db_paths():
            if not db_path.exists():
                continue
            label = self._db_label(db_path)
            rows_left = 0
            for thread in preview.get("threads") or []:
                db_preview = self._preview_sqlite_thread_rows(
                    db_path,
                    thread["id"],
                    thread.get("rollout_path") or "",
                )
                rows_left += sum((db_preview.get("rows_to_delete") or {}).values())
            sqlite_clear[label] = rows_left == 0
            try:
                self._integrity_check(db_path)
                sqlite_integrity[label] = "ok"
            except Exception as exc:
                sqlite_integrity[label] = str(exc)

        session_refs_absent = not self._session_index_lines_to_remove(refs)
        global_refs_absent = not self._global_state_paths_to_remove(refs)
        source_paths_missing = {row["source"]: not Path(row["source"]).exists() for row in moved_files}
        trash_copies_exist = {row["trash"]: Path(row["trash"]).exists() for row in moved_files}
        paths = preview.get("paths") or []
        config_paths_absent = not any(self._config_has_project_block(path) for path in paths)
        required = [
            all(sqlite_clear.values()) if sqlite_clear else True,
            session_refs_absent,
            global_refs_absent,
            config_paths_absent,
            all(source_paths_missing.values()),
            all(trash_copies_exist.values()),
            (item_dir / "sqlite_rows.json").exists(),
            (item_dir / "metadata-snapshots").exists(),
            all(value == "ok" for value in sqlite_integrity.values()),
        ]
        if manual_review:
            result = "manual-review-needed"
        elif all(required):
            result = "clean"
        else:
            result = "partial"
        return {
            "result": result,
            "sqlite_thread_absent": all(sqlite_clear.values()) if sqlite_clear else True,
            "sqlite_integrity": sqlite_integrity,
            "session_index_refs_absent": session_refs_absent,
            "global_state_refs_absent": global_refs_absent,
            "config_paths_absent": config_paths_absent,
            "source_paths_missing": source_paths_missing,
            "trash_copies_exist": trash_copies_exist,
            "trash_item_has_restore_data": (item_dir / "sqlite_rows.json").exists(),
            "manual_review": manual_review,
        }

    def _verify_trash_bin_restore(self, manifest: dict[str, Any]) -> dict[str, Any]:
        thread_ids = manifest.get("thread_ids") or []
        sqlite_present: dict[str, bool] = {}
        for db_path in self.state_db_paths():
            if not db_path.exists():
                continue
            label = self._db_label(db_path)
            if not thread_ids:
                sqlite_present[label] = True
                continue
            placeholders = ",".join(["?"] * len(thread_ids))
            con = sqlite3.connect(db_path)
            try:
                count = con.execute(
                    f"SELECT COUNT(*) FROM threads WHERE id IN ({placeholders})",
                    thread_ids,
                ).fetchone()[0]
            finally:
                con.close()
            sqlite_present[label] = int(count) == len(thread_ids)
        files_restored = all(
            Path(moved["source"]).exists()
            for moved in manifest.get("moved_files") or []
            if moved.get("source")
        )
        return {
            "restored": (all(sqlite_present.values()) if sqlite_present else True) and files_restored,
            "sqlite_present": sqlite_present,
            "files_restored": files_restored,
        }

    def _read_trash_bin_manifest(self, item_id: str) -> dict[str, Any]:
        manifest_path = managed_item_path(self.trash_bin_dir, item_id, "Trash Bin item") / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"Trash Bin manifest not found: {item_id}")
        return read_json(manifest_path, {})

    def _folder_size(self, path: Path) -> int:
        if not path.exists():
            return 0
        total = 0
        for file_path in path.rglob("*"):
            if file_path.is_file():
                try:
                    total += file_path.stat().st_size
                except FileNotFoundError:
                    continue
        return total

    def _render_trash_bin_restore_report(self, manifest: dict[str, Any]) -> str:
        lines = [
            f"# Restore Report: {manifest.get('title') or manifest.get('item_id')}",
            "",
            f"- Item ID: `{manifest.get('item_id')}`",
            f"- Item type: `{manifest.get('item_type')}`",
            f"- Deleted at: `{manifest.get('deleted_at')}`",
            f"- Thread IDs: `{', '.join(manifest.get('thread_ids') or [])}`",
            "- Restore confirmation: `RESTORE FROM TRASH`",
            "",
            "This Trash Bin item contains moved session files, metadata snapshots, SQLite row exports, removed session index lines, removed global-state paths, and original path mappings.",
            "",
            "Permanent delete is allowed only by deleting this Trash Bin item with `PERMANENT DELETE`.",
        ]
        return "\n".join(lines) + "\n"

    def _find_trash_manifest_for_thread(self, thread_id: str) -> tuple[Path | None, dict[str, Any]]:
        for manifest_path in sorted(self.trash_dir.glob("*/manifest.json"), reverse=True):
            try:
                manifest = read_json(manifest_path, {})
            except Exception:
                continue
            if thread_id in (manifest.get("thread_ids") or []):
                return manifest_path, manifest
        return None, {}

    def _find_backup_manifest_for_thread(self, thread_id: str) -> tuple[Path | None, dict[str, Any]]:
        for manifest_path in sorted(self.backup.backups_dir.glob("*/manifest.json"), reverse=True):
            try:
                manifest = read_json(manifest_path, {})
            except Exception:
                continue
            preview = manifest.get("preview") or {}
            if thread_id in (preview.get("thread_ids") or []):
                return manifest_path, manifest
        return None, {}

    def _thread_for_purge_preview(
        self,
        thread_id: str,
        trash_manifest: dict[str, Any],
    ) -> dict[str, Any]:
        for chat in self.scan_chats():
            if chat["id"] == thread_id:
                return chat
        for thread in trash_manifest.get("threads") or []:
            if thread.get("id") == thread_id:
                return thread
        return {"id": thread_id}

    @staticmethod
    def _title_from_trash(trash_manifest: dict[str, Any], thread_id: str) -> str:
        for thread in trash_manifest.get("threads") or []:
            if thread.get("id") == thread_id:
                return thread.get("title") or ""
        return ""

    @staticmethod
    def _rollout_path_for_purge(
        thread: dict[str, Any],
        trash_manifest: dict[str, Any],
        backup_manifest: dict[str, Any],
    ) -> str:
        if thread.get("rollout_path"):
            return str(thread["rollout_path"])
        for moved in trash_manifest.get("moved_files") or []:
            if moved.get("source"):
                return str(moved["source"])
        preview = backup_manifest.get("preview") or {}
        for thread_row in preview.get("threads") or []:
            if thread_row.get("rollout_path"):
                return str(thread_row["rollout_path"])
        return ""

    def _trash_or_backup_session_exists(
        self,
        trash_manifest: dict[str, Any],
        backup_manifest: dict[str, Any],
        rollout_path: str,
    ) -> bool:
        for moved in trash_manifest.get("moved_files") or []:
            if moved.get("source") == rollout_path and Path(moved.get("trash", "")).exists():
                return True
        for copied in backup_manifest.get("copied") or []:
            if copied.get("source") == rollout_path and Path(copied.get("backup", "")).exists():
                return True
        return False

    def _related_files_for_thread(self, thread: dict[str, Any]) -> list[Path]:
        thread_id = thread.get("id") or ""
        rollout_path = thread.get("rollout_path") or ""
        candidates: list[Path] = []
        if rollout_path:
            session_path = Path(rollout_path)
            if self._movable_codex_file(session_path):
                candidates.append(session_path)
            if session_path.parent.exists():
                session_name = session_path.name
                session_stem = session_path.stem
                for sibling in session_path.parent.iterdir():
                    if not sibling.is_file() or sibling == session_path:
                        continue
                    name = sibling.name
                    if (
                        (thread_id and self._filename_has_exact_identifier(name, thread_id))
                        or name.startswith(session_name + ".")
                        or name.startswith(session_stem + ".")
                    ) and self._movable_codex_file(sibling):
                        candidates.append(sibling)
        sessions_root = self.codex_home / "sessions"
        if thread_id and sessions_root.is_dir() and not is_link_like(sessions_root):
            for path in sessions_root.rglob("*"):
                if (
                    path.is_file()
                    and self._filename_has_exact_identifier(path.name, thread_id)
                    and self._movable_codex_file(path)
                ):
                    candidates.append(path)
        return dedupe_keep_order(candidates)

    @staticmethod
    def _filename_has_exact_identifier(name: str, identifier: str) -> bool:
        if not identifier:
            return False
        return bool(
            re.search(
                rf"(?<![A-Za-z0-9]){re.escape(identifier)}(?![A-Za-z0-9])",
                name,
            )
        )

    def _movable_codex_file(self, path: Path) -> bool:
        try:
            resolved = path.resolve()
            resolved.relative_to(self.codex_home.resolve())
        except (FileNotFoundError, ValueError):
            return False
        active_files = {
            self.config_path.resolve(),
            self.global_state_path.resolve(),
            self.session_index_path.resolve(),
            *(db_path.resolve() for db_path in self.state_db_paths()),
            (self.codex_home / "auth.json").resolve(),
            (self.codex_home / "logs_2.sqlite").resolve(),
            (self.codex_home / "sqlite" / "logs_2.sqlite").resolve(),
        }
        return path.exists() and path.is_file() and resolved not in active_files

    def _refs_from_clean_preview(self, preview: dict[str, Any]) -> list[str]:
        refs: list[str] = []
        for thread in preview.get("threads") or []:
            if thread.get("id"):
                refs.append(thread["id"])
            if thread.get("rollout_path"):
                refs.append(thread["rollout_path"])
        return dedupe_keep_order(refs)

    def _purge_session_index_refs(
        self,
        thread_ids: list[str],
        threads: list[dict[str, Any]],
        expected_digest: str | None = None,
    ) -> dict[str, Any]:
        if not self.session_index_path.exists():
            return {"removed_count": 0, "removed_lines": []}
        current_digest = file_sha256(self.session_index_path)
        if expected_digest and current_digest != expected_digest:
            raise RuntimeError("Active session index changed after preview")
        refs: list[str] = list(thread_ids)
        refs.extend([thread.get("rollout_path") for thread in threads if thread.get("rollout_path")])
        refs = dedupe_keep_order([ref for ref in refs if ref])
        original = self.session_index_path.read_text(encoding="utf-8", errors="replace").splitlines(
            keepends=True
        )
        kept: list[str] = []
        removed_lines: list[int] = []
        for index, line in enumerate(original, start=1):
            if self._session_index_line_has_exact_ref(line, refs):
                removed_lines.append(index)
            else:
                kept.append(line)
        if removed_lines:
            post_digest = atomic_write_text_if_unchanged(
                self.session_index_path,
                "".join(kept),
                current_digest,
            )
        else:
            post_digest = current_digest
        return {
            "removed_count": len(removed_lines),
            "removed_lines": removed_lines,
            "post_digest": post_digest,
        }

    def _purge_global_state_refs_for_preview(
        self,
        preview: dict[str, Any],
        expected_digest: str | None = None,
    ) -> dict[str, Any]:
        if not self.global_state_path.exists():
            return {"removed_paths": []}
        current_digest = file_sha256(self.global_state_path)
        if expected_digest and current_digest != expected_digest:
            raise RuntimeError("Active global state changed after preview")
        refs = self._refs_from_clean_preview(preview)
        refs.extend(preview.get("paths") or [])
        refs = dedupe_keep_order([ref for ref in refs if ref])
        state = read_json(self.global_state_path, {})
        pruned, removed = self._prune_json_refs(state, refs, "")
        if removed:
            post_digest = atomic_write_text_if_unchanged(
                self.global_state_path,
                json.dumps(pruned, indent=2, ensure_ascii=False) + "\n",
                current_digest,
            )
        else:
            post_digest = current_digest
        return {"removed_paths": removed, "post_digest": post_digest}

    def _restore_backup_copies(self, backup_dir: Path) -> None:
        backup_dir = confined_path(
            self.backup.backups_dir,
            backup_dir,
            "operation backup",
        )
        manifest = read_json(backup_dir / "manifest.json", {})
        for row in manifest.get("copied") or []:
            source_text = row.get("source")
            backup_text = row.get("backup")
            if not source_text or not backup_text:
                continue
            backup = confined_path(backup_dir, Path(backup_text), "operation backup source")
            if not backup.is_file() or is_link_like(backup):
                raise ValueError(f"Operation backup is missing or unsafe: {backup}")
            source = confined_path(self.codex_home, Path(source_text), "operation backup target")
            source.parent.mkdir(parents=True, exist_ok=True)
            if source.suffix == ".sqlite":
                BackupManager._sqlite_backup(backup, source)
                self._integrity_check(source)
            else:
                shutil.copy2(backup, source)

    def _rollback_relocation_changes(
        self,
        old_path: str,
        new_path: str,
        pre_digests: dict[str, str],
        mutation_post_digests: dict[str, str],
        original_text: dict[str, str],
    ) -> list[str]:
        conflicts: list[str] = []
        for source_key, expected_post in reversed(list(mutation_post_digests.items())):
            expected_pre = pre_digests.get(source_key)
            if not expected_pre:
                conflicts.append(f"{source_key}: missing pre-operation fingerprint")
                continue
            try:
                source = confined_path(
                    self.codex_home,
                    Path(source_key),
                    "relocation rollback target",
                )
                if source.suffix == ".sqlite":
                    self._update_thread_cwd(
                        source,
                        new_path,
                        old_path,
                        expected_post,
                        target_digest=expected_pre,
                    )
                else:
                    original = original_text.get(source_key)
                    if original is None:
                        raise RuntimeError("missing original text")
                    restored_digest = atomic_write_text_if_unchanged(
                        source,
                        original,
                        expected_post,
                    )
                    if restored_digest != expected_pre:
                        raise RuntimeError("rollback did not restore the preview fingerprint")
            except Exception as exc:
                conflicts.append(f"{source_key}: {exc}")
        return conflicts

    def _preview_sqlite_thread_rows(
        self,
        db_path: Path,
        thread_id: str,
        rollout_path: str,
    ) -> dict[str, Any]:
        con = sqlite3.connect(db_path)
        try:
            rows_to_delete: dict[str, int] = {}
            manual_review_tables: list[str] = []
            for (table_name,) in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ):
                columns = [row[1] for row in con.execute(f"PRAGMA table_info({self._quote_sqlite_ident(table_name)})")]
                foreign_key_columns = self._sqlite_thread_foreign_key_columns(con, table_name)
                conditions, params = self._sqlite_safe_thread_conditions(
                    table_name,
                    columns,
                    thread_id,
                    rollout_path,
                    foreign_key_columns,
                )
                if not conditions:
                    if any("thread" in column.lower() for column in columns):
                        manual_review_tables.append(table_name)
                    continue
                count = con.execute(
                    f"SELECT COUNT(*) FROM {self._quote_sqlite_ident(table_name)} WHERE {' OR '.join(conditions)}",
                    params,
                ).fetchone()[0]
                if count:
                    rows_to_delete[table_name] = int(count)
            return {
                "database": str(db_path),
                "rows_to_delete": rows_to_delete,
                "manual_review_tables": manual_review_tables,
            }
        finally:
            con.close()

    def _delete_sqlite_thread_rows(
        self,
        db_path: Path,
        thread_id: str,
        rollout_path: str,
    ) -> dict[str, Any]:
        con = sqlite3.connect(db_path)
        deleted_rows: dict[str, int] = {}
        try:
            con.execute("PRAGMA foreign_keys = ON")
            con.execute("BEGIN IMMEDIATE")
            tables = [
                row[0]
                for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
            tables.sort(key=lambda value: value == "threads")
            for table_name in tables:
                columns = [row[1] for row in con.execute(f"PRAGMA table_info({self._quote_sqlite_ident(table_name)})")]
                foreign_key_columns = self._sqlite_thread_foreign_key_columns(con, table_name)
                conditions, params = self._sqlite_safe_thread_conditions(
                    table_name,
                    columns,
                    thread_id,
                    rollout_path,
                    foreign_key_columns,
                )
                if not conditions:
                    continue
                cursor = con.execute(
                    f"DELETE FROM {self._quote_sqlite_ident(table_name)} WHERE {' OR '.join(conditions)}",
                    params,
                )
                if cursor.rowcount and cursor.rowcount > 0:
                    deleted_rows[table_name] = int(cursor.rowcount)
            self._integrity_check_connection(con, db_path)
            if con.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError(f"SQLite foreign-key check failed for {db_path}")
            con.commit()
            return {"deleted_rows": deleted_rows}
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def _delete_sqlite_threads_rows(
        self,
        db_path: Path,
        threads: list[dict[str, Any]],
        expected_digest: str,
        target_digest: str | None = None,
    ) -> dict[str, Any]:
        con = sqlite3.connect(db_path)
        deleted_rows: dict[str, int] = {}
        try:
            con.execute("PRAGMA foreign_keys = ON")
            con.execute("BEGIN IMMEDIATE")
            if sqlite_connection_sha256(con) != expected_digest:
                raise RuntimeError(f"Active SQLite metadata changed after preview: {db_path}")
            tables = [
                row[0]
                for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
            tables.sort(key=lambda value: value == "threads")
            for table_name in tables:
                columns = [
                    row[1]
                    for row in con.execute(
                        f"PRAGMA table_info({self._quote_sqlite_ident(table_name)})"
                    )
                ]
                foreign_key_columns = self._sqlite_thread_foreign_key_columns(con, table_name)
                for thread in threads:
                    conditions, params = self._sqlite_safe_thread_conditions(
                        table_name,
                        columns,
                        str(thread["id"]),
                        str(thread.get("rollout_path") or ""),
                        foreign_key_columns,
                    )
                    if not conditions:
                        continue
                    cursor = con.execute(
                        f"DELETE FROM {self._quote_sqlite_ident(table_name)} "
                        f"WHERE {' OR '.join(conditions)}",
                        params,
                    )
                    if cursor.rowcount and cursor.rowcount > 0:
                        deleted_rows[table_name] = deleted_rows.get(table_name, 0) + int(cursor.rowcount)
            self._integrity_check_connection(con, db_path)
            if con.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError(f"SQLite foreign-key check failed for {db_path}")
            post_digest = sqlite_connection_sha256(con)
            if target_digest and post_digest != target_digest:
                raise RuntimeError(f"SQLite rollback did not restore {db_path}")
            con.commit()
            return {"deleted_rows": deleted_rows, "post_digest": post_digest}
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def _sqlite_safe_thread_conditions(
        self,
        table_name: str,
        columns: list[str],
        thread_id: str,
        rollout_path: str,
        foreign_key_columns: list[str] | None = None,
    ) -> tuple[list[str], list[str]]:
        conditions: list[str] = []
        params: list[str] = []
        foreign_key_columns = foreign_key_columns or []
        for column in columns:
            if table_name == "threads" and column == "id":
                conditions.append(f"{self._quote_sqlite_ident(column)} = ?")
                params.append(thread_id)
            elif (
                column in {"thread_id", "threadId"}
                or column.endswith("_thread_id")
                or column in foreign_key_columns
            ):
                conditions.append(f"{self._quote_sqlite_ident(column)} = ?")
                params.append(thread_id)
            elif rollout_path and column in {"rollout_path", "session_path"}:
                conditions.append(f"{self._quote_sqlite_ident(column)} = ?")
                params.append(rollout_path)
        return conditions, params

    def _sqlite_thread_foreign_key_columns(
        self,
        con: sqlite3.Connection,
        table_name: str,
    ) -> list[str]:
        return [
            str(row[3])
            for row in con.execute(f"PRAGMA foreign_key_list({self._quote_sqlite_ident(table_name)})")
            if str(row[2]).lower() == "threads" and str(row[4]).lower() == "id"
        ]

    @staticmethod
    def _quote_sqlite_ident(identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'

    def _session_index_lines_to_remove(self, refs: list[str]) -> list[int]:
        if not self.session_index_path.exists():
            return []
        lines = self.session_index_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return [
            index
            for index, line in enumerate(lines, start=1)
            if self._session_index_line_has_exact_ref(line, refs)
        ]

    def _purge_session_index_lines(self, thread_id: str, rollout_path: str) -> dict[str, Any]:
        if not self.session_index_path.exists():
            return {"removed_count": 0, "removed_lines": []}
        refs = [thread_id]
        if rollout_path:
            refs.append(rollout_path)
        original = self.session_index_path.read_text(encoding="utf-8", errors="replace").splitlines(
            keepends=True
        )
        kept: list[str] = []
        removed_lines: list[int] = []
        for index, line in enumerate(original, start=1):
            if self._session_index_line_has_exact_ref(line, refs):
                removed_lines.append(index)
            else:
                kept.append(line)
        if removed_lines:
            atomic_write_text(self.session_index_path, "".join(kept))
        return {"removed_count": len(removed_lines), "removed_lines": removed_lines}

    def _global_state_paths_to_remove(self, refs: list[str]) -> list[str]:
        if not self.global_state_path.exists():
            return []
        state = read_json(self.global_state_path, {})
        _, removed = self._prune_json_refs(state, refs, "")
        return removed

    def _purge_global_state_refs(self, thread_id: str, rollout_path: str) -> dict[str, Any]:
        if not self.global_state_path.exists():
            return {"removed_paths": []}
        refs = [thread_id]
        if rollout_path:
            refs.append(rollout_path)
        state = read_json(self.global_state_path, {})
        pruned, removed = self._prune_json_refs(state, refs, "")
        if removed:
            atomic_write_text(
                self.global_state_path,
                json.dumps(pruned, indent=2, ensure_ascii=False) + "\n",
            )
        return {"removed_paths": removed}

    def _prune_json_refs(
        self,
        value: Any,
        refs: list[str],
        path: str,
    ) -> tuple[Any, list[str]]:
        removed: list[str] = []
        if isinstance(value, dict):
            new_value: dict[str, Any] = {}
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else str(key)
                if any(ref and str(key) == ref for ref in refs):
                    removed.append(child_path)
                    continue
                pruned_child, child_removed = self._prune_json_refs(child, refs, child_path)
                removed.extend(child_removed)
                if pruned_child is _RemovedJsonRef:
                    removed.append(child_path)
                    continue
                new_value[key] = pruned_child
            if path and not new_value and removed:
                return _RemovedJsonRef, removed
            return new_value, removed
        if isinstance(value, list):
            new_list: list[Any] = []
            for index, child in enumerate(value):
                child_path = f"{path}[{index}]"
                pruned_child, child_removed = self._prune_json_refs(child, refs, child_path)
                removed.extend(child_removed)
                if pruned_child is _RemovedJsonRef:
                    if not child_removed:
                        removed.append(child_path)
                    continue
                new_list.append(pruned_child)
            if path and not new_list and removed:
                return _RemovedJsonRef, removed
            return new_list, removed
        if isinstance(value, str) and value in {str(ref) for ref in refs if ref}:
            return _RemovedJsonRef, removed
        return value, removed

    def _copy_purge_backup_files(self, purge_dir: Path, preview: dict[str, Any]) -> list[dict[str, str]]:
        files_dir = purge_dir / "files"
        copied: list[dict[str, str]] = []
        sources: list[Path] = [path for path in self.state_db_paths() if path.exists()]
        for path in [self.session_index_path, self.global_state_path]:
            if path.exists():
                sources.append(path)
        session_path = Path(preview["original_session_path"]) if preview.get("original_session_path") else None
        if session_path and session_path.exists():
            sources.append(session_path)
        for source in sources:
            if is_link_like(source):
                raise ValueError(f"Refusing to back up a link or junction: {source}")
            relative = self._trash_relative(source)
            destination = confined_path(
                files_dir,
                files_dir / relative,
                "purge backup destination",
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.suffix == ".sqlite":
                BackupManager._sqlite_backup(source, destination)
            else:
                shutil.copy2(source, destination)
            copied.append({"source": str(source), "backup": str(destination)})
        return copied

    def _restore_files_from_purge_backup(self, copied: list[dict[str, str]]) -> None:
        for row in copied:
            source = Path(row["source"])
            backup = Path(row["backup"])
            if not backup.exists():
                continue
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, source)

    def _move_session_to_existing_trash_if_needed(self, preview: dict[str, Any]) -> dict[str, str] | None:
        session_path = Path(preview["original_session_path"]) if preview.get("original_session_path") else None
        if not session_path or not session_path.exists():
            return None
        trash_manifest_path = Path(preview["trash_manifest_path"]) if preview.get("trash_manifest_path") else None
        if trash_manifest_path and trash_manifest_path.exists():
            trash_dir = trash_manifest_path.parent
        else:
            trash_dir = self.trash_dir / f"{utc_stamp()}-trash-{uuid.uuid4().hex[:8]}"
            trash_dir.mkdir(parents=True, exist_ok=True)
        files_dir = trash_dir / "files"
        destination = confined_path(
            files_dir,
            files_dir / self._trash_relative(session_path),
            "legacy Trash destination",
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(session_path), str(destination))
        return {"source": str(session_path), "trash": str(destination)}

    def _restore_session_from_trash_or_backup(
        self,
        session_path: str,
        purge_manifest: dict[str, Any],
    ) -> dict[str, str]:
        if not session_path:
            return {}
        destination = Path(session_path)
        candidates: list[Path] = []
        moved = purge_manifest.get("moved_session") or {}
        if moved.get("trash"):
            candidates.append(Path(moved["trash"]))
        preview = purge_manifest.get("preview") or {}
        trash_manifest_path = preview.get("trash_manifest_path")
        if trash_manifest_path:
            trash_manifest = read_json(Path(trash_manifest_path), {})
            for item in trash_manifest.get("moved_files") or []:
                if item.get("source") == session_path:
                    candidates.append(Path(item["trash"]))
        backup_manifest_path = preview.get("backup_manifest_path")
        if backup_manifest_path:
            backup_manifest = read_json(Path(backup_manifest_path), {})
            for item in backup_manifest.get("copied") or []:
                if item.get("source") == session_path:
                    candidates.append(Path(item["backup"]))
        for item in purge_manifest.get("copied") or []:
            if item.get("source") == session_path:
                candidates.append(Path(item["backup"]))
        for candidate in candidates:
            if candidate.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(candidate, destination)
                return {"source": str(candidate), "restored": str(destination)}
        return {}

    def _render_purge_report(self, manifest: dict[str, Any]) -> str:
        preview = manifest.get("preview") or {}
        verification = manifest.get("verification") or {}
        return "\n".join(
            [
                "# Full Purge From Codex Report",
                "",
                f"Created: {manifest.get('created_at')}",
                f"Status: {manifest.get('status')}",
                f"Thread ID: {manifest.get('thread_id')}",
                f"Title: {manifest.get('title')}",
                f"Original session: {preview.get('original_session_path')}",
                "",
                "## Files Modified",
                *(f"- {path}" for path in manifest.get("modified_files") or []),
                "",
                "## SQLite Tables Affected",
                json.dumps(manifest.get("sqlite_tables_affected") or {}, indent=2, ensure_ascii=False),
                "",
                "## Session Index",
                json.dumps(manifest.get("session_index") or {}, indent=2, ensure_ascii=False),
                "",
                "## Global State",
                json.dumps(manifest.get("global_state") or {}, indent=2, ensure_ascii=False),
                "",
                "## Verification",
                json.dumps(verification, indent=2, ensure_ascii=False),
                "",
                "## Restore",
                "Use confirmation text: RESTORE FULL PURGE",
            ]
        ) + "\n"

    def _load_session_index_titles(self) -> dict[str, str]:
        titles: dict[str, str] = {}
        if not self.session_index_path.exists():
            return titles
        with self.session_index_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                thread_id = row.get("id")
                title = row.get("thread_name")
                if thread_id and title:
                    titles[thread_id] = title
        return titles

    @staticmethod
    def _chat_badges(chat: dict[str, Any]) -> list[str]:
        badges: list[str] = []
        if chat.get("archived"):
            badges.append("Already trashed")
        elif chat.get("session_exists"):
            badges.append("Clean removable")
        if chat.get("sources"):
            badges.append("Database-linked")
        if chat.get("indexed_title") or chat.get("sources") or chat.get("session_exists"):
            badges.append("References found")
        return badges

    def _fetch_threads(self, db_path: Path) -> list[sqlite3.Row]:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        try:
            return list(
                con.execute(
                    """
                    SELECT id, rollout_path, created_at, updated_at, cwd, title,
                           tokens_used, archived, first_user_message, preview
                    FROM threads
                    """
                )
            )
        finally:
            con.close()

    def _threads_by_id(self, thread_ids: list[str]) -> list[dict[str, Any]]:
        wanted = set(thread_ids)
        rows = [chat for chat in self.scan_chats() if chat["id"] in wanted]
        found = {row["id"] for row in rows}
        missing = wanted - found
        if missing:
            raise ValueError(f"Unknown thread ids: {', '.join(sorted(missing))}")
        return rows

    def _count_thread_cwd(self, db_path: Path, old_path: str) -> int:
        con = sqlite3.connect(db_path)
        try:
            return int(
                con.execute("SELECT COUNT(*) FROM threads WHERE cwd = ?", (old_path,)).fetchone()[0]
            )
        finally:
            con.close()

    def _update_thread_cwd(
        self,
        db_path: Path,
        old_path: str,
        new_path: str,
        expected_digest: str,
        target_digest: str | None = None,
    ) -> str:
        con = sqlite3.connect(db_path)
        try:
            con.execute("BEGIN IMMEDIATE")
            if sqlite_connection_sha256(con) != expected_digest:
                raise RuntimeError(f"Active SQLite metadata changed after preview: {db_path}")
            con.execute("UPDATE threads SET cwd = ? WHERE cwd = ?", (new_path, old_path))
            self._integrity_check_connection(con, db_path)
            post_digest = sqlite_connection_sha256(con)
            if target_digest and post_digest != target_digest:
                raise RuntimeError(f"SQLite relocation rollback did not restore {db_path}")
            con.commit()
            return post_digest
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def _set_archived(self, db_path: Path, thread_ids: list[str], archived: bool) -> None:
        if not thread_ids:
            return
        placeholders = ",".join(["?"] * len(thread_ids))
        con = sqlite3.connect(db_path)
        try:
            con.execute("BEGIN IMMEDIATE")
            if archived:
                params: list[Any] = [1, now_ms(), *thread_ids]
                con.execute(
                    f"UPDATE threads SET archived = ?, archived_at = ? WHERE id IN ({placeholders})",
                    params,
                )
            else:
                params = [0, None, *thread_ids]
                con.execute(
                    f"UPDATE threads SET archived = ?, archived_at = ? WHERE id IN ({placeholders})",
                    params,
                )
            self._integrity_check_connection(con, db_path)
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def _delete_threads(self, db_path: Path, thread_ids: list[str]) -> None:
        if not thread_ids:
            return
        placeholders = ",".join(["?"] * len(thread_ids))
        con = sqlite3.connect(db_path)
        try:
            con.execute("BEGIN IMMEDIATE")
            con.execute(f"DELETE FROM thread_dynamic_tools WHERE thread_id IN ({placeholders})", thread_ids)
            con.execute(f"DELETE FROM threads WHERE id IN ({placeholders})", thread_ids)
            self._integrity_check_connection(con, db_path)
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    @staticmethod
    def _integrity_check(db_path: Path) -> None:
        con = sqlite3.connect(db_path)
        try:
            result = con.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise RuntimeError(f"SQLite integrity check failed for {db_path}: {result}")
        finally:
            con.close()

    @staticmethod
    def _integrity_check_connection(con: sqlite3.Connection, db_path: Path) -> None:
        result = con.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"SQLite integrity check failed for {db_path}: {result}")

    def _db_label(self, db_path: Path) -> str:
        try:
            return db_path.relative_to(self.codex_home).as_posix()
        except ValueError:
            return db_path.as_posix()

    def _trash_relative(self, source: Path) -> Path:
        if is_link_like(source):
            raise ValueError(f"Links and junctions are not supported: {source}")
        try:
            relative = source.resolve().relative_to(self.codex_home.resolve())
        except ValueError as exc:
            raise ValueError(f"Path is outside the Codex data directory: {source}") from exc
        return Path(".codex") / relative
