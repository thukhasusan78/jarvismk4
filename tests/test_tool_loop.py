"""One /chat turn can run a chain of tools before the final answer. No network."""

from __future__ import annotations

import contextlib
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import google_account
import server

MULTI_STEP_RULE = (
    "When a request implies multiple steps "
    "(find a file, then read it, then send it; check unread mail, then draft "
    "a reply for one of them), do all the steps in this same turn using "
    "multiple tool calls before giving your final answer, rather than stopping "
    "after the first tool result and waiting to be asked to continue."
)
SEARCH_RESULT = "SEARCH_HIT file-1 Budget.gdoc"
READ_RESULT = "READ_BODY quarterly numbers 42"
SEND_RESULT = "TELEGRAM_SENT ok"
CHAIN = (
    ("drive_search", {"query": "budget"}, SEARCH_RESULT),
    ("drive_read", {"file_id": "file-1"}, READ_RESULT),
    ("send_telegram", {"text": "quarterly numbers 42"}, SEND_RESULT),
)


class _Fn:
    def __init__(self, name: str, args: dict) -> None:
        self.name = name
        self.args = args


class _Part:
    def __init__(self, function_call: _Fn) -> None:
        self.function_call = function_call
        self.text = None


class _Content:
    def __init__(self, parts: list) -> None:
        self.role = "model"
        self.parts = parts


class _Candidate:
    def __init__(self, content: _Content) -> None:
        self.content = content


class _Response:
    def __init__(self, *, text: str = "", calls: list | None = None, content=None) -> None:
        self.text = text
        self.function_calls = calls or []
        self.candidates = [_Candidate(content)] if content is not None else []


def _tool_response(name: str, args: dict) -> _Response:
    call = _Fn(name, args)
    return _Response(calls=[call], content=_Content([_Part(call)]))


def _function_results(contents) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for content in contents:
        for part in getattr(content, "parts", None) or []:
            response = getattr(part, "function_response", None)
            if response is None:
                continue
            payload = getattr(response, "response", None) or {}
            result = payload.get("result", "") if isinstance(payload, dict) else ""
            found.append((str(getattr(response, "name", "")), str(result)))
    return found


class ScriptedModels:
    """Three tool calls, then a text answer built from every function result so far."""

    def __init__(self) -> None:
        self.contents_seen: list = []

    def generate_content(self, *, model, contents, config):
        self.contents_seen.append(contents)
        step = len(self.contents_seen)
        if step <= len(CHAIN):
            name, args, _result = CHAIN[step - 1]
            return _tool_response(name, args)
        lines = [f"{name}: {result}" for name, result in _function_results(contents)]
        return _Response(text="Done. " + " | ".join(lines))


class FakeClient:
    models: ScriptedModels

    def __init__(self, *, api_key: str) -> None:
        self.api_key = api_key

    def close(self) -> None:
        return None


def _run_chat(rounds: int | None = None) -> tuple[dict, list[tuple[str, dict]], ScriptedModels]:
    executed: list[tuple[str, dict]] = []
    models = ScriptedModels()
    FakeClient.models = models

    def drive_search(args: dict) -> str:
        executed.append(("drive_search", dict(args)))
        return SEARCH_RESULT

    def drive_read(args: dict) -> str:
        executed.append(("drive_read", dict(args)))
        return READ_RESULT

    def deliver(text: str) -> str:
        executed.append(("send_telegram", {"text": text}))
        return SEND_RESULT

    with contextlib.ExitStack() as stack:
        if rounds is not None:
            stack.enter_context(patch("server.max_tool_rounds", return_value=rounds))
        stack.enter_context(patch("google.genai.Client", FakeClient))
        stack.enter_context(
            patch.dict(
                google_account.TOOL_IMPL,
                {"drive_search": drive_search, "drive_read": drive_read},
            )
        )
        stack.enter_context(patch("telegram_bot.deliver_to_paired", deliver))
        stack.enter_context(patch("server.google_connected", return_value=True))
        stack.enter_context(patch("server.api_keys", return_value=["test-key"]))
        stack.enter_context(patch("server.remember"))
        payload = server.chat_payload(
            "Find the budget file, read it, and send a summary on Telegram."
        )
    return payload, executed, models


class ToolLoopTests(unittest.TestCase):
    def test_multi_step_rule_is_in_the_tool_system_prompt(self) -> None:
        self.assertIn(MULTI_STEP_RULE, server.TOOL_RULES)
        self.assertIn(MULTI_STEP_RULE, server.TOOL_SYSTEM)

    def test_max_tool_rounds_reads_config_and_defaults_to_eight(self) -> None:
        self.assertEqual(server.TOOL_ROUNDS, 8)
        self.assertEqual(server.load_config()["max_tool_rounds"], 8)
        self.assertEqual(server.max_tool_rounds({}), 8)
        self.assertEqual(server.max_tool_rounds({"max_tool_rounds": 12}), 12)
        self.assertEqual(server.max_tool_rounds({"max_tool_rounds": "nope"}), 8)
        self.assertEqual(server.max_tool_rounds({"max_tool_rounds": 0}), 8)
        self.assertEqual(server.max_tool_rounds({"max_tool_rounds": -3}), 8)
        self.assertEqual(server.max_tool_rounds({"max_tool_rounds": True}), 8)

    def test_chat_runs_three_tools_then_answers_from_all_of_them(self) -> None:
        payload, executed, models = _run_chat()
        self.assertEqual(
            executed,
            [
                ("drive_search", {"query": "budget"}),
                ("drive_read", {"file_id": "file-1"}),
                ("send_telegram", {"text": "quarterly numbers 42"}),
            ],
        )
        self.assertEqual(len(models.contents_seen), 4)
        fed = _function_results(models.contents_seen[-1])
        self.assertEqual(
            fed,
            [
                ("drive_search", SEARCH_RESULT),
                ("drive_read", READ_RESULT),
                ("send_telegram", SEND_RESULT),
            ],
        )
        answer = payload["answer"]
        self.assertIn(SEARCH_RESULT, answer)
        self.assertIn(READ_RESULT, answer)
        self.assertIn(SEND_RESULT, answer)
        self.assertEqual(
            answer,
            "Done. "
            f"drive_search: {SEARCH_RESULT} | "
            f"drive_read: {READ_RESULT} | "
            f"send_telegram: {SEND_RESULT}",
        )

    def test_configured_cap_stops_the_chain(self) -> None:
        payload, executed, models = _run_chat(rounds=2)
        self.assertEqual(
            [name for name, _args in executed],
            ["drive_search", "drive_read"],
        )
        self.assertEqual(len(models.contents_seen), 2)
        self.assertNotIn(SEND_RESULT, payload["answer"])
        self.assertNotIn(READ_RESULT, payload["answer"])


class HandoffStopTests(unittest.TestCase):
    def tearDown(self) -> None:
        from browser.session import BrowserSession

        BrowserSession.stop()

    def test_needs_human_skips_the_rest_and_handoff_ends_the_turn(self) -> None:
        from browser.session import BrowserSession

        executed: list[tuple[str, str]] = []

        def opened(url: str) -> dict:
            executed.append(("browser_open", url))
            return {
                "ok": False,
                "needs_human": True,
                "reason": "captcha",
                "page": url,
            }

        def clicked(hint: str, confirmed_action: bool = False) -> dict:
            executed.append(("browser_click", hint))
            return {"ok": True, "clicked": hint, "confirmed": confirmed_action}

        def handoff(reason: str) -> dict:
            executed.append(("browser_request_handoff", reason))
            BrowserSession.awaiting_human = True
            BrowserSession.awaiting_human_reason = reason
            BrowserSession.awaiting_human_at = "2026-10-03T00:00:00+00:00"
            return {"ok": True, "awaiting_human": True}

        class Scripted:
            def __init__(self) -> None:
                self.n = 0

            def generate_content(self, *, model, contents, config):
                self.n += 1
                if self.n == 1:
                    return _Response(
                        calls=[
                            _Fn("browser_open", {"url": "https://example.com/login"}),
                            _Fn("browser_click", {"selector_hint": "Next"}),
                        ]
                    )
                if self.n == 2:
                    return _Response(
                        calls=[
                            _Fn("browser_request_handoff", {"reason": "captcha"}),
                            _Fn("browser_click", {"selector_hint": "Next"}),
                        ]
                    )
                return _Response(text="should not be asked")

        models = Scripted()
        FakeClient.models = models
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch("google.genai.Client", FakeClient))
            stack.enter_context(patch("browser.tools.browser_open", opened))
            stack.enter_context(patch("browser.tools.browser_click", clicked))
            stack.enter_context(patch("browser.tools.browser_request_handoff", handoff))
            stack.enter_context(patch("server.google_connected", return_value=True))
            stack.enter_context(patch("server.api_keys", return_value=["test-key"]))
            stack.enter_context(patch("server.remember"))
            payload = server.chat_payload("Sign in on that site.")
        self.assertEqual(
            executed,
            [
                ("browser_open", "https://example.com/login"),
                ("browser_request_handoff", "captcha"),
            ],
        )
        self.assertEqual(models.n, 2)
        self.assertEqual(
            payload["answer"],
            "I need you to take over the browser — captcha. "
            "The window is open on your screen. "
            "Reply 'done' (or just continue chatting) once you've finished.",
        )


class _Page:
    def __init__(self) -> None:
        self.url = ""

    def goto(self, url: str, **kwargs: object) -> None:
        self.url = url

    def title(self) -> str:
        return "Login"

    def bring_to_front(self) -> None:
        return None


def _arm_captcha_wait() -> None:
    from browser.session import BrowserSession

    BrowserSession.awaiting_human = True
    BrowserSession.awaiting_human_reason = "captcha"
    BrowserSession.awaiting_human_at = datetime.now(timezone.utc).isoformat()


class HandoffResumeTests(unittest.TestCase):
    def tearDown(self) -> None:
        from browser.session import BrowserSession

        BrowserSession.stop()

    def test_captcha_on_the_page_hands_off_and_stops_the_round(self) -> None:
        from browser.session import BrowserSession
        from browser.tools import browser_request_handoff

        executed: list[tuple[str, str]] = []
        page = _Page()

        def run(fn):
            return fn(page)

        def human() -> dict:
            executed.append(("page_needs_human", "captcha"))
            return {"needs_human": True, "reason": "captcha", "detail": "Captcha frame"}

        def handoff(reason: str) -> dict:
            executed.append(("browser_request_handoff", reason))
            return browser_request_handoff(reason)

        def clicked(hint: str, confirmed_action: bool = False) -> dict:
            executed.append(("browser_click", hint))
            return {"ok": True, "confirmed": confirmed_action}

        class Once:
            def __init__(self) -> None:
                self.n = 0

            def generate_content(self, *, model, contents, config):
                self.n += 1
                if self.n == 1:
                    return _Response(
                        calls=[
                            _Fn("browser_open", {"url": "https://example.com/login"}),
                            _Fn("browser_click", {"selector_hint": "Next"}),
                        ]
                    )
                return _Response(text="should not be asked")

        models = Once()
        FakeClient.models = models
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(BrowserSession, "start", lambda *_a, **_k: None))
            stack.enter_context(patch.object(BrowserSession, "_on_browser_thread", run))
            stack.enter_context(patch("browser.tools._blocked_domains", return_value=[]))
            stack.enter_context(patch("browser.tools.page_needs_human", human))
            stack.enter_context(patch("browser.tools.browser_request_handoff", handoff))
            stack.enter_context(patch("telegram_bot.deliver_to_paired", return_value="Sent to Telegram."))
            stack.enter_context(patch("google.genai.Client", FakeClient))
            stack.enter_context(patch("server.google_connected", return_value=True))
            stack.enter_context(patch("server.api_keys", return_value=["test-key"]))
            stack.enter_context(patch("server.remember"))
            payload = server.chat_payload("Open the login page.")
        self.assertEqual(
            executed,
            [
                ("page_needs_human", "captcha"),
                ("browser_request_handoff", "captcha"),
            ],
        )
        self.assertNotIn(("browser_click", "Next"), executed)
        self.assertEqual(models.n, 1)
        self.assertTrue(BrowserSession.awaiting_human)
        self.assertEqual(BrowserSession.awaiting_human_reason, "captcha")
        self.assertIn("take over the browser — captcha", payload["answer"])
        self.assertIn("one-time-code field", server.TOOL_RULES)
        self.assertIn("do not attempt to fill in a CAPTCHA", server.TOOL_RULES)

    def test_a_clear_page_resumes_without_showing_the_note(self) -> None:
        from browser.session import BrowserSession

        _arm_captcha_wait()

        class Resume:
            def __init__(self) -> None:
                self.n = 0
                self.system = ""

            def generate_content(self, *, model, contents, config):
                self.n += 1
                self.system = str(getattr(config, "system_instruction", "") or "")
                return _Response(text="Continuing.")

        models = Resume()
        FakeClient.models = models
        cleared = {"needs_human": False, "reason": None, "detail": ""}
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch("browser.tools.page_needs_human", return_value=cleared))
            stack.enter_context(patch("google.genai.Client", FakeClient))
            stack.enter_context(patch("server.google_connected", return_value=True))
            stack.enter_context(patch("server.api_keys", return_value=["test-key"]))
            stack.enter_context(patch("server.remember"))
            payload = server.chat_payload("done")
        note = "The user has completed the manual step (captcha)."
        self.assertEqual(models.n, 1)
        self.assertIn(note, models.system)
        self.assertIn("browser_read", models.system)
        self.assertEqual(payload["answer"], "Continuing.")
        self.assertNotIn("completed the manual step", payload["answer"])
        self.assertFalse(BrowserSession.awaiting_human)
        self.assertEqual(BrowserSession.awaiting_human_reason, "")

    def test_a_page_that_still_matches_keeps_the_pause(self) -> None:
        from browser.session import BrowserSession

        _arm_captcha_wait()
        started = BrowserSession.awaiting_human_at
        blocked = {"needs_human": True, "reason": "captcha", "detail": "Captcha frame"}

        def refuse_model(*_args, **_kwargs):
            raise AssertionError("the model ran while the page was still blocked")

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch("browser.tools.page_needs_human", return_value=blocked))
            stack.enter_context(patch("server.run_tool_loop", refuse_model))
            stack.enter_context(patch("server.remember"))
            web = server.chat_payload("done")
        self.assertTrue(BrowserSession.awaiting_human)
        self.assertEqual(BrowserSession.awaiting_human_reason, "captcha")
        self.assertEqual(BrowserSession.awaiting_human_at, started)
        self.assertIn("still looks unresolved", web["answer"])
        self.assertIn("captcha", web["answer"])
        self.assertNotIn("completed the manual step", web["answer"])

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch("browser.tools.page_needs_human", return_value=blocked))
            stack.enter_context(patch("server.run_tool_loop", refuse_model))
            stack.enter_context(patch("server.remember_pocket"))
            pocket = server.answer_for_pocket("done")
        self.assertTrue(BrowserSession.awaiting_human)
        self.assertIn("still looks unresolved", pocket)
        self.assertNotIn("completed the manual step", pocket)

    def test_an_old_handoff_drops_the_flag_without_reading_the_page(self) -> None:
        from browser.session import BrowserSession

        BrowserSession.awaiting_human = True
        BrowserSession.awaiting_human_reason = "captcha"
        BrowserSession.awaiting_human_at = (
            datetime.now(timezone.utc) - timedelta(seconds=601)
        ).isoformat()

        def explode() -> dict:
            raise AssertionError("expired handoff should not re-check the page")

        with patch("browser.tools.page_needs_human", explode):
            follow = server.browser_handoff_followup()
        self.assertEqual(follow, {"action": "proceed", "note": "", "reply": ""})
        self.assertFalse(BrowserSession.awaiting_human)
        self.assertEqual(server.handoff_timeout_seconds({}), 600)
        self.assertEqual(server.handoff_timeout_seconds({"handoff_timeout_seconds": 30}), 30)
        self.assertEqual(server.handoff_timeout_seconds({"handoff_timeout_seconds": "nope"}), 600)
        self.assertEqual(server.HANDOFF_TIMEOUT_S, 600)


if __name__ == "__main__":
    unittest.main()
