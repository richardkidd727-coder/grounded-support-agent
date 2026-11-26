"""Voice endpoints: speech-to-text for utterances, text-to-speech per sentence.

A voice turn is otherwise an ordinary turn: the browser transcribes here, then posts the text to
`/api/chat/stream` with `mode=voice`. Same agent loop, tools, guardrails and memory as chat.
Both endpoints require a live session, so the audio API cannot be used as an open proxy.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from support_agent.agent.runner import TurnRunner
from support_agent.agent.store import SESSION_ID_PATTERN
from support_agent.config import Settings
from support_agent.voice.groq_audio import AudioError, AudioErrorKind, GroqAudio

router = APIRouter(prefix="/api/voice", tags=["voice"])

ACCEPTED_AUDIO = frozenset({"audio/wav", "audio/x-wav", "audio/wave", "audio/webm", "audio/ogg"})
MIN_AUDIO_BYTES = 1_000  # a WAV header plus a few ms; anything smaller is not an utterance


class VoiceConfig(BaseModel):
    stt: bool
    tts: Literal["server", "browser"]  # "browser": use the Web Speech API fallback
    stt_model: str
    tts_voice: str | None


class TranscriptOut(BaseModel):
    text: str  # empty when no speech was detected
    language: str | None
    duration_s: float
    latency_ms: int


class SpeakRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    session_id: str = Field(pattern=SESSION_ID_PATTERN)
    text: str = Field(min_length=1, max_length=600)


def _audio(request: Request) -> GroqAudio:
    audio: GroqAudio = request.app.state.audio
    return audio


async def _require_session(request: Request, session_id: str) -> None:
    runner: TurnRunner = request.app.state.runner
    if not await runner.store.exists(session_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found or expired")


def _http_error(exc: AudioError) -> HTTPException:
    if exc.kind is AudioErrorKind.RATE_LIMITED:
        headers = {"Retry-After": str(round(exc.retry_after_s))} if exc.retry_after_s else None
        return HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "voice is busy", headers=headers)
    if exc.kind is AudioErrorKind.BAD_AUDIO:
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "could not read that audio")
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "voice service unavailable")


@router.get("/config")
async def voice_config(request: Request) -> VoiceConfig:
    audio = _audio(request)
    # Probed once and cached, so browsers learn up front whether to use their own speech.
    tts_ok = await audio.probe_tts()
    return VoiceConfig(
        stt=audio.stt_available,
        tts="server" if tts_ok else "browser",
        stt_model=audio.stt_model,
        tts_voice=audio.tts_voice if tts_ok else None,
    )


@router.post(
    "/transcribe",
    openapi_extra={
        "requestBody": {
            "content": {
                t: {"schema": {"type": "string", "format": "binary"}} for t in ACCEPTED_AUDIO
            }
        }
    },
)
async def transcribe(
    request: Request,
    session_id: Annotated[str, Query(pattern=SESSION_ID_PATTERN)],
    content_type: Annotated[str, Header()] = "",
) -> TranscriptOut:
    """Raw audio body (one utterance, e.g. 16 kHz mono WAV) -> text."""
    settings: Settings = request.app.state.settings
    media_type = content_type.split(";")[0].strip().lower()
    if media_type not in ACCEPTED_AUDIO:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "send audio/wav or audio/webm")
    await _require_session(request, session_id)

    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > settings.voice_max_audio_bytes:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "utterance too long")
        chunks.append(chunk)
    if size < MIN_AUDIO_BYTES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "no audio received")

    try:
        result = await _audio(request).transcribe(b"".join(chunks), media_type)
    except AudioError as exc:
        raise _http_error(exc) from None
    return TranscriptOut(
        text=result.text,
        language=result.language,
        duration_s=round(result.duration_s, 2),
        latency_ms=result.latency_ms,
    )


@router.post(
    "/speak",
    response_class=Response,
    responses={
        200: {"content": {"audio/wav": {}}, "description": "Spoken sentence"},
        503: {"description": "Server TTS unavailable - use the browser's speech synthesis"},
    },
)
async def speak(request: Request, body: SpeakRequest) -> Response:
    """One sentence of the reply -> WAV audio."""
    await _require_session(request, body.session_id)
    try:
        audio = await _audio(request).synthesize(body.text)
    except AudioError as exc:
        raise _http_error(exc) from None
    return Response(content=audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})
