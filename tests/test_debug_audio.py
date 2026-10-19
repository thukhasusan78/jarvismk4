from __future__ import annotations

import asyncio
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

from voice import debug_audio


class DebugAudioDumpTests(unittest.TestCase):
    def test_endpoint_writes_the_tapped_pcm_at_the_uplink_rate(self) -> None:
        first = b"\x01\x00" * 160
        second = b"\x02\x00" * 160

        async def run(directory: Path) -> None:
            debug_audio.DUMP_DIR = directory
            debug_audio.begin_listen_cycle(enabled=True)
            debug_audio.note_speech(first, 1)
            debug_audio.note_speech(second, 1)
            debug_audio.finish_listen_cycle()
            while debug_audio._pending:
                await asyncio.sleep(0.01)

        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            asyncio.run(run(directory))
            wavs = list(directory.glob("*.wav"))
            self.assertEqual(len(wavs), 1)
            self.assertIn("_gen1", wavs[0].name)
            with wave.open(str(wavs[0]), "rb") as handle:
                self.assertEqual(handle.getnchannels(), 1)
                self.assertEqual(handle.getsampwidth(), 2)
                self.assertEqual(handle.getframerate(), 16000)
                self.assertEqual(handle.readframes(handle.getnframes()), first + second)

    def test_disabled_tap_writes_nothing(self) -> None:
        async def run(directory: Path) -> None:
            debug_audio.DUMP_DIR = directory
            debug_audio.begin_listen_cycle(enabled=False)
            debug_audio.note_speech(b"\x00\x01" * 80, 4)
            debug_audio.finish_listen_cycle()
            while debug_audio._pending:
                await asyncio.sleep(0.01)

        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            asyncio.run(run(directory))
            self.assertEqual(list(directory.glob("*.wav")), [])

    def tearDown(self) -> None:
        debug_audio._armed = False
        debug_audio._chunks = []
        debug_audio._generation = 0
        debug_audio.DUMP_DIR = debug_audio.ROOT / "debug_audio"
