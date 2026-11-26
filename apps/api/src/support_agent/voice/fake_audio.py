"""Offline speech backend for end-to-end tests (`VOICE_BACKEND=fake`).

Real speech recognition can't run offline, so the fake "hears" by pitch: the E2E suite feeds the
browser's fake microphone tone bursts, and a low tone transcribes to the first configured phrase,
a high tone to the second. That is deterministic even though the browser also sends partial
audio for live captions. Text-to-speech returns a quiet tone whose length follows the sentence,
so the real playback path (fetch -> decode -> play -> barge-in) is exercised.
"""

from __future__ import annotations

import io
import itertools
import math
import struct
import wave

from support_agent.voice.groq_audio import AudioError, AudioErrorKind, Transcript

LOUD = 0.05  # sample amplitude that counts as "sound"
PITCH_SPLIT_HZ = 450.0


def _pcm(audio: bytes) -> tuple[list[float], int]:
    try:
        with wave.open(io.BytesIO(audio)) as w:
            rate, width, frames = w.getframerate(), w.getsampwidth(), w.readframes(w.getnframes())
    except (wave.Error, EOFError) as exc:
        raise AudioError(AudioErrorKind.BAD_AUDIO, "not a WAV file") from exc
    if width != 2:
        raise AudioError(AudioErrorKind.BAD_AUDIO, "expected 16-bit PCM")
    samples = struct.unpack(f"<{len(frames) // 2}h", frames[: len(frames) // 2 * 2])
    return [s / 32768 for s in samples], rate


def dominant_pitch_hz(samples: list[float], rate: int) -> float | None:
    """Zero-crossing pitch estimate over the loud part of the signal (None if silent)."""
    loud = [s for s in samples if abs(s) > LOUD / 2]
    if len(loud) < rate * 0.15:  # less than ~150 ms of sound
        return None
    crossings = sum(1 for a, b in itertools.pairwise(loud) if (a < 0) != (b < 0))
    return crossings / 2 / (len(loud) / rate)


def tone_wav(
    seconds: float, *, hz: float = 220.0, amplitude: float = 0.05, rate: int = 16_000
) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        n = int(seconds * rate)
        w.writeframes(
            b"".join(
                struct.pack("<h", int(amplitude * 32767 * math.sin(2 * math.pi * hz * i / rate)))
                for i in range(n)
            )
        )
    return buffer.getvalue()


class FakeAudio:
    stt_model = "fake-stt"
    tts_voice = "fake-tone"

    def __init__(self, phrases: list[str]) -> None:
        self._phrases = phrases or ["Hello."]
        self.tts_available: bool | None = True

    @property
    def stt_available(self) -> bool:
        return True

    async def transcribe(self, audio: bytes, content_type: str) -> Transcript:
        samples, rate = _pcm(audio)
        pitch = dominant_pitch_hz(samples, rate)
        if pitch is None:
            text = ""
        else:
            text = self._phrases[0 if pitch < PITCH_SPLIT_HZ else min(1, len(self._phrases) - 1)]
        return Transcript(
            text=text, language="english", duration_s=len(samples) / rate, latency_ms=120
        )

    async def synthesize(self, text: str) -> bytes:
        return tone_wav(max(0.6, len(text) * 0.055))

    async def probe_tts(self) -> bool:
        return True
