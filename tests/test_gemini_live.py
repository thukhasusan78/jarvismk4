from __future__ import annotations

import array
import asyncio
import json
import time
import unittest
from types import SimpleNamespace

from voice.gemini_live import (
    GeminiLiveBrain,
    KeyPool,
    LiveSettings,
    TurnResult,
    begin_connection_attempt,
    current_live_identity,
    go_away_seconds,
    is_dead_resumption,
    is_timeout_error,
    is_transient_gemini_error,
)
from voice.session import VoiceSession
from voice.speech_gate import SpeechGate, SpeechGateConfig


class GeminiLiveHelpersTests(unittest.TestCase):
    def test_asyncio_timeout_is_a_timeout_on_py310(self) -> None:
        blank = asyncio.TimeoutError()
        self.assertEqual(str(blank), "")
        self.assertTrue(is_timeout_error(blank))
        self.assertTrue(is_timeout_error(TimeoutError()))

    def test_dead_resumption_detects_session_not_found(self) -> None:
        error = (
            "received 1008 (policy violation) BidiGenerateContent session not found; "
            "then sent 1008 (policy violation) BidiGenerateContent session not found"
        )
        self.assertTrue(is_dead_resumption(error))
        self.assertFalse(is_transient_gemini_error(error))

    def test_idle_abort_reconnects_without_dropping_the_handle(self) -> None:
        error = (
            "received 1008 (policy violation) The operation was aborted.; "
            "then sent 1008 (policy violation) The operation was aborted."
        )
        self.assertTrue(is_transient_gemini_error(error))
        self.assertFalse(is_dead_resumption(error))

    def test_go_away_seconds_reads_timedelta_and_text(self) -> None:
        class FakeDelta:
            def total_seconds(self) -> float:
                return 50.0

        self.assertEqual(go_away_seconds(SimpleNamespace(time_left=FakeDelta())), 50.0)
        self.assertEqual(go_away_seconds(SimpleNamespace(time_left="50s")), 50.0)
        self.assertEqual(
            go_away_seconds(SimpleNamespace(time_left=SimpleNamespace(seconds=4, nanos=0))),
            4.0,
        )
        self.assertIsNone(go_away_seconds(SimpleNamespace(time_left=None)))


class FakeLiveSocket:
    def __init__(self, mode: str = "ok") -> None:
        self.mode = mode
        self.pings = 0

    async def ping(self):
        self.pings += 1
        if self.mode == "busy":
            from websockets.exceptions import ConcurrencyError

            raise ConcurrencyError("ping already in flight")
        loop = asyncio.get_running_loop()
        waiter = loop.create_future()
        if self.mode == "ok":
            waiter.set_result(0.001)
        return waiter


class FakeLiveSession:
    def __init__(self, sock: FakeLiveSocket) -> None:
        self._ws = sock
        self.audio = 0

    async def send_realtime_input(self, **kwargs: object) -> None:
        self.audio += 1
        if kwargs.get("audio") is None:
            raise AssertionError("heartbeat must send audio")


class FakeClientSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, payload: str) -> None:
        self.sent.append(payload)

    async def close(self) -> None:
        return None


class FakeBrain:
    def __init__(self) -> None:
        self.has_session = True
        self.began = 0
        self.nudges = 0
        self.closed = 0
        self.abandoned = 0
        self.finished = 0

    async def begin_utterance(self) -> None:
        self.began += 1

    async def cancel(self) -> None:
        return None

    async def close(self) -> None:
        self.closed += 1

    async def abandon_open_turn(self) -> None:
        self.abandoned += 1

    async def finish_audio(self) -> None:
        self.finished += 1

    def nudge_keepalive(self) -> None:
        self.nudges += 1


class GeminiKeepaliveTests(unittest.TestCase):
    def _brain(self, sock: FakeLiveSocket) -> tuple[GeminiLiveBrain, FakeLiveSession]:
        brain = GeminiLiveBrain(LiveSettings(), KeyPool(["test-key"]), "sys")
        session = FakeLiveSession(sock)
        brain._session = session
        return brain, session

    def test_ping_waits_for_pong(self) -> None:
        brain, _session = self._brain(FakeLiveSocket())

        async def run() -> bool:
            try:
                return await brain._ping_session()
            finally:
                await brain.close()

        self.assertTrue(asyncio.run(run()))

    def test_overlapping_ping_counts_as_alive(self) -> None:
        brain, _session = self._brain(FakeLiveSocket("busy"))

        async def run() -> bool:
            try:
                return await brain._ping_session()
            finally:
                await brain.close()

        self.assertTrue(asyncio.run(run()))

    def test_keepalive_pings_idle_session(self) -> None:
        sock = FakeLiveSocket()
        brain, session = self._brain(sock)
        brain._keepalive_interval_s = 0.02

        async def run() -> None:
            brain._start_keepalive()
            deadline = asyncio.get_running_loop().time() + 2.0
            while (sock.pings < 1 or session.audio < 1) and (
                asyncio.get_running_loop().time() < deadline
            ):
                await asyncio.sleep(0.02)
            await brain.close()

        asyncio.run(run())
        self.assertGreaterEqual(sock.pings, 1)
        self.assertGreaterEqual(session.audio, 1)
        self.assertFalse(brain._model_turn_open())

    def test_keepalive_does_not_inject_silence_while_listening(self) -> None:
        brain, session = self._brain(FakeLiveSocket())
        brain._streaming = True

        async def run() -> int:
            await brain._keepalive_once()
            audio = session.audio
            brain._streaming = False
            await brain.close()
            return audio

        self.assertEqual(asyncio.run(run()), 0)

    def test_recover_keeps_a_socket_opened_by_someone_else(self) -> None:
        brain = self._brain(FakeLiveSocket())[0]
        brain._session = object()
        brain._session_token = "fresh"
        closed: list[str] = []

        async def close_socket() -> None:
            closed.append("close")
            brain._session = None

        async def connect() -> bool:
            raise AssertionError("fresh socket must not be reopened")

        brain._close_live_socket = close_socket  # type: ignore[method-assign]
        brain._connect_attempts = connect  # type: ignore[method-assign]

        async def run() -> None:
            ok = await brain._recover_after_transient(
                RuntimeError("stale close"),
                restart_receive=False,
                dead_token="stale",
            )
            self.assertTrue(ok)
            self.assertEqual(closed, [])
            brain._close_live_socket = GeminiLiveBrain._close_live_socket.__get__(
                brain, GeminiLiveBrain
            )
            brain._session = None
            brain._connect_cm = None
            await brain.close()

        asyncio.run(run())

    def test_keepalive_does_not_drop_an_open_model_turn(self) -> None:
        sock = FakeLiveSocket("timeout")
        brain, session = self._brain(sock)
        brain._keepalive_pong_timeout_s = 0.05
        brain._activity_started = True

        async def run() -> bool:
            await brain._keepalive_once()
            same = brain._session is session
            audio = session.audio
            await brain.close()
            return same and audio == 0

        self.assertTrue(asyncio.run(run()))
        self.assertGreaterEqual(sock.pings, 1)


class RecordingLiveSession:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def send_realtime_input(self, **kwargs: object) -> None:
        if "activity_start" in kwargs:
            self.events.append("start")
        elif "audio" in kwargs:
            self.events.append("audio")
        else:
            self.events.append("other")


class LiveTurnRecoveryTests(unittest.TestCase):
    def _brain(self) -> GeminiLiveBrain:
        return GeminiLiveBrain(LiveSettings(), KeyPool(["test-key"]), "sys")

    def test_activity_start_wraps_the_first_chunk(self) -> None:
        brain = self._brain()
        session = RecordingLiveSession()
        brain._session = session
        brain._streaming = True
        brain._turn = TurnResult()

        async def run() -> None:
            self.assertTrue(await brain._push_pcm_locked(b"\x00\x01" * 8))
            self.assertTrue(await brain._push_pcm_locked(b"\x02\x00" * 8))
            await brain.close()

        asyncio.run(run())
        self.assertEqual(session.events, ["audio", "start", "audio", "audio"])

    def test_generation_complete_keeps_native_audio(self) -> None:
        brain = self._brain()
        turn = TurnResult()
        audio = b"\x01\x02" * 8
        sc = SimpleNamespace(
            input_transcription=None,
            output_transcription=SimpleNamespace(text="hello"),
            model_turn=SimpleNamespace(
                parts=[
                    SimpleNamespace(
                        inline_data=SimpleNamespace(data=audio),
                        text=None,
                    )
                ]
            ),
            turn_complete=False,
            generation_complete=True,
            interrupted=False,
        )
        self.assertTrue(brain._ingest_server_content(turn, sc))
        self.assertEqual(brain._audio_q.get_nowait(), audio)
        self.assertEqual(turn.output_text, "hello")

    def test_disconnect_while_listening_keeps_the_mic_open(self) -> None:
        brain = self._brain()
        brain._streaming = True
        brain._activity_started = True
        brain._resumption_handle = "stale-handle"
        brain._turn = TurnResult()
        brain._turn_done = asyncio.Event()
        closed: list[str] = []

        async def close_socket() -> None:
            closed.append("close")
            brain._session = None

        async def reconnect() -> bool:
            brain._session = object()
            return True

        brain._close_live_socket = close_socket  # type: ignore[method-assign]
        brain._connect_attempts = reconnect  # type: ignore[method-assign]

        async def run() -> None:
            recovered = await brain._recover_after_transient(
                RuntimeError(
                    "received 1011 (internal error) Internal error encountered."
                ),
                restart_receive=False,
            )
            self.assertTrue(recovered)
            self.assertEqual(closed, ["close"])
            self.assertIsNone(brain._resumption_handle)
            self.assertTrue(brain._streaming)
            self.assertFalse(brain._activity_started)
            self.assertIsNotNone(brain._turn)
            assert brain._turn is not None
            self.assertFalse(brain._turn.transient_disconnect)
            self.assertFalse(brain._turn_done.is_set())
            brain._streaming = False
            await brain.close()

        asyncio.run(run())

    def test_timeout_tells_the_desk_to_ask_again_on_a_fresh_socket(self) -> None:
        async def run() -> None:
            ws = FakeClientSocket()
            session = VoiceSession(ws)
            brain = FakeBrain()
            session._brain = brain
            session.generation = 4
            session._listen_wanted = True
            try:
                again = await session._complete_result(
                    4,
                    TurnResult(error="gemini timeout"),
                    user_text="",
                )
            finally:
                session._brain = None
                await session.close()
            messages = [json.loads(item) for item in ws.sent]
            self.assertTrue(again)
            self.assertEqual(brain.abandoned, 1)
            self.assertEqual(brain.finished, 0)
            self.assertEqual(
                messages[0]["message"],
                "The line dropped for a moment. Ask again, ဆရာ.",
            )
            self.assertTrue(all(item.get("type") != "turn_done" for item in messages))
            self.assertNotIn("brain failed", messages[0]["message"].lower())

        asyncio.run(run())


class HubGateTests(unittest.TestCase):
    def test_text_waits_until_the_mic_releases(self) -> None:
        async def run() -> list[str]:
            from voice.hub import LiveHub

            hub = LiveHub()
            order: list[str] = []

            async def mic() -> None:
                await hub.mic_enter()
                order.append("mic")
                await asyncio.sleep(0.05)
                order.append("mic-out")
                await hub.mic_exit()

            async def text() -> None:
                await asyncio.sleep(0.01)
                await hub.text_enter()
                order.append("text")
                await hub.text_exit()

            await asyncio.gather(mic(), text())
            return order

        self.assertEqual(
            asyncio.run(run()),
            ["mic", "mic-out", "text"],
        )


class VoicePrewarmTests(unittest.TestCase):
    def test_client_ping_gets_pong(self) -> None:
        async def run() -> None:
            ws = FakeClientSocket()
            session = VoiceSession(ws)
            brain = FakeBrain()
            session._brain = brain
            try:
                await session._on_text(json.dumps({"type": "ping"}))
            finally:
                session._brain = None
                await session.close()
            self.assertEqual(json.loads(ws.sent[-1])["type"], "pong")
            self.assertEqual(brain.nudges, 1)

        asyncio.run(run())

    def test_warm_listen_begins_before_returning(self) -> None:
        async def run() -> None:
            ws = FakeClientSocket()
            session = VoiceSession(ws)
            brain = FakeBrain()
            session._brain = brain
            ready = asyncio.get_running_loop().create_future()
            ready.set_result(None)
            session._preconnect_task = ready
            try:
                await session._start_listen()
                self.assertTrue(session._brain_ready)
                self.assertEqual(brain.began, 1)
                self.assertEqual(json.loads(ws.sent[-1])["type"], "listening")
            finally:
                session._brain = None
                await session.close()

        asyncio.run(run())

    def test_cold_listen_waits_to_say_listening(self) -> None:
        async def run() -> None:
            ws = FakeClientSocket()
            session = VoiceSession(ws)
            brain = FakeBrain()
            session._brain = brain
            pending = asyncio.get_running_loop().create_future()
            session._preconnect_task = pending
            try:
                await session._start_listen()
                self.assertFalse(session._brain_ready)
                self.assertEqual(json.loads(ws.sent[-1])["type"], "starting")
                pending.set_result(None)
                assert session._begin_task is not None
                await session._begin_task
                self.assertTrue(session._brain_ready)
                self.assertEqual(json.loads(ws.sent[-1])["type"], "listening")
            finally:
                session._brain = None
                await session.close()

        asyncio.run(run())

    def test_short_gap_keeps_silero_state(self) -> None:
        class CountingScorer:
            def __init__(self) -> None:
                self.resets = 0

            def reset(self) -> None:
                self.resets += 1

            def score(self, pcm: bytes) -> float:
                return 0.0

        async def run() -> None:
            ws = FakeClientSocket()
            session = VoiceSession(ws)
            brain = FakeBrain()
            session._brain = brain
            scorer = CountingScorer()
            session._gate = SpeechGate(SpeechGateConfig(), scorer)
            ready = asyncio.get_running_loop().create_future()
            ready.set_result(None)
            session._preconnect_task = ready
            try:
                await session._start_listen()
                self.assertEqual(scorer.resets, 2)
                self.assertEqual(session._gate._elapsed_ms, 0.0)

                session._last_uplink = time.monotonic() - 1.0
                await session._start_listen()
                self.assertEqual(scorer.resets, 2)
                self.assertGreaterEqual(
                    session._gate._elapsed_ms, session._gate.config.warmup_ms
                )

                session._last_uplink = time.monotonic() - 6.0
                await session._start_listen()
                self.assertEqual(scorer.resets, 3)
                self.assertEqual(session._gate._elapsed_ms, 0.0)
            finally:
                session._brain = None
                await session.close()

        asyncio.run(run())

    def test_close_kills_the_live_session(self) -> None:
        async def run() -> None:
            ws = FakeClientSocket()
            session = VoiceSession(ws)
            brain = FakeBrain()
            session._brain = brain
            await session.close()
            self.assertEqual(brain.closed, 1)

        asyncio.run(run())


class VoiceEndpointTests(unittest.TestCase):
    def test_hangover_ends_before_the_brain_is_ready(self) -> None:
        async def run() -> list[str]:
            ws = FakeClientSocket()
            session = VoiceSession(ws)
            session.state = "listening"
            session._listen_started_at = time.monotonic()
            session._brain_ready = False
            reasons: list[str] = []

            async def end(reason: str) -> None:
                reasons.append(reason)

            class StubGate:
                ever_opened = True
                last_reason = "below_threshold"
                accepted_chunks = 4

                def process(self, _packet: bytes) -> list[bytes]:
                    self.last_reason = "hangover_expired"
                    return []

            session._end_utterance_now = end  # type: ignore[method-assign]
            session._gate = StubGate()  # type: ignore[assignment]
            try:
                await session._on_binary(b"\x00\x00" * 960)
            finally:
                session._brain = None
                await session.close()
            return reasons

        self.assertEqual(asyncio.run(run()), ["hangover_expired"])

    def test_eight_seconds_without_speech_stays_listening(self) -> None:
        async def run() -> list[str]:
            ws = FakeClientSocket()
            session = VoiceSession(ws)
            session.state = "listening"
            session._listen_wanted = True
            session._listen_started_at = time.monotonic() - 9
            try:
                await session._on_binary(b"\x00\x00" * 960)
                kinds = [
                    json.loads(item).get("type")
                    for item in ws.sent
                    if item.startswith("{")
                ]
                age = time.monotonic() - session._listen_started_at
                stayed = session.state
            finally:
                session._brain = None
                await session.close()
            self.assertEqual(stayed, "listening")
            self.assertLess(age, 2)
            self.assertNotIn("thinking", kinds)
            return kinds

        asyncio.run(run())

    def test_speech_burst_thinks_then_listens_again(self) -> None:
        async def run() -> list[str]:
            from voice.speech_gate import create_speech_gate

            ws = FakeClientSocket()
            session = VoiceSession(ws)
            brain = TurnBrain()
            session._brain = brain
            session._listen_wanted = True
            session._gate = create_speech_gate(backend="energy")
            ready = asyncio.get_running_loop().create_future()
            ready.set_result(None)
            session._preconnect_task = ready

            async def complete(_generation: int, _result: TurnResult, user_text: str = "") -> bool:
                return True

            session._complete_result = complete  # type: ignore[method-assign]
            try:
                await session._start_listen()
                loud = array.array("h", [8000] * 960).tobytes()
                quiet = array.array("h", [0] * 960).tobytes()
                for _ in range(6):
                    await session._on_binary(loud)
                for _ in range(16):
                    await session._on_binary(quiet)
                turn = session._turn_task
                if turn is not None:
                    await turn
                kinds = [json.loads(item).get("type") for item in ws.sent]
            finally:
                session._brain = None
                await session.close()
            return kinds

        kinds = asyncio.run(run())
        self.assertIn("thinking", kinds)
        think_at = kinds.index("thinking")
        self.assertIn("listening", kinds[think_at + 1 :])
        self.assertTrue(all(kind != "error" for kind in kinds))


class TurnBrain(FakeBrain):
    def __init__(self) -> None:
        super().__init__()
        self.activity_started = False
        self._turn = None
        self._turn = TurnResult(input_text="what time is it", output_text="noon")

    async def push_pcm(self, _pcm: bytes) -> bool:
        self.activity_started = True
        return True

    async def send_activity_end(self) -> bool:
        return True

    async def wait_turn_result(self) -> TurnResult:
        return self._turn or TurnResult(
            input_text="what time is it", output_text="noon"
        )

    async def iter_audio(self):
        if False:
            yield b""

    async def iter_replies(self):
        if False:
            yield ""

    async def reconnect_fresh(self) -> None:
        return None


class HangingReceiveSession:
    def __init__(self, first: object) -> None:
        self.first = first

    async def receive(self):
        yield self.first
        await asyncio.Event().wait()


def _audio_response(audio: bytes) -> SimpleNamespace:
    sc = SimpleNamespace(
        input_transcription=None,
        output_transcription=None,
        model_turn=SimpleNamespace(
            parts=[
                SimpleNamespace(
                    inline_data=SimpleNamespace(data=audio),
                    text=None,
                )
            ]
        ),
        turn_complete=False,
        generation_complete=False,
        interrupted=False,
    )
    return SimpleNamespace(
        server_content=sc,
        go_away=None,
        tool_call=None,
        session_resumption_update=None,
        data=None,
    )


class ConnectionGenerationTests(unittest.TestCase):
    def test_each_attempt_advances_generation(self) -> None:
        first_id, first_gen = begin_connection_attempt()
        second_id, second_gen = begin_connection_attempt()
        self.assertEqual(len(first_id), 8)
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(second_gen, first_gen + 1)
        self.assertEqual(current_live_identity(), (second_id, second_gen))

    def test_pcm_send_uses_the_generation_at_send_time(self) -> None:
        brain = GeminiLiveBrain(LiveSettings(), KeyPool(["test-key"]), "sys")
        brain._session = RecordingLiveSession()
        brain._streaming = True
        brain._turn = TurnResult()
        begin_connection_attempt()
        session_id, generation = begin_connection_attempt()

        async def run() -> None:
            self.assertTrue(await brain._push_pcm_locked(b"\x00\x01" * 8))
            await brain.close()

        with self.assertLogs("voice.gemini_live", level="INFO") as captured:
            asyncio.run(run())
        sent = [line for line in captured.output if "gemini.audio_sent" in line]
        self.assertGreaterEqual(len(sent), 1)
        for line in sent:
            self.assertIn(f"session_id={session_id}", line)
            self.assertIn(f"generation={generation}", line)
            self.assertIn("kind=speech", line)

    def test_stale_response_is_logged_and_not_played(self) -> None:
        brain = GeminiLiveBrain(LiveSettings(), KeyPool(["test-key"]), "sys")
        audio = b"\x01\x02" * 8
        session_id, generation = begin_connection_attempt()
        brain._session = HangingReceiveSession(_audio_response(audio))
        brain._session_token = object()
        brain._conn_session_id = session_id
        brain._conn_generation = generation
        brain._turn = TurnResult()
        brain._turn_done = asyncio.Event()
        _, current = begin_connection_attempt()
        self.assertNotEqual(generation, current)

        async def run() -> None:
            brain._start_receive_loop()
            await asyncio.sleep(0.05)
            task = brain._receive_task
            assert task is not None
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            brain._session = None
            brain._connect_cm = None
            await brain.close()

        with self.assertLogs("voice.gemini_live", level="INFO") as captured:
            asyncio.run(run())
        text = "\n".join(captured.output)
        self.assertIn(
            f"STALE RESPONSE DISCARDED (gen {generation}, current gen {current})",
            text,
        )
        self.assertIn("gemini.response_chunk", text)
        queued: list[object] = []
        while True:
            try:
                queued.append(brain._audio_q.get_nowait())
            except asyncio.QueueEmpty:
                break
        self.assertNotIn(audio, queued)


if __name__ == "__main__":
    unittest.main()
