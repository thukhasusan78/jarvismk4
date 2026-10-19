"""The attach proxy must not wait on tabs Playwright cannot initialize."""

from __future__ import annotations

import json
import threading
import unittest

from browser.cdp_proxy import (
    CdpAttachProxy,
    client_connect,
    fill_empty_frame_urls,
    keep_target,
)


class KeepTargetTests(unittest.TestCase):
    def test_keeps_http_pages_with_a_browser_context(self) -> None:
        self.assertTrue(
            keep_target(
                {
                    "type": "page",
                    "url": "https://example.com/form",
                    "browserContextId": "ctx",
                }
            )
        )

    def test_drops_tab_targets_and_chrome_pages(self) -> None:
        self.assertFalse(keep_target({"type": "tab", "url": "https://example.com", "browserContextId": "ctx"}))
        self.assertFalse(
            keep_target(
                {
                    "type": "page",
                    "url": "chrome://inspect/#remote-debugging",
                    "browserContextId": "ctx",
                }
            )
        )
        self.assertFalse(keep_target({"type": "page", "url": "https://example.com"}))

    def test_empty_frame_url_is_replaced(self) -> None:
        tree = {"frame": {"id": "f", "url": ""}, "childFrames": [{"frame": {"id": "c", "url": ":"}}]}
        fill_empty_frame_urls(tree, "https://example.com/form")
        self.assertEqual(tree["frame"]["url"], "https://example.com/form")
        self.assertEqual(tree["childFrames"][0]["frame"]["url"], "https://example.com/form")


class ProxyFilterTests(unittest.TestCase):
    def test_skipped_targets_are_resumed_and_not_forwarded(self) -> None:
        chrome = _FakeDevtools()
        proxy = CdpAttachProxy(chrome.url)
        client = client_connect(proxy.endpoint, 5)
        try:
            client.send_json({"id": 1, "method": "Target.setAutoAttach", "params": {}})
            self.assertEqual(chrome.wait_json()["method"], "Target.setAutoAttach")
            chrome.send_json(_attached("tab-session", {"type": "tab", "url": "https://example.com"}))
            chrome.send_json(
                _attached(
                    "chrome-session",
                    {
                        "type": "page",
                        "url": "chrome://inspect/",
                        "browserContextId": "ctx",
                    },
                )
            )
            chrome.send_json(
                _attached(
                    "page-session",
                    {
                        "type": "page",
                        "url": "https://example.com/form",
                        "browserContextId": "ctx",
                    },
                )
            )
            released = []
            while len(released) < 4:
                message = chrome.wait_json()
                if message.get("method") in {"Runtime.runIfWaitingForDebugger", "Target.detachFromTarget"}:
                    released.append(message)
            sessions = {item.get("sessionId") or item.get("params", {}).get("sessionId") for item in released}
            self.assertEqual(sessions, {"tab-session", "chrome-session"})
            seen = client.recv_json(5)
            self.assertEqual(seen["method"], "Target.attachedToTarget")
            self.assertEqual(seen["params"]["sessionId"], "page-session")

            client.send_json(
                {"id": 2, "sessionId": "page-session", "method": "Page.getFrameTree", "params": {}}
            )
            self.assertEqual(chrome.wait_json()["method"], "Page.getFrameTree")
            chrome.send_json(
                {
                    "id": 2,
                    "sessionId": "page-session",
                    "result": {"frameTree": {"frame": {"id": "f", "url": ""}}},
                }
            )
            rewritten = client.recv_json(5)
            self.assertEqual(
                rewritten["result"]["frameTree"]["frame"]["url"],
                "https://example.com/form",
            )
        finally:
            client.close()
            proxy.close()
            chrome.close()


def _attached(session_id: str, info: dict) -> dict:
    return {
        "method": "Target.attachedToTarget",
        "params": {
            "sessionId": session_id,
            "waitingForDebugger": True,
            "targetInfo": info,
        },
    }


class _FakeDevtools:
    """A Chrome DevTools websocket that records what the proxy sends."""

    def __init__(self) -> None:
        import socket

        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(1)
        port = self._listener.getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/devtools/browser/test"
        self._conn = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self) -> None:
        from browser.cdp_proxy import server_accept

        try:
            self._conn = server_accept(self._listener, 5)
        except OSError:
            return
        self._ready.set()

    def wait_json(self) -> dict:
        if not self._ready.wait(5) or self._conn is None:
            raise AssertionError("fake Chrome accepted no connection")
        return self._conn_json()

    def _conn_json(self) -> dict:
        assert self._conn is not None
        _opcode, payload = self._conn.recv_data(5)
        return json.loads(payload.decode("utf-8"))

    def send_json(self, payload: dict) -> None:
        if not self._ready.wait(5) or self._conn is None:
            raise AssertionError("fake Chrome accepted no connection")
        self._conn.send_json(payload)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
        self._listener.close()


class _ClientJson:
    def __init__(self, conn) -> None:
        self._conn = conn

    def send_json(self, payload: dict) -> None:
        self._conn.send_json(payload)

    def recv_json(self, timeout: float) -> dict:
        _opcode, payload = self._conn.recv_data(timeout)
        return json.loads(payload.decode("utf-8"))

    def close(self) -> None:
        self._conn.close()


def client_connect(url: str, timeout: float) -> _ClientJson:
    from browser.cdp_proxy import client_connect as open_client

    return _ClientJson(open_client(url, timeout))


if __name__ == "__main__":
    unittest.main()
