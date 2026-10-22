from __future__ import annotations

import array
import math
import unittest
from pathlib import Path

from voice.speech_gate import (
    EnergyScorer,
    SileroScorer,
    SpeechGate,
    SpeechGateConfig,
    create_speech_gate,
    pcm16le_rms,
)


FRAME_SAMPLES = 960


def pcm_amp(amplitude: int, samples: int = FRAME_SAMPLES) -> bytes:
    value = max(-32767, min(32767, int(amplitude)))
    return array.array("h", [value] * samples).tobytes()


# Same loudness band as the flat warmup fixture. The spread is several
# times warmup_energy_variance_min; a constant 4000 frame is inside it.
VARYING_LEVELS = [2200, 4000, 5800, 2500, 5200, 3100, 4900, 2800]


def varying_loud_frames(n_frames: int = 12) -> list[bytes]:
    return [pcm_amp(VARYING_LEVELS[index % len(VARYING_LEVELS)]) for index in range(n_frames)]


def syllable_frames(n_frames: int = 12) -> list[bytes]:
    """Loud 4 Hz amplitude contour. Cold Silero still has to accept this."""
    frames: list[bytes] = []
    for frame_index in range(n_frames):
        hops = []
        for hop in range(3):
            index = frame_index * 3 + hop
            shape = 0.5 - 0.5 * math.cos(2 * math.pi * index / 12.0)
            hops.append(pcm_amp(int(1500 + 7500 * shape), samples=320))
        frames.append(b"".join(hops))
    return frames


class ZeroScorer:
    def __init__(self) -> None:
        self.resets = 0

    def reset(self) -> None:
        self.resets += 1

    def score(self, pcm: bytes) -> float:
        return 0.0


class FixedScorer:
    def __init__(self, prob: float) -> None:
        self.prob = prob

    def reset(self) -> None:
        return None

    def score(self, pcm: bytes) -> float:
        return self.prob


class SpeechGateTests(unittest.TestCase):
    def energy_gate(self) -> SpeechGate:
        return create_speech_gate(backend="energy")

    def test_factory_uses_silero(self) -> None:
        gate = create_speech_gate()
        self.assertIsInstance(gate.scorer, SileroScorer)

    def test_silero_silence_stays_closed(self) -> None:
        gate = create_speech_gate(backend="silero")
        quiet = pcm_amp(0)
        for _ in range(20):
            self.assertEqual(gate.process(quiet), [])
            self.assertFalse(gate.is_open)
            self.assertLess(gate.last_prob, 0.5)
        self.assertFalse(gate.ever_opened)
        self.assertEqual(gate.last_reason, "below_threshold")

    def test_silero_speech_opens_gate(self) -> None:
        gate = create_speech_gate(backend="silero")
        pcm = (Path(__file__).with_name("speech_16k.pcm")).read_bytes()
        frame = FRAME_SAMPLES * 2
        opened = False
        for offset in range(0, len(pcm) - frame + 1, frame):
            gate.process(pcm[offset : offset + frame])
            if gate.ever_opened:
                opened = True
                break
        self.assertTrue(opened)
        self.assertGreaterEqual(gate.last_prob, 0.5)

    def test_silence_stays_closed(self) -> None:
        gate = self.energy_gate()
        quiet = pcm_amp(0)
        for _ in range(20):
            emitted = gate.process(quiet)
            self.assertEqual(emitted, [])
            self.assertFalse(gate.is_open)
        self.assertFalse(gate.ever_opened)
        self.assertEqual(gate.last_reason, "below_threshold")

    def test_speech_opens_after_180ms(self) -> None:
        gate = self.energy_gate()
        loud = pcm_amp(2000)
        self.assertGreaterEqual(pcm16le_rms(loud), 500.0)
        first = gate.process(loud)
        self.assertEqual(first, [])
        self.assertFalse(gate.is_open)
        self.assertEqual(gate.last_reason, "attack_wait")
        second = gate.process(loud)
        self.assertEqual(second, [])
        self.assertFalse(gate.is_open)
        opened = gate.process(loud)
        self.assertTrue(gate.is_open)
        self.assertTrue(gate.ever_opened)
        self.assertEqual(gate.last_reason, "attack_open")
        self.assertEqual(len(opened), 3)
        self.assertEqual(opened[-1], loud)
        self.assertEqual(gate.last_preroll_frames, 2)
        self.assertEqual(gate.last_preroll_ms, 120.0)

    def test_open_prepends_audio_from_before_the_attack(self) -> None:
        gate = self.energy_gate()
        quiet = pcm_amp(0)
        loud = pcm_amp(2000)
        marker = pcm_amp(400)
        for _ in range(8):
            self.assertEqual(gate.process(marker), [])
        for _ in range(5):
            self.assertEqual(gate.process(quiet), [])
        opened = None
        for _ in range(3):
            opened = gate.process(loud)
        self.assertIsNotNone(opened)
        assert opened is not None
        self.assertTrue(gate.is_open)
        self.assertEqual(opened, [quiet] * 5 + [loud] * 3)
        self.assertEqual(gate.last_preroll_frames, 7)
        self.assertEqual(gate.last_preroll_ms, 420.0)
        self.assertNotIn(marker, opened)

    def test_next_utterance_does_not_inherit_the_previous_tail(self) -> None:
        gate = self.energy_gate()
        first = pcm_amp(2500)
        quiet = pcm_amp(0)
        second = pcm_amp(1800)
        for _ in range(3):
            gate.process(first)
        self.assertTrue(gate.is_open)
        # 20 quiet 60ms frames = the 1200ms endpointing silence.
        for _ in range(20):
            gate.process(quiet)
        self.assertFalse(gate.is_open)
        opened = None
        for _ in range(3):
            opened = gate.process(second)
        self.assertIsNotNone(opened)
        assert opened is not None
        self.assertNotIn(first, opened)
        self.assertEqual(opened[-3:], [second, second, second])

    def test_hangover_expires_once_after_1200ms_nonspeech(self) -> None:
        gate = self.energy_gate()
        loud = pcm_amp(2000)
        quiet = pcm_amp(0)
        for _ in range(3):
            gate.process(loud)
        self.assertTrue(gate.is_open)
        expired = 0
        for index in range(30):
            emitted = gate.process(quiet)
            if gate.last_reason == "hangover_expired":
                expired += 1
                self.assertEqual(emitted, [])
                self.assertFalse(gate.is_open)
                self.assertEqual(index, 19)
                break
            self.assertEqual(emitted, [quiet])
            self.assertEqual(gate.last_reason, "hangover")
            self.assertTrue(gate.is_open)
        self.assertEqual(expired, 1)
        later = gate.process(quiet)
        self.assertEqual(later, [])
        self.assertEqual(gate.last_reason, "below_threshold")
        self.assertEqual(expired, 1)

    def test_warmup_rms_opens_before_lstm_is_warm(self) -> None:
        gate = SpeechGate(SpeechGateConfig(), ZeroScorer())
        opened = None
        for frame in varying_loud_frames():
            self.assertGreaterEqual(pcm16le_rms(frame), 1800.0)
            opened = gate.process(frame)
            self.assertEqual(gate.last_prob, 0.0)
            if gate.is_open:
                break
        self.assertTrue(gate.is_open)
        self.assertEqual(gate.last_reason, "attack_open_warmup")
        self.assertGreaterEqual(
            gate.last_energy_span, gate.config.warmup_energy_variance_min
        )
        self.assertIsNotNone(opened)
        assert opened is not None
        self.assertGreaterEqual(len(opened), 1)
        self.assertLess(gate._elapsed_ms, gate.config.warmup_ms)

    def test_warmup_rejects_steady_noise(self) -> None:
        gate = SpeechGate(SpeechGateConfig(), ZeroScorer())
        loud = pcm_amp(4000)
        self.assertIn(4000, VARYING_LEVELS)
        self.assertGreaterEqual(pcm16le_rms(loud), 1800.0)
        for _ in range(16):
            self.assertEqual(gate.process(loud), [])
            self.assertFalse(gate.is_open)
        self.assertFalse(gate.ever_opened)
        self.assertEqual(gate.last_reason, "warmup_flat")
        self.assertEqual(gate.last_prob, 0.0)
        self.assertLess(gate.last_energy_span, gate.config.warmup_energy_variance_min)

    def test_warmup_steady_band_does_not_open(self) -> None:
        """The 1.32 s failure: RMS wanders inside 1100–2500 with no syllable."""
        gate = SpeechGate(SpeechGateConfig(), FixedScorer(0.0))
        levels = [1900, 2200, 2500, 2000, 2100, 1800]
        for index in range(22):
            emitted = gate.process(pcm_amp(levels[index % len(levels)]))
            self.assertEqual(emitted, [])
            self.assertFalse(gate.is_open)
        self.assertFalse(gate.ever_opened)
        self.assertEqual(gate.last_reason, "warmup_flat")
        self.assertEqual(gate.last_prob, 0.0)
        self.assertLess(gate.last_energy_span, gate.config.warmup_energy_variance_min)

    def test_warmup_shape_does_not_apply_after_the_window(self) -> None:
        gate = SpeechGate(SpeechGateConfig(), FixedScorer(0.07))
        for _ in range(25):
            gate.process(pcm_amp(0))
        self.assertGreaterEqual(gate._elapsed_ms, gate.config.warmup_ms)
        for frame in syllable_frames(16):
            self.assertEqual(gate.process(frame), [])
            self.assertFalse(gate.is_open)
        self.assertFalse(gate.ever_opened)
        self.assertLess(gate.last_prob, 0.5)

    def test_keep_warm_leaves_the_lstm_and_skips_the_energy_fallback(self) -> None:
        scorer = ZeroScorer()
        gate = SpeechGate(SpeechGateConfig(), scorer)
        self.assertEqual(scorer.resets, 1)
        gate.reset(reset_scorer=False, keep_warm=True)
        self.assertEqual(scorer.resets, 1)
        self.assertGreaterEqual(gate._elapsed_ms, gate.config.warmup_ms)
        for frame in varying_loud_frames():
            self.assertEqual(gate.process(frame), [])
            self.assertFalse(gate.is_open)
        self.assertFalse(gate.ever_opened)
        gate.reset()
        self.assertEqual(scorer.resets, 2)
        self.assertEqual(gate._elapsed_ms, 0.0)

    def test_hangover_does_not_stay_open_on_energy_alone(self) -> None:
        gate = SpeechGate(SpeechGateConfig(), ZeroScorer())
        for frame in syllable_frames():
            gate.process(frame)
            if gate.is_open:
                break
        self.assertTrue(gate.is_open)
        self.assertEqual(gate.last_reason, "attack_open_warmup")
        loud = pcm_amp(4000)
        for _ in range(13):
            emitted = gate.process(loud)
            self.assertEqual(emitted, [loud])
            self.assertEqual(gate.last_reason, "hangover")
            self.assertTrue(gate.is_open)
        closed = gate.process(loud)
        self.assertEqual(closed, [])
        self.assertFalse(gate.is_open)
        self.assertEqual(gate.last_reason, "hangover_expired")
        self.assertGreaterEqual(gate.last_rms, 1800.0)
        self.assertEqual(gate.last_prob, 0.0)


if __name__ == "__main__":
    unittest.main()
