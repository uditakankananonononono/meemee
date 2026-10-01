from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from itertools import pairwise
from typing import Protocol

TOKEN = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    model: str
    version: int
    dimensions: int
    def embed(self, text: str) -> list[float]: ...


class HashingEmbedder:
    """Deterministic local feature-hashing vectors (unigram+bigram, blake2b). Lexical-feature
    similarity, not learned semantics. `model`/`version`/`dimensions` identify stored vectors."""
    model = "hashing-blake2b-uni-bigram"
    version = 1

    def __init__(self, dimensions: int = 256):
        if dimensions < 32: raise ValueError("dimensions must be at least 32")
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        values = [0.0] * self.dimensions
        tokens = TOKEN.findall(text.lower())
        features = tokens + [f"{a}_{b}" for a, b in pairwise(tokens)]
        for feature, count in Counter(features).items():
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            index = int.from_bytes(digest, "big") % self.dimensions
            sign = 1.0 if digest[0] & 1 else -1.0
            values[index] += sign * (1.0 + math.log(count))
        norm = math.sqrt(sum(value * value for value in values)) or 1.0
        return [value / norm for value in values]


def cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


RRF_K = 60


def reciprocal_rank_fusion(*ranked_lists: list[dict], limit: int, score_key: str = "hybrid_score") -> list[dict]:
    """Fuse ranked row lists (each ordered best-first, rows carry integer `id`) with RRF; ties break on newest id."""
    by_id: dict[int, dict] = {}
    scores: dict[int, float] = {}
    for result_set in ranked_lists:
        for rank, row in enumerate(result_set, 1):
            ident = int(row["id"]); by_id[ident] = row
            scores[ident] = scores.get(ident, 0.0) + 1.0 / (RRF_K + rank)
    ordered = sorted(by_id, key=lambda ident: (-scores[ident], -ident))[:limit]
    return [{**by_id[ident], score_key: scores[ident]} for ident in ordered]
