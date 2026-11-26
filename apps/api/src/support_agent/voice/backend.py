"""The speech backend interface the voice endpoints depend on (Groq, or the fake for E2E)."""

from __future__ import annotations

from typing import Protocol

from support_agent.voice.groq_audio import Transcript


class SpeechBackend(Protocol):
    stt_model: str
    tts_voice: str
    tts_available: bool | None

    @property
    def stt_available(self) -> bool: ...

    async def transcribe(self, audio: bytes, content_type: str) -> Transcript: ...

    async def synthesize(self, text: str) -> bytes: ...

    async def probe_tts(self) -> bool: ...
