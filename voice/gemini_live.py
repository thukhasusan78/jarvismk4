from __future__ import annotations

import asyncio
import base64
import itertools
import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from voice.debug_audio import UPLINK_PCM_RATE, note_speech

log = logging.getLogger(__name__)

try:
    from websockets.exceptions import ConcurrencyError
except Exception:  # websockets missing: keepalive must degrade, not crash

    class ConcurrencyError(Exception):  # type: ignore[no-redef]
        """Stand-in so the except clause in _ping_session still resolves."""

# One counter for every Live socket attempt, including reconnects.
# Mic sends read this at send time so a mid-utterance reconnect retags later chunks.
_generation_lock = threading.Lock()
_generation_counter = itertools.count(1)
_current_generation = 0
_current_session_id = ""


def begin_connection_attempt() -> tuple[str, int]:
    """Start one Live connection attempt. Returns (session_id, generation)."""
    global _current_generation, _current_session_id
    session_id = uuid.uuid4().hex[:8]
    with _generation_lock:
        generation = next(_generation_counter)
        _current_generation = generation
        _current_session_id = session_id
    return session_id, generation


def current_live_identity() -> tuple[str, int]:
    """Session id and generation of the newest connection attempt."""
    with _generation_lock:
        return _current_session_id, _current_generation


def _close_reason_for(exc: BaseException) -> str:
    if is_timeout_error(exc):
        return "timeout"
    return "error"


def _put_sentinel(queue: "asyncio.Queue[Any]") -> None:
    """Enqueue the None end-of-turn marker even when the queue is full:
    drop the oldest item to make room, because consumers wait on it."""
    try:
        queue.put_nowait(None)
        return
    except asyncio.QueueFull:
        pass
    try:
        queue.get_nowait()
    except Exception:
        pass
    try:
        queue.put_nowait(None)
    except Exception:
        pass

_RECONNECT_DELAYS_S = (0.3, 0.8, 2.0)
_GOAWAY_KEEPALIVE_S = 5.0
# Turn output queues are bounded so a stalled consumer cannot grow them
# for the life of the brain; producers drop the oldest item when full.
TURN_QUEUE_MAXSIZE = 1024
KEEPALIVE_INTERVAL_S = 10.0
KEEPALIVE_PONG_TIMEOUT_S = 5.0
# 60 ms of 16 kHz s16le silence. Gemini aborts an idle Live socket after ~150s
# unless the API itself sees traffic; WebSocket ping frames do not count.
_HEARTBEAT_PCM = b"\x00" * 1920
DEFAULT_LIVE_MODEL = "gemini-3.1-flash-live-preview"
DEFAULT_LIVE_VOICE = "Enceladus"
# Native-audio replies are 24 kHz s16le, the same rate the desk plays.
OUTPUT_PCM_RATE = 24000


def is_transient_gemini_error(exc: BaseException | str) -> bool:
    """True for Gemini Live WebSocket 1011 / internal disconnects that should reconnect."""
    text = str(exc).lower()
    if "1011" in text:
        return True
    if "internal error occurred" in text:
        return True
    if "keepalive ping timeout" in text:
        return True
    if "operation was aborted" in text:
        return True
    # Our own reconnect closes the old socket with 1000. That is not a
    # second failure, and a peer close should open one fresh socket.
    if "sent 1000" in text or "received 1000" in text:
        return True
    if "connection closed" in text:
        return True
    compact = text.replace(" ", "").replace("_", "")
    if "goaway" in compact:
        return True
    code = getattr(exc, "code", None)
    if code == 1011:
        return True
    return False


def is_timeout_error(exc: BaseException) -> bool:
    """True for a wait_for timeout.

    Python 3.10.9's asyncio.TimeoutError is not the builtin TimeoutError, and
    its message is empty, so ``except TimeoutError`` lets it escape.
    """
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return True
    return exc.__class__.__name__ == "TimeoutError"


def is_dead_resumption(exc: BaseException | str) -> bool:
    """True when Gemini rejected a stored Live session handle."""
    text = str(exc).lower()
    if "session not found" in text:
        return True
    code = getattr(exc, "code", None)
    return code == 1008 and "session not found" in text


def go_away_seconds(go_away: Any) -> float | None:
    raw = getattr(go_away, "time_left", None)
    if raw is None:
        return None
    if hasattr(raw, "total_seconds"):
        try:
            return float(raw.total_seconds())
        except Exception:
            return None
    seconds = getattr(raw, "seconds", None)
    nanos = getattr(raw, "nanos", 0) or 0
    if seconds is not None:
        try:
            return float(seconds) + float(nanos) / 1_000_000_000
        except Exception:
            return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip().lower()
    if text.endswith("s"):
        text = text[:-1]
    try:
        return float(text)
    except ValueError:
        return None


@dataclass
class FunctionCall:
    name: str
    arguments: dict[str, Any]
    call_id: str | None = None


@dataclass
class TurnResult:
    input_text: str = ""
    output_text: str = ""
    function_calls: list[FunctionCall] = field(default_factory=list)
    error: str | None = None
    transient_disconnect: bool = False


@dataclass
class LiveSettings:
    gemini_model: str = DEFAULT_LIVE_MODEL
    gemini_voice: str = DEFAULT_LIVE_VOICE
    gemini_connect_timeout_s: float = 15.0
    gemini_send_timeout_s: float = 8.0


class KeyPool:
    def __init__(self, keys: list[str]) -> None:
        self.keys = keys
        self.index = 0
        self._lock = asyncio.Lock()

    async def current(self) -> str:
        if not self.keys:
            raise RuntimeError("no Gemini API keys configured")
        return self.keys[self.index % len(self.keys)]

    async def rotate(self) -> str:
        async with self._lock:
            self.index = (self.index + 1) % max(len(self.keys), 1)
            return await self.current()


class GeminiLiveBrain:
    """Gemini Live native-audio session with server-owned activity markers."""

    def __init__(
        self,
        settings: LiveSettings,
        key_pool: KeyPool,
        system_instruction: str,
    ) -> None:
        self.settings = settings
        self.key_pool = key_pool
        self._system_instruction = system_instruction
        self._session = None
        self._client = None
        self._connect_cm = None
        self._tools: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._reconnect_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._receive_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._turn: TurnResult | None = None
        self._turn_done = asyncio.Event()
        self._cancelled = False
        self._abandoning = False
        self._streaming = False
        self._activity_started = False
        self._resumption_handle: str | None = None
        self._session_token = object()
        self._conn_session_id = ""
        self._conn_generation = 0
        self._close_reason = "clean shutdown"
        self._pcm_sent_bytes = 0
        self._audio_out_bytes = 0
        self._last_turn_complete = False
        self._awaiting_tool_followup = False
        self._audio_q: asyncio.Queue[bytes | None] = asyncio.Queue(
            maxsize=TURN_QUEUE_MAXSIZE
        )
        self._audio_closed = False
        self._reply_q: asyncio.Queue[str | None] = asyncio.Queue(
            maxsize=TURN_QUEUE_MAXSIZE
        )
        self._last_reply = ""
        self._closed = False
        self._keepalive_interval_s = KEEPALIVE_INTERVAL_S
        self._keepalive_pong_timeout_s = KEEPALIVE_PONG_TIMEOUT_S
        self._keepalive_task: asyncio.Task | None = None
        self._keepalive_nudge: asyncio.Task | None = None

    @property
    def activity_started(self) -> bool:
        return self._activity_started

    @property
    def has_session(self) -> bool:
        return self._session is not None

    def set_system_instruction(self, text: str) -> None:
        self._system_instruction = text

    async def configure_tools(self, declarations: list[dict[str, Any]]) -> None:
        self._tools = declarations
        await self.close()

    async def ensure_connected(self) -> bool:
        """Connect if needed. False means the brain still has no session:
        callers must not treat that as a successful prewarm."""
        self._cancelled = False
        self._closed = False
        async with self._reconnect_lock:
            try:
                await self._ensure_session()
                self._start_receive_loop()
            except Exception as exc:
                log.warning("gemini.preconnect_failed error=%s", exc)
                self._close_reason = _close_reason_for(exc)
                await self._close_live_socket()
                return False
        self._start_keepalive()
        log.info("gemini.prewarmed model=%s", self.settings.gemini_model)
        return self._session is not None

    def _model_turn_open(self) -> bool:
        """True while a model reply is in flight. Idle and silent listen stay pingable."""
        if self._activity_started or self._awaiting_tool_followup:
            return True
        if self._streaming:
            return False
        return (
            self._turn is not None
            and self._turn_done is not None
            and not self._turn_done.is_set()
        )

    def _release_turn_latch(self) -> None:
        """Mark a turn finished when this side stops waiting for it.

        A local timeout or give-up must not leave _model_turn_open() true
        forever: keepalive treats an open turn as the socket's owner and
        then skips both the silence heartbeat and recovery, so the Live
        session dies silently and never reconnects.
        """
        if self._turn_done and not self._turn_done.is_set():
            self._turn_done.set()

    def _start_keepalive(self) -> None:
        if self._closed:
            return
        task = self._keepalive_task
        if task is not None and not task.done():
            return
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def _stop_keepalive(self) -> None:
        tasks = (self._keepalive_task, self._keepalive_nudge)
        self._keepalive_task = None
        self._keepalive_nudge = None
        for task in tasks:
            if task is None or task.done() or task is asyncio.current_task():
                continue
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    def nudge_keepalive(self) -> None:
        """Ping Gemini now. Used when the voice client sends its own ping."""
        if self._closed or self._session is None:
            return
        task = self._keepalive_nudge
        if task is not None and not task.done():
            return
        self._keepalive_nudge = asyncio.create_task(self._keepalive_once())

    async def _keepalive_loop(self) -> None:
        while not self._closed:
            try:
                await asyncio.sleep(self._keepalive_interval_s)
            except asyncio.CancelledError:
                return
            if self._closed:
                return
            try:
                await self._keepalive_once()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.warning("gemini.keepalive_failed error=%s", exc)

    async def _keepalive_once(self) -> None:
        if self._closed:
            return
        if self._session is None:
            # An open model reply owns the socket. A silent mic does not:
            # leaving it on a missing socket is why speech gets no answer.
            if self._model_turn_open() and not self._streaming:
                return
            await self._recover_after_transient(
                RuntimeError("gemini socket missing"),
                restart_receive=True,
            )
            return
        alive = await self._ping_session()
        if not alive:
            if self._closed or (self._model_turn_open() and not self._streaming):
                return
            log.warning("gemini.keepalive_pong_timeout")
            await self._recover_after_transient(
                RuntimeError("keepalive ping timeout"),
                restart_receive=True,
                dead_token=self._session_token,
            )
            return
        if (
            self._closed
            or self._streaming
            or self._activity_started
            or self._model_turn_open()
        ):
            return
        try:
            await self._send_silence_heartbeat()
        except Exception as exc:
            log.warning("gemini.keepalive_audio_failed error=%s", exc)
            if self._closed or self._streaming or self._model_turn_open():
                return
            await self._recover_after_transient(
                exc,
                restart_receive=True,
                dead_token=self._session_token,
            )

    async def _reconnect_idle(self) -> None:
        """One reconnect. Does not close a socket another path just opened."""
        await self._recover_after_transient(
            RuntimeError("gemini keepalive"),
            restart_receive=True,
            dead_token=self._session_token,
        )

    async def _ping_session(self) -> bool:
        """WebSocket ping/pong. Protocol frames keep the Live socket up without audio."""
        session = self._session
        ws = getattr(session, "_ws", None) if session is not None else None
        if ws is None:
            return False
        try:
            waiter = await ws.ping()
            await asyncio.wait_for(waiter, timeout=self._keepalive_pong_timeout_s)
            return True
        except ConcurrencyError:
            return True
        except Exception as exc:
            detail = str(exc).strip() or exc.__class__.__name__
            log.warning("gemini.keepalive_ping_failed error=%s", detail)
            return False

    async def _send_silence_heartbeat(self) -> None:
        from google.genai import types

        if (
            self._session is None
            or self._streaming
            or self._activity_started
            or self._model_turn_open()
        ):
            return
        blob = types.Blob(
            data=_HEARTBEAT_PCM, mime_type=f"audio/pcm;rate={UPLINK_PCM_RATE}"
        )
        async with self._send_lock:
            if (
                self._closed
                or self._session is None
                or self._streaming
                or self._activity_started
                or self._model_turn_open()
            ):
                return
            session_id, generation = current_live_identity()
            await asyncio.wait_for(
                self._session.send_realtime_input(audio=blob),
                timeout=self.settings.gemini_send_timeout_s,
            )
            log.info(
                "gemini.audio_sent session_id=%s generation=%s bytes=%s kind=heartbeat",
                session_id,
                generation,
                len(_HEARTBEAT_PCM),
            )

    def _build_live_config(self) -> Any:
        from google.genai import types

        realtime = types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                disabled=True
            ),
            turn_coverage=types.TurnCoverage.TURN_INCLUDES_ALL_INPUT,
        )
        resumption = None
        if self._resumption_handle:
            resumption = types.SessionResumptionConfig(handle=self._resumption_handle)
        voice_name = self.settings.gemini_voice or DEFAULT_LIVE_VOICE
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=self._system_instruction,
            # This google-genai build only exposes LOW/HIGH, and
            # AudioTranscriptionConfig has no language_codes field.
            thinking_config=types.ThinkingConfig(thinking_level="LOW"),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=voice_name
                    )
                )
            ),
            realtime_input_config=realtime,
            session_resumption=resumption,
            context_window_compression=types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow()
            ),
            tools=[types.Tool(function_declarations=self._tools)]
            if self._tools
            else None,
        )

    async def _connect_once(self) -> None:
        from google import genai

        session_id, generation = begin_connection_attempt()
        log.info(
            "gemini.connection_attempt session_id=%s generation=%s",
            session_id,
            generation,
        )
        cm = None
        try:
            key = await self.key_pool.current()
            self._client = genai.Client(api_key=key)
            config = self._build_live_config()
            cm = self._client.aio.live.connect(
                model=self.settings.gemini_model, config=config
            )
            self._session = await asyncio.wait_for(
                cm.__aenter__(),
                timeout=self.settings.gemini_connect_timeout_s,
            )
            self._connect_cm = cm
            self._session_token = object()
            self._conn_session_id = session_id
            self._conn_generation = generation
        except Exception as exc:
            self._session = None
            self._connect_cm = None
            self._client = None
            if cm is not None:
                try:
                    await cm.__aexit__(None, None, None)
                except Exception:
                    pass
            log.info(
                "gemini.connection_failed session_id=%s generation=%s error=%s",
                session_id,
                generation,
                exc,
            )
            raise
        log.info(
            "gemini.connection_established session_id=%s generation=%s",
            session_id,
            generation,
        )
        log.info(
            "gemini.connected model=%s resumed=%s",
            self.settings.gemini_model,
            bool(self._resumption_handle),
        )

    async def _ensure_session(self) -> None:
        if self._session is not None:
            return
        async with self._lock:
            if self._session is not None:
                return
            try:
                await self._connect_once()
            except Exception as exc:
                if is_dead_resumption(exc) and self._resumption_handle:
                    log.warning(
                        "gemini.resumption_rejected dropping_handle error=%s", exc
                    )
                    self._resumption_handle = None
                    await self._connect_once()
                else:
                    raise

    def _reset_turn_queues(self) -> None:
        self._audio_q = asyncio.Queue(maxsize=TURN_QUEUE_MAXSIZE)
        self._audio_closed = False
        self._reply_q = asyncio.Queue(maxsize=TURN_QUEUE_MAXSIZE)
        self._last_reply = ""
        self._audio_out_bytes = 0

    async def begin_utterance(self) -> None:
        self._cancelled = False
        self._closed = False
        self._streaming = True
        self._activity_started = False
        self._pcm_sent_bytes = 0
        self._last_turn_complete = False
        self._awaiting_tool_followup = False
        self._turn = TurnResult()
        self._turn_done = asyncio.Event()
        self._reset_turn_queues()
        async with self._reconnect_lock:
            try:
                await self._ensure_session()
                self._start_receive_loop()
                return
            except Exception as exc:
                log.warning("gemini.begin_failed error=%s", exc)
                self._close_reason = _close_reason_for(exc)
                await self._close_live_socket()
                if is_dead_resumption(exc):
                    self._resumption_handle = None
                try:
                    await self.key_pool.rotate()
                    await self._ensure_session()
                    self._start_receive_loop()
                    return
                except Exception as exc2:
                    self._streaming = False
                    if self._turn is not None:
                        self._turn.error = str(exc2)
                        self._turn_done.set()
                    raise RuntimeError(str(exc2)) from exc2

    async def push_pcm(self, pcm: bytes) -> bool:
        if self._cancelled or not self._streaming or not pcm:
            return False
        for attempt in (1, 2):
            if self._session is None or (
                self._turn is not None and self._turn.transient_disconnect
            ):
                await self._recover_after_transient(
                    RuntimeError("gemini socket missing"),
                    restart_receive=True,
                )
                if self._session is None or not self._streaming:
                    return False
            try:
                async with self._send_lock:
                    return await self._push_pcm_locked(pcm)
            except Exception as exc:
                transient = is_transient_gemini_error(exc) or is_dead_resumption(exc)
                if attempt == 2 or not transient:
                    if not transient:
                        log.warning("gemini.push_failed error=%s", exc)
                    return False
                if is_dead_resumption(exc):
                    self._resumption_handle = None
                await self._recover_after_transient(exc, restart_receive=True)
        return False

    async def _push_pcm_locked(self, pcm: bytes) -> bool:
        from google.genai import types

        if self._cancelled or not self._streaming or self._session is None or not pcm:
            return False
        blob = types.Blob(data=pcm, mime_type=f"audio/pcm;rate={UPLINK_PCM_RATE}")
        timeout = self.settings.gemini_send_timeout_s
        if not self._activity_started:
            # 3.1 keeps no pre-speech buffer. The first chunk is context,
            # then the activity window opens, then that chunk is sent again
            # inside the window. Later chunks are audio only.
            await self._send_logged_audio(blob, timeout)
            await asyncio.wait_for(
                self._session.send_realtime_input(
                    activity_start=types.ActivityStart()
                ),
                timeout=timeout,
            )
            await self._send_logged_audio(blob, timeout)
            self._activity_started = True
            self._pcm_sent_bytes += len(pcm)
            log.info("gemini.activity_start pcm_bytes=%s", len(pcm))
            return True
        await self._send_logged_audio(blob, timeout)
        self._pcm_sent_bytes += len(pcm)
        return True

    async def _send_logged_audio(self, blob: Any, timeout: float) -> None:
        """Write one PCM blob. Generation is read at send time, not at mic start."""
        session_id, generation = current_live_identity()
        payload = getattr(blob, "data", b"") or b""
        await asyncio.wait_for(
            self._session.send_realtime_input(audio=blob),
            timeout=timeout,
        )
        log.info(
            "gemini.audio_sent session_id=%s generation=%s bytes=%s kind=speech",
            session_id,
            generation,
            len(payload),
        )
        note_speech(payload if isinstance(payload, bytes) else bytes(payload), generation)

    async def push_jpeg(self, jpeg: bytes) -> None:
        from google.genai import types

        if self._cancelled or self._session is None or not jpeg:
            return
        try:
            blob = types.Blob(data=jpeg, mime_type="image/jpeg")
            await asyncio.wait_for(
                self._session.send_realtime_input(video=blob),
                timeout=self.settings.gemini_send_timeout_s,
            )
            log.info("gemini.jpeg_sent bytes=%s", len(jpeg))
        except Exception as exc:
            if is_transient_gemini_error(exc):
                await self._recover_after_transient(exc, restart_receive=True)
                return
            log.warning("gemini.jpeg_failed error=%s", exc)

    async def send_activity_end(self) -> bool:
        from google.genai import types

        self._streaming = False
        if self._session is None or not self._activity_started:
            return False
        if self._turn and self._turn.transient_disconnect:
            return False
        try:
            await asyncio.wait_for(
                self._session.send_realtime_input(activity_end=types.ActivityEnd()),
                timeout=self.settings.gemini_send_timeout_s,
            )
            log.info(
                "gemini.activity_end pcm_sent_bytes=%s pcm_sent_s=%.2f",
                self._pcm_sent_bytes,
                self._pcm_sent_bytes / 32000,
            )
            return True
        except Exception as exc:
            if is_transient_gemini_error(exc) or is_dead_resumption(exc):
                if is_dead_resumption(exc):
                    self._resumption_handle = None
                await self._recover_after_transient(exc, restart_receive=True)
                return False
            log.warning("gemini.activity_end_failed error=%s", exc)
            return False

    def _log_turn_complete(self, finished: TurnResult) -> None:
        log.info(
            "gemini.turn_complete input=%s output=%s error=%s transient=%s "
            "tools=%s audio_bytes=%s",
            finished.input_text,
            finished.output_text,
            finished.error,
            finished.transient_disconnect,
            [call.name for call in finished.function_calls],
            self._audio_out_bytes,
        )
        if (finished.output_text or "").strip() and self._audio_out_bytes == 0:
            log.warning(
                "gemini.audio_missing text_chars=%s",
                len(finished.output_text or ""),
            )

    async def wait_turn_result(self) -> TurnResult:
        turn = self._turn or TurnResult()
        if turn.transient_disconnect:
            return turn
        try:
            await asyncio.wait_for(self._turn_done.wait(), timeout=25)
        except Exception as exc:
            if not is_timeout_error(exc):
                raise
            log.info(
                "gemini.turn_timeout input=%s output=%s",
                turn.input_text,
                turn.output_text,
            )
            self._release_turn_latch()
            return TurnResult(error="gemini timeout", input_text=turn.input_text)
        finished = self._turn or TurnResult()
        self._log_turn_complete(finished)
        return finished

    async def end_utterance(self) -> TurnResult:
        turn = self._turn or TurnResult()
        if turn.transient_disconnect:
            return turn
        if not self._activity_started:
            return TurnResult()
        if self._session is None:
            return (
                turn
                if turn.error or turn.transient_disconnect
                else TurnResult(error="gemini unavailable", input_text=turn.input_text)
            )
        if not await self.send_activity_end():
            recovered = self._turn or TurnResult(input_text=turn.input_text)
            if recovered.transient_disconnect:
                recovered.error = None
                return recovered
            return TurnResult(
                error=recovered.error or "gemini unavailable",
                input_text=turn.input_text,
            )
        return await self.wait_turn_result()

    async def continue_with_functions(self, results: list[dict[str, Any]]) -> TurnResult:
        from google.genai import types

        if self._session is None:
            return TurnResult(error="gemini unavailable")
        prior = self._turn.input_text if self._turn else ""
        self._turn = TurnResult(input_text=prior)
        self._turn_done = asyncio.Event()
        self._awaiting_tool_followup = True
        self._start_receive_loop()
        responses = []
        for item in results:
            payload = item.get("response") or {"result": item.get("result")}
            kwargs: dict[str, Any] = {"name": item["name"], "response": payload}
            if item.get("call_id"):
                kwargs["id"] = item["call_id"]
            responses.append(types.FunctionResponse(**kwargs))
        log.info("gemini.tool_response names=%s", [item["name"] for item in results])
        try:
            await asyncio.wait_for(
                self._session.send_tool_response(function_responses=responses),
                timeout=8,
            )
            await asyncio.wait_for(self._turn_done.wait(), timeout=25)
        except Exception as exc:
            if is_timeout_error(exc):
                self._release_turn_latch()
                return TurnResult(error="gemini timeout", input_text=prior)
            if is_transient_gemini_error(exc):
                await self._recover_after_transient(exc, restart_receive=True)
                result = self._turn or TurnResult(input_text=prior)
                result.transient_disconnect = True
                result.error = None
                return result
            self._release_turn_latch()
            return TurnResult(error=str(exc), input_text=prior)
        finally:
            self._awaiting_tool_followup = False
        return self._turn or TurnResult()

    async def send_text_turn(self, user_text: str) -> TurnResult:
        return await self.send_user_turn(user_text)

    async def send_user_turn(self, user_text: str, jpeg: bytes | None = None) -> TurnResult:
        from google.genai import types

        cleaned = " ".join((user_text or "").split()).strip()
        # Reset before the first await so a playback pump attaches to this turn's queue.
        self._cancelled = False
        self._closed = False
        self._streaming = False
        self._activity_started = False
        self._awaiting_tool_followup = False
        self._turn = TurnResult(input_text=cleaned)
        self._turn_done = asyncio.Event()
        self._reset_turn_queues()
        try:
            await self._ensure_session()
        except Exception as exc:
            log.warning("gemini.text_turn_connect_failed error=%s", exc)
            self._release_turn_latch()
            return TurnResult(error=str(exc), input_text=cleaned)
        if self._session is None:
            self._release_turn_latch()
            return TurnResult(error="gemini unavailable", input_text=cleaned)

        self._start_receive_loop()
        parts: list[Any] = []
        if jpeg:
            try:
                parts.append(types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"))
            except Exception:
                parts.append(
                    types.Part(inline_data=types.Blob(data=jpeg, mime_type="image/jpeg"))
                )
            if cleaned:
                parts.append(
                    types.Part(
                        text=(
                            cleaned
                            + "\n\nA screenshot is attached. Answer only from what is visible."
                        )
                    )
                )
            else:
                parts.append(
                    types.Part(text="A screenshot is attached. What is visible?")
                )
        else:
            parts.append(types.Part(text=cleaned))
        try:
            await asyncio.wait_for(
                self._session.send_client_content(
                    turns=types.Content(role="user", parts=parts),
                    turn_complete=True,
                ),
                timeout=self.settings.gemini_send_timeout_s,
            )
            log.info(
                "gemini.text_turn_sent chars=%s jpeg_bytes=%s",
                len(cleaned),
                len(jpeg) if jpeg else 0,
            )
            await asyncio.wait_for(self._turn_done.wait(), timeout=40 if jpeg else 25)
        except Exception as exc:
            if is_timeout_error(exc):
                log.warning("gemini.text_turn_timeout")
                self._release_turn_latch()
                await self.finish_audio()
                return TurnResult(input_text=cleaned, error="gemini timeout")
            if is_transient_gemini_error(exc):
                await self._recover_after_transient(exc, restart_receive=True)
                return TurnResult(input_text=cleaned, transient_disconnect=True)
            log.warning("gemini.text_turn_failed error=%s", exc)
            self._release_turn_latch()
            return TurnResult(error=str(exc), input_text=cleaned)
        finished = self._turn or TurnResult()
        finished.input_text = cleaned
        log.info(
            "gemini.text_turn_complete output=%s audio_bytes=%s",
            finished.output_text,
            self._audio_out_bytes,
        )
        if (finished.output_text or "").strip() and self._audio_out_bytes == 0:
            log.warning(
                "gemini.audio_missing text_chars=%s",
                len(finished.output_text or ""),
            )
        return finished

    def _cancel_reconnect_task(self) -> None:
        task = self._reconnect_task
        self._reconnect_task = None
        if task is not None and not task.done():
            task.cancel()

    async def cancel(self) -> None:
        self._cancelled = True
        self._streaming = False
        self._activity_started = False
        if self._turn_done and not self._turn_done.is_set():
            self._turn_done.set()
        await self.finish_audio()

    async def close(self) -> None:
        self._closed = True
        self._close_reason = "clean shutdown"
        await self.cancel()
        await self._stop_keepalive()
        self._cancel_reconnect_task()
        task = self._receive_task
        self._receive_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await asyncio.sleep(0)
            except Exception:
                pass
        await self._close_live_socket()

    def _start_receive_loop(self) -> None:
        if self._receive_task is None or self._receive_task.done():
            self._receive_task = asyncio.create_task(self._receive_loop())

    async def _close_live_socket(self) -> None:
        reason = self._close_reason or "clean shutdown"
        self._close_reason = "clean shutdown"
        async with self._lock:
            cm = self._connect_cm
            session_id = self._conn_session_id
            generation = self._conn_generation
            had_session = self._session is not None or cm is not None
            self._session = None
            self._connect_cm = None
            self._client = None
            if had_session:
                log.info(
                    "gemini.connection_closed session_id=%s generation=%s reason=%s",
                    session_id or "-",
                    generation,
                    reason,
                )
            if cm is None:
                return
            try:
                await cm.__aexit__(None, None, None)
            except Exception:
                pass

    async def _try_reconnect(self) -> bool:
        async with self._reconnect_lock:
            return await self._connect_attempts()

    async def _connect_attempts(self) -> bool:
        """Open one Live socket. Caller holds ``_reconnect_lock``."""
        if self._session is not None:
            return True
        for attempt, delay in enumerate(_RECONNECT_DELAYS_S):
            if attempt:
                await asyncio.sleep(delay)
            if self._closed:
                return False
            try:
                await self._ensure_session()
                log.info(
                    "gemini.reconnected attempt=%s resumed=%s",
                    attempt + 1,
                    bool(self._resumption_handle),
                )
                return True
            except Exception as exc:
                log.warning(
                    "gemini.reconnect_failed attempt=%s error=%s",
                    attempt + 1,
                    exc,
                )
                self._close_reason = _close_reason_for(exc)
                await self._close_live_socket()
                if is_dead_resumption(exc):
                    self._resumption_handle = None
                else:
                    await self.key_pool.rotate()
        return False

    def _schedule_reconnect(self, delay_s: float, token: object) -> None:
        if self._reconnect_task is not None and not self._reconnect_task.done():
            return
        self._reconnect_task = asyncio.create_task(
            self._reconnect_after_delay(delay_s, token)
        )

    async def _reconnect_after_delay(self, delay_s: float, token: object) -> None:
        try:
            await asyncio.sleep(delay_s)
        except asyncio.CancelledError:
            return
        if self._cancelled:
            return
        if self._activity_started and self._turn_done and not self._turn_done.is_set():
            try:
                # Cap the turn wait by the scheduled delay so the reconnect
                # still starts before the goAway deadline passes.
                await asyncio.wait_for(
                    self._turn_done.wait(),
                    timeout=min(25, max(1.0, delay_s)),
                )
            except Exception:
                pass
        if self._cancelled or self._session_token != token:
            log.info("gemini.go_away_reconnect skipped")
            # A stale skip must not leave nothing scheduled: if the socket
            # is gone and nobody else is already reconnecting, queue a
            # fresh attempt against the current token.
            if not self._cancelled and not self._closed and self._session is None:
                if self._reconnect_task is asyncio.current_task():
                    self._reconnect_task = None
                self._schedule_reconnect(1.0, self._session_token)
            return
        log.info("gemini.go_away_reconnect")
        await self._recover_after_transient(
            RuntimeError("gemini go_away"),
            restart_receive=True,
            dead_token=token,
        )

    async def _handle_go_away(self, go_away: Any) -> None:
        seconds = go_away_seconds(go_away)
        log.warning(
            "gemini.go_away time_left=%s seconds=%s",
            getattr(go_away, "time_left", None),
            seconds,
        )
        if seconds is not None and seconds > _GOAWAY_KEEPALIVE_S:
            delay = max(1.0, seconds - _GOAWAY_KEEPALIVE_S)
            log.info("gemini.go_away_deferred delay_s=%.1f", delay)
            self._schedule_reconnect(delay, self._session_token)
            return
        self._cancel_reconnect_task()
        if seconds is not None:
            self._resumption_handle = None
        # An unparseable time_left (seconds is None) says nothing about
        # the resumption handle's validity, so the handle is kept then.
        await self._recover_after_transient(
            RuntimeError("gemini go_away"),
            restart_receive=False,
            dead_token=self._session_token,
        )

    async def _recover_after_transient(
        self,
        exc: BaseException,
        *,
        restart_receive: bool,
        dead_token: object | None = None,
    ) -> bool:
        """Replace a dead Live socket once.

        A second caller that still names the old socket must not close the
        socket the first caller just opened. That race left the mic deaf
        while typed turns still spoke.
        """
        log.warning("gemini.transient_disconnect error=%s", exc)
        # Only an explicit dead-resumption rejection proves the handle is
        # useless. 1011 / internal errors are the common transient case:
        # dropping the handle there throws away the conversation context
        # on exactly the reconnects resumption exists for.
        drop_handle = is_dead_resumption(exc)
        was_listening = self._streaming
        async with self._reconnect_lock:
            if self._closed:
                return False
            already = (
                dead_token is not None
                and self._session is not None
                and self._session_token != dead_token
            )
            if already:
                recovered = True
                if was_listening and not self._cancelled:
                    self._streaming = True
                    self._activity_started = False
                    if self._turn is not None:
                        self._turn.transient_disconnect = False
                        self._turn.error = None
            else:
                self._streaming = False
                self._activity_started = False
                if not was_listening:
                    if self._turn is not None:
                        self._turn.transient_disconnect = True
                        self._turn.error = None
                    if self._turn_done and not self._turn_done.is_set():
                        self._turn_done.set()
                if drop_handle:
                    self._resumption_handle = None
                self._close_reason = _close_reason_for(exc)
                await self._close_live_socket()
                recovered = await self._connect_attempts()
                if recovered and was_listening and not self._cancelled:
                    self._streaming = True
                    if self._turn is not None:
                        self._turn.transient_disconnect = False
                        self._turn.error = None
                    if self._turn_done is None or self._turn_done.is_set():
                        self._turn_done = asyncio.Event()
            if recovered and restart_receive:
                self._start_receive_loop()
        return recovered

    def _publish_audio(self, chunks: list[bytes]) -> None:
        if self._audio_closed:
            return
        for chunk in chunks:
            if chunk:
                self._audio_out_bytes += len(chunk)
                try:
                    self._audio_q.put_nowait(chunk)
                except asyncio.QueueFull:
                    # Bounded queue, stalled consumer: drop the oldest
                    # chunk instead of growing memory without limit.
                    try:
                        self._audio_q.get_nowait()
                    except Exception:
                        pass
                    try:
                        self._audio_q.put_nowait(chunk)
                    except Exception:
                        pass

    def _publish_reply(self, text: str) -> None:
        cleaned = " ".join((text or "").split()).strip()
        if cleaned == self._last_reply:
            return
        self._last_reply = cleaned
        try:
            self._reply_q.put_nowait(cleaned)
        except Exception:
            pass

    def _extract_audio(self, sc: Any) -> list[bytes]:
        chunks: list[bytes] = []
        model_turn = getattr(sc, "model_turn", None)
        if model_turn is None:
            return chunks
        for part in getattr(model_turn, "parts", None) or []:
            inline = getattr(part, "inline_data", None)
            if inline is None:
                raw = self._as_audio_bytes(getattr(part, "data", None))
                if raw:
                    chunks.append(raw)
                continue
            data = getattr(inline, "data", None)
            raw = self._as_audio_bytes(data)
            if raw:
                chunks.append(raw)
        return chunks

    def _response_chunk_meta(self, response: Any) -> tuple[int, str]:
        size = self._response_chunk_size(response)
        labels: list[str] = []
        sc = getattr(response, "server_content", None)
        if sc is not None:
            if self._extract_audio(sc):
                labels.append("audio")
            if getattr(sc, "input_transcription", None) or getattr(
                sc, "output_transcription", None
            ):
                labels.append("transcript")
            if getattr(sc, "turn_complete", False) or getattr(
                sc, "generation_complete", False
            ):
                labels.append("turn_complete")
            if getattr(sc, "interrupted", False):
                labels.append("interrupted")
        if getattr(response, "go_away", None) is not None:
            labels.append("go_away")
        tool_call = getattr(response, "tool_call", None)
        if tool_call is not None and getattr(tool_call, "function_calls", None):
            labels.append("tool")
        return size, ",".join(labels) or "other"

    def _response_chunk_size(self, response: Any) -> int:
        """Byte size of one Live server message. Audio plus transcript text."""
        total = 0
        data = getattr(response, "data", None)
        if isinstance(data, (bytes, bytearray)):
            total += len(data)
        sc = getattr(response, "server_content", None)
        if sc is None:
            return total
        for chunk in self._extract_audio(sc):
            total += len(chunk)
        for attr in ("input_transcription", "output_transcription"):
            node = getattr(sc, attr, None)
            text = getattr(node, "text", None) if node is not None else None
            if isinstance(text, str):
                total += len(text.encode("utf-8"))
        interim = getattr(sc, "interim_input_transcription", None)
        interim_text = getattr(interim, "text", None) if interim is not None else None
        if isinstance(interim_text, str):
            total += len(interim_text.encode("utf-8"))
        model_turn = getattr(sc, "model_turn", None)
        if model_turn is not None:
            for part in getattr(model_turn, "parts", None) or []:
                text = getattr(part, "text", None)
                if isinstance(text, str):
                    total += len(text.encode("utf-8"))
        return total

    def _as_audio_bytes(self, data: Any) -> bytes:
        if isinstance(data, (bytes, bytearray)) and data:
            return bytes(data)
        if isinstance(data, str) and data:
            try:
                return base64.b64decode(data)
            except Exception:
                return b""
        return b""

    def _ingest_server_content(self, turn: TurnResult, sc: Any) -> bool:
        input_delta = ""
        output_delta = ""
        if sc.input_transcription and sc.input_transcription.text:
            input_delta = sc.input_transcription.text
            turn.input_text += input_delta
        interim = getattr(sc, "interim_input_transcription", None)
        interim_text = getattr(interim, "text", None) if interim is not None else None
        if interim_text and not turn.input_text:
            turn.input_text = interim_text
        if sc.output_transcription and sc.output_transcription.text:
            output_delta = sc.output_transcription.text
            turn.output_text += output_delta
        model_turn = getattr(sc, "model_turn", None)
        if model_turn is not None and not output_delta:
            for part in getattr(model_turn, "parts", None) or []:
                text = getattr(part, "text", None)
                if text:
                    turn.output_text += text
        self._publish_audio(self._extract_audio(sc))
        if turn.output_text:
            self._publish_reply(turn.output_text)
        complete = bool(sc.turn_complete)
        generation_complete = bool(getattr(sc, "generation_complete", False))
        interrupted = bool(getattr(sc, "interrupted", False))
        # generation_complete means the text and audio bytes are ready.
        # turn_complete waits out playback we are already streaming.
        return complete or generation_complete or interrupted

    async def iter_audio(self) -> AsyncIterator[bytes]:
        while True:
            item = await self._audio_q.get()
            if item is None:
                return
            yield item

    async def iter_replies(self) -> AsyncIterator[str]:
        while True:
            item = await self._reply_q.get()
            if item is None:
                return
            yield item

    async def abandon_open_turn(self) -> None:
        """Close a Live socket that never finished the turn, then open a fresh one.

        A timeout leaves receive() blocked on the old socket. The next listen
        must not reuse it: Gemini then answers with 1011 and that listen goes deaf.
        """
        self._abandoning = True
        self._streaming = False
        self._activity_started = False
        self._resumption_handle = None
        if self._turn_done and not self._turn_done.is_set():
            self._turn_done.set()
        await self.finish_audio()
        task = self._receive_task
        self._receive_task = None
        self._close_reason = "timeout"
        await self._close_live_socket()
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._abandoning = False
        if self._cancelled or self._closed:
            return
        if await self._try_reconnect():
            self._start_receive_loop()

    async def finish_audio(self) -> None:
        if self._audio_closed:
            return
        self._audio_closed = True
        _put_sentinel(self._audio_q)
        _put_sentinel(self._reply_q)

    def discard_output(self) -> None:
        """Drop audio already queued for a turn the server is answering itself."""
        self._audio_closed = True
        self._audio_q = asyncio.Queue(maxsize=TURN_QUEUE_MAXSIZE)
        self._reply_q = asyncio.Queue(maxsize=TURN_QUEUE_MAXSIZE)
        self._last_reply = ""
        try:
            self._audio_q.put_nowait(None)
            self._reply_q.put_nowait(None)
        except Exception:
            pass

    async def reconnect_fresh(self) -> None:
        """Drop session memory so a discarded model reply cannot leak into the next turn."""
        self._resumption_handle = None
        self.discard_output()
        await self.close()
        await self.ensure_connected()

    async def _handle_response(self, response: Any) -> None:
        update = getattr(response, "session_resumption_update", None)
        if update is not None and getattr(update, "new_handle", None):
            self._resumption_handle = update.new_handle

        go_away = getattr(response, "go_away", None)
        if go_away is not None:
            await self._handle_go_away(go_away)
            return

        sc = getattr(response, "server_content", None)
        if sc is not None and (
            getattr(sc, "turn_complete", False)
            or getattr(sc, "generation_complete", False)
        ):
            self._last_turn_complete = True

        turn = self._turn or TurnResult()
        if sc is not None and self._ingest_server_content(turn, sc):
            self._turn = turn
            self._turn_done.set()

        tool_call = getattr(response, "tool_call", None)
        if tool_call and getattr(tool_call, "function_calls", None):
            for fc in tool_call.function_calls:
                args = fc.args if isinstance(fc.args, dict) else {}
                turn.function_calls.append(
                    FunctionCall(
                        name=fc.name,
                        arguments=args,
                        call_id=getattr(fc, "id", None),
                    )
                )
            log.info(
                "gemini.raw_tool_call input=%s output=%s tools=%s",
                turn.input_text,
                turn.output_text,
                [call.name for call in turn.function_calls],
            )
            self._turn = turn
            self._turn_done.set()

    async def _receive_loop(self) -> None:
        while not self._cancelled:
            token = self._session_token
            try:
                await self._ensure_session()
                assert self._session is not None
                token = self._session_token
                bound_session_id = self._conn_session_id
                bound_generation = self._conn_generation
                self._last_turn_complete = False
                async for response in self._session.receive():
                    try:
                        size, kind = self._response_chunk_meta(response)
                    except Exception:
                        size, kind = 0, "other"
                    _, current_gen = current_live_identity()
                    log.info(
                        "gemini.response_chunk session_id=%s generation=%s bytes=%s kind=%s",
                        bound_session_id or "-",
                        bound_generation,
                        size,
                        kind,
                    )
                    generation_stale = bound_generation != current_gen
                    if generation_stale:
                        log.info(
                            "STALE RESPONSE DISCARDED (gen %s, current gen %s)",
                            bound_generation,
                            current_gen,
                        )
                    if self._cancelled:
                        continue
                    if self._session_token != token:
                        break
                    if generation_stale:
                        continue
                    await self._handle_response(response)
                    if self._session is None or self._session_token != token:
                        break
                else:
                    if (
                        self._last_turn_complete
                        or (self._turn_done and self._turn_done.is_set())
                        or self._awaiting_tool_followup
                    ):
                        log.info("gemini.receive_turn_complete")
                        continue
                    log.warning("gemini.receive_ended")
                    if not await self._recover_after_transient(
                        RuntimeError("gemini receive ended"),
                        restart_receive=False,
                        dead_token=token,
                    ):
                        if self._turn is not None and not self._turn.transient_disconnect:
                            self._turn.error = "gemini disconnected"
                        if self._turn_done and not self._turn_done.is_set():
                            self._turn_done.set()
                        return
                    continue
            except asyncio.CancelledError:
                return
            except Exception as exc:
                if self._abandoning:
                    return
                if self._session_token != token:
                    continue
                if self._reconnect_lock.locked():
                    async with self._reconnect_lock:
                        pass
                    if self._session is not None and self._session_token != token:
                        continue
                log.warning("gemini.receive_failed error=%s", exc)
                if is_dead_resumption(exc):
                    self._resumption_handle = None
                if is_transient_gemini_error(exc) or is_dead_resumption(exc):
                    recovered = await self._recover_after_transient(
                        exc, restart_receive=False, dead_token=token
                    )
                    if recovered:
                        continue
                    if self._turn is not None:
                        self._turn.error = str(exc)
                        self._turn.transient_disconnect = False
                    if self._turn_done and not self._turn_done.is_set():
                        self._turn_done.set()
                    return
                if self._turn:
                    self._turn.error = str(exc)
                if self._turn_done and not self._turn_done.is_set():
                    self._turn_done.set()
                self._close_reason = _close_reason_for(exc)
                await self._close_live_socket()
                return
