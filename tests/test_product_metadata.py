import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

from clean_my_codex import (
    APP_NAME,
    APP_VERSION,
    CREATOR_NAME,
    CREATOR_URL,
    ISSUES_URL,
    REQUIRES_ACCOUNT_SIGN_IN,
    REPOSITORY_URL,
    __version__,
)
from clean_my_codex.server import (
    ALLOWED_BIND_HOSTS,
    CleanMyCodexHandler,
    JsonError,
    REQUEST_TOKEN_HEADER,
)


ROOT = Path(__file__).resolve().parents[1]


class ProductMetadataTests(unittest.TestCase):
    def test_version_and_creator_have_one_canonical_source(self):
        self.assertEqual(APP_NAME, "Clean My Codex")
        self.assertEqual(APP_VERSION, "0.3.1")
        self.assertEqual(__version__, APP_VERSION)
        self.assertEqual(CREATOR_NAME, "ENVOCS Studio")
        self.assertEqual(CREATOR_URL, "https://github.com/ethan3113")
        self.assertEqual(REPOSITORY_URL, "https://github.com/ethan3113/clean-my-codex")
        self.assertEqual(ISSUES_URL, f"{REPOSITORY_URL}/issues/new?template=bug_report.yml")
        self.assertFalse(REQUIRES_ACCOUNT_SIGN_IN)
        self.assertEqual(CleanMyCodexHandler.server_version, f"CleanMyCodex/{APP_VERSION}")
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn(f'id="app-version">v{APP_VERSION}</span>', html)
        self.assertIn(f'meta.version || "{APP_VERSION}"', script)

    def test_logo_is_valid_svg(self):
        logo = ROOT / "static" / "logo.svg"
        self.assertTrue(logo.is_file())
        root = ElementTree.parse(logo).getroot()
        self.assertTrue(root.tag.endswith("svg"))
        self.assertEqual(root.attrib.get("viewBox"), "0 0 32 32")

    def test_interface_uses_semantic_status_icons(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertNotIn("<i></i>", html)
        self.assertNotIn("status-pill::before", css)
        for name in ["ok", "warn", "bad", "info", "muted", "sync"]:
            self.assertIn(f'id="icon-status-{name}"', html)

    def test_checkbox_motion_is_circular_and_accessible(self):
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertIn('input[type="checkbox"]', css)
        self.assertIn("appearance: none", css)
        self.assertIn("border-radius: 50%", css)
        self.assertIn("@keyframes checkbox-shell-pop", css)
        self.assertIn("@keyframes checkbox-tick-in", css)
        self.assertIn('input[type="checkbox"]:indeterminate', css)
        self.assertIn(':hover:not(:disabled):not(:checked):not(:indeterminate)', css)
        self.assertIn('input[type="checkbox"]:checked:hover:not(:disabled)', css)
        self.assertIn("@media (prefers-reduced-motion: reduce)", css)

    def test_every_navigation_destination_has_specific_motion(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        motions = {
            "chats": "nav-chat-left",
            "missing": "nav-missing-search",
            "relocation": "nav-relocate-left",
            "cleaner": "nav-brush-head",
            "trashbin": "nav-trash-lid",
            "logs": "nav-log-line-one",
        }
        for page, motion_class in motions.items():
            self.assertIn(f'data-page="{page}"', html)
            self.assertIn(motion_class, html)
            self.assertIn(f'data-page="{page}"', css)
        self.assertIn("playNavMotion(button)", script)
        self.assertIn('classList.add("is-animating")', script)
        self.assertIn("requestAnimationFrame", script)
        self.assertIn('(prefers-reduced-motion: reduce)', script)
        self.assertIn("--nav-motion-easing: ease-in-out", css)
        self.assertIn("nav-trash-lid-action 1000ms", css)
        self.assertIn("}, 1000);", script)

    def test_issue_report_control_uses_canonical_github_url(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        template = ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml"
        self.assertIn('id="icon-bug"', html)
        self.assertIn('id="issue-link"', html)
        self.assertIn(ISSUES_URL, html)
        self.assertIn('meta.issues_url', script)
        self.assertTrue(template.is_file())

    def test_post_requests_require_json_and_the_runtime_token(self):
        handler = CleanMyCodexHandler.__new__(CleanMyCodexHandler)
        handler.request_token = "test-token"
        handler.server = SimpleNamespace(server_port=8765)
        handler.headers = {
            "Content-Type": "application/json",
            REQUEST_TOKEN_HEADER: "test-token",
            "Origin": "http://127.0.0.1:8765",
        }
        handler._validate_post_request()

        handler.headers[REQUEST_TOKEN_HEADER] = "wrong-token"
        with self.assertRaises(JsonError):
            handler._validate_post_request()

        handler.headers = {
            "Content-Type": "text/plain",
            REQUEST_TOKEN_HEADER: "test-token",
        }
        with self.assertRaises(JsonError):
            handler._validate_post_request()

    def test_health_does_not_disclose_the_session_or_paths_without_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = CleanMyCodexHandler.__new__(CleanMyCodexHandler)
            handler.request_token = "test-token"
            handler.headers = {}
            handler.store = SimpleNamespace(
                app_home=Path(tmp) / "app",
                codex_home=Path(tmp) / ".codex",
            )
            public = handler._handle_get("/api/health", {})
            self.assertEqual(set(public), {"ok", "name", "version"})
            self.assertNotIn("request_token", public)
            self.assertNotIn("app_home", public)
            self.assertNotIn("codex_home", public)

            handler.headers = {REQUEST_TOKEN_HEADER: "test-token"}
            protected = handler._handle_get("/api/health", {})
            self.assertIn("app", protected)
            self.assertFalse(protected["app"]["requires_account_sign_in"])
            self.assertIn("app_home", protected)
            self.assertNotIn("request_token", protected)

    def test_all_data_apis_require_the_app_session_token(self):
        handler = CleanMyCodexHandler.__new__(CleanMyCodexHandler)
        handler.request_token = "test-token"
        handler.headers = {}
        with self.assertRaises(JsonError):
            handler._validate_api_token()
        handler.headers = {REQUEST_TOKEN_HEADER: "test-token"}
        handler._validate_api_token()

    def test_dialog_resets_stale_confirmation_and_handles_escape_as_cancel(self):
        script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('dialog.returnValue = "";', script)
        self.assertIn('dialog.returnValue = "cancel";', script)
        self.assertIn('dialog.addEventListener("cancel", onCancel)', script)
        self.assertIn('dialog.removeEventListener("cancel", onCancel)', script)
        self.assertIn('if (dialog.open)', script)
        self.assertIn('aria-labelledby="confirm-title"', html)
        self.assertIn('aria-describedby="confirm-message"', html)

    def test_launchers_hide_capability_from_process_arguments(self):
        for name in ["Clean My Codex.command", "run.command"]:
            source = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn('/#token=%s', source)
            self.assertIn('/usr/bin/open "$BOOTSTRAP_FILE"', source)
            self.assertNotIn('/usr/bin/open "${URL}/#token=', source)
            self.assertNotIn('CLEAN_MY_CODEX_TOKEN="$TOKEN"', source)
            self.assertIn('--token-file "$SESSION_FILE"', source)
            self.assertIn('--codex-home "$CODEX_HOME"', source)
            self.assertIn('CLEAN_MY_CODEX_HOME', source)
            self.assertIn(".clean-my-codex-session", source)
            self.assertIn('prefix="clean-my-codex-launch."', source)
            self.assertIn('suffix=".html"', source)
            self.assertIn("os.path.realpath", source)
            self.assertIn("os.O_EXCL", source)
            self.assertIn("value.st_uid == os.getuid()", source)
        script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("consumeSessionToken();", script)
        self.assertNotIn("health.request_token", script)

    def test_destructive_actions_are_single_flight_and_preview_bound(self):
        script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("runControlledAction", script)
        self.assertIn("destructiveActionActive", script)
        self.assertIn("shell.inert = true", script)
        self.assertIn("shell.inert = false", script)
        self.assertIn("preview_token", script)
        self.assertIn("Preview these exact paths before applying relocation", script)
        self.assertIn('$("old-path").addEventListener("input", invalidateRelocationPreview)', script)

    def test_preview_receipts_bind_action_targets_and_current_preview(self):
        handler = CleanMyCodexHandler.__new__(CleanMyCodexHandler)
        CleanMyCodexHandler.preview_receipts = {}
        preview = {"thread_ids": ["thread-1"], "dry_run": True}
        issued = handler._issue_preview("delete-chat", {"thread_ids": ["thread-1"]}, preview)
        body = {"preview_token": issued["preview_token"]}
        handler._consume_preview("delete-chat", {"thread_ids": ["thread-1"]}, preview, body)
        with self.assertRaises(JsonError):
            handler._consume_preview("delete-chat", {"thread_ids": ["thread-1"]}, preview, body)

        issued = handler._issue_preview("delete-chat", {"thread_ids": ["thread-1"]}, preview)
        with self.assertRaises(JsonError):
            handler._consume_preview(
                "delete-chat",
                {"thread_ids": ["thread-2"]},
                preview,
                {"preview_token": issued["preview_token"]},
            )

    def test_host_validation_blocks_non_loopback_hosts(self):
        self.assertEqual(ALLOWED_BIND_HOSTS, {"127.0.0.1"})
        handler = CleanMyCodexHandler.__new__(CleanMyCodexHandler)
        handler.headers = {"Host": "127.0.0.1:8765"}
        handler._validate_host()

        handler.headers = {"Host": "example.com:8765"}
        with self.assertRaises(JsonError):
            handler._validate_host()
        handler.headers = {"Host": "localhost:8765"}
        with self.assertRaises(JsonError):
            handler._validate_host()


if __name__ == "__main__":
    unittest.main()
