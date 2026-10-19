"""Drive file send. Drive and Telegram are mocked. No network."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from google_account import DRIVE_SEND_EXPORT, drive_send, tool_drive_send
from voice.session import GOOGLE_TOOLS, live_tool_declarations

import server

DOC_MIME = "application/vnd.google-apps.document"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"
PDF_MIME = "application/pdf"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _drive_id(mark: str) -> str:
    return (mark * 28)[:28]


class DriveSendTests(unittest.TestCase):
    def test_single_match_sends_without_showing_bytes(self) -> None:
        file_id = _drive_id("c")
        secret = b"SECRET-BYTES"
        sent: dict[str, str] = {}

        def fake_http(method, url, query=None, payload=None):
            if str(url).rstrip("/").endswith("/files"):
                return {
                    "files": [
                        {
                            "id": file_id,
                            "name": "Quarterly Report",
                            "mimeType": PDF_MIME,
                        }
                    ]
                }
            self.assertIn(file_id, url)
            return {
                "id": file_id,
                "name": "Quarterly Report",
                "mimeType": PDF_MIME,
                "size": "12",
            }

        def fake_bytes(method, url, query=None, limit=50_000_000):
            self.assertEqual((query or {}).get("alt"), "media")
            self.assertNotIn("/export", url)
            return secret

        def fake_send(chat_id, path, caption=""):
            sent["path"] = path
            self.assertTrue(os.path.isfile(path))
            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), secret)
            return True

        with (
            patch("google_account.google_http", side_effect=fake_http),
            patch("google_account.google_http_bytes", side_effect=fake_bytes),
            patch("telegram_bot.bot_token", return_value="test-token"),
            patch("telegram_bot.send_telegram_document", side_effect=fake_send),
        ):
            text = tool_drive_send(
                {"file_id_or_name": "Quarterly Report", "chat_id": "42"}
            )
        self.assertNotIn("UNTRUSTED", text)
        self.assertNotIn("SECRET-BYTES", text)
        result = json.loads(text)
        self.assertEqual(
            result,
            {"ok": True, "sent_to": ["42"], "name": "Quarterly Report"},
        )
        self.assertFalse(os.path.exists(sent["path"]))

    def test_multiple_matches_return_candidates_without_sending(self) -> None:
        def fake_http(method, url, query=None, payload=None):
            self.assertTrue(str(url).rstrip("/").endswith("/files"))
            return {
                "files": [
                    {"id": _drive_id("a"), "name": "Report A", "mimeType": PDF_MIME},
                    {"id": _drive_id("b"), "name": "Report B", "mimeType": PDF_MIME},
                ]
            }

        with (
            patch("google_account.google_http", side_effect=fake_http),
            patch("google_account.google_http_bytes") as download,
            patch("telegram_bot.send_telegram_document") as send,
        ):
            result = drive_send("Report")
        download.assert_not_called()
        send.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "multiple files matched, be more specific")
        self.assertEqual(
            result["candidates"],
            [
                {"id": _drive_id("a"), "name": "Report A", "mimeType": PDF_MIME},
                {"id": _drive_id("b"), "name": "Report B", "mimeType": PDF_MIME},
            ],
        )

    def test_oversize_metadata_refuses_without_downloading(self) -> None:
        file_id = _drive_id("d")

        def fake_http(method, url, query=None, payload=None):
            return {
                "id": file_id,
                "name": "huge.zip",
                "mimeType": "application/zip",
                "size": "50000001",
            }

        with (
            patch("google_account.google_http", side_effect=fake_http),
            patch("google_account.google_http_bytes") as download,
            patch("telegram_bot.send_telegram_document") as send,
        ):
            result = drive_send(file_id)
        download.assert_not_called()
        send.assert_not_called()
        self.assertEqual(result, {"ok": False, "error": "file is over 50 MB"})

    def test_export_mime_for_doc_and_sheet(self) -> None:
        self.assertEqual(DRIVE_SEND_EXPORT[DOC_MIME][0], PDF_MIME)
        self.assertEqual(DRIVE_SEND_EXPORT[SHEET_MIME][0], XLSX_MIME)
        doc = self._export(_drive_id("e"), DOC_MIME, "Notes")
        sheet = self._export(_drive_id("f"), SHEET_MIME, "Budget")
        self.assertIn("/export", doc["url"])
        self.assertEqual(doc["query"].get("mimeType"), PDF_MIME)
        self.assertEqual(doc["result"]["name"], "Notes.pdf")
        self.assertIn("/export", sheet["url"])
        self.assertEqual(sheet["query"].get("mimeType"), XLSX_MIME)
        self.assertEqual(sheet["result"]["name"], "Budget.xlsx")

    def test_declared_only_when_drive_is_enabled(self) -> None:
        self.assertIn("drive_send", [tool["name"] for tool in GOOGLE_TOOLS])
        with patch("google_account.google_status", return_value={"connected": True, "drive": False}):
            hidden = [tool["name"] for tool in live_tool_declarations(tools_ready=True)]
        self.assertIn("drive_read", hidden)
        self.assertNotIn("drive_send", hidden)
        with patch("google_account.google_status", return_value={"connected": True, "drive": True}):
            shown = [tool["name"] for tool in live_tool_declarations(tools_ready=True)]
        self.assertIn("drive_send", shown)
        with patch("server.google_status", return_value={"connected": True, "drive": False}):
            spec = server.google_tool_spec(include_google=True)
        names = [item.name for item in spec.function_declarations]
        self.assertIn("drive_read", names)
        self.assertNotIn("drive_send", names)
        with patch("server.google_status", return_value={"connected": True, "drive": True}):
            spec = server.google_tool_spec(include_google=True)
        names = [item.name for item in spec.function_declarations]
        self.assertIn("drive_send", names)

    def _export(self, file_id: str, mime: str, name: str) -> dict:
        seen: dict = {}

        def fake_http(method, url, query=None, payload=None):
            return {"id": file_id, "name": name, "mimeType": mime, "size": "20"}

        def fake_bytes(method, url, query=None, limit=50_000_000):
            seen["url"] = url
            seen["query"] = dict(query or {})
            return b"exported"

        with (
            patch("google_account.google_http", side_effect=fake_http),
            patch("google_account.google_http_bytes", side_effect=fake_bytes),
            patch("telegram_bot.bot_token", return_value="test-token"),
            patch("telegram_bot.send_telegram_document", return_value=True),
        ):
            result = drive_send(file_id, chat_id="7")
        self.assertTrue(result["ok"])
        seen["result"] = result
        return seen


if __name__ == "__main__":
    unittest.main()
