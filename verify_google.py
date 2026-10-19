#!/usr/bin/env python3
"""Live Gmail and Calendar checks after Connect Google has stored a refresh token."""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request

from google_account import (
    TurnGuard,
    check_same_turn_refusal,
    get_draft,
    gmail_profile_email,
    google_connected,
    run_tool,
)
from server import HOST, PORT

BASE = f"http://{HOST}:{PORT}"
DRAFT_SUBJECT = "Jarvis verification draft"


def _out(text: str) -> None:
    print(text, flush=True)


def fail(label: str, detail: str) -> int:
    _out(f"fail {label} — {detail}")
    return 1


def pass_line(label: str, detail: str = "") -> None:
    line = f"pass {label}"
    if detail:
        line = f"{line} — {detail}"
    _out(line)


def post_tool_chat(message: str) -> tuple[int, dict]:
    request = urllib.request.Request(
        BASE + "/tool_chat",
        data=json.dumps({"message": message}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return status, data


def main() -> int:
    ok, detail = check_same_turn_refusal()
    if not ok:
        return fail("same-turn overwrite refusal", detail)
    pass_line("same-turn overwrite refusal", detail)

    if not google_connected():
        return fail(
            "Google connected",
            "Press Connect Google in the viewer, finish consent, then run this again.",
        )
    pass_line("Google connected", "refresh token file is present")

    guard = TurnGuard()
    unread = run_tool("gmail_unread", {"max_results": 5}, guard)
    if "UNTRUSTED EXTERNAL CONTENT" not in unread:
        return fail("gmail_unread", "result was not labeled untrusted")
    if "Google HTTP" in unread or (
        "failed" in unread.lower() and "No messages" not in unread
    ):
        return fail("gmail_unread", unread.replace("\n", " ")[:180])
    pass_line("gmail_unread", unread.replace("\n", " ")[:160])

    events = run_tool("gcal_events", {"days": 14, "max_results": 8}, TurnGuard())
    if "UNTRUSTED EXTERNAL CONTENT" not in events:
        return fail("gcal_events", "result was not labeled untrusted")
    if "Google HTTP" in events:
        return fail("gcal_events", events.replace("\n", " ")[:180])
    pass_line("gcal_events", events.replace("\n", " ")[:160])

    try:
        address = gmail_profile_email()
    except RuntimeError as exc:
        return fail("gmail profile", str(exc))
    if not address or "@" not in address:
        return fail("gmail profile", "no email address on the connected account")

    created = run_tool(
        "gmail_draft",
        {
            "to": address,
            "subject": DRAFT_SUBJECT,
            "body": "Created by Jarvis to verify draft-only Gmail access. Not sent.",
        },
        TurnGuard(),
    )
    match = re.search(r"draft_id:\s*(\S+)", created)
    if not match:
        return fail("gmail_draft", created.replace("\n", " ")[:180])
    draft_id = match.group(1)
    fetched = get_draft(draft_id)
    message = fetched.get("message") if isinstance(fetched.get("message"), dict) else {}
    if fetched.get("id") != draft_id:
        return fail("drafts.get", "created draft was not readable back from Gmail")
    if message.get("labelIds") == ["SENT"]:
        return fail("drafts.get", "the verification message was sent")
    pass_line("gmail_draft", f"draft {draft_id} exists in Gmail and was not sent")

    status, data = post_tool_chat(
        "What is on my calendar in the next week? Then create a Gmail draft to me "
        f"with subject {DRAFT_SUBJECT} and a one-line body that it is a verification draft. Do not send it."
    )
    answer = str(data.get("answer") or "").replace("\n", " ")
    if status != 200 or not answer:
        return fail("/tool_chat", f"HTTP {status} {answer[:160]}")
    if "not connected" in answer.lower():
        return fail("/tool_chat", answer[:160])
    pass_line("/tool_chat", answer[:160])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(1)
