"""Action items in tasks.json. No network. Separate from notes and /remember."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class TaskStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "tasks.json"
        self._saved = server.TASKS_PATH
        server.TASKS_PATH = self.path
        server.set_task_source("web")

    def tearDown(self) -> None:
        server.TASKS_PATH = self._saved
        server.set_task_source("web")
        self.tmp.cleanup()

    def test_add_then_list_hides_the_task_once_it_is_done(self) -> None:
        added = server.task_add("call the supplier", due="2026-10-05")
        self.assertTrue(added["id"])
        self.assertEqual(added["text"], "call the supplier")
        self.assertEqual(added["due"], "2026-10-05")
        self.assertFalse(added["done"])
        self.assertEqual(added["source"], "web")
        self.assertRegex(added["created"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+06:30$")
        self.assertFalse(self.path.with_suffix(".json.tmp").exists())

        open_tasks = server.task_list(include_done=False)
        self.assertEqual(open_tasks, [added])

        finished = server.task_done(added["id"])
        self.assertEqual(finished["id"], added["id"])
        self.assertTrue(finished["done"])
        self.assertEqual(finished["text"], "call the supplier")
        self.assertEqual(server.task_list(include_done=False), [])

        kept = server.task_list(include_done=True)
        self.assertEqual([item["id"] for item in kept], [added["id"]])
        self.assertTrue(kept[0]["done"])
        on_disk = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk[0]["id"], added["id"])
        self.assertTrue(on_disk[0]["done"])

    def test_done_on_unknown_id_returns_an_error_dict(self) -> None:
        added = server.task_add("keep me")
        missing = server.task_done("does-not-exist")
        self.assertEqual(missing, {"error": "No task with that id."})
        self.assertEqual(server.task_done(""), {"error": "No task with that id."})
        still_open = server.task_list(include_done=False)
        self.assertEqual([item["id"] for item in still_open], [added["id"]])
        self.assertFalse(still_open[0]["done"])

    def test_open_tasks_sort_by_due_then_created_with_nulls_last(self) -> None:
        self.path.write_text(
            json.dumps(
                [
                    {
                        "id": "c",
                        "text": "no due",
                        "created": "2026-01-01T00:00:00+06:30",
                        "due": None,
                        "done": False,
                        "source": "web",
                    },
                    {
                        "id": "b",
                        "text": "later",
                        "created": "2026-01-02T00:00:00+06:30",
                        "due": "2026-10-05",
                        "done": False,
                        "source": "desk",
                    },
                    {
                        "id": "a",
                        "text": "sooner later created",
                        "created": "2026-01-03T00:00:00+06:30",
                        "due": "2026-10-01",
                        "done": False,
                        "source": "pocket",
                    },
                    {
                        "id": "d",
                        "text": "sooner earlier created",
                        "created": "2026-01-01T00:00:00+06:30",
                        "due": "2026-10-01",
                        "done": False,
                        "source": "web",
                    },
                    {
                        "id": "e",
                        "text": "finished",
                        "created": "2026-01-01T00:00:00+06:30",
                        "due": "2026-09-01",
                        "done": True,
                        "source": "web",
                    },
                ]
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            [item["id"] for item in server.task_list(include_done=False)],
            ["d", "a", "b", "c"],
        )

    def test_source_follows_the_channel(self) -> None:
        server.set_task_source("desk")
        desk = server.task_add("from the desk")
        server.set_task_source("pocket")
        pocket = server.task_add("from the pocket")
        server.set_task_source("somewhere-else")
        web = server.task_add("from the web")
        self.assertEqual(desk["source"], "desk")
        self.assertEqual(pocket["source"], "pocket")
        self.assertEqual(web["source"], "web")
        self.assertEqual(server.task_add("undated")["due"], None)

    def test_atomic_write_survives_simulated_crash(self) -> None:
        kept = server.task_add("keep me")
        original = self.path.read_text(encoding="utf-8")
        tmp = self.path.with_suffix(".json.tmp")
        original_write = Path.write_text

        def crash(target, data, *args, **kwargs):
            if os.path.normcase(str(target)) == os.path.normcase(str(tmp)):
                target.write_bytes(b"{partial")
                raise OSError("simulated crash")
            return original_write(target, data, *args, **kwargs)

        def replaced(*_args, **_kwargs):
            raise AssertionError("os.replace ran after a partial temp write")

        with patch.object(Path, "write_text", crash), patch("server.os.replace", replaced):
            with self.assertRaises(OSError):
                server.task_add("should not land")

        self.assertEqual(self.path.read_text(encoding="utf-8"), original)
        self.assertEqual(tmp.read_bytes(), b"{partial")
        self.assertNotIn("should not land", original)
        listed = server.task_list(include_done=False)
        self.assertEqual([item["id"] for item in listed], [kept["id"]])
        self.assertEqual(listed[0]["text"], "keep me")

    def test_rules_and_declarations_do_not_require_google(self) -> None:
        from voice.session import GOOGLE_TOOLS, live_system_instruction, live_tool_declarations

        self.assertIn(server.TASK_RULES, server.TOOL_RULES)
        self.assertIn(server.TASK_RULES, server.LOCAL_FILE_RULES)
        self.assertIn(server.TASK_RULES, live_system_instruction(tools_ready=False))
        names = [tool["name"] for tool in GOOGLE_TOOLS]
        for name in ("task_add", "task_list", "task_done"):
            described = next(tool for tool in GOOGLE_TOOLS if tool["name"] == name)
            self.assertEqual(described["description"], getattr(server, name.upper() + "_DESCRIPTION"))
            self.assertIn(name, names)
        disconnected = [tool["name"] for tool in live_tool_declarations(tools_ready=False)]
        for name in ("task_add", "task_list", "task_done"):
            self.assertIn(name, disconnected)
        with patch("google_account.google_status", return_value={"connected": True, "drive": False}):
            connected = [tool["name"] for tool in live_tool_declarations(tools_ready=True)]
        self.assertEqual(connected.count("task_add"), 1)
        self.assertEqual(connected.count("task_list"), 1)
        self.assertEqual(connected.count("task_done"), 1)
        spec = server.google_tool_spec(include_google=False)
        declared = [item.name for item in spec.function_declarations]
        self.assertEqual(
            declared,
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
        self.assertEqual(described["task_add"], server.TASK_ADD_DESCRIPTION)
        self.assertEqual(described["task_list"], server.TASK_LIST_DESCRIPTION)
        self.assertEqual(described["task_done"], server.TASK_DONE_DESCRIPTION)

    def test_tool_result_is_local_json_without_a_google_gate(self) -> None:
        from google_account import TurnGuard, run_tool

        added = json.loads(run_tool("task_add", {"text": "pay the bill"}, TurnGuard()))
        self.assertEqual(added["text"], "pay the bill")
        self.assertNotIn("error", added)
        listed = json.loads(run_tool("task_list", {}, TurnGuard()))
        self.assertEqual(listed[0]["id"], added["id"])
        missing = json.loads(
            run_tool("task_done", {"task_id": "missing"}, TurnGuard())
        )
        self.assertEqual(missing, {"error": "No task with that id."})
        self.assertNotIn("Google is not connected", json.dumps(missing))


if __name__ == "__main__":
    unittest.main()
