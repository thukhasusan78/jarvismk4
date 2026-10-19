"""Local file root confinement and filename search. No network."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class LocalFileRootTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = list(server.LOCAL_FILE_ROOTS)
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = self.base / "me"
        self.root.mkdir()
        server.LOCAL_FILE_ROOTS = [os.path.realpath(self.root)]

    def tearDown(self) -> None:
        server.LOCAL_FILE_ROOTS = self._saved
        self.tmp.cleanup()

    def test_path_inside_root_passes(self) -> None:
        inside = self.root / "docs" / "note.txt"
        self.assertTrue(server.is_within_roots(str(inside)))

    def test_path_outside_roots_fails(self) -> None:
        outside = self.base / "other" / "file.txt"
        self.assertFalse(server.is_within_roots(str(outside)))

    def test_dotdot_escape_fails(self) -> None:
        nested = self.root / "docs"
        nested.mkdir()
        escaped = os.path.join(str(nested), "..", "..", "secret.txt")
        self.assertFalse(server.is_within_roots(escaped))

    def test_sibling_prefix_fails(self) -> None:
        sibling = self.base / "me-backup" / "secret.txt"
        self.assertFalse(server.is_within_roots(str(sibling)))

    def test_bad_path_returns_false(self) -> None:
        self.assertFalse(server.is_within_roots("\0"))


class LocalFileFindTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = list(server.LOCAL_FILE_ROOTS)
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = self.base / "me"
        self.outside = self.base / "me-backup"
        self.root.mkdir()
        self.outside.mkdir()
        server.LOCAL_FILE_ROOTS = [os.path.realpath(self.root)]
        self.now = time.time()
        self.report = self._touch(self.root / "notes" / "quarterly-report-final.txt", "report", 100)
        self.budget = self._touch(self.root / "Budget.XLSX", "budget-bytes", 10)
        self.older_note = self._touch(self.root / "alpha-note.txt", "old", 500)
        self.newer_note = self._touch(self.root / "beta-note.txt", "new", 20)
        self._touch(self.root / ".secret-report.txt", "hidden-file", 1)
        self._touch(self.root / ".hidden" / "report-inside.txt", "hidden-dir", 1)
        self._touch(self.root / "node_modules" / "report.js", "modules", 1)
        self._touch(self.root / "pkg" / "__pycache__" / "report.pyc", "cache", 1)
        self._touch(self.root / ".git" / "report.txt", "git", 1)
        self._touch(self.root / "report-archive" / "plain.txt", "path-only", 1)
        self._touch(self.outside / "quarterly-report-final.txt", "sibling", 1)
        self._touch(self.outside / "hosts", "not-the-system-file", 1)
        server.begin_local_file_turn()

    def tearDown(self) -> None:
        server.LOCAL_FILE_ROOTS = self._saved
        self.tmp.cleanup()

    def _touch(self, path: Path, text: str, age: float) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        stamp = self.now - age
        os.utime(path, (stamp, stamp))
        return path

    def test_substring_and_case_match_filename_only(self) -> None:
        found = server.local_file_find("report")
        names = [item["name"] for item in found]
        self.assertEqual(names, ["quarterly-report-final.txt"])
        self.assertEqual(server.local_file_find("REPORT"), found)
        budget = server.local_file_find("BuDgEt")
        self.assertEqual(len(budget), 1)
        self.assertEqual(budget[0]["name"], "Budget.XLSX")
        self.assertEqual(budget[0]["path"], os.path.realpath(self.budget))
        self.assertEqual(budget[0]["size"], len("budget-bytes"))
        self.assertRegex(budget[0]["modified"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$")
        self.assertTrue(server.is_within_roots(budget[0]["path"]))

    def test_hidden_and_skipped_dirs_are_ignored(self) -> None:
        names = {item["name"] for item in server.local_file_find("report")}
        self.assertEqual(names, {"quarterly-report-final.txt"})
        self.assertEqual(server.local_file_find("secret-report"), [])
        self.assertEqual(server.local_file_find("report.js"), [])
        self.assertEqual(server.local_file_find("report.pyc"), [])

    def test_results_stay_inside_the_root(self) -> None:
        found = server.local_file_find("report")
        self.assertTrue(found)
        root = os.path.realpath(self.root)
        for item in found:
            self.assertTrue(server.is_within_roots(item["path"]))
            self.assertEqual(os.path.commonpath([root, item["path"]]), root)
            self.assertNotEqual(item["path"], os.path.realpath(self.outside / "quarterly-report-final.txt"))
        self.assertEqual(server.local_file_find("hosts"), [])

    def test_newest_first_and_max_results(self) -> None:
        found = server.local_file_find("note")
        self.assertEqual([item["name"] for item in found], ["beta-note.txt", "alpha-note.txt"])
        limited = server.local_file_find("note", max_results=1)
        self.assertEqual([item["name"] for item in limited], ["beta-note.txt"])

    def test_empty_roots_does_not_walk(self) -> None:
        server.LOCAL_FILE_ROOTS = []
        with patch("server.os.walk", side_effect=AssertionError("walked the disk")):
            self.assertEqual(server.local_file_find("hosts"), [])

    def test_tool_result_is_local_json_without_untrusted_prefix(self) -> None:
        from google_account import TurnGuard, run_tool

        text = run_tool("local_file_find", {"query": "budget"}, TurnGuard())
        self.assertNotIn("UNTRUSTED", text)
        self.assertNotIn("Google is not connected", text)
        payload = json.loads(text)
        self.assertEqual(payload[0]["name"], "Budget.XLSX")

    def test_declared_for_live_and_http_without_google(self) -> None:
        from voice.session import GOOGLE_TOOLS, live_tool_declarations

        described = next(tool for tool in GOOGLE_TOOLS if tool["name"] == "local_file_find")
        self.assertEqual(described["description"], server.LOCAL_FILE_FIND_DESCRIPTION)
        send_tool = next(tool for tool in GOOGLE_TOOLS if tool["name"] == "local_file_send")
        self.assertEqual(send_tool["description"], server.LOCAL_FILE_SEND_DESCRIPTION)
        with patch("google_account.google_status", return_value={"connected": True, "drive": False}):
            disconnected = [tool["name"] for tool in live_tool_declarations(tools_ready=False)]
            self.assertIn("local_file_find", disconnected)
            self.assertIn("local_file_send", disconnected)
            self.assertNotIn("gmail_unread", disconnected)
            self.assertNotIn("drive_send", disconnected)
            connected = [tool["name"] for tool in live_tool_declarations(tools_ready=True)]
        self.assertEqual(connected.count("local_file_find"), 1)
        self.assertEqual(connected.count("local_file_send"), 1)
        self.assertIn("gmail_unread", connected)
        self.assertNotIn("drive_send", connected)
        spec = server.google_tool_spec(include_google=False)
        names = [item.name for item in spec.function_declarations]
        self.assertEqual(
            names,
            [
                "local_file_find",
                "local_file_send",
                "task_add",
                "task_list",
                "task_done",
                "browser_open",
                "browser_read",
                "browser_click",
                "browser_type",
                "browser_go_back",
                "browser_list_tabs",
                "browser_attach_active_tab",
                "browser_request_handoff",
            ],
        )
        described = {item.name: item.description for item in spec.function_declarations}
        self.assertEqual(described["local_file_find"], server.LOCAL_FILE_FIND_DESCRIPTION)
        self.assertEqual(described["local_file_send"], server.LOCAL_FILE_SEND_DESCRIPTION)

    def test_local_tools_stay_declared_when_google_status_is_disconnected(self) -> None:
        status = {"connected": False, "drive": False}
        with patch("server.google_status", return_value=status):
            spec = server.google_tool_spec(include_google=True)
        names = [item.name for item in spec.function_declarations]
        self.assertIn("local_file_find", names)
        self.assertIn("local_file_send", names)
        self.assertNotIn("drive_send", names)
        self.assertIn("from any channel", server.TOOL_RULES)
        self.assertIn("drive_search", server.TOOL_RULES)
        self.assertIn("rather than picking", server.TOOL_RULES)

    def test_file_send_names_keep_successful_sends_only(self) -> None:
        calls = [
            ("local_file_find", {"query": "report"}),
            ("local_file_send", {"path": "a"}),
            ("drive_send", {"file_id_or_name": "abc"}),
            ("drive_send", {"file_id_or_name": "def"}),
            ("gmail_unread", {}),
        ]
        results = [
            "[]",
            json.dumps({"ok": True, "sent_to": ["1"], "name": "report.pdf"}),
            json.dumps({"ok": False, "error": "no Drive file matched"}),
            json.dumps({"ok": True, "sent_to": ["1"], "name": "Budget.xlsx"}),
            "unread",
        ]
        self.assertEqual(server.file_send_names(calls, results), ["report.pdf", "Budget.xlsx"])

    def test_chat_responses_include_send_filenames(self) -> None:
        with (
            patch("server.google_connected", return_value=True),
            patch("server.api_keys", return_value=["test-key"]),
            patch("server.run_tool_loop", return_value=("On its way.", True, ["report.pdf"])),
            patch("server.remember"),
            patch("server.remember_tool"),
        ):
            chat = server.chat_payload("send the quarterly report")
            tool_chat = server.tool_chat_payload("send the quarterly report")
        self.assertEqual(chat["answer"], "On its way.")
        self.assertEqual(chat["sends"], ["report.pdf"])
        self.assertEqual(chat["nodes"], [])
        self.assertEqual(tool_chat["sends"], ["report.pdf"])
        self.assertEqual(tool_chat["nodes"], [])

    def test_send_rejects_path_outside_roots(self) -> None:
        outside = str(self.outside / "secret.txt")
        with patch("telegram_bot.send_telegram_document") as send:
            result = server.local_file_send(outside)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "path outside allowed directories")
        send.assert_not_called()

    def test_send_found_path(self) -> None:
        found = server.local_file_find("budget")
        path = found[0]["path"]
        with (
            patch("telegram_bot.send_telegram_document", return_value=True) as send,
            patch("telegram_bot.bot_token", return_value="test-token"),
        ):
            result = server.local_file_send(path, chat_id="42")
        self.assertTrue(result["ok"])
        self.assertEqual(result["sent_to"], ["42"])
        self.assertEqual(result["name"], "Budget.XLSX")
        send.assert_called_once_with("42", path)

    def test_document_over_50mb_does_not_hit_network(self) -> None:
        from telegram_bot import send_telegram_document

        with (
            patch("telegram_bot.os.path.getsize", return_value=50_000_001),
            patch("telegram_bot.urllib.request.urlopen") as urlopen,
            patch("builtins.open") as opened,
        ):
            self.assertFalse(send_telegram_document("9", "big.bin"))
        urlopen.assert_not_called()
        opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
