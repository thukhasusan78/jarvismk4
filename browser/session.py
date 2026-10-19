"""One shared browser for the whole process.

Dedicated mode (``start()``) launches Playwright's Chromium against the
isolated agent profile. Logins there survive restarts. ``stop()`` closes
that Chromium. ``server.py`` calls ``stop()`` on Ctrl+C so the profile is
not left locked.

Attached mode (``attach_over_cdp()``) is a different browser: the user's
real Chrome, including logged-in sessions on the sites they already use.
It is never selected automatically. ``stop()`` disconnects and leaves that
Chrome running.

Playwright's sync API is bound to the thread that created it. Tool calls and
the Ctrl+C handler run on other threads, so this module owns the Playwright
thread and ``BrowserSession.page`` runs each call there. Tools use that page
whichever mode produced it.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import sys
import threading
from pathlib import Path
from typing import Any, Callable

from build import ROOT

log = logging.getLogger("browser.session")

CONFIG_PATH = ROOT / "config.json"
# This process runs on the user's own machine. A visible window is the default
# so the session looks like an ordinary browser and is already on screen if
# the user needs to take over. config.json "browser_headless": true hides it.
DEFAULT_HEADLESS = False
DEFAULT_PROFILE_DIR = "./browser_profile"
DEFAULT_CDP_PORT = 9222
LAUNCH_TIMEOUT_S = 180
# Attached connect goes through the CDP proxy. If Chrome never answers,
# return in half a minute instead of blocking the tab picker for the
# dedicated-launch timeout.
ATTACH_READY_S = 30
CLOSE_TIMEOUT_S = 45
CALL_TIMEOUT_S = 120

_PLAIN = (str, bytes, int, float, bool, type(None))


def _as_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _read_browser_config() -> tuple[bool, str]:
    headless = DEFAULT_HEADLESS
    profile = DEFAULT_PROFILE_DIR
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return headless, profile
    if not isinstance(data, dict):
        return headless, profile
    headless = _as_bool(data.get("browser_headless", DEFAULT_HEADLESS), DEFAULT_HEADLESS)
    raw = data.get("browser_profile_dir", DEFAULT_PROFILE_DIR)
    if isinstance(raw, str) and raw.strip():
        profile = raw.strip()
    return headless, profile


def _resolve_profile(raw: str | Path) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def _coerce_cdp_port(value: object) -> int:
    """Accept an int port. Reject bools (they are ints) and out-of-range values."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Chrome debugging port must be an integer.")
    if value < 1 or value > 65535:
        raise ValueError(f"Chrome debugging port {value} is out of range.")
    return value


def configured_cdp_port() -> int:
    """``config.json`` ``cdp_port``. Missing or invalid values use 9222."""
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return DEFAULT_CDP_PORT
    if not isinstance(data, dict):
        return DEFAULT_CDP_PORT
    raw = data.get("cdp_port", DEFAULT_CDP_PORT)
    if isinstance(raw, str):
        text = raw.strip()
        if not text.isdigit():
            return DEFAULT_CDP_PORT
        raw = int(text)
    try:
        return _coerce_cdp_port(raw)
    except ValueError:
        return DEFAULT_CDP_PORT


def _default_chrome_user_data_dir() -> Path | None:
    """The real Chrome user-data directory. Never the agent profile."""
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        if not local:
            return None
        return Path(local) / "Google" / "Chrome" / "User Data"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    return Path.home() / ".config" / "google-chrome"


def _websocket_from_active_port(path: Path) -> str | None:
    """WebSocket URL from Chrome's DevToolsActivePort file.

    Chrome 136 and later ignore --remote-debugging-port on the default
    profile. Chrome 144 and later write this file after the user turns on
    remote debugging at chrome://inspect/#remote-debugging. The first line
    is the port and the second is the browser websocket path. The HTTP
    /json/version endpoint stays closed, so the websocket is the connection.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2 or not lines[0].isdigit():
        return None
    port = int(lines[0])
    if port < 1 or port > 65535 or not lines[1].startswith("/"):
        return None
    return f"ws://127.0.0.1:{port}{lines[1]}"


def _devtools_websocket() -> str | None:
    """Endpoint for the Chrome the user already has open, when debugging is on."""
    directory = _default_chrome_user_data_dir()
    if directory is None:
        return None
    return _websocket_from_active_port(directory / "DevToolsActivePort")


def _attach_failure_message(endpoint: str, exc: BaseException) -> str:
    detail = " ".join(str(exc).split()).strip()
    if detail:
        log.warning("attach_over_cdp detail for %s: %s", endpoint, detail)
    folded = detail.casefold()
    timed_out = isinstance(exc, TimeoutError) or "timed out" in folded
    rejected = isinstance(exc, PermissionError) or "connection rejected" in folded
    if timed_out or rejected:
        return (
            "Chrome did not finish the connection. "
            "If an Allow remote debugging prompt is open, click Allow, then try again."
        )
    return (
        "Chrome is open, but remote debugging is off, so Jarvis cannot see its tabs. "
        "In that Chrome, open chrome://inspect/#remote-debugging and turn Remote debugging on, "
        "then try again. Chrome will ask you to allow the connection."
    )


def _browser_settings(
    *,
    headless: bool | None = None,
    profile_dir: str | Path | None = None,
) -> tuple[bool, Path]:
    """Headless flag and absolute profile path. Explicit arguments win."""
    file_headless = DEFAULT_HEADLESS
    file_profile: str | Path = DEFAULT_PROFILE_DIR
    if headless is None or profile_dir is None:
        file_headless, file_profile = _read_browser_config()
    resolved_headless = file_headless if headless is None else _as_bool(headless, DEFAULT_HEADLESS)
    raw = file_profile if profile_dir is None else profile_dir
    return resolved_headless, _resolve_profile(raw)


def _is_target_closed(exc: BaseException) -> bool:
    """True when Playwright says the page, context, or browser is already gone."""
    if type(exc).__name__ == "TargetClosedError":
        return True
    text = " ".join(str(exc).casefold().split())
    return "target page, context or browser has been closed" in text


def _handoff(value: Any) -> Any:
    """Return plain data. Playwright objects must die on the browser thread."""
    if isinstance(value, _PLAIN):
        return value
    if isinstance(value, tuple):
        return tuple(_handoff(item) for item in value)
    if isinstance(value, list):
        return [_handoff(item) for item in value]
    if isinstance(value, dict):
        return {key: _handoff(item) for key, item in value.items()}
    module = getattr(type(value), "__module__", "") or ""
    if module.startswith("playwright"):
        return None
    return value


class _BoundPage:
    """Active page. Attribute access runs on the browser thread."""

    def __init__(self, session: type[BrowserSession]) -> None:
        self._session = session

    def __getattr__(self, name: str) -> Any:
        session = self._session

        def kind(page: Any) -> str | None:
            if not hasattr(page, name):
                return None
            return "call" if callable(getattr(page, name)) else "value"

        found = session._on_browser_thread(kind)
        if found is None:
            raise AttributeError(name)
        if found == "value":
            return session._on_browser_thread(lambda page: _handoff(getattr(page, name)))

        def call(*args: Any, **kwargs: Any) -> Any:
            def invoke(page: Any) -> Any:
                return _handoff(getattr(page, name)(*args, **kwargs))

            return session._on_browser_thread(invoke)

        return call


class _PageProperty:
    """Active Playwright page. Starts the session on first access."""

    def __get__(self, obj: Any, owner: type | None = None) -> _BoundPage:
        cls = owner if isinstance(owner, type) else type(obj)
        cls.start()
        return _BoundPage(cls)


class BrowserSession:
    """The process-wide browser. One context, shared by every caller.

    ``mode`` is ``"dedicated"`` or ``"attached"`` while a session is up.
    Dedicated mode is the isolated agent profile. Attached mode is the
    user's real Chrome. Tools only see ``page``.
    """

    _start_lock = threading.Lock()
    _state_lock = threading.Lock()
    _thread: threading.Thread | None = None
    _jobs: queue.Queue | None = None
    _stop_requested: threading.Event | None = None
    _start_error: BaseException | None = None
    _context: Any = None
    _browser: Any = None
    _page: Any = None
    _cdp_proxy: Any = None
    # Set when the open window is gone but the Playwright thread is still
    # parked in the job pump. start() must relaunch instead of reusing it.
    _context_closed: bool = False
    # Bumped each time a session becomes ready. A closed-target retry uses it
    # so only one caller relaunches.
    _generation: int = 0
    _launch_mode: str = ""
    _launch_headless: bool = DEFAULT_HEADLESS
    _launch_profile: Path | None = None
    _launch_cdp_url: str = ""
    # "dedicated" after start() launches the isolated agent profile.
    # "attached" after attach_over_cdp() connects to the user's real Chrome
    # and its logged-in sessions. Empty when nothing is running.
    # start() never sets "attached". Attached mode is only entered when a
    # caller asks for attach_over_cdp().
    mode: str = ""
    # Set when automation pauses for a person. The reason is a short label
    # such as "captcha". The timestamp is UTC ISO-8601. Passwords are never stored.
    awaiting_human: bool = False
    awaiting_human_reason: str = ""
    awaiting_human_at: str = ""
    # Page identities where browser_type filled a password field in this
    # browser session. The password itself is never stored.
    password_supplied_flows: set[str] = set()

    page = _PageProperty()

    def __init__(self) -> None:
        """Instances share the single process session."""

    @classmethod
    def start(
        cls,
        *,
        headless: bool | None = None,
        profile_dir: str | Path | None = None,
    ) -> None:
        """Launch the isolated agent Chromium if it is not already running.

        ``headless`` and ``profile_dir`` override config.json for this launch.
        A second call while a session is up does not relaunch and does not
        switch an attached Chrome back to the agent profile.
        This never connects to the user's real Chrome.
        """
        with cls._start_lock:
            headless_flag, profile = _browser_settings(headless=headless, profile_dir=profile_dir)
            cls._spawn_locked(mode="dedicated", headless=headless_flag, profile=profile, cdp_url="")

    @classmethod
    def attach_over_cdp(cls, port: int = 9222) -> tuple[bool, str]:
        """Attach to Chrome already listening at ``http://localhost:{port}``.

        Attached mode drives the user's real Chrome and the logged-in
        sessions on every site they use. It is not the isolated agent
        profile that ``start()`` launches. Nothing calls this on its own.

        Returns ``(True, "")`` when the shared session is attached.
        When nothing is listening, or the connection fails, returns
        ``(False, reason)`` and does not raise.

        Chrome 144 and later expose the already-open window through
        DevToolsActivePort after remote debugging is turned on at
        chrome://inspect/#remote-debugging. That websocket is preferred.
        ``http://127.0.0.1:{port}`` remains the fallback for a Chrome that
        was started with a debugging port on a non-default profile.
        """
        try:
            chosen = _coerce_cdp_port(port)
        except ValueError as exc:
            return False, str(exc)
        url = _devtools_websocket() or f"http://127.0.0.1:{chosen}"
        with cls._start_lock:
            with cls._state_lock:
                if cls._is_warm_locked():
                    if cls.mode == "attached":
                        return True, ""
                    return (
                        False,
                        "A dedicated browser session is already running. "
                        "Stop it before attaching to the user's Chrome.",
                    )
            try:
                cls._spawn_locked(mode="attached", headless=False, profile=None, cdp_url=url)
            except Exception as exc:
                message = _attach_failure_message(url, exc)
                log.warning("attach_over_cdp failed: %s", message)
                return False, message
        return True, ""

    @classmethod
    def stop(cls) -> None:
        """Close a dedicated Chromium, or disconnect an attached Chrome.

        Safe when the session was never started. Attached mode does not
        close the user's Chrome.
        """
        cls._reset_human_state()
        with cls._state_lock:
            proxy = cls._cdp_proxy
            thread = cls._thread
            jobs = cls._jobs
            if thread is None or jobs is None:
                cls.mode = ""
                cls._browser = None
                cls._context = None
                cls._page = None
                cls._context_closed = False
                return
            if cls._stop_requested is not None:
                cls._stop_requested.set()
            jobs.put(None)
        # Closing the proxy unblocks connect_over_cdp, which runs before the
        # thread reads the job queue. stop() otherwise waits out the launch timeout.
        if proxy is not None:
            proxy.close()
        thread.join(timeout=CLOSE_TIMEOUT_S)
        with cls._state_lock:
            if thread.is_alive():
                log.warning("browser session did not close within %ss", CLOSE_TIMEOUT_S)
                return
            if cls._thread is thread:
                cls._clear_locked()

    @classmethod
    def activate_page(cls, page: Any) -> None:
        """Make ``page`` the page later tool calls use. Call on the browser thread."""
        cls._page = page

    @classmethod
    def _is_warm_locked(cls) -> bool:
        return (
            cls._thread is not None
            and cls._thread.is_alive()
            and cls._context is not None
            and not cls._context_closed
            and cls._stop_requested is not None
            and not cls._stop_requested.is_set()
        )

    @classmethod
    def _reset_human_state(cls) -> None:
        cls.awaiting_human = False
        cls.awaiting_human_reason = ""
        cls.awaiting_human_at = ""
        cls.password_supplied_flows = set()

    @classmethod
    def _clear_locked(cls) -> None:
        cls._thread = None
        cls._jobs = None
        cls._stop_requested = None
        cls._start_error = None
        cls._context = None
        cls._browser = None
        cls._page = None
        cls._cdp_proxy = None
        cls._context_closed = False
        cls.mode = ""
        cls._reset_human_state()

    @classmethod
    def _spawn_locked(
        cls,
        *,
        mode: str,
        headless: bool,
        profile: Path | None,
        cdp_url: str,
    ) -> None:
        with cls._state_lock:
            if cls._is_warm_locked():
                return
            old = cls._thread
        # A closed window leaves the pump blocked on the job queue. Joining
        # without signalling it waits out the close timeout and never relaunches.
        if old is not None and old.is_alive():
            cls.stop()
            if old.is_alive():
                raise RuntimeError("Browser session is still closing.")
        jobs: queue.Queue = queue.Queue()
        ready = threading.Event()
        stop_requested = threading.Event()
        with cls._state_lock:
            if cls._is_warm_locked():
                return
            if cls._thread is not None and cls._thread.is_alive():
                raise RuntimeError("Browser session is still closing.")
            cls._jobs = jobs
            cls._stop_requested = stop_requested
            cls._start_error = None
            cls._context = None
            cls._browser = None
            cls._page = None
            cls._context_closed = False
            cls._launch_mode = mode
            cls._launch_headless = headless
            cls._launch_profile = profile
            cls._launch_cdp_url = cdp_url
            cls.mode = ""
            thread = threading.Thread(
                target=cls._thread_main,
                args=(mode, headless, profile, cdp_url, jobs, ready, stop_requested),
                name="browser-session",
                daemon=False,
            )
            cls._thread = thread
            thread.start()
        wait_s = ATTACH_READY_S if mode == "attached" else LAUNCH_TIMEOUT_S
        if not ready.wait(timeout=wait_s):
            cls.stop()
            if mode == "attached":
                raise TimeoutError(f"Timed out attaching to Chrome at {cdp_url}.")
            raise TimeoutError("Timed out launching Chromium.")
        with cls._state_lock:
            error = cls._start_error
            alive = cls._thread is thread and thread.is_alive()
            stopped = stop_requested.is_set()
        if error is not None or not alive or stopped:
            cls.stop()
            if error is not None:
                raise error
            if stopped:
                raise RuntimeError("Browser session stopped during launch.")
            raise RuntimeError("Browser session exited during launch.")
        with cls._state_lock:
            cls._generation += 1

    @classmethod
    def _thread_main(
        cls,
        mode: str,
        headless: bool,
        profile: Path | None,
        cdp_url: str,
        jobs: queue.Queue,
        ready: threading.Event,
        stop_requested: threading.Event,
    ) -> None:
        playwright = None
        context = None
        browser = None
        proxy = None
        try:
            from playwright.sync_api import sync_playwright

            from browser.cdp_proxy import CdpAttachProxy

            playwright = sync_playwright().start()
            if mode == "attached":
                # Attached mode is not dedicated mode. connect_over_cdp drives
                # the user's real Chrome and the logged-in sessions on every
                # site they use, not the isolated agent profile. start() never
                # takes this path, and nothing selects it automatically.
                # The proxy is the socket Playwright connects to. It keeps
                # chrome://, extension, and stuck targets from blocking connect.
                proxy = CdpAttachProxy(cdp_url)
                with cls._state_lock:
                    cls._cdp_proxy = proxy
                browser = playwright.chromium.connect_over_cdp(proxy.endpoint)
                proxy.mark_connected()
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                cls._browser = browser
            else:
                if profile is None:
                    raise RuntimeError("Dedicated browser session has no profile directory.")
                profile.mkdir(parents=True, exist_ok=True)
                context = playwright.chromium.launch_persistent_context(
                    str(profile),
                    headless=headless,
                    handle_sigint=False,
                    handle_sigterm=False,
                    handle_sighup=False,
                )

            def note(new_page: Any) -> None:
                cls._page = new_page

            def note_closed(*_args: object) -> None:
                # A late close from the browser just replaced must not mark the new one dead.
                if cls._context is context:
                    cls._context_closed = True
                    log.info("browser context closed")

            context.on("page", note)
            cls._context = context
            context.on("close", note_closed)
            owner = browser
            if owner is None:
                try:
                    owner = getattr(context, "browser", None)
                except Exception:
                    owner = None
            listen = getattr(owner, "on", None)
            if callable(listen):

                def note_disconnected(*_args: object) -> None:
                    if cls._browser is owner or cls._context is context:
                        cls._context_closed = True
                        log.info("browser disconnected")

                try:
                    listen("disconnected", note_disconnected)
                except Exception:
                    log.exception("browser disconnect listener failed")
            cls._page = context.pages[0] if context.pages else context.new_page()
            cls.mode = mode
            log.info("browser session ready (%s)", mode)
            ready.set()
            if not stop_requested.is_set():
                cls._pump(jobs)
        except Exception as exc:
            if not ready.is_set():
                cls._start_error = exc
                ready.set()
            else:
                log.exception("browser session failed")
        finally:
            cls._context = None
            cls._page = None
            cls._browser = None
            if cls.mode == mode:
                cls.mode = ""
            # Dedicated mode owns the Chromium it launched, so close it.
            # Attached mode is the user's real Chrome. context.close() and
            # browser.close() would close their tabs. playwright.stop() only
            # drops this process's DevTools connection and leaves Chrome up.
            if mode != "attached" and context is not None:
                try:
                    context.close()
                except Exception:
                    log.exception("browser context close failed")
            if proxy is not None:
                proxy.close()
                with cls._state_lock:
                    if cls._cdp_proxy is proxy:
                        cls._cdp_proxy = None
            if playwright is not None:
                try:
                    playwright.stop()
                except Exception:
                    log.exception("playwright stop failed")

    @classmethod
    def _pump(cls, jobs: queue.Queue) -> None:
        while True:
            job = jobs.get()
            if job is None:
                return
            fn, result = job
            try:
                result.put((True, fn()))
            except Exception as exc:
                result.put((False, exc))

    @classmethod
    def _live_page(cls) -> Any:
        context = cls._context
        if context is None or cls._context_closed:
            raise RuntimeError("Browser session is not running.")
        page = cls._page
        try:
            if page is not None and not page.is_closed():
                return page
            open_pages = [candidate for candidate in context.pages if not candidate.is_closed()]
            page = open_pages[-1] if open_pages else context.new_page()
        except Exception as exc:
            if _is_target_closed(exc):
                cls._context_closed = True
                log.warning("browser context is closed")
            raise
        cls._page = page
        return page

    @classmethod
    def _should_relaunch(cls, exc: BaseException) -> bool:
        if _is_target_closed(exc):
            return True
        return bool(cls._context_closed) and isinstance(exc, RuntimeError)

    @classmethod
    def _revive(cls, generation: int) -> None:
        """Open the same kind of session again after its window or context died.

        ``generation`` is the session that failed. A caller that lost the race
        leaves the replacement another thread already started.
        """
        with cls._start_lock:
            with cls._state_lock:
                if cls._generation != generation:
                    return
                mode = cls._launch_mode
                headless = cls._launch_headless
                profile = cls._launch_profile
                cdp_url = cls._launch_cdp_url
            cls._context_closed = True
            cls.stop()
            if mode == "attached" and cdp_url:
                cls._spawn_locked(mode="attached", headless=False, profile=None, cdp_url=cdp_url)
                return
            if mode != "dedicated" or profile is None:
                headless_flag, resolved = _browser_settings()
                cls._spawn_locked(
                    mode="dedicated",
                    headless=headless_flag,
                    profile=resolved,
                    cdp_url="",
                )
                return
            cls._spawn_locked(mode="dedicated", headless=headless, profile=profile, cdp_url="")

    @classmethod
    def _on_browser_thread(cls, fn: Callable[[Any], Any], *, _recover: bool = True) -> Any:
        with cls._state_lock:
            jobs = cls._jobs
            thread = cls._thread
            generation = cls._generation
            stopping = cls._stop_requested is not None and cls._stop_requested.is_set()
            closed = cls._context_closed
        # Reviving joins this thread. Doing that from the Playwright thread deadlocks.
        on_browser_thread = thread is not None and threading.current_thread() is thread
        if closed and _recover and not on_browser_thread:
            log.warning("browser target closed; relaunching session")
            cls._revive(generation)
            return cls._on_browser_thread(fn, _recover=False)
        if jobs is None or thread is None or not thread.is_alive() or stopping:
            raise RuntimeError("Browser session is not running.")
        if on_browser_thread:
            return fn(cls._live_page())
        result: queue.Queue = queue.Queue(maxsize=1)
        jobs.put((lambda: fn(cls._live_page()), result))
        try:
            ok, value = result.get(timeout=CALL_TIMEOUT_S)
        except queue.Empty as exc:
            raise RuntimeError("Browser session did not answer.") from exc
        if not ok:
            if _is_target_closed(value):
                cls._context_closed = True
            if _recover and cls._should_relaunch(value):
                log.warning("browser target closed; relaunching session")
                cls._revive(generation)
                return cls._on_browser_thread(fn, _recover=False)
            raise value
        return value
