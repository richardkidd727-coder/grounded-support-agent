"""Speech-to-text (Whisper) and text-to-speech (Orpheus) on Groq's OpenAI-compatible audio API.

The browser never sees the API key: it uploads a short utterance here and gets text back, and
asks for speech one sentence at a time so playback can start while the reply is still streaming.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx

from support_agent.logs import get_logger

log = get_logger("voice")

BASE_URL = "https://api.groq.com/openai/v1"

# Whisper "hears" words in silence and noise ("Thank you.", "Bye."). Segments it is itself unsure
# contain speech are dropped instead of being sent to the agent as a customer message.
NO_SPEECH_PROB_MAX = 0.6


class AudioErrorKind(StrEnum):
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"  # model not enabled for this key (e.g. terms not accepted), 5xx
    BAD_AUDIO = "bad_audio"


class AudioError(Exception):
    def __init__(self, kind: AudioErrorKind, message: str, retry_after_s: float | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.retry_after_s = retry_after_s


@dataclass(frozen=True, slots=True)
class Transcript:
    text: str  # empty when the audio held no speech
    language: str | None
    duration_s: float
    latency_ms: int


class GroqAudio:
    def __init__(
        self,
        http: httpx.AsyncClient,
        api_key: str | None,
        *,
        stt_model: str,
        tts_model: str,
        tts_voice: str,
        base_url: str = BASE_URL,
    ) -> None:
        self._http = http
        self._key = api_key
        self.stt_model = stt_model
        self.tts_model = tts_model
        self.tts_voice = tts_voice
        self._base = base_url.rstrip("/")
        # None = untried. Flips to False on "terms not accepted"/403 so clients fall back to the
        # browser's speech synthesis instead of paying a failed round trip per sentence.
        self.tts_available: bool | None = None if api_key else False

    @property
    def stt_available(self) -> bool:
        return self._key is not None

    def __repr__(self) -> str:  # never print the key
        return f"GroqAudio(stt={self.stt_model}, tts={self.tts_model})"

    def _headers(self) -> dict[str, str]:
        if not self._key:
            raise AudioError(AudioErrorKind.UNAVAILABLE, "no Groq API key configured")
        return {"Authorization": f"Bearer {self._key}"}

    async def transcribe(self, audio: bytes, content_type: str) -> Transcript:
        started = time.perf_counter()
        extension = {"audio/wav": "wav", "audio/webm": "webm", "audio/ogg": "ogg"}.get(
            content_type.split(";")[0], "wav"
        )
        try:
            response = await self._post(
                f"{self._base}/audio/transcriptions",
                headers=self._headers(),
                files={"file": (f"utterance.{extension}", audio, content_type)},
                data={
                    "model": self.stt_model,
                    "response_format": "verbose_json",
                    "language": "en",
                    "temperature": "0",
                },
                timeout=httpx.Timeout(30.0, connect=10.0),
            )
        except httpx.HTTPError as exc:
            raise AudioError(AudioErrorKind.UNAVAILABLE, f"transcription failed: {exc!r}") from exc
        self._raise_for(response)
        body: dict[str, Any] = response.json()
        segments = body.get("segments") or []
        heard = [
            str(s.get("text", "")).strip()
            for s in segments
            if float(s.get("no_speech_prob") or 0.0) <= NO_SPEECH_PROB_MAX
        ]
        text = " ".join(t for t in heard if t) if segments else str(body.get("text", "")).strip()
        return Transcript(
            text=text,
            language=body.get("language"),
            duration_s=float(body.get("duration") or 0.0),
            latency_ms=round((time.perf_counter() - started) * 1000),
        )

    async def probe_tts(self) -> bool:
        """Find out once whether server TTS works (e.g. Orpheus terms accepted), never raising."""
        if self.tts_available is None:
            with contextlib.suppress(AudioError):  # failure already recorded in tts_available
                await self.synthesize("Hi.")
        return self.tts_available is True

    async def synthesize(self, text: str) -> bytes:
        if self.tts_available is False:
            raise AudioError(AudioErrorKind.UNAVAILABLE, "server speech synthesis is unavailable")
        try:
            response = await self._post(
                f"{self._base}/audio/speech",
                headers=self._headers(),
                json={
                    "model": self.tts_model,
                    "voice": self.tts_voice,
                    "input": text,
                    "response_format": "wav",
                },
                timeout=httpx.Timeout(30.0, connect=10.0),
            )
        except httpx.HTTPError as exc:
            raise AudioError(AudioErrorKind.UNAVAILABLE, f"synthesis failed: {exc!r}") from exc
        try:
            self._raise_for(response)
        except AudioError as exc:
            if exc.kind is AudioErrorKind.UNAVAILABLE and response.status_code < 500:
                # logged once: later calls short-circuit at the top of this method
                log.warning("tts_disabled", model=self.tts_model, reason=exc.message[:200])
                self.tts_available = False
            raise
        self.tts_available = True
        return response.content

    async def _post(self, url: str, **kwargs: Any) -> httpx.Response:
        """POST, retrying once if the connection could not even be opened (nothing was sent)."""
        try:
            return await self._http.post(url, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            log.warning("voice_connect_retry", url=url.rsplit("/", 1)[-1])
            return await self._http.post(url, **kwargs)

    @staticmethod
    def _raise_for(response: httpx.Response) -> None:
        if response.status_code == 200:
            return
        try:
            message = str(response.json().get("error", {}).get("message", ""))
        except ValueError:
            message = response.text
        message = message[:300] or f"HTTP {response.status_code}"
        if response.status_code == 429:
            retry = response.headers.get("retry-after")
            raise AudioError(AudioErrorKind.RATE_LIMITED, message, float(retry) if retry else None)
        lowered = message.lower()
        if response.status_code in (401, 403, 404) or "terms" in lowered or "model" in lowered:
            raise AudioError(AudioErrorKind.UNAVAILABLE, message)
        if response.status_code >= 500:
            raise AudioError(AudioErrorKind.UNAVAILABLE, message)
        raise AudioError(AudioErrorKind.BAD_AUDIO, message)
