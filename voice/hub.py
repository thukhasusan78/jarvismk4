"""One Gemini Live brain for the desk and the Telegram pocket."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from voice.gemini_live import OUTPUT_PCM_RATE, GeminiLiveBrain, KeyPool

log = logging.getLogger(__name__)

# A gate wait must fail visibly instead of hanging a turn forever when the
# other side never releases the brain.
GATE_WAIT_TIMEOUT_S = 120.0


async def _gate_wait(cv: asyncio.Condition, who: str) -> None:
    try:
        await asyncio.wait_for(cv.wait(), timeout=GATE_WAIT_TIMEOUT_S)
    except (asyncio.TimeoutError, TimeoutError) as exc:
        log.warning("hub.gate_wait_timeout who=%s", who)
        raise TimeoutError(f"hub {who} gate wait timed out") from exc


@dataclass
class LiveReply:
    text: str
    pcm: bytes = b""
    sample_rate: int = OUTPUT_PCM_RATE


class LiveHub:
    """Serializes mic turns and pocket turns on a single Live session."""

    def __init__(self) -> None:
        self._brain: GeminiLiveBrain | None = None
        self._setup: asyncio.Lock | None = None
        self._setup_loop: asyncio.AbstractEventLoop | None = None
        self._cv: asyncio.Condition | None = None
        self._gate_loop: asyncio.AbstractEventLoop | None = None
        self._mic = 0
        self._text = 0
        self._tools_ready = False
        self._pocket_on = False
        self._instruction = ""

    def current(self) -> GeminiLiveBrain | None:
        return self._brain

    async def _connect_brain(self) -> None:
        brain = self._brain
        if brain is not None and not await brain.ensure_connected():
            # ensure_connected no longer hides failure: without a session
            # the state recorded below would describe a dead brain.
            log.warning("hub.brain_not_connected")

    def _condition(self) -> asyncio.Condition:
        loop = asyncio.get_running_loop()
        if self._cv is None or self._gate_loop is not loop:
            self._cv = asyncio.Condition()
            self._gate_loop = loop
            self._mic = 0
            self._text = 0
        return self._cv

    def _setup_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._setup is None or self._setup_loop is not loop:
            self._setup = asyncio.Lock()
            self._setup_loop = loop
        return self._setup

    async def mic_enter(self) -> None:
        cv = self._condition()
        async with cv:
            while self._text:
                await _gate_wait(cv, "mic")
            self._mic += 1

    async def mic_exit(self) -> None:
        cv = self._condition()
        async with cv:
            if self._mic:
                self._mic -= 1
            cv.notify_all()

    async def text_enter(self) -> None:
        cv = self._condition()
        async with cv:
            while self._mic or self._text:
                await _gate_wait(cv, "text")
            self._text += 1

    async def text_exit(self) -> None:
        cv = self._condition()
        async with cv:
            if self._text:
                self._text -= 1
            cv.notify_all()

    async def ensure_brain(self) -> GeminiLiveBrain:
        import server
        from google_account import google_connected
        from telegram_bot import token_configured
        from voice.session import live_settings, live_system_instruction, live_tool_declarations

        async with self._setup_lock():
            tools_ready = google_connected()
            pocket_on = token_configured()
            instruction = live_system_instruction(tools_ready=tools_ready)
            decls = live_tool_declarations(tools_ready=tools_ready)
            if self._brain is None:
                keys = server.api_keys(server.load_config())
                if not keys:
                    raise RuntimeError("gemini-missing")
                self._brain = GeminiLiveBrain(live_settings(), KeyPool(keys), instruction)
                await self._brain.configure_tools(decls)
                await self._connect_brain()
                self._tools_ready = tools_ready
                self._pocket_on = pocket_on
                self._instruction = instruction
                return self._brain
            changed = (
                tools_ready != self._tools_ready
                or pocket_on != self._pocket_on
                or instruction != self._instruction
            )
            if changed:
                self._brain.set_system_instruction(instruction)
                await self._brain.configure_tools(decls)
                await self._connect_brain()
                self._tools_ready = tools_ready
                self._pocket_on = pocket_on
                self._instruction = instruction
            elif not self._brain.has_session:
                self._brain.set_system_instruction(instruction)
                await self._connect_brain()
            return self._brain

    async def ask(self, text: str) -> LiveReply:
        from voice.session import apply_tools

        cleaned = " ".join(str(text or "").split()).strip()
        if not cleaned:
            return LiveReply(text="The message was empty.")
        import server

        server.set_task_source("pocket")
        server.begin_browser_turn(cleaned)
        gate = server.browser_handoff_followup()
        if gate["action"] == "wait":
            return LiveReply(text=gate["reply"])
        await _yield_idle_mic()
        try:
            await self.text_enter()
        except TimeoutError:
            return LiveReply(
                text="The brain stayed busy with the desk for too long. "
                "Ask again in a moment, ဆရာ."
            )
        try:
            await _yield_idle_mic()
            try:
                brain = await self.ensure_brain()
            except RuntimeError as exc:
                return LiveReply(text=_missing_text(exc))
            from telegram_bot import telegram_user_text

            spoken = telegram_user_text(cleaned)
            if gate["note"]:
                spoken = gate["note"] + "\n\n" + spoken
            result = await brain.send_user_turn(spoken)
            if result.transient_disconnect:
                return LiveReply(text="The line dropped for a moment. Ask again, ဆရာ.")
            if result.function_calls:
                result = await apply_tools(brain, result)
            reply = " ".join((result.output_text or "").split()).strip()
            if result.error and not reply:
                reply = f"The brain failed, ဆရာ. ({result.error})"
            pcm = await _collect_pcm(brain)
            if reply and not result.error:
                import server

                server.remember("user", cleaned)
                server.remember("assistant", reply)
            log.info(
                "pocket.live_turn chars=%s audio_bytes=%s",
                len(reply),
                len(pcm),
            )
            return LiveReply(text=reply or "The model returned no text.", pcm=pcm)
        finally:
            await self.text_exit()
            await _resume_mic()

    async def close(self) -> None:
        brain = self._brain
        self._brain = None
        if brain is None:
            return
        try:
            await brain.close()
        except Exception:
            pass


async def _yield_idle_mic() -> None:
    from voice.server import current_session

    session = current_session()
    if session is None:
        return
    try:
        await session.yield_to_pocket()
    except Exception as exc:
        log.warning("pocket.yield_failed error=%s", exc.__class__.__name__)


async def _resume_mic() -> None:
    from voice.server import current_session

    session = current_session()
    if session is None:
        return
    try:
        await session.resume_after_pocket()
    except Exception as exc:
        log.warning("pocket.resume_failed error=%s", exc.__class__.__name__)


def _missing_text(exc: BaseException) -> str:
    if str(exc) == "gemini-missing":
        return (
            "No Gemini API key is configured. Paste comma-separated keys into "
            "gemini_api_keys in config.json at the project root."
        )
    return f"The brain failed, ဆရာ. ({exc.__class__.__name__})."


async def _collect_pcm(brain: GeminiLiveBrain) -> bytes:
    already = brain._audio_closed
    if not already:
        await brain.finish_audio()
    chunks: list[bytes] = []
    if already:
        while True:
            try:
                item = brain._audio_q.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is None:
                break
            chunks.append(item)
        return b"".join(chunks)
    async for chunk in brain.iter_audio():
        chunks.append(chunk)
    return b"".join(chunks)


_hub: LiveHub | None = None


def get_hub() -> LiveHub:
    global _hub
    if _hub is None:
        _hub = LiveHub()
    return _hub


def set_hub(hub: LiveHub | None) -> None:
    global _hub
    _hub = hub
