"""Voice: Groq audio client (mocked HTTP) and the voice endpoints (real Postgres)."""

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from support_agent.agent.prompts import interruption_note
from support_agent.config import Settings
from support_agent.llm.fake import FakeLlm, Reply
from support_agent.llm.router import LlmRouter
from support_agent.main import create_app
from support_agent.rag.embeddings import HashingEmbedder
from support_agent.voice.groq_audio import AudioError, AudioErrorKind, GroqAudio

WAV = b"RIFF" + b"\x00" * 4000  # content is opaque to our code; Groq decodes it

Handler = Callable[[httpx.Request], httpx.Response]


def audio_client(handler: Handler, *, key: str | None = "k") -> GroqAudio:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GroqAudio(http, key, stt_model="whisper-x", tts_model="orpheus-x", tts_voice="troy")


def whisper(segments: list[tuple[str, float]], duration: float = 2.0) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "text": " ".join(t for t, _ in segments),
            "language": "english",
            "duration": duration,
            "segments": [{"text": t, "no_speech_prob": p} for t, p in segments],
        },
    )


# --- client ------------------------------------------------------------------------------------


async def test_transcribe_sends_the_audio_and_drops_hallucinated_silence() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content
        return whisper([(" My card ends in 4242.", 0.01), (" Thank you.", 0.93)])

    result = await audio_client(handler).transcribe(WAV, "audio/wav")
    assert result.text == "My card ends in 4242."
    assert seen["url"].endswith("/audio/transcriptions")
    assert b"whisper-x" in seen["body"] and b'filename="utterance.wav"' in seen["body"]


async def test_a_connection_that_never_opened_is_retried_once() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectTimeout("slow handshake", request=request)
        return whisper([(" hello there", 0.01)])

    assert (await audio_client(handler).transcribe(WAV, "audio/wav")).text == "hello there"
    assert calls == 2


async def test_pure_noise_transcribes_to_nothing() -> None:
    result = await audio_client(lambda _: whisper([(" Bye.", 0.97)])).transcribe(WAV, "audio/wav")
    assert result.text == ""


@pytest.mark.parametrize(
    ("status", "body", "kind"),
    [
        (429, {"error": {"message": "slow down"}}, AudioErrorKind.RATE_LIMITED),
        (400, {"error": {"message": "could not process file"}}, AudioErrorKind.BAD_AUDIO),
        (503, {"error": {"message": "over capacity"}}, AudioErrorKind.UNAVAILABLE),
    ],
)
async def test_transcription_errors(
    status: int, body: dict[str, Any], kind: AudioErrorKind
) -> None:
    client = audio_client(lambda _: httpx.Response(status, json=body, headers={"retry-after": "3"}))
    with pytest.raises(AudioError) as info:
        await client.transcribe(WAV, "audio/wav")
    assert info.value.kind is kind


async def test_tts_terms_error_disables_server_speech_without_further_calls() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            400, json={"error": {"message": "The model `orpheus-x` requires terms acceptance."}}
        )

    client = audio_client(handler)
    assert client.tts_available is None
    for _ in range(2):
        with pytest.raises(AudioError) as info:
            await client.synthesize("Hello there.")
        assert info.value.kind is AudioErrorKind.UNAVAILABLE
    assert client.tts_available is False
    assert calls == 1  # the second sentence never left the server


async def test_tts_success_returns_audio() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["voice"] == "troy"
        return httpx.Response(200, content=b"RIFFwav", headers={"content-type": "audio/wav"})

    client = audio_client(handler)
    assert await client.synthesize("Hi.") == b"RIFFwav"
    assert client.tts_available is True


def test_without_a_key_voice_reports_unavailable() -> None:
    client = audio_client(lambda _: httpx.Response(500), key=None)
    assert not client.stt_available and client.tts_available is False


def test_interruption_note() -> None:
    assert '"Your refund of forty"' in interruption_note("Your  refund of\nforty")
    assert "before hearing any of it" in interruption_note("   ")


# --- endpoints ----------------------------------------------------------------------------------


@pytest.fixture
def make_client(test_database_url: str) -> Callable[..., Any]:
    @contextmanager
    def factory(
        handler: Handler, *, script: list[Any] | None = None, **overrides: Any
    ) -> Iterator[tuple[TestClient, FakeLlm]]:
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            env="test",
            database_url=test_database_url,
            **overrides,
        )
        llm = FakeLlm(script or [])
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        audio = GroqAudio(http, "k", stt_model="whisper-x", tts_model="orpheus-x", tts_voice="troy")
        app = create_app(
            settings, embedder=HashingEmbedder(), agent_llm=LlmRouter([llm]), audio=audio
        )
        with TestClient(app) as client:
            yield client, llm

    return factory


def groq(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/audio/transcriptions"):
        return whisper([(" What's my plan?", 0.02)], duration=1.4)
    return httpx.Response(200, content=b"RIFF....WAVE", headers={"content-type": "audio/wav"})


def test_transcribe_and_speak(make_client: Any) -> None:
    with make_client(groq) as (client, _):
        sid = client.post("/api/sessions").json()["session_id"]
        res = client.post(
            f"/api/voice/transcribe?session_id={sid}",
            content=WAV,
            headers={"Content-Type": "audio/wav"},
        )
        assert res.status_code == 200
        assert res.json()["text"] == "What's my plan?" and res.json()["duration_s"] == 1.4

        spoken = client.post("/api/voice/speak", json={"session_id": sid, "text": "Hello."})
        assert spoken.status_code == 200 and spoken.headers["content-type"] == "audio/wav"
        assert client.get("/api/voice/config").json()["tts"] == "server"


def test_transcribe_rejects_bad_requests(make_client: Any) -> None:
    with make_client(groq, voice_max_audio_bytes=5_000) as (client, _):
        sid = client.post("/api/sessions").json()["session_id"]
        url = f"/api/voice/transcribe?session_id={sid}"
        wav = {"Content-Type": "audio/wav"}
        assert (
            client.post(url, content=WAV, headers={"Content-Type": "text/plain"}).status_code == 415
        )
        assert client.post(url, content=b"RIFF", headers=wav).status_code == 422
        assert client.post(url, content=b"x" * 6_000, headers=wav).status_code == 413
        missing = "/api/voice/transcribe?session_id=sess_" + "x" * 24
        assert client.post(missing, content=WAV, headers=wav).status_code == 404
        assert (
            client.post("/api/voice/speak", json={"session_id": "nope", "text": "x"}).status_code
            == 422
        )


def test_speak_falls_back_to_browser_when_tts_is_not_enabled(make_client: Any) -> None:
    def no_tts(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "requires terms acceptance"}})

    with make_client(no_tts) as (client, _):
        sid = client.post("/api/sessions").json()["session_id"]
        res = client.post("/api/voice/speak", json={"session_id": sid, "text": "Hello."})
        assert res.status_code == 503
        assert client.get("/api/voice/config").json() == {
            "stt": True,
            "tts": "browser",
            "stt_model": "whisper-x",
            "tts_voice": None,
        }


def test_interrupted_reply_is_explained_to_the_model(make_client: Any) -> None:
    with make_client(groq, script=[Reply("Sure.")]) as (client, llm):
        sid = client.post("/api/sessions").json()["session_id"]
        res = client.post(
            "/api/chat/stream",
            json={
                "session_id": sid,
                "message": "wait, which invoice?",
                "mode": "voice",
                "interrupted_after": "Your refund of forty-five dollars",
            },
        )
        assert res.status_code == 200
        started = json.loads(res.text.split("data: ", 1)[1].split("\n", 1)[0])
        assert started["interrupted_after"] == "Your refund of forty-five dollars"  # traced
        system = llm.requests[0].messages[0].content
        assert "They only heard" in system and "forty-five dollars" in system
        assert "spoken aloud" in system  # voice style
