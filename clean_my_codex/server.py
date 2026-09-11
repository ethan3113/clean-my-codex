from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import secrets
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import (
    APP_NAME,
    APP_VERSION,
    CREATOR_NAME,
    CREATOR_URL,
    ISSUES_URL,
    REPOSITORY_URL,
    REQUIRES_ACCOUNT_SIGN_IN,
)
from .core import CodexStore
from .platform_security import (
    PERMISSION_MODEL,
    is_link_like,
    private_directory,
    private_file,
    set_private_mode,
)


APP_HOME = Path(__file__).resolve().parents[1]
DEFAULT_CODEX_HOME = Path.home() / ".codex"
ALLOWED_BIND_HOSTS = {"127.0.0.1"}
ALLOWED_REQUEST_HOSTS = {"127.0.0.1"}
REQUEST_TOKEN_HEADER = "X-Clean-My-Codex-Token"
PREVIEW_TTL_SECONDS = 10 * 60
LIFECYCLE_SHUTDOWN_PATH = "/api/lifecycle/shutdown"


class JsonError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class CleanMyCodexHandler(BaseHTTPRequestHandler):
    store: CodexStore
    static_dir: Path
    request_token: str
    operation_lock: threading.RLock
    lifecycle_lock: threading.Lock
    preview_receipts: dict[str, dict[str, Any]]
    shutting_down: bool

    server_version = f"CleanMyCodex/{APP_VERSION}"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")

    def do_GET(self) -> None:
        try:
            self._validate_host()
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/"):
                if parsed.path != "/api/health":
                    self._validate_api_token()
                self._send_json(self._handle_get(parsed.path, parse_qs(parsed.query)))
            else:
                self._serve_static(parsed.path)
        except JsonError as exc:
            self._send_json({"error": exc.message}, status=exc.status)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        try:
            self._validate_host()
            self._validate_post_request()
            parsed = urlparse(self.path)
            if not parsed.path.startswith("/api/"):
                raise JsonError(HTTPStatus.NOT_FOUND, "Unknown endpoint")
            body = self._read_json_body()
            handler = type(self)
            is_shutdown_request = parsed.path == LIFECYCLE_SHUTDOWN_PATH
            with handler.lifecycle_lock:
                if is_shutdown_request:
                    handler.shutting_down = True
                elif handler.shutting_down:
                    raise JsonError(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        "The app is closing; no new operation was started",
                    )
            with self.operation_lock:
                with handler.lifecycle_lock:
                    if handler.shutting_down and not is_shutdown_request:
                        raise JsonError(
                            HTTPStatus.SERVICE_UNAVAILABLE,
                            "The app is closing; no new operation was started",
                        )
                if is_shutdown_request:
                    response = {"ok": True, "safe_to_terminate": True}
                else:
                    response = self._handle_post(parsed.path, body)
                self._send_json(response)
        except JsonError as exc:
            self._send_json({"error": exc.message}, status=exc.status)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_get(self, path: str, query: dict[str, list[str]]) -> dict[str, Any]:
        if path == "/api/health":
            public_health: dict[str, Any] = {
                "ok": True,
                "name": APP_NAME,
                "version": APP_VERSION,
            }
            if not self._has_valid_api_token():
                return public_health
            return {
                **public_health,
                "app": {
                    "name": APP_NAME,
                    "version": APP_VERSION,
                    "creator": CREATOR_NAME,
                    "creator_url": CREATOR_URL,
                    "repository_url": REPOSITORY_URL,
                    "issues_url": ISSUES_URL,
                    "requires_account_sign_in": REQUIRES_ACCOUNT_SIGN_IN,
                },
                "app_home": str(self.store.app_home),
                "codex_home": str(self.store.codex_home),
                "codex_home_exists": self.store.codex_home.exists(),
                "dry_run_default": True,
                "permission_model": PERMISSION_MODEL,
                "report_exists": (self.store.app_home / "CODEX_DATA_STRUCTURE_REPORT.md").exists(),
            }
        if path == "/api/discovery":
            paths = self.store.scan_paths()
            chats = self.store.scan_chats()
            return {
                "chat_count": len(chats),
                "active_chat_count": len([chat for chat in chats if not chat["archived"]]),
                "archived_chat_count": len([chat for chat in chats if chat["archived"]]),
                "missing_path_count": len(paths["missing_paths"]),
                "managed_active_files": [str(path) for path in self.store.managed_active_paths()],
                "never_touch": [str(self.store.codex_home / "auth.json")],
                "paths": paths,
            }
        if path == "/api/chats":
            return {"chats": self._filter_chats(query)}
        if path == "/api/paths":
            return self.store.scan_paths()
        if path == "/api/missing-paths":
            return self.store.scan_missing_paths()
        if path == "/api/trash-bin":
            return {"items": self.store.list_trash_bin()}
        if path == "/api/folder-cleaner/scan":
            return self.store.scan_codex_folder()
        if path == "/api/logs":
            limit = self._int_query(query, "limit", 250)
            return {"logs": self.store.list_logs(limit=limit)}
        raise JsonError(HTTPStatus.NOT_FOUND, "Unknown endpoint")

    def _handle_post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if path == "/api/chats/preview-delete":
            thread_ids = self._thread_ids(body)
            preview = self.store.preview_delete_chat_to_trash_bin(thread_ids)
            return self._issue_preview("delete-chat", {"thread_ids": thread_ids}, preview)
        if path == "/api/chats/delete":
            if not bool(body.get("confirm")) or body.get("confirmation") != "DELETE CHAT":
                raise JsonError(HTTPStatus.BAD_REQUEST, "Type DELETE CHAT to confirm")
            thread_ids = self._thread_ids(body)
            preview = self.store.preview_delete_chat_to_trash_bin(thread_ids)
            self._consume_preview("delete-chat", {"thread_ids": thread_ids}, preview, body)
            return self.store.delete_chat_to_trash_bin(
                thread_ids,
                confirmation="DELETE CHAT",
                preview=preview,
            )
        if path == "/api/projects/preview-delete":
            project_path = self._required(body, "path")
            preview = self.store.preview_delete_project_from_codex_to_trash_bin(project_path)
            return self._issue_preview("delete-project", {"path": project_path}, preview)
        if path == "/api/projects/delete":
            if not bool(body.get("confirm")) or body.get("confirmation") != "DELETE PROJECT FROM CODEX":
                raise JsonError(HTTPStatus.BAD_REQUEST, "Type DELETE PROJECT FROM CODEX to confirm")
            project_path = self._required(body, "path")
            preview = self.store.preview_delete_project_from_codex_to_trash_bin(project_path)
            self._consume_preview("delete-project", {"path": project_path}, preview, body)
            return self.store.delete_project_from_codex_to_trash_bin(
                project_path,
                confirmation="DELETE PROJECT FROM CODEX",
                preview=preview,
            )
        if path == "/api/missing-paths/preview-trash":
            missing_path = self._required(body, "path")
            preview = self.store.preview_move_missing_path_to_trash_bin(missing_path)
            return self._issue_preview("trash-missing-path", {"path": missing_path}, preview)
        if path == "/api/missing-paths/trash":
            if not bool(body.get("confirm")) or body.get("confirmation") != "MOVE MISSING PATHS TO TRASH":
                raise JsonError(HTTPStatus.BAD_REQUEST, "Type MOVE MISSING PATHS TO TRASH to confirm")
            missing_path = self._required(body, "path")
            preview = self.store.preview_move_missing_path_to_trash_bin(missing_path)
            self._consume_preview("trash-missing-path", {"path": missing_path}, preview, body)
            return self.store.move_missing_path_to_trash_bin(
                missing_path,
                confirmation="MOVE MISSING PATHS TO TRASH",
                preview=preview,
            )
        if path == "/api/missing-paths/preview-trash-all":
            preview = self.store.preview_move_all_unrecoverable_missing_paths_to_trash_bin()
            return self._issue_preview("trash-all-missing-paths", {}, preview)
        if path == "/api/missing-paths/trash-all":
            if not bool(body.get("confirm")) or body.get("confirmation") != "MOVE MISSING PATHS TO TRASH":
                raise JsonError(HTTPStatus.BAD_REQUEST, "Type MOVE MISSING PATHS TO TRASH to confirm")
            preview = self.store.preview_move_all_unrecoverable_missing_paths_to_trash_bin()
            self._consume_preview("trash-all-missing-paths", {}, preview, body)
            return self.store.move_all_unrecoverable_missing_paths_to_trash_bin(
                confirmation="MOVE MISSING PATHS TO TRASH",
                preview=preview,
            )
        if path == "/api/trash-bin/preview-restore":
            item_id = self._required(body, "item_id")
            preview = self.store.preview_restore_trash_bin_item(item_id)
            return self._issue_preview("restore-trash-bin", {"item_id": item_id}, preview)
        if path == "/api/trash-bin/restore":
            if not bool(body.get("confirm")) or body.get("confirmation") != "RESTORE FROM TRASH":
                raise JsonError(HTTPStatus.BAD_REQUEST, "Type RESTORE FROM TRASH to confirm")
            item_id = self._required(body, "item_id")
            preview = self.store.preview_restore_trash_bin_item(item_id)
            self._consume_preview("restore-trash-bin", {"item_id": item_id}, preview, body)
            return self.store.restore_trash_bin_item(
                item_id,
                confirmation="RESTORE FROM TRASH",
                overwrite=bool(body.get("overwrite")),
            )
        if path == "/api/trash-bin/preview-permanent-delete":
            item_id = self._required(body, "item_id")
            preview = self.store.preview_permanently_delete_trash_bin_item(item_id)
            return self._issue_preview("permanent-delete-trash-bin", {"item_id": item_id}, preview)
        if path == "/api/trash-bin/permanent-delete":
            if not bool(body.get("confirm")) or body.get("confirmation") != "PERMANENT DELETE":
                raise JsonError(HTTPStatus.BAD_REQUEST, "Type PERMANENT DELETE to confirm")
            item_id = self._required(body, "item_id")
            preview = self.store.preview_permanently_delete_trash_bin_item(item_id)
            self._consume_preview("permanent-delete-trash-bin", {"item_id": item_id}, preview, body)
            return self.store.permanently_delete_trash_bin_item(
                item_id,
                confirmation="PERMANENT DELETE",
            )
        if path == "/api/folder-cleaner/preview-archive":
            paths = self._paths(body)
            preview = self.store.preview_move_codex_files_to_trash_bin(paths)
            return self._issue_preview("trash-stale-files", {"paths": paths}, preview)
        if path == "/api/folder-cleaner/archive":
            if not bool(body.get("confirm")) or body.get("confirmation") != "MOVE CODEX FILES TO TRASH":
                raise JsonError(HTTPStatus.BAD_REQUEST, "Type MOVE CODEX FILES TO TRASH to confirm")
            paths = self._paths(body)
            preview = self.store.preview_move_codex_files_to_trash_bin(paths)
            self._consume_preview("trash-stale-files", {"paths": paths}, preview, body)
            return self.store.move_codex_files_to_trash_bin(
                paths,
                confirmation="MOVE CODEX FILES TO TRASH",
                preview=preview,
            )
        if path == "/api/folder-cleaner/report":
            return self.store.generate_codex_cleanliness_report()
        if path == "/api/relocation/preview":
            request = {
                "old_path": self._required(body, "old_path"),
                "new_path": self._required(body, "new_path"),
            }
            preview = self.store.preview_relocation(**request)
            return self._issue_preview("apply-relocation", request, preview)
        if path == "/api/relocation/apply":
            if not bool(body.get("confirm")) or body.get("confirmation") != "APPLY RELOCATION":
                raise JsonError(HTTPStatus.BAD_REQUEST, "Type APPLY RELOCATION to confirm")
            request = {
                "old_path": self._required(body, "old_path"),
                "new_path": self._required(body, "new_path"),
            }
            preview = self.store.preview_relocation(**request)
            self._consume_preview("apply-relocation", request, preview, body)
            return self.store.apply_relocation(
                **request,
                confirm=True,
                preview=preview,
            )
        raise JsonError(HTTPStatus.NOT_FOUND, "Unknown endpoint")

    def _filter_chats(self, query: dict[str, list[str]]) -> list[dict[str, Any]]:
        search = self._query(query, "search").lower()
        cwd = self._query(query, "cwd")
        archived = self._query(query, "archived", "active")
        chats = self.store.scan_chats()
        if archived == "active":
            chats = [chat for chat in chats if not chat["archived"]]
        elif archived == "archived":
            chats = [chat for chat in chats if chat["archived"]]
        if cwd:
            chats = [chat for chat in chats if chat.get("cwd") == cwd]
        if search:
            fields = ["id", "display_title", "title", "indexed_title", "cwd", "preview", "rollout_path"]
            chats = [
                chat
                for chat in chats
                if any(search in str(chat.get(field) or "").lower() for field in fields)
            ]
        return chats

    def _serve_static(self, raw_path: str) -> None:
        if raw_path in {"", "/"}:
            raw_path = "/index.html"
        relative = Path(unquote(raw_path.lstrip("/")))
        target = (self.static_dir / relative).resolve()
        static_root = self.static_dir.resolve()
        try:
            target.relative_to(static_root)
        except ValueError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not target.exists() or target.is_dir():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        payload = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(payload)

    def _validate_host(self) -> None:
        raw_host = self.headers.get("Host") or ""
        try:
            hostname = (urlparse(f"//{raw_host}").hostname or "").lower()
        except ValueError as exc:
            raise JsonError(HTTPStatus.BAD_REQUEST, "Invalid Host header") from exc
        if hostname not in ALLOWED_REQUEST_HOSTS:
            raise JsonError(HTTPStatus.FORBIDDEN, "Requests must use a loopback host")

    def _validate_post_request(self) -> None:
        content_type = (self.headers.get("Content-Type") or "").partition(";")[0].strip().lower()
        if content_type != "application/json":
            raise JsonError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "POST requests require application/json")

        self._validate_api_token()

        origin = self.headers.get("Origin")
        if not origin:
            return
        parsed = urlparse(origin)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme != "http" or hostname not in ALLOWED_REQUEST_HOSTS:
            raise JsonError(HTTPStatus.FORBIDDEN, "Cross-origin requests are not allowed")
        expected_port = int(self.server.server_port)
        origin_port = parsed.port or 80
        if origin_port != expected_port:
            raise JsonError(HTTPStatus.FORBIDDEN, "Cross-origin requests are not allowed")

    def _has_valid_api_token(self) -> bool:
        supplied_token = self.headers.get(REQUEST_TOKEN_HEADER) or ""
        return bool(supplied_token) and secrets.compare_digest(supplied_token, self.request_token)

    def _validate_api_token(self) -> None:
        if not self._has_valid_api_token():
            raise JsonError(HTTPStatus.FORBIDDEN, "Invalid app session")

    @staticmethod
    def _payload_digest(payload: Any) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _issue_preview(
        self,
        action: str,
        request_payload: dict[str, Any],
        preview: dict[str, Any],
    ) -> dict[str, Any]:
        now = time.monotonic()
        receipts = type(self).preview_receipts
        for expired_token in [
            token
            for token, receipt in receipts.items()
            if float(receipt.get("expires_at", 0)) <= now
        ]:
            receipts.pop(expired_token, None)
        token = secrets.token_urlsafe(24)
        receipts[token] = {
            "action": action,
            "request_digest": self._payload_digest(request_payload),
            "preview_digest": self._payload_digest(preview),
            "expires_at": now + PREVIEW_TTL_SECONDS,
        }
        return {**preview, "preview_token": token}

    def _consume_preview(
        self,
        action: str,
        request_payload: dict[str, Any],
        current_preview: dict[str, Any],
        body: dict[str, Any],
    ) -> None:
        token = str(body.get("preview_token") or "")
        if not token:
            raise JsonError(HTTPStatus.CONFLICT, "Run a fresh preview before applying this action")
        receipt = type(self).preview_receipts.pop(token, None)
        if not receipt or float(receipt.get("expires_at", 0)) <= time.monotonic():
            raise JsonError(HTTPStatus.CONFLICT, "The preview expired; run it again")
        if receipt.get("action") != action:
            raise JsonError(HTTPStatus.CONFLICT, "The preview belongs to a different action")
        if receipt.get("request_digest") != self._payload_digest(request_payload):
            raise JsonError(HTTPStatus.CONFLICT, "The selected targets changed after preview")
        if receipt.get("preview_digest") != self._payload_digest(current_preview):
            raise JsonError(HTTPStatus.CONFLICT, "Codex data changed after preview; run it again")

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length < 0:
            raise JsonError(HTTPStatus.BAD_REQUEST, "Invalid request body length")
        if length > 2_000_000:
            raise JsonError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body too large")
        if length == 0:
            return {}
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise JsonError(HTTPStatus.BAD_REQUEST, f"Invalid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise JsonError(HTTPStatus.BAD_REQUEST, "JSON body must be an object")
        return payload

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _send_security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")

    @staticmethod
    def _query(query: dict[str, list[str]], key: str, default: str = "") -> str:
        values = query.get(key)
        return values[0] if values else default

    def _int_query(self, query: dict[str, list[str]], key: str, default: int) -> int:
        try:
            return int(self._query(query, key, str(default)))
        except ValueError:
            return default

    @staticmethod
    def _required(body: dict[str, Any], key: str) -> str:
        value = str(body.get(key) or "").strip()
        if not value:
            raise JsonError(HTTPStatus.BAD_REQUEST, f"Missing required field: {key}")
        return value

    @staticmethod
    def _thread_ids(body: dict[str, Any]) -> list[str]:
        values = body.get("thread_ids") or []
        if not isinstance(values, list) or not values:
            raise JsonError(HTTPStatus.BAD_REQUEST, "thread_ids must be a non-empty list")
        return [str(value) for value in values]

    @staticmethod
    def _paths(body: dict[str, Any]) -> list[str]:
        values = body.get("paths") or []
        if not isinstance(values, list) or not values:
            raise JsonError(HTTPStatus.BAD_REQUEST, "paths must be a non-empty list")
        return [str(value) for value in values]


def make_handler(
    store: CodexStore,
    static_dir: Path,
    request_token: str | None = None,
) -> type[CleanMyCodexHandler]:
    class BoundCleanMyCodexHandler(CleanMyCodexHandler):
        pass

    BoundCleanMyCodexHandler.store = store
    BoundCleanMyCodexHandler.static_dir = static_dir
    BoundCleanMyCodexHandler.request_token = request_token or secrets.token_urlsafe(32)
    BoundCleanMyCodexHandler.operation_lock = threading.RLock()
    BoundCleanMyCodexHandler.lifecycle_lock = threading.Lock()
    BoundCleanMyCodexHandler.preview_receipts = {}
    BoundCleanMyCodexHandler.shutting_down = False
    return BoundCleanMyCodexHandler


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Clean My Codex")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--codex-home", default=str(DEFAULT_CODEX_HOME))
    parser.add_argument("--app-home", default=str(APP_HOME))
    parser.add_argument("--static-dir")
    parser.add_argument("--token-file")
    parser.add_argument("--ready-file")
    return parser


def write_ready_file(path: Path, port: int) -> None:
    path = path.expanduser()
    if path.exists() or is_link_like(path):
        raise SystemExit("The app ready file already exists or is unsafe")
    if is_link_like(path.parent):
        raise SystemExit("The app ready-file directory is unsafe")
    parent = path.parent.resolve()
    if not private_directory(parent):
        raise SystemExit("The app ready-file directory is missing or unsafe")

    path = parent / path.name
    temporary = parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        set_private_mode(temporary, 0o600)
        payload = json.dumps({"port": int(port)}, separators=(",", ":")).encode("utf-8")
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("Unable to publish the app ready file")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.link(temporary, path)
    except FileExistsError as exc:
        raise SystemExit("The app ready file was replaced before publication") from exc
    finally:
        temporary.unlink(missing_ok=True)


def remove_ready_file(path: Path | None) -> None:
    if path is None or not path.exists() or is_link_like(path):
        return
    if private_file(path):
        path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.host not in ALLOWED_BIND_HOSTS:
        raise SystemExit("Clean My Codex only binds to 127.0.0.1")
    if hasattr(os, "umask"):
        os.umask(0o077)
    app_home = Path(args.app_home).expanduser().resolve()
    codex_home = Path(args.codex_home).expanduser().resolve()
    static_dir = (
        Path(args.static_dir).expanduser().resolve()
        if args.static_dir
        else app_home / "static"
    )
    if not static_dir.is_dir() or not (static_dir / "index.html").is_file():
        raise SystemExit("Clean My Codex could not find its interface files")
    store = CodexStore(codex_home, app_home)
    if args.token_file:
        token_file = Path(args.token_file).expanduser()
        if not private_file(token_file):
            raise SystemExit("The app session file is missing or unsafe")
        request_token = token_file.read_text(encoding="utf-8").strip()
        if len(request_token) < 32:
            raise SystemExit("The app session capability is invalid")
    else:
        request_token = os.environ.get("CLEAN_MY_CODEX_TOKEN") or secrets.token_urlsafe(32)
    ready_file = Path(args.ready_file).expanduser() if args.ready_file else None
    server = ThreadingHTTPServer(
        (args.host, args.port),
        make_handler(store, static_dir, request_token=request_token),
    )
    launch_url = f"http://{args.host}:{server.server_port}"
    if ready_file is not None:
        write_ready_file(ready_file, server.server_port)
    print(f"{APP_NAME} v{APP_VERSION}: {launch_url}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Clean My Codex")
    finally:
        server.server_close()
        remove_ready_file(ready_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
