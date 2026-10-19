"""Long-poll Telegram. No webhook and no public port."""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from pocket import MESSAGE_MAX_AGE_S, PAIRED_TEXT, REFUSAL, get_store

log = logging.getLogger("pocket.telegram")

API = "https://api.telegram.org"
POLL_TIMEOUT_S = 25
TEXT_LIMIT = 4000
CAPTION_LIMIT = 1024
VOICE_LIMIT = 8_000_000
DOCUMENT_LIMIT = 50_000_000
TYPING_INTERVAL_S = 4.0

Brain = Callable[[str], Any]
Transcribe = Callable[[bytes, str], str]
Speak = Callable[[str], bytes]


class TelegramApi:
    def __init__(self, token: str) -> None:
        self._token = token

    def send_text(self, chat_id: int, text: str) -> None:
        _call(self._token, "sendMessage", {"chat_id": chat_id, "text": text})

    def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        _call(
            self._token,
            "sendChatAction",
            {"chat_id": chat_id, "action": action or "typing"},
            timeout=15,
        )

    def send_voice(self, chat_id: int, ogg: bytes, caption: str = "") -> None:
        if not ogg:
            return
        fields = {"chat_id": str(chat_id)}
        text = str(caption or "").strip()
        if text:
            fields["caption"] = text
        _call_multipart(
            self._token,
            "sendVoice",
            fields,
            field="voice",
            filename="reply.ogg",
            data=ogg,
            mime="audio/ogg",
        )

    def download_voice(self, file_id: str) -> tuple[bytes, str]:
        result = _call(self._token, "getFile", {"file_id": file_id}, timeout=30)
        if not isinstance(result, dict):
            raise RuntimeError("telegram file missing")
        path = str(result.get("file_path") or "")
        if not path or path.startswith("/") or ".." in path.split("/"):
            raise RuntimeError("telegram file missing")
        url = f"{API}/file/bot{self._token}/{path}"
        request = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read(VOICE_LIMIT + 1)
        except urllib.error.HTTPError as exc:
            exc.read()
            raise RuntimeError(f"telegram HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            raise RuntimeError("telegram unreachable") from exc
        if len(data) > VOICE_LIMIT:
            raise RuntimeError("voice note too large")
        return data, _mime_for(path)


def bot_token() -> str:
    import server

    raw = str(server.load_config().get("telegram_bot_token") or "").strip()
    if not raw or raw.upper().startswith("YOUR_") or "PASTE" in raw.upper():
        return ""
    return raw


def token_configured() -> bool:
    return bool(bot_token())


def delete_webhook(token: str) -> None:
    _call(token, "deleteWebhook", {"drop_pending_updates": False}, timeout=20)


def get_updates(token: str, offset: int | None) -> list[dict]:
    payload: dict[str, Any] = {
        "timeout": POLL_TIMEOUT_S,
        "allowed_updates": ["message"],
    }
    if offset is not None:
        payload["offset"] = offset
    result = _call(token, "getUpdates", payload, timeout=POLL_TIMEOUT_S + 15)
    if not isinstance(result, list):
        return []
    return [item for item in result if isinstance(item, dict)]


def handle_update(
    update: dict,
    *,
    api: Any,
    brain: Brain,
    transcribe: Transcribe,
    speak: Speak,
    now: Callable[[], float] = time.time,
) -> None:
    message = update.get("message")
    if not isinstance(message, dict):
        return
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    if chat.get("type") != "private":
        return
    chat_id = chat.get("id")
    if not isinstance(chat_id, int):
        return
    stamp = message.get("date")
    if isinstance(stamp, (int, float)) and now() - float(stamp) > MESSAGE_MAX_AGE_S:
        return
    store = get_store()
    text = message.get("text") if isinstance(message.get("text"), str) else None
    voice = message.get("voice") if isinstance(message.get("voice"), dict) else None
    if not store.paired(chat_id):
        if text is not None and store.redeem(chat_id, text.strip()):
            log.info("pocket.paired chat=%s", chat_id)
            api.send_text(chat_id, PAIRED_TEXT)
            return
        log.info("pocket.refused chat=%s", chat_id)
        api.send_text(chat_id, REFUSAL)
        return
    if text is not None:
        cleaned = " ".join(text.split()).strip()
        if not cleaned:
            return
        _answer(api, speak, chat_id, brain, cleaned)
        return
    if voice is not None:
        file_id = str(voice.get("file_id") or "").strip()
        if not file_id:
            api.send_text(chat_id, "That voice note had no audio.")
            return
        stop, thread = _start_typing(api, chat_id)
        try:
            data, mime = api.download_voice(file_id)
            transcript = " ".join(transcribe(data, mime).split()).strip()
            if not transcript:
                api.send_text(chat_id, "I could not hear any words in that voice note.")
                return
            log.info("pocket.voice chat=%s chars=%s", chat_id, len(transcript))
            _deliver(api, speak, chat_id, brain(transcript))
        finally:
            _stop_typing(stop, thread)
        return
    api.send_text(chat_id, "Send a text or a voice note.")


def caption_and_rest(text: str, limit: int = CAPTION_LIMIT) -> tuple[str, str]:
    """Voice captions hold 1024 characters. The rest is a follow-up text message."""
    cleaned = str(text or "").strip()
    if len(cleaned) <= limit:
        return cleaned, ""
    cut = cleaned.rfind("\n", 0, limit)
    if cut < limit // 2:
        cut = cleaned.rfind(" ", 0, limit)
    if cut < limit // 2:
        cut = limit
    head = cleaned[:cut].strip()
    tail = cleaned[cut:].strip()
    if not head:
        head, tail = cleaned[:limit], cleaned[limit:].strip()
    return head, tail


def text_chunks(text: str, limit: int = TEXT_LIMIT) -> list[str]:
    remaining = str(text or "").strip()
    if not remaining:
        return []
    chunks: list[str] = []
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        piece = remaining[:cut].strip()
        if piece:
            chunks.append(piece)
        remaining = remaining[cut:].strip()
    return chunks


_stop = threading.Event()
_thread: threading.Thread | None = None


def start_telegram_bot() -> threading.Thread:
    global _thread
    _stop.clear()
    if _thread and _thread.is_alive():
        return _thread
    _thread = threading.Thread(target=_loop, name="telegram-pocket", daemon=True)
    _thread.start()
    return _thread


def stop_telegram_bot() -> None:
    _stop.set()
    thread = _thread
    if thread and thread.is_alive():
        thread.join(timeout=1.0)


def _loop() -> None:
    announced = False
    missing_announced = False
    webhook_cleared = False
    while not _stop.is_set():
        token = bot_token()
        if not token:
            webhook_cleared = False
            announced = False
            if not missing_announced:
                log.warning(
                    "telegram.token_missing paste telegram_bot_token into config.json"
                )
                missing_announced = True
            _stop.wait(5)
            continue
        missing_announced = False
        if not webhook_cleared:
            try:
                delete_webhook(token)
                webhook_cleared = True
            except Exception as exc:
                log.warning("telegram.webhook_clear_failed error=%s", exc.__class__.__name__)
                _stop.wait(5)
                continue
        if not announced:
            log.info("telegram.pocket long-poll")
            announced = True
        store = get_store()
        try:
            updates = get_updates(token, store.offset())
        except Exception as exc:
            log.warning("telegram.poll_failed error=%s", _public_error(exc))
            wait = 30 if _unauthorized(exc) else 3
            _stop.wait(wait)
            continue
        api = TelegramApi(token)
        for update in updates:
            update_id = update.get("update_id")
            try:
                handle_update(
                    update,
                    api=api,
                    brain=_brain,
                    transcribe=_transcribe,
                    speak=_speak,
                )
            except Exception as exc:
                log.warning(
                    "telegram.update_failed id=%s error=%s",
                    update_id,
                    exc.__class__.__name__,
                )
            if isinstance(update_id, int):
                store.set_offset(update_id + 1)


CHANNEL_NOTE = (
    "This message arrived on Telegram. "
    "Your reply is delivered to that Telegram chat automatically, as text and voice. "
    "Calendar, Gmail, notes, file-sending tools, and the browser tools work on this turn. "
    "local_file_find, local_file_send, and drive_send work from Telegram itself. "
    "browser_open, browser_read, browser_click, browser_type, browser_go_back, "
    "browser_list_tabs, browser_attach_active_tab, "
    "and browser_request_handoff "
    "work from Telegram itself. The shared browser is on this computer, not a reply channel. "
    "Do not say you cannot browse the web from here. "
    "Do not say you cannot send files here. "
    "Do not say you cannot send results to Telegram. "
    "Do not ask to answer on the web instead. Answer the request in this reply."
)


def telegram_user_text(text: str) -> str:
    body = " ".join(str(text or "").split()).strip()
    return f"{CHANNEL_NOTE}\n\n{body}"


def deliver_to_paired(text: str) -> str:
    """Send one desk-requested message to every paired Telegram chat."""
    body = " ".join(str(text or "").split()).strip()
    if not body:
        return "The Telegram message was empty. Nothing was sent."
    targets = get_store().paired_ids()
    if not targets:
        return (
            "No Telegram chat is paired yet. On the desk, say Jarvis, pocket code, "
            "then send that exact code to the bot."
        )
    token = bot_token()
    if not token:
        return "The Telegram bot token is not in config.json, so nothing was sent."
    api = TelegramApi(token)
    ogg = b""
    try:
        import speech_note

        ogg = speech_note.voice_ogg(body)
    except Exception as exc:
        log.warning("pocket.deliver_voice_failed error=%s", exc.__class__.__name__)
        ogg = b""
    for chat_id in targets:
        try:
            api.send_chat_action(chat_id, "typing")
        except Exception:
            pass
        if ogg:
            _send_voice_with_text(api, chat_id, ogg, body)
        else:
            for chunk in text_chunks(body):
                api.send_text(chat_id, chunk)
    return "Sent to Telegram."


def _brain(text: str) -> Any:
    """The desk's Live brain. Falls back to the text brain only if Live is down."""
    from voice.server import ask_live

    try:
        return ask_live(text)
    except Exception as exc:
        log.warning("pocket.live_failed error=%s", exc.__class__.__name__)
        import server

        return server.fallback_reply(server.answer_for_pocket(text))


def _transcribe(data: bytes, mime: str) -> str:
    import speech_note

    return speech_note.transcribe(data, mime)


def _speak(text: str) -> bytes:
    import speech_note

    return speech_note.voice_ogg(text)


def _answer(api: Any, speak: Speak, chat_id: int, brain: Brain, text: str) -> None:
    stop, thread = _start_typing(api, chat_id)
    try:
        _deliver(api, speak, chat_id, brain(text))
    finally:
        _stop_typing(stop, thread)


def _deliver(api: Any, speak: Speak, chat_id: int, answer: Any) -> None:
    text, pcm, rate = _coerce_reply(answer)
    text = text.strip() or "The model returned no text."
    ogg = b""
    if pcm:
        try:
            import speech_note

            ogg = speech_note.pcm_to_ogg(pcm, rate)
        except Exception as exc:
            log.warning("pocket.live_audio_failed error=%s", exc.__class__.__name__)
            ogg = b""
    if not ogg:
        try:
            ogg = speak(text)
        except Exception as exc:
            log.warning("pocket.voice_failed error=%s", exc.__class__.__name__)
            _send_text_only(api, chat_id, text)
            return
    if not ogg:
        _send_text_only(api, chat_id, text)
        return
    _send_voice_with_text(api, chat_id, ogg, text)


def _send_text_only(api: Any, chat_id: int, text: str) -> None:
    for chunk in text_chunks(text):
        api.send_text(chat_id, chunk)
    api.send_text(chat_id, "The text is above. The voice note failed.")


def _send_voice_with_text(api: Any, chat_id: int, ogg: bytes, text: str) -> None:
    caption, rest = caption_and_rest(text)
    try:
        api.send_voice(chat_id, ogg, caption=caption)
    except Exception as exc:
        log.warning("pocket.voice_failed error=%s", exc.__class__.__name__)
        _send_text_only(api, chat_id, text)
        return
    for chunk in text_chunks(rest):
        api.send_text(chat_id, chunk)


def _coerce_reply(answer: Any) -> tuple[str, bytes, int]:
    text = getattr(answer, "text", None)
    pcm = getattr(answer, "pcm", None)
    rate = getattr(answer, "sample_rate", None)
    if isinstance(text, str) and isinstance(pcm, (bytes, bytearray)):
        sample_rate = int(rate or 24000)
        return text, bytes(pcm), sample_rate or 24000
    return str(answer or ""), b"", 24000


def _start_typing(api: Any, chat_id: int) -> tuple[threading.Event, threading.Thread | None]:
    send = getattr(api, "send_chat_action", None)
    stop = threading.Event()
    if not callable(send):
        return stop, None
    try:
        send(chat_id, "typing")
    except Exception as exc:
        log.warning("pocket.typing_failed error=%s", exc.__class__.__name__)
        return stop, None

    def _pulse() -> None:
        while not stop.wait(TYPING_INTERVAL_S):
            try:
                send(chat_id, "typing")
            except Exception:
                return

    thread = threading.Thread(target=_pulse, name="telegram-typing", daemon=True)
    thread.start()
    return stop, thread


def _stop_typing(stop: threading.Event, thread: threading.Thread | None) -> None:
    stop.set()
    if thread is not None and thread.is_alive() and thread is not threading.current_thread():
        thread.join(timeout=1.0)


def _mime_for(path: str) -> str:
    lowered = path.lower()
    if lowered.endswith(".mp3"):
        return "audio/mpeg"
    if lowered.endswith(".wav"):
        return "audio/wav"
    if lowered.endswith(".webm"):
        return "audio/webm"
    return "audio/ogg"


def _public_error(exc: BaseException) -> str:
    text = str(exc)
    if "bot" in text and ":" in text:
        return exc.__class__.__name__
    return f"{exc.__class__.__name__}: {text[:160]}"


def _unauthorized(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "401" in text or "unauthorized" in text


def _call(token: str, method: str, payload: dict | None, timeout: float = 40) -> Any:
    url = f"{API}/bot{token}/{method}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {} if data is None else {"Content-Type": "application/json"}
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        exc.read()
        raise RuntimeError(f"telegram HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError("telegram unreachable") from exc
    if not isinstance(body, dict) or not body.get("ok"):
        description = ""
        if isinstance(body, dict):
            description = str(body.get("description") or "")
        raise RuntimeError(description or "telegram error")
    return body.get("result")


def _call_multipart(
    token: str,
    method: str,
    fields: dict[str, str],
    *,
    field: str,
    filename: str,
    data: bytes,
    mime: str,
) -> Any:
    boundary = "----jarvis" + secrets.token_hex(8)
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
        chunks.append(str(value).encode())
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}\r\n".encode())
    chunks.append(
        (
            f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        ).encode()
    )
    chunks.append(f"Content-Type: {mime}\r\n\r\n".encode())
    chunks.append(data)
    chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    body = b"".join(chunks)
    url = f"{API}/bot{token}/{method}"
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        exc.read()
        raise RuntimeError(f"telegram HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError("telegram unreachable") from exc
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise RuntimeError("telegram voice send failed")
    return payload.get("result")


def send_telegram_document(chat_id: str, file_path: str, caption: str = "") -> bool:
    try:
        size = os.path.getsize(file_path)
    except OSError as exc:
        log.warning("telegram.document_failed error=%s", exc.__class__.__name__)
        return False
    if size > DOCUMENT_LIMIT:
        log.warning("telegram.document_too_large")
        return False
    token = bot_token()
    if not token:
        log.warning("telegram.token_missing paste telegram_bot_token into config.json")
        return False
    text, _rest = caption_and_rest(caption)
    fields = {"chat_id": str(chat_id)}
    if text:
        fields["caption"] = text
    filename = os.path.basename(file_path).replace('"', "").replace("\r", "").replace("\n", "")
    if not filename:
        filename = "file"
    try:
        with open(file_path, "rb") as handle:
            data = handle.read()
        if len(data) > DOCUMENT_LIMIT:
            log.warning("telegram.document_too_large")
            return False
        _call_multipart(
            token,
            "sendDocument",
            fields,
            field="document",
            filename=filename,
            data=data,
            mime="application/octet-stream",
        )
    except Exception as exc:
        log.warning("telegram.document_failed error=%s", exc.__class__.__name__)
        return False
    return True
