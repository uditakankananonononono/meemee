"""Pure supplied-event replay preparation. No authentication, persistence or evaluation.

The caller must bind owner/source/event ID to an authenticated producer and apply
this decision and monitor firing atomically. A supplied prior record proves nothing
about authorship. Payload equality alone never identifies an event. Canonical JSON
is Python JSON encoding, not RFC 8785 or a cross-language canonicalization claim.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class EventIdentity:
    owner_id: str
    source_id: str
    event_id: str
    payload_sha256: str


@dataclass(frozen=True)
class ReplayDecision:
    status: Literal['new', 'replay', 'conflict']
    identity: EventIdentity
    # Detached canonical text preserves the exact bytes used for the digest.
    canonical_payload: str


def _identifier(value: str) -> str:
    if type(value) is not str or not value or len(value) > 240:
        raise ValueError('bounded exact string identity required')
    if value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('identity whitespace or controls refused')
    try:
        if len(value.encode('utf-8')) > 960:
            raise ValueError('identity byte limit exceeded')
    except UnicodeEncodeError:
        raise ValueError('identity must encode as UTF-8') from None
    return value


def _payload(value: dict, max_bytes: int) -> str:
    if type(value) is not dict:
        raise TypeError('exact payload object required')
    # Preflight bounds stop cycles, excessive nesting/width and oversized scalars
    # before recursive JSON encoding or integer-to-text allocation.
    remaining = 4096
    def visit(item, depth):
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 16:
            raise ValueError('payload structural limit exceeded')
        kind = type(item)
        if kind is str:
            if len(item) > max_bytes:
                raise ValueError('payload string limit exceeded')
            try:
                if len(item.encode('utf-8')) > max_bytes:
                    raise ValueError('payload string byte limit exceeded')
            except UnicodeEncodeError:
                raise ValueError('payload must encode as UTF-8') from None
        elif kind is int:
            if item.bit_length() > 256:
                raise ValueError('payload integer limit exceeded')
        elif kind is float:
            if not math.isfinite(item):
                raise ValueError('finite JSON values required')
        elif kind in (bool, type(None)):
            pass
        elif kind is list:
            if len(item) > 4096:
                raise ValueError('payload list limit exceeded')
            for child in item:
                visit(child, depth + 1)
        elif kind is dict:
            if len(item) > 100:
                raise ValueError('payload object field limit exceeded')
            for key, child in item.items():
                if type(key) is not str:
                    raise TypeError('exact string JSON keys required')
                visit(key, depth + 1)
                visit(child, depth + 1)
        else:
            raise TypeError('exact JSON types required')
    visit(value, 0)
    chunks, size = [], 0
    encoder = json.JSONEncoder(sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                               allow_nan=False)
    for chunk in encoder.iterencode(value):
        size += len(chunk.encode('utf-8'))
        if size > max_bytes:
            raise ValueError('payload byte limit exceeded')
        chunks.append(chunk)
    return ''.join(chunks)


def identify_event(owner_id: str, source_id: str, event_id: str, payload: dict,
                   *, prior: EventIdentity | None = None,
                   max_payload_bytes: int = 32768) -> ReplayDecision:
    """Classify a supplied exact identity and optional matching prior record.

    Wrong-key prior records are refused, never interpreted as absence. Distinct
    event IDs with identical payloads remain distinct. Conflict means the same
    identity has different content and must not evaluate again. This function
    neither records a new event nor suppresses concurrent duplicate evaluation.
    """
    if type(max_payload_bytes) is not int or not 1 <= max_payload_bytes <= 32768:
        raise ValueError('exact payload byte cap between1 and32768 required')
    key = tuple(_identifier(v) for v in (owner_id, source_id, event_id))
    text = _payload(payload, max_payload_bytes)
    identity = EventIdentity(*key, hashlib.sha256(text.encode('utf-8')).hexdigest())
    if prior is None:
        status = 'new'
    else:
        if type(prior) is not EventIdentity:
            raise TypeError('exact prior identity record required')
        prior_key = tuple(_identifier(v) for v in (prior.owner_id, prior.source_id, prior.event_id))
        if key != prior_key:
            raise ValueError('prior identity key mismatch')
        digest = prior.payload_sha256
        if (type(digest) is not str or len(digest) != 64
                or any(c not in '0123456789abcdef' for c in digest)):
            raise ValueError('prior lowercase SHA256 required')
        status = 'replay' if digest == identity.payload_sha256 else 'conflict'
    return ReplayDecision(status, identity, text)
