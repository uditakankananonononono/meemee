"""PostgreSQL secret vault and key rotation.

With per-host vault.sqlite3 a secret stored by ``meemee vault-put`` on one host was missing on every
other host. ``rotate_keys`` re-encrypted only the local vault.sqlite3 and webhooks.sqlite3, so in
PostgreSQL mode the shared webhook secrets stayed encrypted under the old key and every delivery
failed once the key was replaced. Here the vault is shared, and ``rotate_pg_keys`` re-encrypts the
vault and every webhook subscription secret in one transaction: all rows change or none do.
"""
from __future__ import annotations

import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from meemee.key_rotation import _material
from meemee.secret_cipher import SecretCipher

from ._db import Database


class SecretVault:
    """Same contract as ``meemee.vault.SecretVault``, stored in ``meemee_vault_secrets``."""

    def __init__(self, db: Database, key: str):
        self.db, self.cipher = db, AESGCM(_material(key))

    def ping(self) -> bool:
        with self.db.transaction() as c:
            c.execute("SELECT 1 FROM meemee_vault_secrets LIMIT 0")
        return True

    def put(self, name: str, value: str) -> None:
        if not name or not value:
            raise ValueError("secret name and value cannot be empty")
        nonce = os.urandom(12)
        ciphertext = self.cipher.encrypt(nonce, value.encode(), name.encode())
        with self.db.transaction() as c:
            c.execute("""INSERT INTO meemee_vault_secrets(name,nonce,ciphertext) VALUES (%s,%s,%s)
                         ON CONFLICT (name) DO UPDATE SET nonce=EXCLUDED.nonce, ciphertext=EXCLUDED.ciphertext""", (name, nonce, ciphertext))

    def get(self, name: str) -> str:
        with self.db.transaction() as c:
            row = c.execute("SELECT nonce,ciphertext FROM meemee_vault_secrets WHERE name=%s", (name,)).fetchone()
        if row is None:
            raise KeyError(name)
        return self.cipher.decrypt(bytes(row["nonce"]), bytes(row["ciphertext"]), name.encode()).decode()

    def names(self) -> list[str]:
        with self.db.transaction() as c:
            return [row["name"] for row in c.execute("SELECT name FROM meemee_vault_secrets ORDER BY name").fetchall()]

    def export_metadata(self) -> str:
        names = self.names()
        return json.dumps({"names": names, "count": len(names)})


def rotate_pg_keys(db: Database, old_key: str, new_key: str) -> dict[str, int]:
    """Re-encrypt PostgreSQL vault secrets and webhook subscription secrets under ``new_key``, atomically."""
    old_aes, new_aes = AESGCM(_material(old_key)), AESGCM(_material(new_key))
    old_cipher, new_cipher = SecretCipher(old_key), SecretCipher(new_key)
    with db.transaction() as c:
        vault_rows = c.execute("SELECT name,nonce,ciphertext FROM meemee_vault_secrets ORDER BY name FOR UPDATE").fetchall()
        hook_rows = c.execute("SELECT id,secret FROM meemee_webhook_subscriptions ORDER BY id FOR UPDATE").fetchall()
        for row in vault_rows:
            value = old_aes.decrypt(bytes(row["nonce"]), bytes(row["ciphertext"]), row["name"].encode())
            nonce = os.urandom(12)
            c.execute("UPDATE meemee_vault_secrets SET nonce=%s, ciphertext=%s WHERE name=%s",
                      (nonce, new_aes.encrypt(nonce, value, row["name"].encode()), row["name"]))
        for row in hook_rows:
            if not row["secret"].startswith(SecretCipher.PREFIX):
                raise ValueError("plaintext webhook secret found; start Meemee once with the old key first")
            context = f"webhook-subscription:{row['id']}"
            c.execute("UPDATE meemee_webhook_subscriptions SET secret=%s WHERE id=%s",
                      (new_cipher.encrypt(old_cipher.decrypt(row["secret"], context), context), row["id"]))
    return {"vault_secrets": len(vault_rows), "webhook_secrets": len(hook_rows)}
