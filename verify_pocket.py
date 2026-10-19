#!/usr/bin/env python3
"""Live pocket checks: calendar text, voice-note reminder, and Telegram token."""

from __future__ import annotations

import re

from google_account import TurnGuard, google_connected, run_tool
from server import answer_for_pocket
from speech_note import pcm_to_ogg, synthesize, transcribe, voice_ogg
from telegram_bot import bot_token, delete_webhook

REMINDER = (
    "Add a calendar event tomorrow at 4 pm titled Pocket verify. "
    "End it at 4:30 pm."
)


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


def clip(text: str, limit: int = 220) -> str:
    return " ".join(str(text or "").split())[:limit]


def main() -> int:
    token = bot_token()
    if not token:
        _out(
            "wait phone — telegram_bot_token is empty. "
            "Create a bot with @BotFather, paste the token into config.json, "
            "say Jarvis, pocket code on the desk, then send that exact code to the bot."
        )
    else:
        try:
            delete_webhook(token)
            from telegram_bot import _call

            me = _call(token, "getMe", {}, timeout=20)
        except Exception as exc:
            return fail("telegram getMe", exc.__class__.__name__)
        name = ""
        if isinstance(me, dict):
            name = str(me.get("username") or "")
        if not name:
            return fail("telegram getMe", "no username")
        pass_line("telegram getMe", f"@{name} long-poll ready")

    if not google_connected():
        return fail("Google connected", "Press Connect Google, then run this again.")
    pass_line("Google connected")

    calendar = answer_for_pocket(
        "What is on my calendar today? Answer with a short list. "
        "If nothing is scheduled, say so. Do not create or move anything."
    )
    if not calendar or "not connected" in calendar.lower() or calendar.startswith("The brain failed"):
        return fail("calendar text", clip(calendar))
    if "Every Gemini API key" in calendar or "No Gemini API key" in calendar:
        return fail("calendar text", clip(calendar))
    pass_line("calendar text", clip(calendar))

    try:
        spoken = voice_ogg(calendar)
    except Exception as exc:
        return fail("calendar voice note", exc.__class__.__name__)
    if not spoken.startswith(b"OggS"):
        return fail("calendar voice note", "speech key did not return an Ogg voice note")
    pass_line("calendar voice note", f"{len(spoken)} bytes ogg")

    try:
        pcm, rate = synthesize(REMINDER)
        note = pcm_to_ogg(pcm, rate)
        transcript = transcribe(note, "audio/ogg")
    except Exception as exc:
        return fail("voice note transcribe", f"{exc.__class__.__name__}: {clip(exc)}")
    if "pocket" not in transcript.lower() or "verify" not in transcript.lower():
        return fail("voice note transcribe", clip(transcript))
    pass_line("voice note transcribe", clip(transcript))

    reply = answer_for_pocket(transcript)
    events = run_tool("gcal_events", {"days": 3, "max_results": 20}, TurnGuard())
    if "pocket verify" not in f"{reply}\n{events}".casefold():
        return fail("voice note reminder", clip(reply) + " || " + clip(events))
    when = ""
    match = re.search(r"Pocket verify.{0,80}", events)
    if match:
        when = match.group(0)
    pass_line("voice note reminder", clip(when or reply))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(1)
