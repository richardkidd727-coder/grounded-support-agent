"""Cheap, deterministic token estimate used for chunk sizing.

bge-small uses a WordPiece tokenizer with a 512-token input limit. Measured on this corpus, the
real tokenizer produces at most 1.37x the word+punctuation count (identifiers such as
"INV-YYYY-NNNNN" split into many pieces, plus [CLS]/[SEP]). Padding by 1.4 keeps the estimate an
upper bound, so chunks are never silently truncated by the embedder. `tests/test_chunking.py`
re-checks this against the real tokenizer whenever the model is cached.
"""

import math
import re

_TOKEN = re.compile(r"\w+|[^\w\s]")
_PADDING = 1.4


def estimate_tokens(text: str) -> int:
    return math.ceil(len(_TOKEN.findall(text)) * _PADDING)
