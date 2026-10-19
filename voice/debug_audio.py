"""Tap PCM already handed to Gemini and write a WAV after the utterance ends.

Off unless config.json sets debug_dump_audio. The send loop only keeps a
reference to bytes it already has. The file is written after voice.endpoint.
"""

from __future__ import annotations

import asyncio
import logging
import wave
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
DUMP_DIR = ROOT / "debug_audio"
# mic-worklet.js resamples to TARGET_RATE 16000 and the Live blob is
# audio/pcm;rate=16000. The WAV header uses this same rate.
UPLINK_PCM_RATE = 16000

_armed = False
_chunks: list[bytes] = []
_generation = 0
_pending: set[asyncio.Task] = set()


def debug_dump_enabled() -> bool:
    try:
        import server

        raw = server.load_config().get("debug_dump_audio", False)
    except Exception:
        return False
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes"}
    return raw is True


def begin_listen_cycle(*, enabled: bool | None = None) -> None:
    """Open one capture window for a voice.listen_start cycle."""
    global _armed, _chunks, _generation
    _armed = debug_dump_enabled() if enabled is None else enabled
    _chunks = []
    _generation = 0


def note_speech(pcm: bytes, generation: int) -> None:
    """Remember one speech payload passed to send. Heartbeats are not tapped."""
    global _generation
    if not _armed or not pcm:
        return
    if not _chunks:
        _generation = generation
    elif generation != _generation:
        _generation = generation
    _chunks.append(pcm if isinstance(pcm, bytes) else bytes(pcm))


def finish_listen_cycle() -> None:
    """Snapshot the utterance and write it off the send loop."""
    global _armed, _chunks, _generation
    chunks = _chunks
    generation = _generation
    _chunks = []
    _generation = 0
    _armed = False
    if not chunks:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _write_wav(chunks, generation)
        return
    task = loop.create_task(asyncio.to_thread(_write_wav, chunks, generation))
    _pending.add(task)
    task.add_done_callback(_pending.discard)


def _write_wav(chunks: list[bytes], generation: int) -> None:
    pcm = b"".join(chunks)
    if not pcm:
        return
    try:
        DUMP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        path = DUMP_DIR / f"{stamp}_gen{generation}.wav"
        if path.exists():
            suffix = 2
            while True:
                candidate = DUMP_DIR / f"{stamp}_gen{generation}_{suffix}.wav"
                if not candidate.exists():
                    path = candidate
                    break
                suffix += 1
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(UPLINK_PCM_RATE)
            handle.writeframes(pcm)
        try:
            shown = path.relative_to(ROOT).as_posix()
        except ValueError:
            shown = str(path)
        duration = len(pcm) / float(UPLINK_PCM_RATE * 2)
        log.info(
            "debug.audio_dumped path=%s bytes=%s duration_s=%.2f",
            shown,
            len(pcm),
            duration,
        )
    except Exception as exc:
        log.warning("debug.audio_dump_failed error=%s", exc)
