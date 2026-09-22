from __future__ import annotations

import base64
import json


def encode_cursor(sort_value: str, ident: str) -> str:
    return base64.urlsafe_b64encode(
        json.dumps([sort_value, ident], separators=(",", ":")).encode()
    ).decode().rstrip("=")


def decode_cursor(value: str) -> tuple[str, str]:
    try:
        decoded = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        if (
            not isinstance(decoded, list)
            or len(decoded) != 2
            or not all(isinstance(item, str) and item for item in decoded)
        ):
            raise ValueError
        return decoded[0], decoded[1]
    except Exception as exc:
        raise ValueError("invalid pagination cursor") from exc
