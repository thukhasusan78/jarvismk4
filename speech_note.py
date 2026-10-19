"""Transcribe Telegram voice notes and speak replies with the configured Gemini voice."""

from __future__ import annotations

import base64
import re
import shutil
import subprocess
import sys

from pocket import CODE_ALPHABET

TTS_MODELS = (
    "gemini-2.5-flash-preview-tts",
    "gemini-3.1-flash-tts-preview",
)
TRANSCRIBE_MODEL = "gemini-2.5-flash"
SPEECH_LIMIT = 1500
CODE_RE = re.compile(rf"\bPocket code ([{CODE_ALPHABET}]{{8}})\b")
TRANSCRIBE_SYSTEM = (
    "You transcribe a voice note. Reply with only the spoken words, "
    "in the language that was spoken. Do not answer the speaker. "
    "Do not add commentary. If there is no speech, reply with an empty string."
)


def for_speech(text: str) -> str:
    cleaned = re.sub(r"[*_#]+", "", str(text or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    match = CODE_RE.search(cleaned)
    if match:
        spelled = " ".join(match.group(1))
        cleaned = cleaned.replace(match.group(0), f"Pocket code {spelled}", 1)
    if len(cleaned) <= SPEECH_LIMIT:
        return cleaned
    clipped = cleaned[:SPEECH_LIMIT].rsplit(" ", 1)[0].strip()
    return f"{clipped} The rest is in the text."


def transcribe(data: bytes, mime: str) -> str:
    import server

    if not data:
        return ""
    keys = server.api_keys(server.load_config())
    if not keys:
        raise RuntimeError("gemini-missing")
    audio_mime = mime or "audio/ogg"

    def call(key: str) -> str:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=key)
        try:
            response = client.models.generate_content(
                model=TRANSCRIBE_MODEL,
                contents=[
                    types.Part.from_bytes(data=data, mime_type=audio_mime),
                    "Transcribe the speech. Output only the spoken words.",
                ],
                config=types.GenerateContentConfig(system_instruction=TRANSCRIBE_SYSTEM),
            )
            return str(getattr(response, "text", None) or "").strip()
        finally:
            close = getattr(client, "close", None)
            if close:
                close()

    return server.generate_with_keys(keys, call)


def synthesize(text: str) -> tuple[bytes, int]:
    """PCM s16le plus sample rate, using rotating Gemini keys and gemini_voice."""
    import server

    spoken = for_speech(text)
    if not spoken:
        return b"", 24000
    config = server.load_config()
    keys = server.api_keys(config)
    if not keys:
        raise RuntimeError("gemini-missing")
    voice = str(config.get("gemini_voice") or "Enceladus").strip() or "Enceladus"
    last_error: BaseException | None = None
    for model in TTS_MODELS:
        try:
            return server.generate_with_keys(
                keys,
                lambda key, model_name=model: _tts_once(key, model_name, spoken, voice),
            )
        except Exception as exc:
            last_error = exc
            if _model_unavailable(exc):
                continue
            raise
    if last_error is not None:
        raise last_error
    raise RuntimeError("speech-missing")


def desk_pcm(text: str) -> bytes:
    pcm, rate = synthesize(text)
    if not pcm:
        return b""
    if rate == 24000:
        return pcm
    return _resample(pcm, rate, 24000)


def voice_ogg(text: str) -> bytes:
    pcm, rate = synthesize(text)
    if not pcm:
        return b""
    return pcm_to_ogg(pcm, rate)


def pcm_to_ogg(pcm: bytes, rate: int) -> bytes:
    if len(pcm) % 2:
        pcm = pcm[:-1]
    if not pcm:
        return b""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg-missing")
    proc = _ffmpeg(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "s16le",
            "-ar",
            str(rate),
            "-ac",
            "1",
            "-i",
            "pipe:0",
            "-c:a",
            "libopus",
            "-b:a",
            "32k",
            "-vbr",
            "on",
            "-application",
            "voip",
            "-f",
            "ogg",
            "pipe:1",
        ],
        pcm,
    )
    if proc.returncode != 0 or not proc.stdout.startswith(b"OggS"):
        raise RuntimeError("ogg-encode-failed")
    return proc.stdout


def _resample(pcm: bytes, source_rate: int, target_rate: int) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg-missing")
    proc = _ffmpeg(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "s16le",
            "-ar",
            str(source_rate),
            "-ac",
            "1",
            "-i",
            "pipe:0",
            "-f",
            "s16le",
            "-ar",
            str(target_rate),
            "-ac",
            "1",
            "pipe:1",
        ],
        pcm,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError("resample-failed")
    data = proc.stdout
    if len(data) % 2:
        data = data[:-1]
    return data


def _tts_once(api_key: str, model: str, text: str, voice: str) -> tuple[bytes, int]:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    try:
        response = client.models.generate_content(
            model=model,
            contents=text,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
                    )
                ),
            ),
        )
    finally:
        close = getattr(client, "close", None)
        if close:
            close()
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        raise RuntimeError("speech returned no audio")
    content = getattr(candidates[0], "content", None)
    for part in getattr(content, "parts", None) or []:
        inline = getattr(part, "inline_data", None)
        if inline is None:
            continue
        raw = _as_audio_bytes(getattr(inline, "data", None))
        if not raw:
            continue
        return _as_pcm(raw, str(getattr(inline, "mime_type", "") or ""))
    raise RuntimeError("speech returned no audio")


def _as_audio_bytes(data: object) -> bytes:
    if isinstance(data, (bytes, bytearray)) and data:
        return bytes(data)
    if isinstance(data, str) and data:
        try:
            return base64.b64decode(data)
        except Exception:
            return b""
    return b""


def _as_pcm(data: bytes, mime: str) -> tuple[bytes, int]:
    if data[:4] == b"RIFF":
        import io
        import wave

        with wave.open(io.BytesIO(data), "rb") as handle:
            rate = handle.getframerate() or 24000
            frames = handle.readframes(handle.getnframes())
        if len(frames) % 2:
            frames = frames[:-1]
        return frames, rate
    match = re.search(r"rate=(\d+)", mime or "")
    rate = int(match.group(1)) if match else 24000
    if len(data) % 2:
        data = data[:-1]
    return data, rate


def _ffmpeg(args: list[str], pcm: bytes) -> subprocess.CompletedProcess[bytes]:
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(
        args,
        input=pcm,
        capture_output=True,
        check=False,
        timeout=30,
        creationflags=flags,
    )


def _model_unavailable(exc: BaseException) -> bool:
    text = f"{exc.__class__.__name__} {exc}".lower()
    return any(
        token in text
        for token in (
            "not found",
            "not supported",
            "invalid model",
            "unknown model",
            "is not available",
        )
    )
