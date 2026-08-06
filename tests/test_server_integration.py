import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from unittest import mock
from http.server import ThreadingHTTPServer
from pathlib import Path

from clean_my_codex.core import CodexStore
from clean_my_codex.server import (
    LIFECYCLE_SHUTDOWN_PATH,
    REQUEST_TOKEN_HEADER,
    build_arg_parser,
    make_handler,
    remove_ready_file,
    write_ready_file,
)


class ServerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.codex_home = self.root / ".codex"
        self.app_home = self.root / "app"
        self.static_dir = self.app_home / "static"
        self.codex_home.mkdir()
        self.static_dir.mkdir(parents=True)
        (self.static_dir / "index.html").write_text("<!doctype html><title>Test</title>", encoding="utf-8")
        self.old_path = str(self.root / "missing-workspace")
        self.new_path = str(self.root / "new-workspace")
        Path(self.new_path).mkdir()
        (self.codex_home / "config.toml").write_text(
            f'[projects."{self.old_path}"]\ntrust_level = "trusted"\n',
            encoding="utf-8",
        )
        (self.codex_home / ".codex-global-state.json").write_text("{}\n", encoding="utf-8")
        self.token = "integration-capability-" + ("x" * 32)
        store = CodexStore(self.codex_home, self.app_home)
        self.handler = make_handler(store, self.static_dir, request_token=self.token)
        try:
            self.server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                self.handler,
            )
        except PermissionError:
            self.temporary.cleanup()
            self.skipTest("Loopback sockets are unavailable in this test environment")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temporary.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        payload = None if body is None else json.dumps(body)
        request_headers = dict(headers or {})
        if body is not None:
            request_headers.setdefault("Content-Type", "application/json")
        connection.request(method, path, body=payload, headers=request_headers)
        response = connection.getresponse()
        content = response.read()
        result = (
            json.loads(content.decode("utf-8"))
            if response.getheader("Content-Type", "").startswith("application/json")
            else content
        )
        headers_result = dict(response.getheaders())
        status = response.status
        connection.close()
        return status, headers_result, result

    def authorized_headers(self):
        return {
            REQUEST_TOKEN_HEADER: self.token,
            "Origin": f"http://127.0.0.1:{self.port}",
        }

    def test_health_is_minimal_and_data_routes_require_capability(self):
        status, headers, health = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(set(health), {"ok", "name", "version"})
        self.assertNotIn("codex_home", health)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

        status, _, _ = self.request("GET", "/api/chats")
        self.assertEqual(status, 403)
        status, _, payload = self.request(
            "GET",
            "/api/chats",
            headers={REQUEST_TOKEN_HEADER: self.token},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"chats": []})

    def test_host_and_origin_checks_reject_non_loopback_requests(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.putrequest("GET", "/api/health", skip_host=True)
        connection.putheader("Host", "example.invalid")
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 403)
        response.read()
        connection.close()

        status, _, _ = self.request(
            "POST",
            "/api/relocation/preview",
            body={"old_path": self.old_path, "new_path": self.new_path},
            headers={
                REQUEST_TOKEN_HEADER: self.token,
                "Origin": "https://example.invalid",
            },
        )
        self.assertEqual(status, 403)

        status, _, _ = self.request(
            "POST",
            LIFECYCLE_SHUTDOWN_PATH,
            body={},
        )
        self.assertEqual(status, 403)
        self.assertTrue(self.thread.is_alive())

    def test_apply_requires_confirmation_and_consumes_exact_preview_once(self):
        request_body = {"old_path": self.old_path, "new_path": self.new_path}
        status, _, preview = self.request(
            "POST",
            "/api/relocation/preview",
            body=request_body,
            headers=self.authorized_headers(),
        )
        self.assertEqual(status, 200)
        self.assertTrue(preview["safe_to_apply"])

        status, _, _ = self.request(
            "POST",
            "/api/relocation/apply",
            body={**request_body, "preview_token": preview["preview_token"]},
            headers=self.authorized_headers(),
        )
        self.assertEqual(status, 400)

        apply_body = {
            **request_body,
            "preview_token": preview["preview_token"],
            "confirm": True,
            "confirmation": "APPLY RELOCATION",
        }
        status, _, result = self.request(
            "POST",
            "/api/relocation/apply",
            body=apply_body,
            headers=self.authorized_headers(),
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "applied")
        self.assertIn(
            self.new_path,
            (self.codex_home / "config.toml").read_text(encoding="utf-8"),
        )

        status, _, _ = self.request(
            "POST",
            "/api/relocation/apply",
            body=apply_body,
            headers=self.authorized_headers(),
        )
        self.assertEqual(status, 409)

    def test_shutdown_waits_for_active_operation(self):
        operation_started = threading.Event()
        release_operation = threading.Event()
        operation_finished = threading.Event()
        shutdown_finished = threading.Event()
        results = {}
        original_handle_post = self.handler._handle_post

        def blocking_handle_post(handler, path, body):
            if path == "/api/test/blocking-operation":
                operation_started.set()
                if not release_operation.wait(timeout=4):
                    raise RuntimeError("Synthetic operation was not released")
                operation_finished.set()
                return {"status": "completed"}
            return original_handle_post(handler, path, body)

        self.handler._handle_post = blocking_handle_post

        def run_operation():
            results["operation"] = self.request(
                "POST",
                "/api/test/blocking-operation",
                body={},
                headers=self.authorized_headers(),
            )

        def request_shutdown():
            results["shutdown"] = self.request(
                "POST",
                LIFECYCLE_SHUTDOWN_PATH,
                body={},
                headers=self.authorized_headers(),
            )
            shutdown_finished.set()

        operation_thread = threading.Thread(target=run_operation)
        operation_thread.start()
        self.assertTrue(operation_started.wait(timeout=2))

        shutdown_thread = threading.Thread(target=request_shutdown)
        shutdown_thread.start()
        for _ in range(200):
            with self.handler.lifecycle_lock:
                if self.handler.shutting_down:
                    break
            time.sleep(0.01)
        with self.handler.lifecycle_lock:
            self.assertTrue(self.handler.shutting_down)

        status, _, late_result = self.request(
            "POST",
            "/api/relocation/preview",
            body={"old_path": self.old_path, "new_path": self.new_path},
            headers=self.authorized_headers(),
        )
        self.assertEqual(status, 503)
        self.assertIn("no new operation", late_result["error"])
        self.assertFalse(shutdown_finished.wait(timeout=0.2))
        self.assertFalse(operation_finished.is_set())
        self.assertTrue(self.thread.is_alive())

        release_operation.set()
        operation_thread.join(timeout=5)
        shutdown_thread.join(timeout=5)

        self.assertFalse(operation_thread.is_alive())
        self.assertFalse(shutdown_thread.is_alive())
        self.assertEqual(results["operation"][0], 200)
        self.assertEqual(results["operation"][2], {"status": "completed"})
        self.assertEqual(results["shutdown"][0], 200)
        self.assertEqual(results["shutdown"][2], {"ok": True, "safe_to_terminate": True})
        self.assertTrue(operation_finished.is_set())
        self.assertTrue(self.thread.is_alive())

class ServerLaunchContractTests(unittest.TestCase):
    def test_packaged_app_arguments_separate_static_and_writable_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            app_home = root / "app"
            static_dir = root / "interface"
            ready_file = root / "private" / "ready.json"
            parsed = build_arg_parser().parse_args(
                [
                    "--port",
                    "0",
                    "--app-home",
                    str(app_home),
                    "--static-dir",
                    str(static_dir),
                    "--ready-file",
                    str(ready_file),
                ]
            )
            self.assertEqual(parsed.port, 0)
            self.assertEqual(parsed.app_home, str(app_home))
            self.assertEqual(parsed.static_dir, str(static_dir))
            self.assertEqual(parsed.ready_file, str(ready_file))

    def test_ready_file_is_private_and_cannot_be_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            private_dir = Path(temporary).resolve() / "private"
            private_dir.mkdir(mode=0o700)
            ready_file = private_dir / "ready.json"

            original_write = os.write
            visible_while_writing = []

            def observed_write(descriptor, payload):
                visible_while_writing.append(ready_file.exists())
                return original_write(descriptor, payload[:2])

            with mock.patch("clean_my_codex.server.os.write", side_effect=observed_write):
                write_ready_file(ready_file, 54321)

            self.assertEqual(json.loads(ready_file.read_text(encoding="utf-8")), {"port": 54321})
            self.assertGreater(len(visible_while_writing), 1)
            self.assertFalse(any(visible_while_writing))
            self.assertEqual(list(private_dir.glob("*.tmp")), [])
            self.assertEqual(ready_file.stat().st_mode & 0o777, 0o600)
            self.assertEqual(ready_file.stat().st_uid, os.getuid())
            with self.assertRaises(SystemExit):
                write_ready_file(ready_file, 12345)

            remove_ready_file(ready_file)
            self.assertFalse(ready_file.exists())


if __name__ == "__main__":
    unittest.main()
