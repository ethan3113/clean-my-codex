import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from clean_my_codex.core import CodexStore
from clean_my_codex.server import REQUEST_TOKEN_HEADER, make_handler


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
        try:
            self.server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                make_handler(store, self.static_dir, request_token=self.token),
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


if __name__ == "__main__":
    unittest.main()
