"""Read-only meeting watcher. Telegram push only.

Each tick may call gcal_events, gmail_search, and search_notes, then
deliver_to_paired. It does not call run_tool_batch, gcal_move,
docs_replace, gmail_draft, or any other create or overwrite tool.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

from google_account import google_connected
from google_account import tool_gcal_events as gcal_events
from google_account import tool_gmail_search as gmail_search
from pocket import get_store
from telegram_bot import deliver_to_paired

log = logging.getLogger("proactive")

DEFAULT_INTERVAL_S = 300
LOOKAHEAD = timedelta(minutes=60)
NOTIFY_WITHIN = timedelta(minutes=15)
_NO_NOTES = ("no notes matched", "no notes were opened", "this is small talk")

_stop = threading.Event()
_thread: threading.Thread | None = None
_lock = threading.Lock()
_notified: set[tuple[str, str]] = set()


def start_proactive_watcher() -> threading.Thread:
    global _thread
    _stop.clear()
    if _thread and _thread.is_alive():
        return _thread
    _thread = threading.Thread(target=_loop, name="proactive-watcher", daemon=True)
    _thread.start()
    return _thread


def stop_proactive_watcher() -> None:
    _stop.set()
    thread = _thread
    if thread and thread.is_alive():
        thread.join(timeout=1.0)


def _loop() -> None:
    while not _stop.is_set():
        _tick()
        if _stop.wait(_interval_seconds()):
            return


def _tick() -> None:
    try:
        run_proactive_cycle()
    except Exception as exc:
        log.warning("proactive.cycle_failed error=%s", exc.__class__.__name__)


def _interval_seconds() -> int:
    try:
        from server import load_config

        raw = load_config().get("proactive_interval_seconds", DEFAULT_INTERVAL_S)
    except Exception:
        return DEFAULT_INTERVAL_S
    if isinstance(raw, bool):
        return DEFAULT_INTERVAL_S
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_S
    if value < 1:
        return DEFAULT_INTERVAL_S
    return value


def run_proactive_cycle(now: datetime | None = None) -> None:
    """One check. Returns immediately when Google or Telegram is not ready."""
    if not google_connected():
        return
    if not get_store().paired_ids():
        return
    moment = now or _now()
    raw = gcal_events({"minutes": 60, "max_results": 20})
    for event in _parse_events(raw):
        start = _parse_start(event["start"])
        if start is None:
            continue
        remaining = start - moment
        if remaining < timedelta(0) or remaining > LOOKAHEAD:
            continue
        if remaining > NOTIFY_WITHIN:
            continue
        key = (event["id"], event["start"])
        with _lock:
            if key in _notified:
                continue
        title = " ".join((event["summary"] or "(no title)").split())
        context = _context_line(title)
        if context[-1:] not in ".!?":
            context = context + "."
        message = f"Meeting '{title}' in 15 minutes. {context}"
        deliver_to_paired(message)
        with _lock:
            _notified.add(key)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _context_line(title: str) -> str:
    query = " ".join(title.split()).strip()
    if not query or query == "(no title)":
        return "no prep notes found"
    try:
        mail_line = _mail_line(gmail_search({"query": query, "max_results": 3}))
    except Exception as exc:
        log.warning("proactive.mail_context_failed error=%s", exc.__class__.__name__)
        mail_line = ""
    if mail_line:
        return mail_line
    try:
        notes, _ids = _search_notes(query)
    except Exception as exc:
        log.warning("proactive.notes_context_failed error=%s", exc.__class__.__name__)
        return "no prep notes found"
    return _notes_line(notes) or "no prep notes found"


def _search_notes(query: str) -> tuple[str, list[int]]:
    from voice.session import search_notes

    return search_notes(query)


def _mail_line(raw: str) -> str:
    if "No messages matched." in raw:
        return ""
    for line in str(raw or "").splitlines():
        if line.startswith("subject:"):
            subject = " ".join(line.split(":", 1)[1].split())
            if subject:
                return _clip(subject)
    return ""


def _notes_line(raw: str) -> str:
    text = str(raw or "")
    lowered = text.casefold()
    if any(phrase in lowered for phrase in _NO_NOTES):
        return ""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not (stripped.startswith("[") and "]" in stripped):
            continue
        for follow in lines[index + 1 :]:
            follow = follow.strip()
            if follow:
                return _clip(follow)
        label = stripped.split("]", 1)[1].strip()
        if label:
            return _clip(label)
    return ""


def _clip(text: str, limit: int = 180) -> str:
    line = " ".join(text.split())
    if len(line) <= limit:
        return line
    return line[: limit - 1].rstrip() + "…"


def _parse_events(text: str) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    current: dict[str, str] | None = None

    def finish() -> None:
        nonlocal current
        if not current:
            return
        event_id = current.get("id", "").strip()
        start = current.get("start", "").strip()
        if event_id and start:
            events.append(
                {
                    "id": event_id,
                    "summary": current.get("summary", "").strip(),
                    "start": start,
                }
            )
        current = None

    for line in str(text or "").splitlines():
        if line.startswith("id: "):
            finish()
            current = {"id": line[4:], "summary": "", "start": ""}
            continue
        if current is None:
            continue
        if line.startswith("summary: "):
            current["summary"] = line[len("summary: ") :]
        elif line.startswith("start: "):
            current["start"] = line[len("start: ") :]
    finish()
    return events


def _parse_start(value: str) -> datetime | None:
    text = value.strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone(timedelta(hours=6, minutes=30)))
    return parsed.astimezone(timezone.utc)
