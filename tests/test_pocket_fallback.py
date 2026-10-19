"""Pocket fallback notice and read-only desk memory. No network."""

from __future__ import annotations

import contextlib
import unittest
from unittest.mock import patch

import server
import telegram_bot

NOTICE = server.FALLBACK_NOTICE


class PocketFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        with server._history_lock:
            self._desk = [dict(item) for item in server._conversation]
            self._pocket = [dict(item) for item in server._pocket_conversation]
            server._conversation.clear()
            server._pocket_conversation.clear()

    def tearDown(self) -> None:
        with server._history_lock:
            server._conversation[:] = self._desk
            server._pocket_conversation[:] = self._pocket

    def test_notice_follows_config_and_desk_memory_stays_readonly(self) -> None:
        with patch("voice.server.ask_live", side_effect=RuntimeError("voice-down")):
            with patch("server.answer_for_pocket", return_value="Clear afternoon."):
                with patch("server.load_config", return_value={"show_fallback_notice": True}):
                    shown = telegram_bot._brain("What's next?")
                with patch("server.load_config", return_value={"show_fallback_notice": False}):
                    hidden = telegram_bot._brain("What's next?")
        self.assertTrue(shown.startswith(NOTICE + "\n"))
        self.assertIn("Clear afternoon.", shown)
        self.assertEqual(hidden, "Clear afternoon.")
        self.assertNotIn(NOTICE, hidden)

        with patch("voice.server.ask_live", return_value="From the desk.") as live:
            with patch("server.answer_for_pocket") as pocket:
                self.assertEqual(telegram_bot._brain("hello"), "From the desk.")
        live.assert_called_once()
        pocket.assert_not_called()

        server.remember("user", "warehouse-token-old")
        server.remember("assistant", "warehouse-token-noted")
        server.remember("user", "supplier-token-naing")
        server.remember("assistant", "supplier-token-friday")
        server.remember_pocket("user", "pocket-only-token")
        server.remember_pocket("assistant", "pocket-only-reply")
        desk_before = server.history_snapshot()
        captured: dict[str, str] = {}

        def fake_loop(keys, prompt, include_google=True, model=None):
            captured["prompt"] = prompt
            return "Friday it is.", False, []

        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch(
                    "server.load_config",
                    return_value={
                        "gemini_api_keys": "test-key",
                        "show_fallback_notice": True,
                    },
                )
            )
            stack.enter_context(patch("server.google_connected", return_value=False))
            stack.enter_context(patch("server.run_tool_loop", side_effect=fake_loop))
            reply = server.answer_for_pocket("What is the supplier name?")

        prompt = captured["prompt"]
        self.assertLess(prompt.index("Earlier on the desk:"), prompt.index("Prior conversation:"))
        self.assertIn("User: supplier-token-naing", prompt)
        self.assertIn("Assistant: supplier-token-friday", prompt)
        self.assertIn("Assistant: warehouse-token-noted", prompt)
        self.assertNotIn("warehouse-token-old", prompt)
        self.assertIn("pocket-only-token", prompt)
        self.assertNotIn(NOTICE, prompt)
        self.assertEqual(reply, "Friday it is.")
        self.assertEqual(server.history_snapshot(), desk_before)
        self.assertEqual(
            server.pocket_history_snapshot()[-2:],
            [
                {"role": "user", "text": "What is the supplier name?"},
                {"role": "assistant", "text": "Friday it is."},
            ],
        )
