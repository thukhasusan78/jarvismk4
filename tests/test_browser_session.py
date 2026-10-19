"""Persistent Chromium session. The smoke test launches local Chromium."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from build import ROOT
from browser.session import (
    BrowserSession,
    _browser_settings,
    _websocket_from_active_port,
    configured_cdp_port,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "test_page.html"
FIXTURE_TITLE = "Jarvis browser fixture"


class BrowserSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        BrowserSession.stop()

    def tearDown(self) -> None:
        BrowserSession.stop()

    def test_stop_without_start(self) -> None:
        BrowserSession.stop()
        self.assertIsNone(BrowserSession._thread)

    def test_browser_settings_defaults_and_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            missing = Path(tmp) / "missing.json"
            with patch("browser.session.CONFIG_PATH", missing):
                headless, profile = _browser_settings()
            self.assertFalse(headless)
            self.assertEqual(profile, (ROOT / "browser_profile").resolve())

            path.write_text("{", encoding="utf-8")
            with patch("browser.session.CONFIG_PATH", path):
                headless, profile = _browser_settings()
            self.assertFalse(headless)
            self.assertEqual(profile, (ROOT / "browser_profile").resolve())

            path.write_text("{}", encoding="utf-8")
            with patch("browser.session.CONFIG_PATH", path):
                headless, profile = _browser_settings()
            self.assertFalse(headless)
            self.assertEqual(profile, (ROOT / "browser_profile").resolve())

            path.write_text(
                json.dumps(
                    {
                        "browser_headless": False,
                        "browser_profile_dir": "custom_profile",
                    }
                ),
                encoding="utf-8",
            )
            with patch("browser.session.CONFIG_PATH", path):
                headless, profile = _browser_settings()
            self.assertFalse(headless)
            self.assertEqual(profile, (ROOT / "custom_profile").resolve())

            path.write_text(
                json.dumps({"browser_headless": "yes", "browser_profile_dir": "  "}),
                encoding="utf-8",
            )
            with patch("browser.session.CONFIG_PATH", path):
                headless, profile = _browser_settings()
            self.assertFalse(headless)
            self.assertEqual(profile, (ROOT / "browser_profile").resolve())

            absolute = Path(tmp) / "session"
            with patch("browser.session.CONFIG_PATH", path):
                headless, profile = _browser_settings(headless=True, profile_dir=absolute)
            self.assertTrue(headless)
            self.assertEqual(profile, absolute.resolve())

    def test_page_property_starts_when_idle(self) -> None:
        calls: list[bool] = []

        def fake_start(*_args: object, **_kwargs: object) -> None:
            calls.append(True)

        with patch.object(BrowserSession, "start", fake_start):
            page = BrowserSession.page
        self.assertEqual(calls, [True])
        self.assertEqual(type(page).__name__, "_BoundPage")

    def test_smoke_reads_local_fixture_title(self) -> None:
        self.assertTrue(FIXTURE.is_file())
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        BrowserSession.start(headless=True, profile_dir=tmp.name)
        BrowserSession.page.goto(FIXTURE.resolve().as_uri())
        self.assertEqual(BrowserSession.page.title(), FIXTURE_TITLE)
        self.assertEqual(BrowserSession.mode, "dedicated")
        running = BrowserSession._thread
        BrowserSession.start()
        self.assertIs(BrowserSession._thread, running)
        self.assertEqual(BrowserSession.page.title(), FIXTURE_TITLE)
        BrowserSession.stop()
        self.assertIsNone(BrowserSession._thread)
        self.assertEqual(BrowserSession.mode, "")

    def test_cdp_port_defaults_and_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            missing = Path(tmp) / "missing.json"
            with patch("browser.session.CONFIG_PATH", missing):
                self.assertEqual(configured_cdp_port(), 9222)
            path.write_text("{}", encoding="utf-8")
            with patch("browser.session.CONFIG_PATH", path):
                self.assertEqual(configured_cdp_port(), 9222)
            path.write_text(json.dumps({"cdp_port": 9333}), encoding="utf-8")
            with patch("browser.session.CONFIG_PATH", path):
                self.assertEqual(configured_cdp_port(), 9333)
            path.write_text(json.dumps({"cdp_port": "nope"}), encoding="utf-8")
            with patch("browser.session.CONFIG_PATH", path):
                self.assertEqual(configured_cdp_port(), 9222)

    def test_attach_over_cdp_failure_returns_false_with_message(self) -> None:
        recorder = _CdpRecorder(error=RuntimeError("connect ECONNREFUSED 127.0.0.1:9222"))
        with _patch_playwright(recorder), patch("browser.session._devtools_websocket", return_value=None):
            ok, message = BrowserSession.attach_over_cdp(9222)
        self.assertFalse(ok)
        self.assertIn("chrome://inspect/#remote-debugging", message)
        self.assertIn("Remote debugging", message)
        self.assertEqual(len(recorder.connect_urls), 1)
        self.assertTrue(recorder.connect_urls[0].startswith("ws://127.0.0.1:"))
        self.assertNotEqual(recorder.connect_urls[0], "http://127.0.0.1:9222")
        self.assertFalse(recorder.launched)
        self.assertNotEqual(BrowserSession.mode, "attached")
        self.assertIsNone(BrowserSession._thread)
        self.assertIsNone(BrowserSession._context)

    def test_devtools_active_port_websocket_is_preferred(self) -> None:
        recorder = _CdpRecorder()
        ws = "ws://127.0.0.1:9333/devtools/browser/abc"
        with _patch_playwright(recorder), patch("browser.session._devtools_websocket", return_value=ws):
            ok, message = BrowserSession.attach_over_cdp(9222)
        self.assertEqual((ok, message), (True, ""))
        self.assertEqual(len(recorder.connect_urls), 1)
        self.assertTrue(recorder.connect_urls[0].startswith("ws://127.0.0.1:"))
        self.assertNotEqual(recorder.connect_urls[0], ws)
        upstream = BrowserSession._cdp_proxy.upstream if BrowserSession._cdp_proxy is not None else ""
        self.assertEqual(upstream, ws)
        BrowserSession.stop()

    def test_devtools_active_port_file_parses_loopback_websocket(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "DevToolsActivePort"
            path.write_text("9222\n/devtools/browser/token\n", encoding="utf-8")
            self.assertEqual(
                _websocket_from_active_port(path),
                "ws://127.0.0.1:9222/devtools/browser/token",
            )
            path.write_text("not-a-port\n/devtools/browser/token\n", encoding="utf-8")
            self.assertIsNone(_websocket_from_active_port(path))
            missing = Path(tmp) / "missing"
            self.assertIsNone(_websocket_from_active_port(missing))

    def test_attach_over_cdp_success_sets_attached_mode(self) -> None:
        recorder = _CdpRecorder()
        with _patch_playwright(recorder), patch("browser.session._devtools_websocket", return_value=None):
            ok, message = BrowserSession.attach_over_cdp(9222)
            self.assertEqual((ok, message), (True, ""))
            self.assertEqual(BrowserSession.mode, "attached")
            self.assertEqual(len(recorder.connect_urls), 1)
            self.assertTrue(recorder.connect_urls[0].startswith("ws://127.0.0.1:"))
            upstream = BrowserSession._cdp_proxy.upstream if BrowserSession._cdp_proxy is not None else ""
            self.assertEqual(upstream, "http://127.0.0.1:9222")
            self.assertFalse(recorder.launched)
            self.assertIs(BrowserSession._browser, recorder.browser)
            self.assertIs(BrowserSession._context, recorder.context)
            running = BrowserSession._thread
            BrowserSession.start()
            self.assertIs(BrowserSession._thread, running)
            self.assertEqual(BrowserSession.mode, "attached")
            self.assertFalse(recorder.launched)
            BrowserSession.stop()
        self.assertEqual(BrowserSession.mode, "")
        self.assertFalse(recorder.context_closed)
        self.assertFalse(recorder.browser_closed)
        self.assertTrue(recorder.stopped)

    def test_attach_over_cdp_refuses_while_dedicated_is_running(self) -> None:
        BrowserSession.mode = "dedicated"
        with patch.object(BrowserSession, "_is_warm_locked", lambda: True):
            ok, message = BrowserSession.attach_over_cdp(9222)
        self.assertFalse(ok)
        self.assertIn("dedicated", message)
        self.assertEqual(BrowserSession.mode, "dedicated")

    def test_closed_browser_relaunches_and_retries(self) -> None:
        recorder = _DedicatedRecorder()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with _patch_dedicated(recorder):
            BrowserSession.start(headless=True, profile_dir=tmp.name)
            self.assertEqual(recorder.launches, 1)
            context = BrowserSession._context
            self.assertIsNotNone(context)
            for page in context.pages:
                page._closed = True
            recorder.fail_new_page = True
            marker = BrowserSession._on_browser_thread(lambda page: page.marker)
        self.assertEqual(marker, 2)
        self.assertEqual(recorder.launches, 2)
        self.assertEqual(BrowserSession.mode, "dedicated")
        self.assertFalse(BrowserSession._context_closed)

    def test_start_replaces_a_closed_context(self) -> None:
        recorder = _DedicatedRecorder()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with _patch_dedicated(recorder):
            BrowserSession.start(headless=True, profile_dir=tmp.name)
            first = BrowserSession._thread
            context = BrowserSession._context
            self.assertIsNotNone(context)
            for callback in list(context.handlers.get("close", [])):
                callback()
            self.assertTrue(BrowserSession._context_closed)
            BrowserSession.start(headless=True, profile_dir=tmp.name)
            self.assertIsNot(BrowserSession._thread, first)
            self.assertFalse(BrowserSession._context_closed)
            self.assertEqual(recorder.launches, 2)
            marker = BrowserSession._on_browser_thread(lambda page: page.marker)
        self.assertEqual(marker, 2)

    def test_launch_debug_chrome_targets_real_profile(self) -> None:
        from launch_debug_chrome import chrome_command, chrome_user_data_dir

        user_data = chrome_user_data_dir()
        agent = (ROOT / "browser_profile").resolve()
        self.assertNotEqual(user_data.resolve(), agent)
        self.assertNotIn("browser_profile", user_data.as_posix())
        command = chrome_command(Path("chrome.exe"), user_data, 9222)
        joined = " ".join(command)
        self.assertIn("--remote-debugging-port=9222", command)
        self.assertIn(f"--user-data-dir={user_data}", command)
        self.assertNotIn("browser_profile", joined)


class _CdpRecorder:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.connect_urls: list[str] = []
        self.launched = False
        self.context_closed = False
        self.browser_closed = False
        self.stopped = False
        self.context = _FakeContext(self)
        self.browser = _FakeBrowser(self, self.context)


class _FakePage:
    def is_closed(self) -> bool:
        return False


class _FakeContext:
    def __init__(self, recorder: _CdpRecorder) -> None:
        self.recorder = recorder
        self.pages = [_FakePage()]

    def on(self, _event: str, _callback: object) -> None:
        return None

    def new_page(self) -> _FakePage:
        page = _FakePage()
        self.pages.append(page)
        return page

    def close(self) -> None:
        self.recorder.context_closed = True


class _FakeBrowser:
    def __init__(self, recorder: _CdpRecorder, context: _FakeContext) -> None:
        self.recorder = recorder
        self.contexts = [context]

    def new_context(self) -> _FakeContext:
        return self.contexts[0]

    def close(self) -> None:
        self.recorder.browser_closed = True


class _FakeChromium:
    def __init__(self, recorder: _CdpRecorder) -> None:
        self.recorder = recorder

    def connect_over_cdp(self, url: str) -> _FakeBrowser:
        self.recorder.connect_urls.append(url)
        if self.recorder.error is not None:
            raise self.recorder.error
        return self.recorder.browser

    def launch_persistent_context(self, *_args: object, **_kwargs: object) -> object:
        self.recorder.launched = True
        raise AssertionError("attached mode launched a browser")


class _FakePlaywright:
    def __init__(self, recorder: _CdpRecorder) -> None:
        self.recorder = recorder
        self.chromium = _FakeChromium(recorder)

    def stop(self) -> None:
        self.recorder.stopped = True


class _FakeManager:
    def __init__(self, playwright: _FakePlaywright) -> None:
        self.playwright = playwright

    def start(self) -> _FakePlaywright:
        return self.playwright


class _ClosedTargetError(Exception):
    """Stand-in whose class name matches Playwright's TargetClosedError."""


_ClosedTargetError.__name__ = "TargetClosedError"


class _DedicatedRecorder:
    def __init__(self) -> None:
        self.launches = 0
        self.fail_new_page = False
        self.stopped = False


class _DedicatedPage:
    def __init__(self, marker: int) -> None:
        self.marker = marker
        self._closed = False

    def is_closed(self) -> bool:
        return self._closed


class _DedicatedContext:
    def __init__(self, recorder: _DedicatedRecorder, marker: int) -> None:
        self.recorder = recorder
        self.pages = [_DedicatedPage(marker)]
        self.handlers: dict[str, list] = {}

    def on(self, event: str, callback: object) -> None:
        self.handlers.setdefault(event, []).append(callback)

    def new_page(self) -> _DedicatedPage:
        if self.recorder.fail_new_page:
            self.recorder.fail_new_page = False
            raise _ClosedTargetError(
                "BrowserContext.new_page: Target page, context or browser has been closed"
            )
        page = _DedicatedPage(self.pages[0].marker)
        self.pages.append(page)
        return page

    def close(self) -> None:
        return None


class _DedicatedChromium:
    def __init__(self, recorder: _DedicatedRecorder) -> None:
        self.recorder = recorder

    def launch_persistent_context(self, *_args: object, **_kwargs: object) -> _DedicatedContext:
        self.recorder.launches += 1
        return _DedicatedContext(self.recorder, self.recorder.launches)

    def connect_over_cdp(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("dedicated relaunch attached over cdp")


class _DedicatedPlaywright:
    def __init__(self, recorder: _DedicatedRecorder) -> None:
        self.recorder = recorder
        self.chromium = _DedicatedChromium(recorder)

    def stop(self) -> None:
        self.recorder.stopped = True


class _DedicatedManager:
    def __init__(self, recorder: _DedicatedRecorder) -> None:
        self.recorder = recorder

    def start(self) -> _DedicatedPlaywright:
        return _DedicatedPlaywright(self.recorder)


def _patch_dedicated(recorder: _DedicatedRecorder):
    manager = _DedicatedManager(recorder)
    return patch("playwright.sync_api.sync_playwright", return_value=manager)


def _patch_playwright(recorder: _CdpRecorder):
    manager = _FakeManager(_FakePlaywright(recorder))
    return patch("playwright.sync_api.sync_playwright", return_value=manager)
