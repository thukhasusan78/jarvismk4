from __future__ import annotations

import array
import logging
import math
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

UPLINK_FRAME_MS = 60.0
SILERO_WINDOW_SAMPLES = 512
SILERO_WINDOW_BYTES = SILERO_WINDOW_SAMPLES * 2
# This ONNX export reads 64 samples of prior audio in front of every hop.
# A bare 512-sample chunk keeps speech probability near zero.
SILERO_CONTEXT_SAMPLES = 64

VAD_BACKEND = "silero"
VAD_SPEECH_THRESHOLD = 0.5
VAD_MIN_SPEECH_MS = 180.0
VAD_MIN_SILENCE_MS = 800.0
VAD_PREROLL_CHUNKS = 3
# Audio before the open decision. The attack window is 180 ms and used to
# fill the whole preroll, so the onset ("What") never reached Gemini.
# The captured turn kept 2.40 s of a phrase closer to 2.5–2.6 s.
VAD_PREROLL_MS = 300.0
VAD_ENERGY_SPEECH_RMS = 500.0
VAD_WARMUP_MS = 1500.0
VAD_WARMUP_ENERGY_RMS = 1800.0
# 5- and 6-frame windows overlapped the steady dump and real speech.
# 8 frames (480 ms) separated them. See SpeechGateConfig.warmup_ms.
VAD_WARMUP_RMS_FRAMES = 8
# Peak-to-trough of those frame RMS values, not a statistical variance.
# Steady dump max 1147; level-matched speech min 1677.
VAD_WARMUP_ENERGY_VARIANCE_MIN = 1400.0


def pcm16le_rms(pcm: bytes) -> float:
    """RMS energy of little-endian int16 mono PCM."""
    if not pcm:
        return 0.0
    if len(pcm) % 2 != 0:
        pcm = pcm[:-1]
    if not pcm:
        return 0.0
    samples = array.array("h")
    samples.frombytes(pcm)
    if not samples:
        return 0.0
    return math.sqrt(sum(int(x) * int(x) for x in samples) / len(samples))


def rms_span(levels: list[float]) -> float:
    """Peak-to-trough range of a short frame-RMS window."""
    if len(levels) < 2:
        return 0.0
    return max(levels) - min(levels)


@dataclass(frozen=True)
class SpeechGateConfig:
    speech_threshold: float = 0.5
    min_speech_ms: float = 180.0
    min_silence_ms: float = 800.0
    preroll_chunks: int = 3
    preroll_ms: float = VAD_PREROLL_MS
    frame_ms: float = UPLINK_FRAME_MS
    energy_speech_rms: float = 500.0
    # Silero's LSTM is cold for about a second after a full reset and scores
    # real speech near 0.07, so this fallback must not require
    # speech_prob >= speech_threshold. Loudness alone is not enough either:
    # a 1.32 s steady capture (RMS 1257–2653, 2026-10-18_171547) opened on
    # warmup_energy with speech_prob at 0. The branch therefore also requires
    # the peak-to-trough of the last 8 frame RMS values
    # (warmup_energy_variance_min). On that session's captures the steady
    # dump stayed at or below 1147 and level-matched speech stayed at or
    # above 1677. Windows of 5–6 frames overlapped, so 8 is the shortest cut
    # that separated them. Fans, traffic, and other rooms sit at different
    # flatness; retune warmup_energy_variance_min in config.json rather than
    # deleting this check. Hangover stays Silero-only.
    warmup_ms: float = 1500.0
    warmup_energy_rms: float = 1800.0
    warmup_energy_variance_min: float = VAD_WARMUP_ENERGY_VARIANCE_MIN


class SpeechScorer(Protocol):
    def reset(self) -> None: ...

    def score(self, pcm: bytes) -> float: ...


class EnergyScorer:
    """Last-resort scorer: high RMS counts as speech. Cannot reject fans."""

    def __init__(self, speech_rms: float = 500.0) -> None:
        self.speech_rms = speech_rms

    def reset(self) -> None:
        return None

    def score(self, pcm: bytes) -> float:
        return 1.0 if pcm16le_rms(pcm) >= self.speech_rms else 0.0


class SileroScorer:
    """Streaming Silero VAD over 512-sample hops at 16 kHz.

    Runs the official Silero ONNX model on ONNX Runtime's CPU provider.
    That provider selects SSE kernels at runtime, so it runs on Goldmont
    (SSE4.1, no AVX). The ggml build shipped with pysilero-vad does not:
    it executes AVX instructions and crashes this CPU.

    Each hop is prefixed with the previous 64 samples. This export returns
    a near-zero speech probability when that context is missing.
    """

    _MODEL = Path(__file__).with_name("silero_vad.onnx")
    _shared: dict[str, object] = {}

    def __init__(self, model_path: Path | None = None) -> None:
        import numpy as np

        path = str(Path(model_path) if model_path is not None else self._MODEL)
        self._np = np
        self._session = self._session_for(path)
        self._sr = np.array(16000, dtype=np.int64)
        self._buf = bytearray()
        self.reset()

    @classmethod
    def _session_for(cls, path: str) -> object:
        cached = cls._shared.get(path)
        if cached is not None:
            return cached
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        session = ort.InferenceSession(
            path,
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        cls._shared[path] = session
        return session

    def reset(self) -> None:
        self._state = self._np.zeros((2, 1, 128), dtype=self._np.float32)
        self._context = self._np.zeros((1, SILERO_CONTEXT_SAMPLES), dtype=self._np.float32)
        self._buf.clear()

    def clear_buffer(self) -> None:
        """Drop leftover PCM without wiping LSTM state."""
        self._buf.clear()

    def score(self, pcm: bytes) -> float:
        if pcm:
            self._buf.extend(pcm)
        max_prob = 0.0
        scored = False
        np = self._np
        while len(self._buf) >= SILERO_WINDOW_BYTES:
            raw = bytes(self._buf[:SILERO_WINDOW_BYTES])
            del self._buf[:SILERO_WINDOW_BYTES]
            samples = array.array("h")
            samples.frombytes(raw)
            chunk = np.asarray(samples, dtype=np.float32).reshape(1, -1) / 32768.0
            audio = np.concatenate([self._context, chunk], axis=1)
            out, self._state = self._session.run(
                None,
                {"input": audio, "state": self._state, "sr": self._sr},
            )
            self._context = np.ascontiguousarray(audio[:, -SILERO_CONTEXT_SAMPLES:])
            max_prob = max(max_prob, float(out[0, 0]))
            scored = True
        return max_prob if scored else 0.0


class SpeechGate:
    """Attack / hangover endpointing on top of a per-frame speech probability.

    Close only after ``min_silence_ms`` of consecutive *non-speech* (Silero),
    not after a dip in RMS.
    """

    def __init__(self, config: SpeechGateConfig, scorer: SpeechScorer) -> None:
        self.config = config
        self.scorer = scorer
        self.reset()

    def reset(self, *, reset_scorer: bool = True, keep_warm: bool = False) -> None:
        """Clear endpointing state.

        ``keep_warm`` leaves the scorer's LSTM alone and does not re-arm the
        cold-start energy fallback. A full reset zeros that state and starts
        the warmup clock over.
        """
        cfg = self.config
        if reset_scorer:
            self.scorer.reset()
            keep_warm = False
        else:
            clearer = getattr(self.scorer, "clear_buffer", None)
            if callable(clearer):
                clearer()
        self._open = False
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self._elapsed_ms = cfg.warmup_ms if keep_warm else 0.0
        self._preroll: deque[bytes] = deque(maxlen=self._history_chunks())
        self.last_preroll_frames = 0
        self.last_preroll_ms = 0.0
        self.accepted_chunks = 0
        self.dropped_chunks = 0
        self.ever_opened = False
        self.last_rms = 0.0
        self.last_prob = 0.0
        self.last_energy_span = 0.0
        self._rms_window: deque[float] = deque(maxlen=VAD_WARMUP_RMS_FRAMES)
        self.last_threshold = cfg.speech_threshold
        self.last_decision = "DROPPED"
        self.last_reason = "reset"
        self.noise_floor = 0.0
        self._last_log_key: tuple[str, str] | None = None

    @property
    def is_open(self) -> bool:
        return self._open

    def _history_chunks(self) -> int:
        """Frames kept while closed: onset lead-in plus the attack window.

        The attack frames used to occupy the entire preroll, which evicted
        the speech that arrived before the open decision.
        """
        cfg = self.config
        frame_ms = cfg.frame_ms if cfg.frame_ms > 0 else UPLINK_FRAME_MS
        lead = max(0, int(round(cfg.preroll_ms / frame_ms)))
        attack = max(1, int(round(cfg.min_speech_ms / frame_ms)))
        return max(lead + attack, cfg.preroll_chunks, 1)

    def _note_frame_rms(self, rms: float) -> None:
        self._rms_window.append(rms)
        if len(self._rms_window) < VAD_WARMUP_RMS_FRAMES:
            self.last_energy_span = 0.0
            return
        self.last_energy_span = rms_span(list(self._rms_window))

    def _warmup_is_varying(self) -> bool:
        if len(self._rms_window) < VAD_WARMUP_RMS_FRAMES:
            return False
        return self.last_energy_span >= self.config.warmup_energy_variance_min

    def _log_chunk(self, *, decision: str, reason: str, emitted: int) -> None:
        self.last_decision = decision
        self.last_reason = reason
        key = (decision, reason)
        changed = key != self._last_log_key
        self._last_log_key = key
        payload = (
            "decision=%s reason=%s rms=%.1f speech_prob=%.3f energy_span=%.0f open=%s "
            "speech_ms=%.1f silence_ms=%.1f emitted=%s"
        )
        args = (
            decision,
            reason,
            self.last_rms,
            self.last_prob,
            self.last_energy_span,
            self._open,
            self._speech_ms,
            self._silence_ms,
            emitted,
        )
        if changed:
            log.info(payload, *args)
        else:
            log.debug(payload, *args)

    def _is_speech(self, pcm: bytes) -> tuple[bool, str]:
        """Silero decision, with a loud-and-varying fallback while the LSTM warms up.

        The fallback does not look at speech_prob. Cold Silero scores real
        speech near 0.07, so requiring 0.5 here would make the first second
        deaf. It requires the recent frame RMS to move, so a steady level in
        the same band cannot open the gate. Hangover / close stays
        Silero-only so a fan cannot hold the gate open.
        """
        cfg = self.config
        self.last_prob = self.scorer.score(pcm)
        silero_speech = self.last_prob >= cfg.speech_threshold
        warming = (not self._open) and self._elapsed_ms < cfg.warmup_ms
        if silero_speech:
            return True, "silero"
        if warming and self.last_rms >= cfg.warmup_energy_rms:
            if self._warmup_is_varying():
                return True, "warmup_energy"
            return False, "warmup_flat"
        return False, "below_threshold"

    def process(self, pcm: bytes) -> list[bytes]:
        cfg = self.config
        self.last_rms = pcm16le_rms(pcm)
        self.last_threshold = cfg.speech_threshold
        if not pcm:
            self.last_prob = 0.0
            self._log_chunk(decision="DROPPED", reason="empty", emitted=0)
            return []

        frame_ms = cfg.frame_ms
        self._elapsed_ms += frame_ms
        self._note_frame_rms(self.last_rms)
        is_speech, speech_kind = self._is_speech(pcm)

        if not self._open:
            # Always retain the recent window, speech or not. maxlen drops
            # anything older, so a gap between utterances cannot carry the
            # previous phrase into the next open.
            self._preroll.append(pcm)
            if is_speech:
                self._speech_ms += frame_ms
                if self._speech_ms >= cfg.min_speech_ms:
                    self._open = True
                    self.ever_opened = True
                    self._silence_ms = 0.0
                    self._speech_ms = 0.0
                    emitted = list(self._preroll) if self._preroll else [pcm]
                    self._preroll.clear()
                    prepended = max(0, len(emitted) - 1)
                    self.last_preroll_frames = prepended
                    self.last_preroll_ms = prepended * frame_ms
                    self.accepted_chunks += len(emitted)
                    reason = (
                        "attack_open_warmup"
                        if speech_kind == "warmup_energy"
                        else "attack_open"
                    )
                    log.info(
                        "voice.preroll_applied frames=%s ms=%.0f",
                        prepended,
                        self.last_preroll_ms,
                    )
                    self._log_chunk(
                        decision="ACCEPTED", reason=reason, emitted=len(emitted)
                    )
                    return emitted
                self._log_chunk(decision="DROPPED", reason="attack_wait", emitted=0)
                return []
            self._speech_ms = 0.0
            self.dropped_chunks += 1
            self._log_chunk(decision="DROPPED", reason=speech_kind, emitted=0)
            return []

        if is_speech:
            self._silence_ms = 0.0
            self.accepted_chunks += 1
            self._log_chunk(decision="ACCEPTED", reason="speech", emitted=1)
            return [pcm]

        self._silence_ms += frame_ms
        if self._silence_ms < cfg.min_silence_ms:
            self.accepted_chunks += 1
            self._log_chunk(decision="ACCEPTED", reason="hangover", emitted=1)
            return [pcm]

        self._open = False
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        # Drop the utterance that just ended. The next open must not
        # inherit its tail; only frames captured after this close qualify.
        self._preroll.clear()
        self.dropped_chunks += 1
        self._log_chunk(decision="DROPPED", reason="hangover_expired", emitted=0)
        return []


def create_speech_gate(
    *,
    backend: str = VAD_BACKEND,
    speech_threshold: float = VAD_SPEECH_THRESHOLD,
    min_speech_ms: float = VAD_MIN_SPEECH_MS,
    min_silence_ms: float = VAD_MIN_SILENCE_MS,
    preroll_chunks: int = VAD_PREROLL_CHUNKS,
    preroll_ms: float = VAD_PREROLL_MS,
    energy_speech_rms: float = VAD_ENERGY_SPEECH_RMS,
    warmup_ms: float = VAD_WARMUP_MS,
    warmup_energy_rms: float = VAD_WARMUP_ENERGY_RMS,
    warmup_energy_variance_min: float = VAD_WARMUP_ENERGY_VARIANCE_MIN,
) -> SpeechGate:
    config = SpeechGateConfig(
        speech_threshold=speech_threshold,
        min_speech_ms=min_speech_ms,
        min_silence_ms=min_silence_ms,
        preroll_chunks=preroll_chunks,
        preroll_ms=preroll_ms,
        energy_speech_rms=energy_speech_rms,
        warmup_ms=warmup_ms,
        warmup_energy_rms=warmup_energy_rms,
        warmup_energy_variance_min=warmup_energy_variance_min,
    )
    if backend == "energy":
        scorer: SpeechScorer = EnergyScorer(energy_speech_rms)
    elif backend == "silero":
        scorer = SileroScorer()
        log.info("voice.vad backend=silero")
    else:
        raise ValueError(f"unknown vad backend: {backend}")
    return SpeechGate(config, scorer)
