from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class SecretCipher:
    """Versioned AES-256-GCM encryption for database secret columns."""

    PREFIX = "enc:v1:"

    def __init__(self, key: str):
        try:
            material = base64.urlsafe_b64decode(key.encode())
        except Exception as exc:
            raise ValueError("secret encryption key must be URL-safe base64") from exc
        if len(material) != 32:
            raise ValueError("secret encryption key must decode to 32 bytes")
        self.cipher = AESGCM(material)

    def encrypt(self, value: str, context: str) -> str:
        nonce = os.urandom(12)
        ciphertext = self.cipher.encrypt(nonce, value.encode(), context.encode())
        return self.PREFIX + base64.urlsafe_b64encode(nonce + ciphertext).decode()

    def decrypt(self, value: str, context: str) -> str:
        if not value.startswith(self.PREFIX):
            raise ValueError("secret is not encrypted")
        payload = base64.urlsafe_b64decode(value[len(self.PREFIX):].encode())
        return self.cipher.decrypt(payload[:12], payload[12:], context.encode()).decode()
