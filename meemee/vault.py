from __future__ import annotations

import base64
import json
import os
import sqlite3
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class SecretVault:
    """Small encrypted-at-rest secret store using AES-256-GCM and per-record nonces."""

    def __init__(self, path: Path, key: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            material = base64.urlsafe_b64decode(key.encode())
        except Exception as exc:
            raise ValueError("vault key must be URL-safe base64") from exc
        if len(material) != 32:
            raise ValueError("vault key must decode to exactly 32 bytes")
        self.cipher = AESGCM(material)
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS secrets (name TEXT PRIMARY KEY, nonce BLOB NOT NULL, ciphertext BLOB NOT NULL)")

    @staticmethod
    def generate_key() -> str:
        return base64.urlsafe_b64encode(AESGCM.generate_key(bit_length=256)).decode()

    def put(self, name: str, value: str) -> None:
        if not name or not value:
            raise ValueError("secret name and value cannot be empty")
        nonce = os.urandom(12)
        ciphertext = self.cipher.encrypt(nonce, value.encode(), name.encode())
        with self.db:
            self.db.execute("INSERT INTO secrets(name,nonce,ciphertext) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET nonce=excluded.nonce,ciphertext=excluded.ciphertext", (name, nonce, ciphertext))

    def get(self, name: str) -> str:
        row = self.db.execute("SELECT nonce,ciphertext FROM secrets WHERE name=?", (name,)).fetchone()
        if row is None:
            raise KeyError(name)
        return self.cipher.decrypt(row[0], row[1], name.encode()).decode()

    def names(self) -> list[str]:
        return [row[0] for row in self.db.execute("SELECT name FROM secrets ORDER BY name")]

    def export_metadata(self) -> str:
        return json.dumps({"names": self.names(), "count": len(self.names())})
