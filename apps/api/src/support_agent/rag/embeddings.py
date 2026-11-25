"""Text embedders.

`FastEmbedEmbedder` runs bge-small-en-v1.5 locally on CPU via ONNX: no API key, no rate limits,
deterministic. `HashingEmbedder` is a dependency-free stand-in for tests and CI.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from fastembed import TextEmbedding

Vector = list[float]


class Embedder(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def dim(self) -> int: ...

    async def embed_documents(self, texts: Sequence[str]) -> list[Vector]: ...

    async def embed_query(self, text: str) -> Vector: ...


def _normalise(values: Sequence[float]) -> Vector:
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [float(v) / norm for v in values]


class FastEmbedEmbedder:
    """Lazily loads the ONNX model once; inference runs in a worker thread to keep the loop free."""

    def __init__(
        self,
        model_name: str,
        dim: int,
        *,
        cache_dir: Path | None = None,
        query_instruction: str = "",
    ) -> None:
        self._model_name = model_name
        self._dim = dim
        self._cache_dir = cache_dir
        self._query_instruction = query_instruction
        self._model: TextEmbedding | None = None
        self._lock = threading.Lock()

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dim(self) -> int:
        return self._dim

    def _load(self) -> TextEmbedding:
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding  # heavy import: only when first needed

                self._model = TextEmbedding(
                    model_name=self._model_name,
                    cache_dir=str(self._cache_dir) if self._cache_dir else None,
                )
            return self._model

    def warm_up(self) -> None:
        """Load the model and run one inference (call from a thread at startup)."""
        self._embed_sync(["warm up"])

    def _embed_sync(self, texts: list[str]) -> list[Vector]:
        model = self._load()
        vectors = [_normalise(v.tolist()) for v in model.embed(texts, batch_size=32)]
        if vectors and len(vectors[0]) != self._dim:
            raise ValueError(
                f"{self._model_name} returned {len(vectors[0])} dims, expected {self._dim}"
            )
        return vectors

    async def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        return await asyncio.to_thread(self._embed_sync, list(texts))

    async def embed_query(self, text: str) -> Vector:
        # bge recommends an instruction prefix for short queries against passages;
        # whether it helps on this corpus is measured by `uv run eval-retrieval`.
        (vector,) = await asyncio.to_thread(self._embed_sync, [self._query_instruction + text])
        return vector


class HashingEmbedder:
    """Deterministic bag-of-words embedder (feature hashing). Lexical overlap -> similarity."""

    _WORD = re.compile(r"[a-z0-9]+")

    def __init__(self, dim: int = 384) -> None:
        self._dim = dim

    @property
    def model_name(self) -> str:
        return f"hashing-{self._dim}"

    @property
    def dim(self) -> int:
        return self._dim

    def _vector(self, text: str) -> Vector:
        values = [0.0] * self._dim
        for word in self._WORD.findall(text.lower()):
            digest = hashlib.blake2b(word.encode(), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "little") % self._dim
            values[bucket] += 1.0 if digest[4] & 1 else -1.0
        return _normalise(values)

    async def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        return [self._vector(t) for t in texts]

    async def embed_query(self, text: str) -> Vector:
        return self._vector(text)


def to_pgvector(vector: Sequence[float]) -> str:
    """pgvector text literal. Passed as text and cast in SQL - no driver codec needed."""
    return "[" + ",".join(f"{v:.7f}" for v in vector) + "]"
