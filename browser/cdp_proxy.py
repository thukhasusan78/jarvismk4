"""Talk to the user's Chrome without waiting for every tab to finish loading.

Playwright's ``connect_over_cdp`` turns on auto-attach with
``waitForDebuggerOnStart`` and then waits until every existing page
initializes. One chrome:// tab, extension target, or document that never
commits keeps that call blocked for the whole launch timeout, so the
tab picker stays on "Looking for open tabs".

Listing uses ``Target.getTargets`` only. That does not pause pages.
Driving a tab still goes through Playwright, but through a local websocket
that resumes and drops targets Playwright cannot initialize, and that
answers a stuck per-tab command so the rest of the window can connect.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import socket
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

log = logging.getLogger("browser.cdp_proxy")

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_INJECTED_ID = 1_000_000_000
# Only Page.getFrameTree is timed out during connect. A discarded tab
# never answers it, and Playwright will not finish connect until it does.
# Other startup commands are left alone: timing them out drops live tabs
# that are just slow to answer while several pages initialize together.
_FRAME_TREE_TIMEOUT_S = 8.0
LIST_TIMEOUT_S = 25.0

_list_lock = threading.Lock()
_list_conn: "WsConn | None" = None


class WsConn:
    """One websocket, after the HTTP upgrade."""

    def __init__(self, sock: socket.socket, *, mask_outgoing: bool) -> None:
        self.sock = sock
        self.mask_outgoing = mask_outgoing
        self._incoming = bytearray()
        self._send_lock = threading.Lock()

    def close(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def send_bytes(self, opcode: int, payload: bytes) -> None:
        frame = bytearray()
        frame.append(0x80 | (opcode & 0x0F))
        length = len(payload)
        mask_bit = 0x80 if self.mask_outgoing else 0x00
        if length < 126:
            frame.append(mask_bit | length)
        elif length < 65536:
            frame.append(mask_bit | 126)
            frame.extend(struct.pack("!H", length))
        else:
            frame.append(mask_bit | 127)
            frame.extend(struct.pack("!Q", length))
        if self.mask_outgoing:
            mask = os.urandom(4)
            frame.extend(mask)
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        frame.extend(payload)
        with self._send_lock:
            self.sock.sendall(frame)

    def send_text(self, text: str) -> None:
        self.send_bytes(0x1, text.encode("utf-8"))

    def send_json(self, payload: object) -> None:
        self.send_text(json.dumps(payload, separators=(",", ":")))

    def recv_data(self, timeout: float | None = None) -> tuple[int, bytes]:
        self.sock.settimeout(timeout)
        while True:
            opcode, payload = self._read_message()
            if opcode == 0x9:
                self.send_bytes(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x8:
                raise ConnectionError("websocket closed")
            return opcode, payload

    def _read_message(self) -> tuple[int, bytes]:
        first_opcode = None
        parts: list[bytes] = []
        while True:
            header = self._read_exact(2)
            opcode = header[0] & 0x0F
            fin = header[0] & 0x80
            length = header[1] & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]
            if header[1] & 0x80:
                mask = self._read_exact(4)
                raw = self._read_exact(length)
                data = bytes(byte ^ mask[index % 4] for index, byte in enumerate(raw))
            else:
                data = self._read_exact(length)
            if first_opcode is None:
                first_opcode = opcode
            parts.append(data)
            if fin:
                return first_opcode, b"".join(parts)

    def _read_exact(self, size: int) -> bytes:
        buf = bytearray()
        if self._incoming:
            take = min(size, len(self._incoming))
            buf.extend(self._incoming[:take])
            del self._incoming[:take]
        while len(buf) < size:
            chunk = self.sock.recv(size - len(buf))
            if not chunk:
                raise ConnectionError("socket closed")
            buf.extend(chunk)
        return bytes(buf)


def keep_target(info: dict) -> bool:
    """True for a real page Playwright can initialize.

    ``tab``, worker, extension, and chrome:// targets are not pages we
    drive. Playwright 1.63 asserts on a target with no browserContextId
    and then never resumes it, so ``Target.setAutoAttach`` never returns.
    """
    if info.get("type") != "page":
        return False
    if not info.get("browserContextId"):
        return False
    url = str(info.get("url") or "")
    return url.startswith(("http://", "https://", "file://"))


def fill_empty_frame_urls(node: object, fallback: str) -> None:
    """Replace an uncommitted frame URL so page init does not wait forever.

    Playwright treats a main frame URL of ``""`` or ``":"`` as a navigation
    that has not committed, and blocks the connection until one does.
    """
    if not isinstance(node, dict):
        return
    frame = node.get("frame")
    if isinstance(frame, dict) and frame.get("url") in ("", ":", None):
        frame["url"] = fallback or "about:blank"
    children = node.get("childFrames")
    if isinstance(children, list):
        for child in children:
            fill_empty_frame_urls(child, fallback)


def _prefer_ipv4(url: str) -> str:
    return (
        url.replace("ws://localhost:", "ws://127.0.0.1:", 1)
        .replace("ws://[::1]:", "ws://127.0.0.1:", 1)
    )


def resolve_browser_ws(endpoint: str, timeout: float) -> str:
    """Browser websocket URL. ``ws://`` is used as-is; ``http://`` asks ``/json/version``."""
    if endpoint.startswith(("ws://", "wss://")):
        return _prefer_ipv4(endpoint)
    if not endpoint.startswith(("http://", "https://")):
        raise OSError(f"Unsupported DevTools endpoint: {endpoint}")
    url = endpoint.rstrip("/") + "/json/version"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError, UnicodeError) as exc:
        raise ConnectionError(f"Could not read {url}") from exc
    websocket = payload.get("webSocketDebuggerUrl") if isinstance(payload, dict) else None
    if not isinstance(websocket, str) or not websocket.startswith("ws"):
        raise ConnectionError("Chrome did not provide a DevTools websocket.")
    return _prefer_ipv4(websocket)


def _parse_ws_url(url: str) -> tuple[str, int, str]:
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = path + "?" + parsed.query
    return host, port, path


def client_connect(url: str, timeout: float) -> WsConn:
    host, port, path = _parse_ws_url(url)
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    sock.sendall(request.encode("ascii"))
    raw = _read_http_headers(sock)
    head, rest = raw.split(b"\r\n\r\n", 1)
    status = head.split(b"\r\n", 1)[0].decode("latin1", "replace")
    if " 101 " not in f" {status} ":
        sock.close()
        # Chrome answers 403 while the Allow prompt is unanswered or was blocked.
        if " 403 " in f" {status} ":
            raise PermissionError("Connection rejected")
        raise ConnectionError(status)
    conn = WsConn(sock, mask_outgoing=True)
    if rest:
        conn._incoming.extend(rest)
    return conn


def server_accept(listener: socket.socket, timeout: float) -> WsConn:
    listener.settimeout(timeout)
    sock, _addr = listener.accept()
    sock.settimeout(timeout)
    raw = _read_http_headers(sock)
    head, rest = raw.split(b"\r\n\r\n", 1)
    key = ""
    for line in head.decode("latin1", "replace").split("\r\n"):
        if line.lower().startswith("sec-websocket-key:"):
            key = line.split(":", 1)[1].strip()
    digest = hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()
    accept = base64.b64encode(digest).decode("ascii")
    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n"
        "\r\n"
    )
    sock.sendall(response.encode("ascii"))
    conn = WsConn(sock, mask_outgoing=False)
    if rest:
        conn._incoming.extend(rest)
    return conn


def _read_http_headers(sock: socket.socket) -> bytes:
    raw = bytearray()
    while b"\r\n\r\n" not in raw:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("websocket handshake closed")
        raw.extend(chunk)
        if len(raw) > 65536:
            raise ConnectionError("websocket handshake was too long")
    return bytes(raw)


def list_page_targets(endpoint: str, timeout: float = LIST_TIMEOUT_S) -> list[dict]:
    """Open pages from ``Target.getTargets``. Does not auto-attach or pause tabs.

    A newer call closes the previous socket so a cancelled picker does not
    leave the next attempt waiting behind it.
    """
    global _list_conn
    with _list_lock:
        previous = _list_conn
        _list_conn = None
    if previous is not None:
        previous.close()
    ws_url = resolve_browser_ws(endpoint, min(timeout, 8.0))
    conn = client_connect(ws_url, timeout)
    with _list_lock:
        _list_conn = conn
    try:
        conn.send_json({"id": 1, "method": "Target.getTargets"})
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for Chrome targets.")
            try:
                opcode, payload = conn.recv_data(remaining)
            except TimeoutError as exc:
                raise TimeoutError("Timed out waiting for Chrome targets.") from exc
            if opcode != 1:
                continue
            message = json.loads(payload.decode("utf-8"))
            if not isinstance(message, dict) or message.get("id") != 1:
                continue
            if "error" in message:
                raise ConnectionError(str(message["error"]))
            result = message.get("result")
            infos = result.get("targetInfos") if isinstance(result, dict) else None
            tabs: list[dict] = []
            for info in infos or []:
                if not isinstance(info, dict) or info.get("type") != "page":
                    continue
                tabs.append(
                    {
                        "index": len(tabs),
                        "title": str(info.get("title") or ""),
                        "url": str(info.get("url") or ""),
                    }
                )
            return tabs
    finally:
        conn.close()
        with _list_lock:
            if _list_conn is conn:
                _list_conn = None


class CdpAttachProxy:
    """Local DevTools websocket Playwright connects to instead of Chrome.

    Targets we will not drive are resumed and detached so Chrome finishes
    ``Target.setAutoAttach``. Pages that never answer a startup command are
    marked dead and later commands to them fail immediately.
    """

    def __init__(self, upstream: str) -> None:
        self.upstream = upstream
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(1)
        port = self._listener.getsockname()[1]
        self.endpoint = f"ws://127.0.0.1:{port}/devtools/browser"
        self._closed = threading.Event()
        self._startup = True
        self._lock = threading.Lock()
        self._skipped: set[str] = set()
        self._dead: set[str] = set()
        self._session_url: dict[str, str] = {}
        self._pending: dict[tuple[str, Any], str] = {}
        self._pending_at: dict[tuple[str, Any], float] = {}
        self._drop_ids: set[tuple[str, Any]] = set()
        self._injected = _INJECTED_ID
        self._chrome: WsConn | None = None
        self._playwright: WsConn | None = None
        self._thread = threading.Thread(
            target=self._serve,
            name="cdp-attach-proxy",
            daemon=True,
        )
        self._thread.start()

    def mark_connected(self) -> None:
        """Playwright finished connect. Stop failing slow commands."""
        self._startup = False

    def close(self) -> None:
        self._closed.set()
        try:
            self._listener.close()
        except OSError:
            pass
        for conn in (self._chrome, self._playwright):
            if conn is not None:
                conn.close()

    def _serve(self) -> None:
        playwright: WsConn | None = None
        chrome: WsConn | None = None
        try:
            while not self._closed.is_set():
                try:
                    playwright = server_accept(self._listener, 0.5)
                except TimeoutError:
                    continue
                except OSError:
                    return
                break
            if playwright is None or self._closed.is_set():
                return
            self._playwright = playwright
            upstream = resolve_browser_ws(self.upstream, 20)
            chrome = client_connect(upstream, 20)
            self._chrome = chrome
            threading.Thread(target=self._watch_startup, name="cdp-attach-watch", daemon=True).start()
            from_playwright = threading.Thread(target=self._pump_playwright, daemon=True)
            from_chrome = threading.Thread(target=self._pump_chrome, daemon=True)
            from_playwright.start()
            from_chrome.start()
            while not self._closed.is_set():
                if not from_playwright.is_alive() or not from_chrome.is_alive():
                    break
                self._closed.wait(0.2)
        except Exception:
            log.exception("cdp attach proxy failed for %s", self.upstream)
        finally:
            self._closed.set()
            if playwright is not None:
                playwright.close()
            if chrome is not None:
                chrome.close()

    def _pump_playwright(self) -> None:
        playwright = self._playwright
        chrome = self._chrome
        if playwright is None or chrome is None:
            return
        try:
            while not self._closed.is_set():
                try:
                    opcode, payload = playwright.recv_data(1.0)
                except TimeoutError:
                    continue
                if opcode != 1:
                    chrome.send_bytes(opcode, payload)
                    continue
                self._on_playwright_text(payload)
        except (OSError, ConnectionError, json.JSONDecodeError):
            self._closed.set()
            chrome.close()

    def _pump_chrome(self) -> None:
        playwright = self._playwright
        chrome = self._chrome
        if playwright is None or chrome is None:
            return
        try:
            while not self._closed.is_set():
                try:
                    opcode, payload = chrome.recv_data(1.0)
                except TimeoutError:
                    continue
                if opcode != 1:
                    playwright.send_bytes(opcode, payload)
                    continue
                self._on_chrome_text(payload)
        except (OSError, ConnectionError, json.JSONDecodeError):
            self._closed.set()
            playwright.close()

    def _on_playwright_text(self, payload: bytes) -> None:
        chrome = self._chrome
        playwright = self._playwright
        if chrome is None or playwright is None:
            return
        try:
            message = json.loads(payload.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            chrome.send_bytes(1, payload)
            return
        if not isinstance(message, dict):
            chrome.send_bytes(1, payload)
            return
        session = str(message.get("sessionId") or "")
        if session and session in self._dead:
            if "id" in message:
                self._send_error(playwright, message["id"], session, "Target closed")
            return
        if "id" in message and "method" in message:
            key = (session, message["id"])
            with self._lock:
                self._pending[key] = str(message.get("method") or "")
                self._pending_at[key] = time.monotonic()
        chrome.send_json(message)

    def _on_chrome_text(self, payload: bytes) -> None:
        playwright = self._playwright
        if playwright is None:
            return
        try:
            message = json.loads(payload.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            playwright.send_bytes(1, payload)
            return
        if not isinstance(message, dict):
            playwright.send_bytes(1, payload)
            return
        if message.get("method") == "Target.attachedToTarget":
            self._on_attached(message)
            return
        session = str(message.get("sessionId") or "")
        if session and (session in self._skipped or session in self._dead):
            return
        if "id" in message and "method" not in message:
            ident = message["id"]
            if isinstance(ident, int) and ident >= _INJECTED_ID:
                return
            key = (session, ident)
            with self._lock:
                method = self._pending.pop(key, None)
                self._pending_at.pop(key, None)
                dropped = key in self._drop_ids
                self._drop_ids.discard(key)
            if dropped:
                return
            result = message.get("result")
            if method == "Page.getFrameTree" and isinstance(result, dict):
                fallback = self._session_url.get(session) or "about:blank"
                fill_empty_frame_urls(result.get("frameTree"), fallback)
            playwright.send_json(message)
            return
        playwright.send_json(message)

    def _on_attached(self, message: dict) -> None:
        playwright = self._playwright
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        info = params.get("targetInfo") if isinstance(params.get("targetInfo"), dict) else {}
        session = str(params.get("sessionId") or "")
        parent = str(message.get("sessionId") or "")
        if parent and (parent in self._skipped or parent in self._dead):
            if session:
                self._skipped.add(session)
                self._release(session)
            return
        if keep_target(info) and playwright is not None:
            if session:
                self._session_url[session] = str(info.get("url") or "")
            log.debug("cdp keep %s", info.get("url"))
            playwright.send_json(message)
            return
        if session:
            log.debug("cdp skip %s %s", info.get("type"), info.get("url"))
            self._skipped.add(session)
            self._release(session)

    def _release(self, session: str) -> None:
        """Resume a paused target and detach. Leaving it paused stalls auto-attach."""
        chrome = self._chrome
        if chrome is None or not session:
            return
        with self._lock:
            run_id = self._injected
            self._injected += 1
            detach_id = self._injected
            self._injected += 1
        try:
            chrome.send_json(
                {
                    "id": run_id,
                    "sessionId": session,
                    "method": "Runtime.runIfWaitingForDebugger",
                    "params": {},
                }
            )
            chrome.send_json(
                {
                    "id": detach_id,
                    "method": "Target.detachFromTarget",
                    "params": {"sessionId": session},
                }
            )
        except OSError:
            log.exception("cdp proxy could not release session %s", session)

    def _watch_startup(self) -> None:
        while not self._closed.wait(0.3):
            if not self._startup:
                continue
            now = time.monotonic()
            expired: list[tuple[str, Any]] = []
            with self._lock:
                for key, started in list(self._pending_at.items()):
                    session = key[0]
                    if not session or self._pending.get(key) != "Page.getFrameTree":
                        continue
                    if now - started < _FRAME_TREE_TIMEOUT_S:
                        continue
                    expired.append(key)
                for key in expired:
                    self._pending.pop(key, None)
                    self._pending_at.pop(key, None)
                    self._drop_ids.add(key)
                    self._dead.add(str(key[0]))
            for session, ident in expired:
                playwright = self._playwright
                if playwright is not None:
                    self._send_error(playwright, ident, str(session), "Target did not answer")
                self._release(str(session))

    def _send_error(self, conn: WsConn, ident: Any, session: str, message: str) -> None:
        body: dict[str, Any] = {
            "id": ident,
            "error": {"code": -32000, "message": message},
        }
        if session:
            body["sessionId"] = session
        try:
            conn.send_json(body)
        except OSError:
            pass
