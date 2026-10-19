"""browser_open scheme and domain checks, and browser_read on the local fixture."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from google_account import TurnGuard, run_tool

from browser.session import BrowserSession
from browser.tools import (
    PAGE_PREFIX,
    PASSWORD_LOG_TEXT,
    _clip_text,
    _field_is_password,
    _host_blocked,
    _looks_like_css,
    browser_attach_active_tab,
    browser_click,
    browser_go_back,
    browser_list_tabs,
    browser_open,
    browser_read,
    browser_request_handoff,
    browser_type,
    page_needs_human,
    take_web_handoff_notice,
    is_consequential,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "test_page.html"


class BrowserToolTests(unittest.TestCase):
    def setUp(self) -> None:
        BrowserSession.stop()
        self._log_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._log_dir.cleanup)
        self.action_log = Path(self._log_dir.name) / "browser_actions.log"
        self._log_patch = patch("browser.tools.BROWSER_ACTION_LOG", self.action_log)
        self._log_patch.start()
        self.addCleanup(self._log_patch.stop)

    def tearDown(self) -> None:
        BrowserSession.stop()

    def _action_log_lines(self) -> list[dict]:
        if not self.action_log.exists():
            return []
        return [
            json.loads(line)
            for line in self.action_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_clip_text_stops_on_a_word_boundary(self) -> None:
        clipped, truncated = _clip_text("Project notes are on this page", 24)
        self.assertTrue(truncated)
        self.assertLessEqual(len(clipped), 24)
        self.assertEqual(clipped, "Project [truncated]")
        self.assertNotIn("note", clipped)

    def test_file_data_and_javascript_urls_are_rejected(self) -> None:
        for url in (
            FIXTURE.resolve().as_uri(),
            "file:///C:/Windows/win.ini",
            "FILE:///etc/passwd",
            "data:text/html,hello",
            "javascript:alert(1)",
        ):
            result = browser_open(url)
            self.assertEqual(result, {"ok": False, "error": "unsupported scheme"})
        self.assertIsNone(BrowserSession._thread)
        text = run_tool("browser_open", {"url": "file:///C:/secret.txt"}, TurnGuard())
        self.assertNotIn("UNTRUSTED", text)
        self.assertEqual(json.loads(text)["error"], "unsupported scheme")

    def test_blocked_domain_is_rejected_before_navigation(self) -> None:
        self.assertFalse(_host_blocked("notevil.example", ["evil.example"]))
        self.assertFalse(_host_blocked("evil.example.not", ["evil.example"]))
        self.assertTrue(_host_blocked("login.evil.example", ["evil.example"]))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps({"browser_blocked_domains": ["evil.example"]}),
                encoding="utf-8",
            )
            with patch("browser.session.CONFIG_PATH", path):
                blocked = browser_open("https://evil.example/login")
                nested = browser_open("https://accounts.evil.example/pay")
        self.assertEqual(blocked, {"ok": False, "error": "blocked domain"})
        self.assertEqual(nested, {"ok": False, "error": "blocked domain"})
        self.assertIsNone(BrowserSession._thread)

    def test_open_timeout_is_reported(self) -> None:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        class FakePage:
            def goto(self, url: str, **kwargs: object) -> None:
                self.kwargs = kwargs
                self.url = url
                raise PlaywrightTimeoutError("Timeout 15000ms exceeded")

            def title(self) -> str:
                return ""

            url = ""

        page = FakePage()

        def run(fn):
            return fn(page)

        with patch.object(BrowserSession, "start", lambda *_args, **_kwargs: None), patch.object(
            BrowserSession, "_on_browser_thread", run
        ), patch("browser.tools._blocked_domains", return_value=[]):
            result = browser_open("https://example.com/slow")
        self.assertEqual(result, {"ok": False, "error": "page did not load in time"})
        self.assertEqual(page.kwargs["wait_until"], "domcontentloaded")
        self.assertEqual(page.kwargs["timeout"], 15000)

    def test_browser_read_lists_fixture_controls(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        BrowserSession.start(headless=True, profile_dir=tmp.name)
        BrowserSession.page.goto(FIXTURE.resolve().as_uri(), wait_until="domcontentloaded")
        result = browser_read()
        self.assertTrue(result["ok"])
        self.assertFalse(result["truncated"])
        self.assertTrue(result["content"].startswith(PAGE_PREFIX))
        blob = json.dumps(result)
        self.assertLess(blob.index("UNTRUSTED EXTERNAL CONTENT"), blob.index("Project notes"))
        by_hint = {item["selector_hint"]: item for item in result["elements"]}
        self.assertEqual(by_hint["e0"]["role"], "link")
        self.assertEqual(by_hint["e0"]["text"], "Project notes")
        self.assertEqual(by_hint["e1"]["role"], "button")
        self.assertEqual(by_hint["e1"]["text"], "Save draft")
        placeholders = [
            item for item in result["elements"] if item.get("placeholder") == "Find a note"
        ]
        self.assertEqual(len(placeholders), 1)
        self.assertEqual(placeholders[0]["role"], "input")
        self.assertTrue(
            any(item["role"] == "button" and item.get("text") == "Send form" for item in result["elements"])
        )
        self.assertTrue(any(item["role"] == "textarea" for item in result["elements"]))
        self.assertTrue(any(item["role"] == "select" for item in result["elements"]))

        one = browser_read(selector="e0")
        self.assertTrue(one["content"].startswith(PAGE_PREFIX))
        self.assertIn("Project notes", one["text"])
        self.assertNotIn("Save draft", one["content"])

        short = browser_read(max_chars=32)
        self.assertTrue(short["truncated"])
        body = short["content"][len(PAGE_PREFIX) :]
        self.assertLessEqual(len(body), 32)
        self.assertTrue(body.endswith("[truncated]"))

    def test_declared_without_google(self) -> None:
        import server
        from voice.session import GOOGLE_TOOLS, live_tool_declarations

        for name, description in (
            ("browser_open", server.BROWSER_OPEN_DESCRIPTION),
            ("browser_read", server.BROWSER_READ_DESCRIPTION),
            ("browser_click", server.BROWSER_CLICK_DESCRIPTION),
            ("browser_type", server.BROWSER_TYPE_DESCRIPTION),
            ("browser_go_back", server.BROWSER_GO_BACK_DESCRIPTION),
            ("browser_list_tabs", server.BROWSER_LIST_TABS_DESCRIPTION),
            ("browser_attach_active_tab", server.BROWSER_ATTACH_ACTIVE_TAB_DESCRIPTION),
            ("browser_request_handoff", server.BROWSER_REQUEST_HANDOFF_DESCRIPTION),
        ):
            described = next(tool for tool in GOOGLE_TOOLS if tool["name"] == name)
            self.assertEqual(described["description"], description)
        disconnected = [tool["name"] for tool in live_tool_declarations(tools_ready=False)]
        for name in (
            "browser_open",
            "browser_read",
            "browser_click",
            "browser_type",
            "browser_go_back",
            "browser_list_tabs",
            "browser_attach_active_tab",
            "browser_request_handoff",
        ):
            self.assertIn(name, disconnected)
        with patch("google_account.google_status", return_value={"connected": True, "drive": True}):
            connected = [tool["name"] for tool in live_tool_declarations(tools_ready=True)]
        for name in (
            "browser_open",
            "browser_read",
            "browser_click",
            "browser_type",
            "browser_go_back",
            "browser_list_tabs",
            "browser_attach_active_tab",
            "browser_request_handoff",
        ):
            self.assertEqual(connected.count(name), 1)
        spec = server.google_tool_spec(include_google=False)
        names = [item.name for item in spec.function_declarations]
        for name in (
            "browser_open",
            "browser_read",
            "browser_click",
            "browser_type",
            "browser_go_back",
            "browser_list_tabs",
            "browser_attach_active_tab",
            "browser_request_handoff",
        ):
            self.assertIn(name, names)
        self.assertNotIn("gmail_unread", names)
        self.assertIn("page_after_action", server.TOOL_RULES)
        self.assertIn("never invent a selector", server.TOOL_RULES)
        self.assertIn("rather than guessing", server.TOOL_RULES)
        self.assertIn("cannot browse the web from here", server.TOOL_RULES)
        self.assertIn("cannot browse the web from here", server.LOCAL_FILE_RULES)
        from voice.session import LIVE_VOICE_RULES

        self.assertIn("cannot browse the web from here", LIVE_VOICE_RULES)
        self.assertIn("needs_human", server.TOOL_RULES)
        self.assertIn("browser_request_handoff", server.TOOL_RULES)
        self.assertIn("browser_list_tabs", server.TOOL_RULES)
        self.assertIn("browser_attach_active_tab", server.TOOL_RULES)
        self.assertIn("browser_list_tabs", server.LOCAL_FILE_RULES)
        self.assertIn("browser_attach_active_tab", LIVE_VOICE_RULES)
        self.assertIn("needs_human", server.LOCAL_FILE_RULES)
        self.assertIn("needs_human", LIVE_VOICE_RULES)
        with patch("google_account.google_status", return_value={"connected": True, "drive": True}):
            connected_spec = server.google_tool_spec(include_google=True)
        connected_names = [item.name for item in connected_spec.function_declarations]
        for name in (
            "browser_open",
            "browser_read",
            "browser_click",
            "browser_type",
            "browser_go_back",
            "browser_list_tabs",
            "browser_attach_active_tab",
            "browser_request_handoff",
        ):
            self.assertEqual(connected_names.count(name), 1)

    def test_plain_labels_are_not_treated_as_css(self) -> None:
        self.assertFalse(_looks_like_css("Find a note"))
        self.assertFalse(_looks_like_css("Save draft"))
        self.assertFalse(_looks_like_css("e0"))
        self.assertFalse(_looks_like_css("//button"))
        self.assertTrue(_looks_like_css("button"))
        self.assertTrue(_looks_like_css("input[name=q]"))
        self.assertTrue(_looks_like_css("#send"))

    def test_type_then_click_submit_reports_the_new_page(self) -> None:
        landed = (
            "<!DOCTYPE html><html><head><title>Landed</title></head>"
            "<body><a href='/return'>Back to form</a></body></html>"
        ).encode("utf-8")

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(landed)))
                self.end_headers()
                self.wfile.write(landed)

            def log_message(self, format: str, *args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        BrowserSession.start(headless=True, profile_dir=tmp.name)
        BrowserSession.page.goto(FIXTURE.resolve().as_uri(), wait_until="domcontentloaded")

        vague = browser_click("button")
        self.assertFalse(vague["ok"])
        self.assertEqual(vague["error"], "ambiguous")
        self.assertEqual(
            sorted(item["text"] for item in vague["candidates"]),
            ["Save draft", "Send form"],
        )
        self.assertNotIn("page_after_action", vague)
        self.assertEqual(BrowserSession.page.title(), "Jarvis browser fixture")

        missing = browser_click("No such control")
        self.assertEqual(missing, {"ok": False, "error": "element not found"})

        stayed = browser_click("Save draft")
        self.assertTrue(stayed["ok"], stayed)
        self.assertIn("test_page.html", stayed["page_after_action"]["url"])
        self.assertTrue(stayed["page_after_action"]["summary"].startswith(PAGE_PREFIX))

        BrowserSession.page.evaluate(
            "(action) => document.querySelector('form').setAttribute('action', action)",
            f"http://127.0.0.1:{port}/landed",
        )
        typed = browser_type("Find a note", "shan noodle")
        self.assertTrue(typed["ok"], typed)
        self.assertIn("test_page.html", typed["page_after_action"]["url"])
        filled = [
            item
            for item in typed["page_after_action"]["elements"]
            if item.get("text") == "shan noodle"
        ]
        self.assertEqual(len(filled), 1)
        self.assertEqual(filled[0]["role"], "input")

        held = browser_click("Send form")
        self.assertFalse(held["ok"])
        self.assertTrue(held["needs_confirmation"])
        self.assertIn("test_page.html", BrowserSession.page.url)

        clicked = browser_click("Send form", confirmed_action=True)
        self.assertTrue(clicked["ok"], clicked)
        after = clicked["page_after_action"]
        self.assertIn(f"127.0.0.1:{port}", after["url"])
        self.assertIn("shan", after["url"])
        self.assertEqual(after["title"], "Landed")
        self.assertTrue(any(item.get("text") == "Back to form" for item in after["elements"]))
        self.assertFalse(any(item.get("text") == "Send form" for item in after["elements"]))
        self.assertTrue(after["summary"].startswith(PAGE_PREFIX))

        back = browser_go_back()
        self.assertTrue(back["ok"], back)
        self.assertIn("test_page.html", back["page_after_action"]["url"])
        self.assertEqual(back["page_after_action"]["title"], "Jarvis browser fixture")
        self.assertTrue(
            any(item.get("text") == "Send form" for item in back["page_after_action"]["elements"])
        )

    def test_ambiguous_submit_hint_clicks_neither_button(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        page_path = root / "ambiguous.html"
        page_path.write_text(
            """<!DOCTYPE html>
<html><head><title>Choose</title></head>
<body>
<button type="button" onclick="document.title='clicked'">Submit draft</button>
<button type="button" onclick="document.title='clicked'">Submit now</button>
<button type="button">Cancel</button>
</body></html>
""",
            encoding="utf-8",
        )
        BrowserSession.start(headless=True, profile_dir=root / "profile")
        BrowserSession.page.goto(page_path.resolve().as_uri(), wait_until="domcontentloaded")
        raw = run_tool("browser_click", {"selector_hint": "Submit"}, TurnGuard())
        result = json.loads(raw)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "ambiguous")
        self.assertEqual(
            [item["text"] for item in result["candidates"]],
            ["Submit draft", "Submit now"],
        )
        self.assertNotIn("Cancel", [item.get("text") for item in result["candidates"]])
        self.assertNotIn("page_after_action", result)
        self.assertEqual(BrowserSession.page.title(), "Choose")

    def test_consequential_keywords_come_from_config(self) -> None:
        self.assertTrue(is_consequential("Buy Now", "https://shop.example/cart"))
        self.assertTrue(is_consequential("Cancel subscription", "https://shop.example/account"))
        self.assertFalse(is_consequential("Save draft", "https://shop.example/cart"))
        self.assertFalse(is_consequential("assignment", "https://shop.example/sign"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps({"consequential_action_keywords": ["delete"]}),
                encoding="utf-8",
            )
            with patch("browser.session.CONFIG_PATH", path):
                self.assertFalse(is_consequential("Buy Now", "https://shop.example/cart"))
                self.assertTrue(is_consequential("Delete item", "https://shop.example/cart"))

    def test_yes_arms_only_the_action_that_was_asked(self) -> None:
        import server

        server.begin_browser_turn("reset")
        self.addCleanup(server.begin_browser_turn, "reset")
        spec = {
            "tool": "browser_click",
            "selector_hint": "Buy Now",
            "action": "click 'Buy Now'",
            "page": "https://shop.example/cart",
        }
        server.note_browser_confirmation(spec)
        server.begin_browser_turn("book a different flight")
        self.assertFalse(
            server.confirmation_allows("browser_click", {"selector_hint": "Buy Now"})
        )
        server.note_browser_confirmation(spec)
        server.begin_browser_turn("yes")
        self.assertFalse(
            server.confirmation_allows("browser_click", {"selector_hint": "Delete"})
        )
        self.assertTrue(
            server.confirmation_allows("browser_click", {"selector_hint": "Buy Now"})
        )
        self.assertFalse(
            server.confirmation_allows("browser_click", {"selector_hint": "Buy Now"})
        )

    def test_buy_now_needs_confirmation_until_the_flag_is_set(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        page_path = root / "shop.html"
        page_path.write_text(
            """<!DOCTYPE html>
<html><head><title>Shop</title></head>
<body>
<button type="button" onclick="document.title='bought'">Buy Now</button>
</body></html>
""",
            encoding="utf-8",
        )
        import server

        server.begin_browser_turn("reset")
        BrowserSession.start(headless=True, profile_dir=root / "profile")
        BrowserSession.page.goto(page_path.resolve().as_uri(), wait_until="domcontentloaded")
        held = browser_click("Buy Now")
        self.assertFalse(held["ok"])
        self.assertTrue(held["needs_confirmation"])
        self.assertEqual(held["action"], "click 'Buy Now'")
        self.assertIn("shop.html", held["page"])
        self.assertNotIn("page_after_action", held)
        self.assertEqual(BrowserSession.page.title(), "Shop")
        done = browser_click("Buy Now", confirmed_action=True)
        self.assertTrue(done["ok"], done)
        self.assertEqual(BrowserSession.page.title(), "bought")

    def test_password_locator_is_detected_before_logging(self) -> None:
        class Locator:
            def __init__(self, attrs: dict[str, str | None]) -> None:
                self.attrs = attrs

            def get_attribute(self, name: str) -> str | None:
                return self.attrs.get(name)

        self.assertTrue(_field_is_password(Locator({"type": "password", "role": None})))
        self.assertTrue(_field_is_password(Locator({"type": "PASSWORD", "role": None})))
        self.assertTrue(_field_is_password(Locator({"type": "text", "role": "password"})))
        self.assertFalse(_field_is_password(Locator({"type": "text", "role": None})))

        class Broken:
            def get_attribute(self, name: str) -> str | None:
                raise RuntimeError(name)

        self.assertTrue(_field_is_password(Broken()))

    def test_browser_actions_are_logged_and_passwords_are_redacted(self) -> None:
        rejected = browser_open("javascript:alert(1)")
        self.assertEqual(rejected["error"], "unsupported scheme")
        opened = self._action_log_lines()
        self.assertEqual(len(opened), 1)
        self.assertEqual(opened[0]["action"], "browser_open")
        self.assertEqual(opened[0]["target"], "javascript:alert(1)")
        self.assertFalse(opened[0]["ok"])
        self.assertEqual(opened[0]["error"], "unsupported scheme")
        self.assertIn("timestamp", opened[0])
        self.assertNotIn("text", opened[0])

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        page_path = root / "login.html"
        secret = "s3cret-passphrase"
        role_secret = "role-secret-value"
        visible = "shan noodle"
        page_path.write_text(
            """<!DOCTYPE html>
<html><head><title>Login</title></head>
<body>
<a href="https://example.com/notes">Project notes</a>
<input type="password" placeholder="Password">
<input type="text" role="password" placeholder="Secret role">
<input type="text" placeholder="Find a note">
<button type="button">Save draft</button>
</body></html>
""",
            encoding="utf-8",
        )
        BrowserSession.start(headless=True, profile_dir=root / "profile")
        BrowserSession.page.goto(page_path.resolve().as_uri(), wait_until="domcontentloaded")
        before_read = len(self._action_log_lines())
        listed = browser_read()
        self.assertTrue(listed["ok"])
        self.assertEqual(len(self._action_log_lines()), before_read)

        typed_secret = browser_type("Password", secret)
        self.assertTrue(typed_secret["ok"], typed_secret)
        typed_role = browser_type("Secret role", role_secret)
        self.assertTrue(typed_role["ok"], typed_role)
        typed_plain = browser_type("Find a note", visible)
        self.assertTrue(typed_plain["ok"], typed_plain)
        missing = browser_click("No such control")
        self.assertEqual(missing["error"], "element not found")
        back = browser_go_back()

        raw = self.action_log.read_text(encoding="utf-8")
        self.assertNotIn(secret, raw)
        self.assertNotIn(role_secret, raw)
        lines = self._action_log_lines()
        by_action: dict[str, list[dict]] = {}
        for line in lines:
            by_action.setdefault(line["action"], []).append(line)

        password_line = next(line for line in by_action["browser_type"] if line["target"] == "Password")
        self.assertEqual(password_line["text"], PASSWORD_LOG_TEXT)
        self.assertTrue(password_line["ok"])
        role_line = next(line for line in by_action["browser_type"] if line["target"] == "Secret role")
        self.assertEqual(role_line["text"], PASSWORD_LOG_TEXT)
        plain_line = next(line for line in by_action["browser_type"] if line["target"] == "Find a note")
        self.assertEqual(plain_line["text"], visible)
        self.assertTrue(plain_line["ok"])

        click_line = by_action["browser_click"][-1]
        self.assertEqual(click_line["target"], "No such control")
        self.assertFalse(click_line["ok"])
        self.assertEqual(click_line["error"], "element not found")

        back_line = by_action["browser_go_back"][-1]
        self.assertEqual(back_line["ok"], bool(back.get("ok")))
        self.assertEqual(back_line["target"], back["page_after_action"]["url"])
        if back.get("ok"):
            self.assertNotIn("error", back_line)
        else:
            self.assertEqual(back_line["error"], back["error"])
        self.assertTrue(back_line["timestamp"])


class HumanGateTests(unittest.TestCase):
    def setUp(self) -> None:
        BrowserSession.stop()
        self._deliver = patch("telegram_bot.deliver_to_paired", return_value="Sent to Telegram.")
        self._deliver.start()
        self.addCleanup(self._deliver.stop)

    def tearDown(self) -> None:
        BrowserSession.stop()

    def test_signals_and_a_typed_password_are_not_stored(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        secret = "s3cret-passphrase"
        pages = {
            "clean.html": "<!DOCTYPE html><html><head><title>Clean</title></head><body><p>Project notes</p></body></html>",
            "captcha-div.html": (
                "<!DOCTYPE html><html><head><title>Div</title></head>"
                "<body><div id='g-recaptcha'></div></body></html>"
            ),
            "captcha-frame.html": (
                "<!DOCTYPE html><html><head><title>Frame</title></head>"
                "<body><iframe src='https://challenges.cloudflare.com/turnstile/v0/api.js'></iframe>"
                "<button type='button'>Save draft</button></body></html>"
            ),
            "password.html": (
                "<!DOCTYPE html><html><head><title>Login</title></head>"
                "<body><input type='password' placeholder='Password'></body></html>"
            ),
            "other-password.html": (
                "<!DOCTYPE html><html><head><title>Other</title></head>"
                "<body><input type='password' placeholder='Password'></body></html>"
            ),
            "otp-auto.html": (
                "<!DOCTYPE html><html><head><title>Code</title></head>"
                "<body><input autocomplete='one-time-code'></body></html>"
            ),
            "otp-text.html": (
                "<!DOCTYPE html><html><head><title>Auth</title></head>"
                "<body><form><label>Verification code <input name='code'></label></form></body></html>"
            ),
            "article.html": (
                "<!DOCTYPE html><html><head><title>Article</title></head>"
                "<body><article>Set up an authenticator app later.</article>"
                "<form><input type='text' placeholder='Find a note'></form></body></html>"
            ),
        }
        for name, html in pages.items():
            (root / name).write_text(html, encoding="utf-8")

        BrowserSession.start(headless=True, profile_dir=root / "profile")

        def open_file(name: str) -> None:
            BrowserSession.page.goto((root / name).resolve().as_uri(), wait_until="domcontentloaded")

        open_file("clean.html")
        clean = page_needs_human()
        self.assertEqual(clean["needs_human"], False)
        self.assertIsNone(clean["reason"])

        open_file("captcha-div.html")
        div_hit = page_needs_human()
        self.assertTrue(div_hit["needs_human"])
        self.assertEqual(div_hit["reason"], "captcha")
        self.assertIn("captcha", div_hit["detail"].lower())

        open_file("captcha-frame.html")
        frame_hit = page_needs_human()
        self.assertEqual(frame_hit["reason"], "captcha")
        clicked = browser_click("Save draft")
        self.assertEqual(clicked["ok"], False)
        self.assertEqual(clicked["needs_human"], True)
        self.assertEqual(clicked["awaiting_human"], True)
        self.assertEqual(clicked["reason"], "captcha")
        self.assertEqual(clicked["page"], BrowserSession.page.url)

        open_file("password.html")
        asked = page_needs_human()
        self.assertEqual(asked["reason"], "password")
        self.assertNotIn(secret, json.dumps(asked))
        blank = browser_type("Password", "   ")
        self.assertEqual(blank["reason"], "password")
        self.assertNotIn(secret, json.dumps(blank))
        typed = browser_type("Password", secret)
        self.assertTrue(typed["ok"], typed)
        self.assertNotIn(secret, " ".join(BrowserSession.password_supplied_flows))
        supplied = page_needs_human()
        self.assertNotIn(secret, json.dumps(supplied))
        self.assertEqual(supplied["needs_human"], False)
        self.assertIsNone(supplied["reason"])

        open_file("other-password.html")
        other = page_needs_human()
        self.assertEqual(other["reason"], "password")

        open_file("otp-auto.html")
        auto = page_needs_human()
        self.assertEqual(auto["reason"], "otp")
        open_file("otp-text.html")
        text = page_needs_human()
        self.assertEqual(text["reason"], "otp")
        self.assertIn("verification code", text["detail"])

        open_file("article.html")
        article = page_needs_human()
        self.assertEqual(article["needs_human"], False)
        self.assertIsNone(article["reason"])

    def test_open_reports_captcha_instead_of_a_loaded_page(self) -> None:
        body = (
            "<!DOCTYPE html><html><head><title>Check</title></head>"
            "<body><div class='hcaptcha'></div></body></html>"
        ).encode("utf-8")

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        BrowserSession.start(headless=True, profile_dir=tmp.name)
        with patch("browser.tools._blocked_domains", return_value=[]):
            result = browser_open(f"http://127.0.0.1:{port}/check")
        self.assertEqual(result["ok"], False)
        self.assertEqual(result["needs_human"], True)
        self.assertEqual(result["reason"], "captcha")
        self.assertIn(f"127.0.0.1:{port}", result["page"])
        self.assertNotIn("title", result)

    def test_handoff_notifies_and_web_chat_only_when_the_turn_started_there(self) -> None:
        import server

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        BrowserSession.start(headless=True, profile_dir=tmp.name)
        BrowserSession.page.goto(FIXTURE.resolve().as_uri(), wait_until="domcontentloaded")
        server.set_task_source("web")
        self.addCleanup(server.set_task_source, "web")
        self.addCleanup(server.begin_browser_turn, "reset")
        sent: list[str] = []

        def deliver(text: str) -> str:
            sent.append(text)
            return "Sent to Telegram."

        with patch("telegram_bot.deliver_to_paired", deliver):
            result = browser_request_handoff("captcha")
        self.assertEqual(result, {"ok": True, "awaiting_human": True})
        self.assertTrue(BrowserSession.awaiting_human)
        self.assertEqual(BrowserSession.awaiting_human_reason, "captcha")
        self.assertTrue(BrowserSession.awaiting_human_at)
        self.assertEqual(
            sent,
            [
                "I need you to take over the browser — captcha. "
                "The window is open on your screen. "
                "Reply 'done' (or just continue chatting) once you've finished."
            ],
        )
        self.assertEqual(take_web_handoff_notice(), sent[0])

        server.set_task_source("pocket")
        with patch("telegram_bot.deliver_to_paired", deliver):
            pocket = browser_request_handoff("otp")
        self.assertEqual(pocket["ok"], True)
        self.assertEqual(take_web_handoff_notice(), "")
        self.assertIn("otp", sent[-1])

        BrowserSession.password_supplied_flows.add("https://example.com/login")
        server.begin_browser_turn("done")
        self.assertTrue(BrowserSession.awaiting_human)
        self.assertEqual(BrowserSession.awaiting_human_reason, "otp")
        self.assertIn("https://example.com/login", BrowserSession.password_supplied_flows)
        with patch(
            "browser.tools.page_needs_human",
            return_value={"needs_human": False, "reason": None, "detail": ""},
        ):
            follow = server.browser_handoff_followup()
        self.assertEqual(follow["action"], "proceed")
        self.assertIn("completed the manual step (otp)", follow["note"])
        self.assertFalse(BrowserSession.awaiting_human)
        self.assertEqual(BrowserSession.awaiting_human_reason, "")

    def test_attach_active_tab_matches_one_none_or_many(self) -> None:
        inbox = _FakeTab("Gmail - Inbox", "https://mail.google.com/mail/u/0/#inbox")
        sent = _FakeTab("Gmail - Sent", "https://mail.google.com/mail/u/0/#sent")
        calendar = _FakeTab("Calendar", "https://calendar.google.com/calendar")
        closed = _FakeTab("Old Gmail", "https://mail.google.com/mail/u/0/#drafts", closed=True)
        other = _FakeTab("Notes", "https://example.com/notes")
        browser = _FakeBrowser(
            [
                _FakeContext([inbox, closed]),
                _FakeContext([sent, calendar]),
            ]
        )
        BrowserSession.mode = "attached"
        BrowserSession._browser = browser
        BrowserSession._context = browser.contexts[0]
        BrowserSession._page = other

        def run(fn):
            return fn(BrowserSession._page)

        try:
            with patch("browser.tools._attached_session_ready", return_value=True), patch.object(
                BrowserSession, "_on_browser_thread", run
            ):
                listed = browser_list_tabs()
                self.assertTrue(listed["ok"])
                self.assertEqual(
                    [(tab["index"], tab["title"]) for tab in listed["tabs"]],
                    [(0, "Gmail - Inbox"), (1, "Gmail - Sent"), (2, "Calendar")],
                )

                one = browser_attach_active_tab("calendar")
                self.assertEqual(one["ok"], True)
                self.assertEqual(one["attached"], {"title": "Calendar", "url": calendar.url})
                self.assertIs(BrowserSession._page, calendar)
                self.assertTrue(calendar.front)
                self.assertFalse(inbox.front)
                self.assertFalse(sent.front)

                BrowserSession._page = other
                calendar.front = False
                missing = browser_attach_active_tab("notion")
                self.assertEqual(missing["ok"], False)
                self.assertEqual(missing["error"], "no matching tab found")
                self.assertEqual(
                    [tab["title"] for tab in missing["available"]],
                    ["Gmail - Inbox", "Gmail - Sent", "Calendar"],
                )
                self.assertIs(BrowserSession._page, other)
                self.assertFalse(calendar.front)

                ambiguous = browser_attach_active_tab("gmail")
                self.assertEqual(ambiguous["ok"], False)
                self.assertEqual(ambiguous["error"], "ambiguous")
                self.assertEqual(
                    [tab["title"] for tab in ambiguous["candidates"]],
                    ["Gmail - Inbox", "Gmail - Sent"],
                )
                self.assertNotIn("available", ambiguous)
                self.assertIs(BrowserSession._page, other)
                self.assertFalse(inbox.front)
                self.assertFalse(sent.front)

                via_tool = json.loads(
                    run_tool(
                        "browser_attach_active_tab",
                        {"match_hint": "CaLeNdAr"},
                        TurnGuard(),
                    )
                )
                self.assertEqual(via_tool["attached"]["url"], calendar.url)
                self.assertIs(BrowserSession._page, calendar)
        finally:
            BrowserSession.mode = ""
            BrowserSession._browser = None
            BrowserSession._context = None
            BrowserSession._page = None

    def test_list_tabs_reports_when_chrome_is_not_listening(self) -> None:
        BrowserSession.mode = ""
        with patch("browser.tools.list_page_targets", side_effect=OSError("refused")) as listed:
            result = browser_list_tabs()
        self.assertFalse(result["ok"])
        self.assertIn("remote debugging", result["error"])
        listed.assert_called_once()

    def test_attach_without_hint_uses_the_focused_tab_only(self) -> None:
        inbox = _FakeTab("Gmail - Inbox", "https://mail.google.com/", focused=True)
        calendar = _FakeTab("Calendar", "https://calendar.google.com/")
        other = _FakeTab("Notes", "https://example.com/notes")
        browser = _FakeBrowser([_FakeContext([inbox, calendar])])
        BrowserSession.mode = "attached"
        BrowserSession._browser = browser
        BrowserSession._context = browser.contexts[0]
        BrowserSession._page = other

        def run(fn):
            return fn(BrowserSession._page)

        try:
            with patch("browser.tools._attached_session_ready", return_value=True), patch.object(
                BrowserSession, "_on_browser_thread", run
            ):
                focused = browser_attach_active_tab(None)
                self.assertEqual(focused["ok"], True)
                self.assertEqual(focused["attached"]["title"], "Gmail - Inbox")
                self.assertIs(BrowserSession._page, inbox)

                inbox.focused = True
                calendar.focused = True
                BrowserSession._page = other
                unclear = browser_attach_active_tab(None)
                self.assertEqual(unclear["ok"], False)
                self.assertIn("match_hint", unclear["error"])
                self.assertIs(BrowserSession._page, other)
                self.assertEqual(len(unclear["available"]), 2)
        finally:
            BrowserSession.mode = ""
            BrowserSession._browser = None
            BrowserSession._context = None
            BrowserSession._page = None


class AttachHintTests(unittest.TestCase):
    def test_list_ignores_capture_label(self) -> None:
        import server

        listed = {
            "ok": True,
            "tabs": [{"index": 1, "title": "Calendar", "url": "https://calendar.example/"}],
        }
        with patch("server.browser_list_tabs", return_value=listed) as list_tabs, patch(
            "server.browser_attach_active_tab"
        ) as attach:
            status, body = server.attach_hint_payload(
                {
                    "track_label": "Calendar - Week of October",
                    "display_surface": "browser",
                }
            )
        self.assertEqual(status, 200)
        self.assertEqual(body, listed)
        list_tabs.assert_called_once_with()
        attach.assert_not_called()
        note = server.attach_hint_payload.__doc__ or ""
        self.assertIn("not the real page title", note)
        self.assertIn("Do not parse", note)

    def test_confirm_attaches_by_title_not_capture_label(self) -> None:
        import server

        attached = {"ok": True, "attached": {"title": "Calendar", "url": "https://calendar.example/"}}
        with patch("server.browser_attach_active_tab", return_value=attached) as attach, patch(
            "server.browser_list_tabs"
        ) as list_tabs:
            status, body = server.attach_hint_payload(
                {
                    "match_hint": "Calendar",
                    "index": 1,
                    "title": "Calendar",
                    "track_label": "Gmail",
                }
            )
        self.assertEqual(status, 200)
        self.assertEqual(body, attached)
        attach.assert_called_once_with("Calendar")
        list_tabs.assert_not_called()

    def test_blank_hint_lists_tabs(self) -> None:
        import server

        listed = {"ok": True, "tabs": []}
        with patch("server.browser_list_tabs", return_value=listed) as list_tabs, patch(
            "server.browser_attach_active_tab"
        ) as attach:
            status, body = server.attach_hint_payload({"match_hint": "   "})
        self.assertEqual(status, 200)
        self.assertEqual(body, listed)
        list_tabs.assert_called_once_with()
        attach.assert_not_called()

    def test_attached_mode_asks_before_consequential_actions(self) -> None:
        import server
        from voice.session import LIVE_VOICE_RULES

        line = "mode == attached"
        for rules in (server.TOOL_RULES, server.LOCAL_FILE_RULES, LIVE_VOICE_RULES):
            self.assertIn(line, rules)
            self.assertIn("already-logged-in accounts", rules)

    def test_viewer_keeps_screen_share_separate_from_attach(self) -> None:
        html = (Path(__file__).resolve().parents[1] / "viewer" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="screen"', html)
        self.assertIn("getDisplayMedia", html)
        self.assertIn("Let Jarvis take over this tab", html)
        self.assertIn("/browser/attach_hint", html)
        self.assertIn("Attached to tab:", html)
        self.assertIn("Do NOT rely", html)
        self.assertIn("not a control channel", html)
        self.assertNotIn("shareStream", html[html.find("postAttachHint"): html.find("postAttachHint") + 400])


class _FakeTab:
    def __init__(self, title: str, url: str, *, closed: bool = False, focused: bool = False) -> None:
        self._title = title
        self._url = url
        self.closed = closed
        self.focused = focused
        self.front = False

    def title(self) -> str:
        return self._title

    @property
    def url(self) -> str:
        return self._url

    def is_closed(self) -> bool:
        return self.closed

    def bring_to_front(self) -> None:
        self.front = True

    def evaluate(self, _script: str) -> bool:
        return self.focused


class _FakeContext:
    def __init__(self, pages: list[_FakeTab]) -> None:
        self.pages = pages


class _FakeBrowser:
    def __init__(self, contexts: list[_FakeContext]) -> None:
        self.contexts = contexts
