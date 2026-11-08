"""Poyo's ears and voice: speech-to-text and text-to-speech through OpenAI's audio API.

Voice is transport only. The transcript goes through the normal tutor turn (Nemotron picks the tool, the
calculation module makes the numbers, the claim checks run), and Poyo speaks the VERIFIED explanation text
word for word. No speech model ever decides or writes an answer (that is why the Realtime speech-to-speech API
is not used: it would answer by itself).

Config (env or .env): OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_STT_MODEL (gpt-4o-mini-transcribe),
OPENAI_TTS_MODEL (gpt-4o-mini-tts), OPENAI_TTS_VOICE (coral), OPENAI_TIMEOUT.
Speech audio is cached by text in <shared>/state/tts_cache/ so repeated lines cost nothing.
"""

from __future__ import annotations

import hashlib
import os
import re

from . import paths
from .nemotron_client import ConfigError, ModelCallError

STT_PROMPT = ("A student talks to Poyo, an electronics tutor, about circuits: Poyo, LED, LEDs, resistor, resistance, "
              "ohms, kilohms, 1,000 ohms, milliamps, volts, current, battery, forward voltage, MOSFET, BJT, "
              "transistor, voltage divider, dimmer, brighter. Also scam texts: scam, smishing, phishing, "
              "suspicious message, link, carrier, official app, verify, password, PIN.")
TTS_INSTRUCTIONS = ("You are Poyo, a small, warm, upbeat tutor robot. Speak clearly at a relaxed pace, like a "
                    "friendly teacher to a curious beginner. Say units in full (mA as milliamps, Ω as ohms).")
WAKE_RE = re.compile(r"^\s*(?:hey\s+|hi\s+|okay\s+|ok\s+)?(?:poyo|poyo's|poio|poyho|pollo|po-yo|boyo|poyou)\b[\s,.!:;-]*",
                     re.I)
MAX_AUDIO_BYTES = 8 * 1024 * 1024
MAX_TTS_CHARS = 1200
MIME_EXT = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav",
            "audio/mpeg": "mp3", "audio/mp4": "mp4", "audio/m4a": "m4a", "audio/flac": "flac"}


def strip_wake_word(text: str) -> str:
    """"Poyo, teach me ..." -> "Teach me ..." (the name is how you talk to Poyo, not part of the question)."""
    rest = WAKE_RE.sub("", text or "", count=1).strip()
    return (rest[:1].upper() + rest[1:]) if rest else ""


def speakable(text: str) -> str:
    """Units read aloud the way a person says them (the subtitle keeps the symbols)."""
    t = re.sub(r"(\d)\s*mA\b", r"\1 milliamps", text)
    t = re.sub(r"(\d)\s*kΩ", r"\1 kilohms", t)
    t = re.sub(r"(\d)\s*Ω", r"\1 ohms", t)
    t = re.sub(r"(\d)\s*V\b", r"\1 volts", t)
    t = re.sub(r"(\d)\s*W\b", r"\1 watts", t)
    t = t.replace("÷", " divided by ").replace("−", " minus ").replace("→", " to ")
    return t


class OpenAIVoice:
    name = "openai"

    def __init__(self, timeout: float | None = None):
        paths.load_env()
        key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise ConfigError("OPENAI_API_KEY is not set: voice needs it (the typed text box still works)")
        from openai import OpenAI
        self.stt_model = os.environ.get("OPENAI_STT_MODEL", "").strip() or "gpt-4o-mini-transcribe"
        self.tts_model = os.environ.get("OPENAI_TTS_MODEL", "").strip() or "gpt-4o-mini-tts"
        self.voice = os.environ.get("OPENAI_TTS_VOICE", "").strip() or "coral"
        base = os.environ.get("OPENAI_BASE_URL", "").strip() or None
        self._client = OpenAI(api_key=key, base_url=base, max_retries=1,
                              timeout=timeout or float(os.environ.get("OPENAI_TIMEOUT", "") or 30))

    def info(self) -> dict:
        return {"provider": "OpenAI", "stt": self.stt_model, "tts": self.tts_model, "voice": self.voice}

    def transcribe(self, audio: bytes, mime: str = "audio/webm") -> str:
        ext = MIME_EXT.get((mime or "").split(";")[0].strip().lower(), "webm")
        try:
            out = self._client.audio.transcriptions.create(model=self.stt_model, file=(f"speech.{ext}", audio, mime),
                                                           prompt=STT_PROMPT, language="en")
        except Exception as exc:  # noqa: BLE001 - reported to the UI; the key never appears in it
            raise ModelCallError(f"speech-to-text failed: {type(exc).__name__}: {str(exc)[:200]}") from exc
        return (getattr(out, "text", "") or "").strip()

    def speak(self, text: str) -> bytes:
        try:
            out = self._client.audio.speech.create(model=self.tts_model, voice=self.voice, input=speakable(text),
                                                   instructions=TTS_INSTRUCTIONS, response_format="mp3")
        except Exception as exc:  # noqa: BLE001
            raise ModelCallError(f"text-to-speech failed: {type(exc).__name__}: {str(exc)[:200]}") from exc
        return out.read() if hasattr(out, "read") else out.content


def cached_speech(provider, text: str) -> bytes:
    """mp3 for `text`, cached on disk by (provider, model, voice, text)."""
    text = " ".join(text.split())[:MAX_TTS_CHARS]
    if getattr(provider, "name", "") == "mock":  # MOCK audio is never cached
        return provider.speak(text)
    info = provider.info()
    key = hashlib.sha256(f"{info.get('tts')}|{info.get('voice')}|{TTS_INSTRUCTIONS}|{text}".encode()).hexdigest()[:32]
    folder = paths.STATE / "tts_cache"
    path = folder / f"{key}.mp3"
    if path.exists():
        return path.read_bytes()
    audio = provider.speak(text)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(audio)
    os.replace(tmp, path)
    return audio


class FakeVoice:
    """MOCK voice for offline tests: 'transcribes' audio bytes that are UTF-8 text, speaks a tiny fake mp3."""
    name = "mock"

    def info(self) -> dict:
        return {"provider": "MOCK voice (not OpenAI)", "stt": "mock", "tts": "mock", "voice": "mock"}

    def transcribe(self, audio: bytes, mime: str = "audio/webm") -> str:
        return audio.decode("utf-8", "replace").strip()

    def speak(self, text: str) -> bytes:
        return b"ID3MOCK" + text.encode("utf-8")[:64]
