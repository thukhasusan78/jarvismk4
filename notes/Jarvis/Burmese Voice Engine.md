# Burmese Voice Engine

Spoken replies use edge-tts, voice `my-MM-ThihaNeural`. Audio is MP3, decoded in the browser. This replaced a Gemini preview TTS voice named Enceladus after invalid-key errors, empty audio, and text-instead-of-speech responses.

`VOICE_MODEL` and `VOICE_NAME` in config are leftovers from that Gemini attempt and are unused. Edge TTS needs no extra API key. The VPS must reach `speech.platform.bing.com`. See [[Voice HUD]] and [[Voice Tool Loop]].
