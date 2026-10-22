from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

log = logging.getLogger(__name__)

VOICE_HOST = "127.0.0.1"
VOICE_PORT = 4702

_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None
_stop: asyncio.Event | None = None
_current: Any = None
_swap_lock: asyncio.Lock | None = None
_preload: asyncio.Future | None = None


async def _kill_session(session: Any) -> None:
    if session is None:
        return
    try:
        await asyncio.wait_for(session.close(), timeout=2.5)
    except Exception as exc:
        log.warning("voice.stale_session_kill_failed error=%s", exc)


async def _handler(ws: Any) -> None:
    """The newest browser socket owns the desk. No path or query handshake."""
    global _current, _swap_lock
    from voice.session import VoiceSession

    if _swap_lock is None:
        _swap_lock = asyncio.Lock()
    # The page connects on load. VoiceSession builds a Silero gate, and doing
    # that inline while the preload still holds the import lock freezes the loop.
    try:
        await asyncio.shield(_ensure_preload())
    except Exception as exc:
        # Not fatal here (the session retries the gate build), but a VAD
        # preload failure must be diagnosable instead of silent.
        log.warning("voice.preload_failed error=%s", exc)
    async with _swap_lock:
        previous = _current
        session = VoiceSession(ws)
        _current = session
    if previous is not None:
        log.info("voice.replaced")
        try:
            await asyncio.wait_for(previous.send_json({"type": "replaced"}), timeout=1)
        except Exception:
            pass
        asyncio.create_task(_kill_session(previous))
    try:
        await session.run()
    finally:
        if _current is session:
            _current = None


def _preload_voice() -> None:
    """Load ONNX and the Gemini SDK off the accept loop.

    Both imports block their thread. Running them inside the handshake
    freezes the loop, and the browser then aborts and reports the server down.
    """
    from voice.speech_gate import create_speech_gate

    create_speech_gate()
    import google.genai  # noqa: F401
    from google.genai import types  # noqa: F401

    log.info("voice.preloaded")


def _ensure_preload() -> asyncio.Future:
    global _preload
    if _preload is None:
        _preload = asyncio.get_running_loop().run_in_executor(None, _preload_voice)
    return _preload


async def _preload_and_warm() -> None:
    """Load ONNX after the port is open so the first mic click can connect."""
    try:
        await _ensure_preload()
    except Exception as exc:
        log.warning("voice.preload_failed error=%s", exc)
    if _stop is not None and _stop.is_set():
        return
    await _warm_hub()


async def _serve() -> None:
    global _stop, _preload
    import websockets

    _stop = asyncio.Event()
    _preload = None
    attempt = 0
    # A restarted server.py can race the old process for the port. Giving
    # up leaves the viewer up with the voice link dead until the next restart.
    while not _stop.is_set():
        attempt += 1
        try:
            async with websockets.serve(
                _handler,
                VOICE_HOST,
                VOICE_PORT,
                max_size=2**22,
                ping_interval=20,
                ping_timeout=20,
            ):
                log.info("voice.listening at ws://%s:%s/live", VOICE_HOST, VOICE_PORT)
                asyncio.create_task(_preload_and_warm())
                assert _stop is not None
                await _stop.wait()
                await _close_hub()
                return
        except OSError as exc:
            if attempt <= 3 or attempt % 30 == 0:
                log.warning("voice.bind_retry attempt=%s error=%s", attempt, exc)
            try:
                await asyncio.wait_for(_stop.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass


async def _warm_hub() -> None:
    from voice.hub import get_hub

    try:
        await get_hub().ensure_brain()
        log.info("voice.hub_ready")
    except Exception as exc:
        log.warning("voice.hub_failed error=%s", exc.__class__.__name__)


async def _close_hub() -> None:
    from voice.hub import get_hub

    try:
        await get_hub().close()
    except Exception as exc:
        log.warning("voice.hub_close_failed error=%s", exc.__class__.__name__)


def current_session() -> Any:
    return _current


def ask_live(text: str) -> Any:
    """Run one turn on the shared Live brain. Called from the Telegram thread."""
    from voice.hub import get_hub

    loop = _loop
    if loop is None or not loop.is_running():
        raise RuntimeError("voice-down")
    future = asyncio.run_coroutine_threadsafe(get_hub().ask(text), loop)
    try:
        return future.result(timeout=45)
    except Exception:
        future.cancel()
        raise


def _thread_main() -> None:
    global _loop
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    try:
        _loop.run_until_complete(_serve())
    except Exception as exc:
        log.warning("voice.server_failed error=%s", exc)
    finally:
        try:
            _loop.run_until_complete(_loop.shutdown_asyncgens())
        except Exception:
            pass
        _loop.close()
        _loop = None


def start_voice_server() -> threading.Thread:
    global _thread
    if _thread and _thread.is_alive():
        return _thread
    _thread = threading.Thread(target=_thread_main, name="voice-live", daemon=True)
    _thread.start()
    return _thread


def stop_voice_server() -> None:
    loop = _loop
    stop = _stop
    if loop is None or stop is None:
        return

    def _request_stop() -> None:
        if not stop.is_set():
            stop.set()

    loop.call_soon_threadsafe(_request_stop)
    thread = _thread
    if thread is not None:
        thread.join(timeout=3)
