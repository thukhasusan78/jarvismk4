"""Pairing, refusal, and Telegram dispatch. No network."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pocket
import server
import speech_note
from pocket import CODE_ALPHABET, CODE_LENGTH, MESSAGE_MAX_AGE_S, PocketStore
from telegram_bot import caption_and_rest, deliver_to_paired, handle_update, telegram_user_text, text_chunks


class FakeApi:
    def __init__(self) -> None:
        self.texts: list[tuple[int, str]] = []
        self.voices: list[tuple[int, bytes, str]] = []
        self.actions: list[tuple[int, str]] = []
        self.downloads: dict[str, tuple[bytes, str]] = {}
        self.download_calls = 0

    def send_text(self, chat_id: int, text: str) -> None:
        self.texts.append((chat_id, text))

    def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        self.actions.append((chat_id, action))

    def send_voice(self, chat_id: int, ogg: bytes, caption: str = "") -> None:
        self.voices.append((chat_id, ogg, caption))

    def download_voice(self, file_id: str) -> tuple[bytes, str]:
        self.download_calls += 1
        return self.downloads[file_id]


class PocketTests(unittest.TestCase):
    def setUp(self) -> None:
        self._now = 1_700_000_000.0
        self.tmp = tempfile.TemporaryDirectory()
        self.store = PocketStore(
            Path(self.tmp.name) / "state.json",
            clock=lambda: self._now,
        )
        pocket.set_store(self.store)

    def tearDown(self) -> None:
        pocket.set_store(None)
        self.tmp.cleanup()

    def test_mint_is_single_use_strict_and_expires(self) -> None:
        first = self.store.mint()
        second = self.store.mint()
        self.assertEqual(len(second), CODE_LENGTH)
        self.assertTrue(all(ch in CODE_ALPHABET for ch in second))
        self.assertNotEqual(first, second)
        self.assertFalse(self.store.redeem(1, first))
        self.assertFalse(self.store.redeem(1, second.lower()))
        self.assertFalse(self.store.redeem(1, second + " please"))
        self.assertFalse(self.store.redeem(1, "code " + second))
        self.assertTrue(self.store.redeem(7, f"  {second}  "))
        self.assertTrue(self.store.paired(7))
        self.assertFalse(self.store.paired(8))
        self.assertFalse(self.store.redeem(8, second))
        self.assertIsNone(self.store.pending_code())

        code = self.store.mint()
        self._now += MESSAGE_MAX_AGE_S
        self.assertFalse(self.store.redeem(3, code))
        self.assertFalse(self.store.paired(3))
        self.assertIsNone(self.store.pending_code())

    def test_pairing_persists_and_offset_survives_reload(self) -> None:
        code = self.store.mint()
        self.assertTrue(self.store.redeem(9, code))
        self.store.set_offset(40)
        loaded = PocketStore(self.store.path, clock=lambda: self._now)
        self.assertTrue(loaded.paired(9))
        self.assertFalse(loaded.redeem(10, code))
        self.assertEqual(loaded.offset(), 40)

    def test_desk_phrase_mints_and_chat_does_not_call_the_brain(self) -> None:
        self.assertFalse(pocket.is_desk_command("what is my pocket code"))
        self.assertFalse(pocket.is_desk_command("Jarvis, pocket code and the weather"))
        self.assertTrue(pocket.is_desk_command("Jarvis, pocket code"))
        self.assertTrue(pocket.is_desk_command("jarvis pocket code."))
        self.assertTrue(pocket.is_voice_desk_command("hey Jarvis, pocket code please"))
        self.assertFalse(pocket.is_voice_desk_command("Jarvis, pocket code and the weather"))
        self.assertTrue(pocket.might_be_desk_command("Jarvis"))
        self.assertTrue(pocket.might_be_desk_command("hey jarvis pocket"))
        self.assertFalse(pocket.might_be_desk_command("Jarvis what time is it"))
        payload = server.chat_payload("Jarvis, pocket code")
        self.assertEqual(payload["nodes"], [])
        code = self.store.pending_code()
        self.assertIsNotNone(code)
        self.assertIn(f"Pocket code {code}", payload["answer"])
        self.assertEqual(server.pocket_history_snapshot(), [])

    def test_unpaired_text_and_voice_never_reach_the_brain(self) -> None:
        api = FakeApi()
        brains: list[str] = []
        handle_update(
            self._message(4, text="what is on my calendar"),
            api=api,
            brain=self._brain(brains),
            transcribe=self._explode,
            speak=self._ogg,
            now=lambda: self._now,
        )
        handle_update(
            self._message(4, voice={"file_id": "abc"}),
            api=api,
            brain=self._brain(brains),
            transcribe=self._explode,
            speak=self._ogg,
            now=lambda: self._now,
        )
        self.assertEqual(brains, [])
        self.assertEqual(api.download_calls, 0)
        self.assertEqual(api.voices, [])
        self.assertTrue(api.texts)
        self.assertIn("locked", api.texts[0][1])

    def test_strict_code_pairs_once(self) -> None:
        code = self.store.mint()
        api = FakeApi()
        brains: list[str] = []
        handle_update(
            self._message(4, text=code + " now"),
            api=api,
            brain=self._brain(brains),
            transcribe=self._explode,
            speak=self._ogg,
            now=lambda: self._now,
        )
        self.assertFalse(self.store.paired(4))
        handle_update(
            self._message(4, text=code),
            api=api,
            brain=self._brain(brains),
            transcribe=self._explode,
            speak=self._ogg,
            now=lambda: self._now,
        )
        self.assertTrue(self.store.paired(4))
        self.assertEqual(brains, [])
        self.assertIn("Paired", api.texts[-1][1])
        handle_update(
            self._message(5, text=code),
            api=api,
            brain=self._brain(brains),
            transcribe=self._explode,
            speak=self._ogg,
            now=lambda: self._now,
        )
        self.assertFalse(self.store.paired(5))
        self.assertEqual(brains, [])

    def test_paired_text_returns_text_and_voice(self) -> None:
        self.store.mint()
        self.assertTrue(self.store.redeem(4, self.store.pending_code() or ""))
        api = FakeApi()
        brains: list[str] = []
        spoken: list[str] = []

        def speak(text: str) -> bytes:
            spoken.append(text)
            return b"OggSvoice"

        handle_update(
            self._message(4, text="What is on my calendar today?"),
            api=api,
            brain=self._brain(brains, answer="You have one event."),
            transcribe=self._explode,
            speak=speak,
            now=lambda: self._now,
        )
        self.assertEqual(brains, ["What is on my calendar today?"])
        self.assertEqual(api.texts, [])
        self.assertEqual(spoken, ["You have one event."])
        self.assertEqual(api.voices, [(4, b"OggSvoice", "You have one event.")])
        self.assertIn((4, "typing"), api.actions)

    def test_voice_note_is_transcribed_then_answered(self) -> None:
        code = self.store.mint()
        self.assertTrue(self.store.redeem(4, code))
        api = FakeApi()
        api.downloads["note"] = (b"ogg-bytes", "audio/ogg")
        brains: list[str] = []
        heard: list[tuple[bytes, str]] = []

        def transcribe(data: bytes, mime: str) -> str:
            heard.append((data, mime))
            return "Remind me tomorrow at 4 to water the plants"

        handle_update(
            self._message(4, voice={"file_id": "note"}),
            api=api,
            brain=self._brain(brains, answer="Added. Tomorrow at 4."),
            transcribe=transcribe,
            speak=self._ogg,
            now=lambda: self._now,
        )
        self.assertEqual(heard, [(b"ogg-bytes", "audio/ogg")])
        self.assertEqual(brains, ["Remind me tomorrow at 4 to water the plants"])
        self.assertEqual(api.texts, [])
        self.assertEqual(api.voices[0], (4, b"OggS", "Added. Tomorrow at 4."))
        self.assertIn((4, "typing"), api.actions)

    def test_stale_group_and_voice_failure(self) -> None:
        code = self.store.mint()
        api = FakeApi()
        handle_update(
            {
                "message": {
                    "date": int(self._now - MESSAGE_MAX_AGE_S - 5),
                    "chat": {"id": 4, "type": "private"},
                    "text": code,
                }
            },
            api=api,
            brain=self._explode,
            transcribe=self._explode,
            speak=self._explode,
            now=lambda: self._now,
        )
        handle_update(
            {
                "message": {
                    "date": int(self._now),
                    "chat": {"id": 4, "type": "group"},
                    "text": code,
                }
            },
            api=api,
            brain=self._explode,
            transcribe=self._explode,
            speak=self._explode,
            now=lambda: self._now,
        )
        self.assertFalse(self.store.paired(4))
        self.assertEqual(self.store.pending_code(), code)
        self.assertEqual(api.texts, [])

        self.assertTrue(self.store.redeem(4, code))

        def boom(_text: str) -> bytes:
            raise RuntimeError("speech down")

        handle_update(
            self._message(4, text="hello"),
            api=api,
            brain=self._brain([], answer="Hi."),
            transcribe=self._explode,
            speak=boom,
            now=lambda: self._now,
        )
        self.assertEqual(api.texts[0], (4, "Hi."))
        self.assertIn("voice note failed", api.texts[1][1])
        self.assertEqual(api.voices, [])

    def test_speech_spells_the_code_and_ogg_is_opus(self) -> None:
        spoken = speech_note.for_speech("Pocket code ABCDEFGH. It works once.")
        self.assertIn("A B C D E F G H", spoken)
        self.assertNotIn("ABCDEFGH", spoken)
        ogg = speech_note.pcm_to_ogg(b"\x00\x01" * 2400, 24000)
        self.assertTrue(ogg.startswith(b"OggS"))
        chunks = text_chunks("a\n" * 5000)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 4000 for chunk in chunks))
        caption, rest = caption_and_rest("word " * 400)
        self.assertLessEqual(len(caption), 1024)
        self.assertTrue(rest)
        self.assertFalse(caption.endswith(" "))

    def test_live_audio_is_one_message(self) -> None:
        self.store.mint()
        self.assertTrue(self.store.redeem(4, self.store.pending_code() or ""))
        api = FakeApi()

        class LiveReply:
            text = "Same live voice."
            pcm = b"\x00\x01" * 2400
            sample_rate = 24000

        def speak(_text: str) -> bytes:
            raise AssertionError("separate tts")

        handle_update(
            self._message(4, text="hello"),
            api=api,
            brain=lambda _text: LiveReply(),
            transcribe=self._explode,
            speak=speak,
            now=lambda: self._now,
        )
        self.assertEqual(api.texts, [])
        self.assertEqual(len(api.voices), 1)
        self.assertEqual(api.voices[0][0], 4)
        self.assertTrue(api.voices[0][1].startswith(b"OggS"))
        self.assertEqual(api.voices[0][2], "Same live voice.")

    def test_long_caption_keeps_the_overflow(self) -> None:
        self.store.mint()
        self.assertTrue(self.store.redeem(4, self.store.pending_code() or ""))
        api = FakeApi()
        answer = "word " * 400
        handle_update(
            self._message(4, text="hello"),
            api=api,
            brain=self._brain([], answer=answer.strip()),
            transcribe=self._explode,
            speak=self._ogg,
            now=lambda: self._now,
        )
        self.assertEqual(len(api.voices), 1)
        caption = api.voices[0][2]
        self.assertLessEqual(len(caption), 1024)
        self.assertTrue(api.texts)
        self.assertIn(api.texts[0][1], answer)

    def test_unpaired_delivery_does_not_call_telegram(self) -> None:
        note = telegram_user_text("What is on my calendar?")
        self.assertIn("arrived on Telegram", note)
        self.assertIn("What is on my calendar?", note)
        self.assertIn("local_file_send", note)
        self.assertIn("drive_send", note)
        self.assertIn("browser_open", note)
        self.assertIn("browser_read", note)
        self.assertIn("browser_click", note)
        self.assertIn("browser_type", note)
        self.assertIn("browser_go_back", note)
        self.assertIn("browser_list_tabs", note)
        self.assertIn("browser_attach_active_tab", note)
        self.assertIn("browser_request_handoff", note)
        self.assertIn("cannot browse the web from here", note)
        self.assertIn("cannot send files here", note)
        self.assertIn("paired", deliver_to_paired("Tomorrow at 4.").lower())
        self.assertEqual(self.store.paired_ids(), [])

    def _message(self, chat_id: int, **extra: object) -> dict:
        payload: dict = {
            "date": int(self._now),
            "chat": {"id": chat_id, "type": "private"},
        }
        payload.update(extra)
        return {"message": payload}

    def _brain(self, sink: list[str], answer: str = "ok"):
        def call(text: str) -> str:
            sink.append(text)
            return answer

        return call

    @staticmethod
    def _ogg(_text: str) -> bytes:
        return b"OggS"

    @staticmethod
    def _explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("unpaired traffic reached the brain")


if __name__ == "__main__":
    unittest.main()
