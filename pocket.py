"""Single-use desk pairing codes for the Telegram pocket."""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
from pathlib import Path

from build import ROOT

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
CODE_TTL_S = 10 * 60
MESSAGE_MAX_AGE_S = 10 * 60
STATE_PATH = ROOT / "pocket_state.json"
DESK_RE = re.compile(
    r"^\s*jarvis[\s,.:;-]+pocket\s+code\s*[.!?]*\s*$",
    re.IGNORECASE,
)
# Spoken transcripts often pick up a short greeting or a trailing "please".
VOICE_DESK_RE = re.compile(
    r"^\s*(?:(?:hey|hi|hello|ok|okay|um|uh)[\s,.:;-]+)*"
    r"jarvis[\s,.:;-]+pocket\s+code(?:[\s,.:;-]+please)?\s*[.!?]*\s*$",
    re.IGNORECASE,
)
_VOICE_LEAD_RE = re.compile(
    r"^(?:(?:hey|hi|hello|ok|okay|um|uh)\s+)*",
    re.IGNORECASE,
)
_DESK_TARGET = "jarvis pocket code"
REFUSAL = (
    "This pocket is locked. On the desk, say Jarvis, pocket code, "
    "then send that exact code here. It works once and expires in 10 minutes."
)
PAIRED_TEXT = "Paired. This pocket answers only you."


def is_desk_command(text: str) -> bool:
    return bool(DESK_RE.match(str(text or "")))


def is_voice_desk_command(text: str) -> bool:
    """True when a live transcript is the pocket-code phrase, plus light speech filler."""
    cleaned = " ".join(str(text or "").split()).strip()
    return bool(DESK_RE.match(cleaned) or VOICE_DESK_RE.match(cleaned))


def might_be_desk_command(text: str) -> bool:
    """True while a partial transcript could still become the pocket-code phrase."""
    if is_voice_desk_command(text):
        return True
    cleaned = " ".join(str(text or "").split()).strip().lower()
    cleaned = _VOICE_LEAD_RE.sub("", cleaned)
    cleaned = re.sub(r"[,.:;!?-]+", " ", cleaned)
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return True
    return _DESK_TARGET.startswith(cleaned)


class PocketStore:
    """One outstanding code, plus the chat ids allowed to use the brain."""

    def __init__(self, path: Path, clock=time.time) -> None:
        self.path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._code = ""
        self._expires_at = 0.0
        self._paired: list[int] = []
        self._offset: int | None = None
        self._load()

    def mint(self) -> str:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        with self._lock:
            self._code = code
            self._expires_at = self._clock() + CODE_TTL_S
            self._save()
        return code

    def pending_code(self) -> str | None:
        with self._lock:
            if self._code and self._clock() < self._expires_at:
                return self._code
            return None

    def redeem(self, chat_id: int, text: str) -> bool:
        """True only when text is the live code, character for character."""
        candidate = str(text or "").strip()
        if not candidate or any(ch.isspace() for ch in candidate):
            return False
        with self._lock:
            if not self._code or candidate != self._code:
                return False
            if self._clock() >= self._expires_at:
                self._code = ""
                self._expires_at = 0.0
                self._save()
                return False
            self._code = ""
            self._expires_at = 0.0
            if chat_id not in self._paired:
                self._paired.append(chat_id)
            self._save()
            return True

    def paired(self, chat_id: int) -> bool:
        with self._lock:
            return chat_id in self._paired

    def paired_ids(self) -> list[int]:
        with self._lock:
            return list(self._paired)

    def offset(self) -> int | None:
        with self._lock:
            return self._offset

    def set_offset(self, value: int) -> None:
        with self._lock:
            self._offset = int(value)
            self._save()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        code = str(data.get("code") or "")
        expires = data.get("expires_at")
        paired = data.get("paired")
        offset = data.get("update_offset")
        if code and isinstance(expires, (int, float)):
            self._code = code
            self._expires_at = float(expires)
        if isinstance(paired, list):
            self._paired = [int(item) for item in paired if isinstance(item, int)]
        if isinstance(offset, int):
            self._offset = offset

    def _save(self) -> None:
        payload = {
            "code": self._code,
            "expires_at": self._expires_at,
            "paired": self._paired,
            "update_offset": self._offset,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(self.path)


_store_lock = threading.Lock()
_store: PocketStore | None = None


def get_store() -> PocketStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = PocketStore(STATE_PATH)
        return _store


def set_store(store: PocketStore | None) -> None:
    global _store
    with _store_lock:
        _store = store


def desk_reply(text: str) -> str | None:
    if not is_desk_command(text):
        return None
    code = get_store().mint()
    return (
        f"Pocket code {code}. It works once and expires in 10 minutes. "
        "Send that exact code to the Telegram bot."
    )
