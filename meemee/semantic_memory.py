from __future__ import annotations

import hashlib
import math
import re
import threading
from collections import Counter
from itertools import pairwise
from pathlib import Path
from typing import Protocol

TOKEN = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    dimensions: int
    space_id: str
    def embed(self, text: str) -> list[float]: ...


class HashingEmbedder:
    """Deterministic local feature-hashing embedding with no external service."""
    def __init__(self, dimensions: int = 256):
        if dimensions < 32: raise ValueError("dimensions must be at least 32")
        self.dimensions = dimensions
        self.space_id = f"lexical-hashing-blake2b-v1:{dimensions}"

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
    if len(left) != len(right):
        raise ValueError("cannot compare vectors of different dimensions")
    if not all(math.isfinite(v) for v in (*left, *right)):
        raise ValueError("vectors must be finite")
    return sum(a * b for a, b in zip(left, right))


MINILM_REPO = "sentence-transformers/all-MiniLM-L6-v2"
MINILM_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
MINILM_FILES = {
    "tokenizer.json": "be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037",
    "onnx/model.onnx": "6fd5d72fe4589f189f8ebc006442dbb529bb7ce38f8082112682524616046452",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pull_minilm(directory: Path) -> Path:
    """Explicit free public weight download. Never called by retrieval or startup."""
    from huggingface_hub import snapshot_download
    directory = Path(directory).expanduser().resolve()
    snapshot_download(MINILM_REPO, revision=MINILM_REVISION,
                      allow_patterns=list(MINILM_FILES), local_dir=str(directory))
    for name, expected in MINILM_FILES.items():
        if _sha256(directory / name) != expected:
            raise ValueError(f"embedding artifact checksum mismatch: {name}")
    return directory


class MiniLMEmbedder:
    """Pinned trained sentence encoder, CPU ONNX inference with masked mean pooling.

    Weights must already exist locally. No network, remote code, or fallback on errors.
    This is English short-text retrieval, not a generative model or personal intelligence.
    """
    dimensions = 384
    space_id = f"{MINILM_REPO}@{MINILM_REVISION}:masked-mean-l2:256"

    def __init__(self, directory: Path):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        directory = Path(directory).expanduser().resolve()
        for name, expected in MINILM_FILES.items():
            path = directory / name
            if not path.is_file():
                raise FileNotFoundError(f"missing {path}; run meemee models pull-embeddings")
            if _sha256(path) != expected:
                raise ValueError(f"embedding artifact checksum mismatch: {name}")
        self.tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=256)
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(directory / "onnx/model.onnx"),
                                           sess_options=options, providers=["CPUExecutionProvider"])
        self._lock = threading.Lock()

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        import numpy as np
        if not texts:
            return []
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("embedding input must be non-empty text")
        with self._lock:
            encodings = self.tokenizer.encode_batch(texts)
        arrays = {
            "input_ids": np.asarray([e.ids for e in encodings], dtype=np.int64),
            "attention_mask": np.asarray([e.attention_mask for e in encodings], dtype=np.int64),
            "token_type_ids": np.asarray([e.type_ids for e in encodings], dtype=np.int64),
        }
        inputs = {node.name: arrays[node.name] for node in self.session.get_inputs()}
        tokens = self.session.run(None, inputs)[0]
        mask = arrays["attention_mask"][..., None]
        pooled = (tokens * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1)
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        if pooled.shape != (len(texts), self.dimensions) or not np.isfinite(pooled).all() or (norms <= 0).any():
            raise ValueError("invalid output from embedding model")
        return (pooled / norms).tolist()

    def embed(self, text: str) -> list[float]:
        return self.embed_many([text])[0]


def embedding_space(embedder: Embedder) -> str:
    """Legacy injected encoders have their own space, never the trained model's."""
    return getattr(embedder, "space_id", f"custom:{type(embedder).__module__}.{type(embedder).__qualname__}:{embedder.dimensions}")


def embedder_from_settings(settings):
    directory = getattr(settings, "embedding_model_dir", None)
    return MiniLMEmbedder(directory) if directory else None
