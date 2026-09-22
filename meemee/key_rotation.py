from __future__ import annotations

import base64
import os
import sqlite3
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .secret_cipher import SecretCipher


def _material(key: str) -> bytes:
    try:
        material = base64.urlsafe_b64decode(key.encode())
    except Exception as exc:
        raise ValueError("key must be URL-safe base64") from exc
    if len(material) != 32:
        raise ValueError("key must decode to 32 bytes")
    return material


def _backup(database: Path, destination: Path) -> None:
    source = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError(f"rotation backup integrity failed for {database.name}")
    finally:
        source.close()
        target.close()


def _restore(backup: Path, database: Path) -> None:
    source = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
    target = sqlite3.connect(database)
    try:
        source.backup(target)
    finally:
        source.close()
        target.close()


def rotate_keys(data_dir: Path, old_key: str, new_key: str) -> dict[str, int]:
    """Re-encrypt all supported secrets, restoring verified backups on any failure."""
    old_aes, new_aes = AESGCM(_material(old_key)), AESGCM(_material(new_key))
    old_cipher, new_cipher = SecretCipher(old_key), SecretCipher(new_key)
    vault_path, webhook_path = data_dir / "vault.sqlite3", data_dir / "webhooks.sqlite3"
    paths = [path for path in (vault_path, webhook_path) if path.exists()]
    data_dir.mkdir(parents=True, exist_ok=True)
    marker = data_dir / ".key-rotation-in-progress"
    if marker.exists():
        raise RuntimeError("an earlier key rotation was interrupted; restore the pre-rotation backup")
    with tempfile.TemporaryDirectory(prefix="meemee-key-rotation-", dir=data_dir) as temporary:
        backup_dir = Path(temporary)
        backups = {path: backup_dir / path.name for path in paths}
        for path, backup in backups.items():
            _backup(path, backup)
        marker.write_text("Do not start Meemee; key rotation is in progress.\n")
        os.chmod(marker, 0o600)
        vault = sqlite3.connect(vault_path) if vault_path.exists() else None
        hooks = sqlite3.connect(webhook_path) if webhook_path.exists() else None
        try:
            vault_rows = vault.execute("SELECT name,nonce,ciphertext FROM secrets").fetchall() if vault else []
            hook_rows = hooks.execute("SELECT id,secret FROM webhook_subscriptions").fetchall() if hooks else []
            decoded_vault = [
                (name, old_aes.decrypt(nonce, ciphertext, name.encode()).decode())
                for name, nonce, ciphertext in vault_rows
            ]
            decoded_hooks = []
            for ident, secret in hook_rows:
                if not secret.startswith(SecretCipher.PREFIX):
                    raise ValueError(
                        "plaintext webhook secret found; start Meemee once with the old key first"
                    )
                decoded_hooks.append(
                    (ident, old_cipher.decrypt(secret, f"webhook-subscription:{ident}"))
                )
            vault_updates = []
            for name, value in decoded_vault:
                nonce = os.urandom(12)
                ciphertext = new_aes.encrypt(nonce, value.encode(), name.encode())
                vault_updates.append((nonce, ciphertext, name))
            hook_updates = [
                (new_cipher.encrypt(value, f"webhook-subscription:{ident}"), ident)
                for ident, value in decoded_hooks
            ]
            if vault:
                vault.execute("BEGIN IMMEDIATE")
                vault.executemany(
                    "UPDATE secrets SET nonce=?,ciphertext=? WHERE name=?", vault_updates
                )
            if hooks:
                hooks.execute("BEGIN IMMEDIATE")
                hooks.executemany(
                    "UPDATE webhook_subscriptions SET secret=? WHERE id=?", hook_updates
                )
            if hooks:
                hooks.commit()
            if vault:
                vault.commit()
            marker.unlink()
            return {
                "vault_secrets": len(vault_updates),
                "webhook_secrets": len(hook_updates),
            }
        except Exception:
            if vault:
                vault.rollback()
            if hooks:
                hooks.rollback()
            if vault:
                vault.close()
                vault = None
            if hooks:
                hooks.close()
                hooks = None
            for path, backup in backups.items():
                _restore(backup, path)
            marker.unlink(missing_ok=True)
            raise
        finally:
            if vault:
                vault.close()
            if hooks:
                hooks.close()
