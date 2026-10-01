"""Standalone encrypted vault compatible with Meemee SecretVault's record schema.
Only the vault database may contain encrypted secret material.
"""
import base64
import os
import sqlite3
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class LocalVault:
    def __init__(self, path, key):
        self.cipher = AESGCM(base64.urlsafe_b64decode(key))
        self.path = str(path)
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS secrets (name TEXT PRIMARY KEY, nonce BLOB NOT NULL, ciphertext BLOB NOT NULL)')
        os.chmod(self.path, 0o600)

    @staticmethod
    def key():
        return base64.urlsafe_b64encode(AESGCM.generate_key(bit_length=256)).decode()

    def put(self, name, value):
        nonce = os.urandom(12)
        cipher = self.cipher.encrypt(nonce, value.encode(), name.encode())
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO secrets VALUES (?,?,?) ON CONFLICT(name) DO UPDATE SET nonce=excluded.nonce,ciphertext=excluded.ciphertext', (name, nonce, cipher))

    def get(self, name):
        with sqlite3.connect(self.path) as db:
            row = db.execute('SELECT nonce,ciphertext FROM secrets WHERE name=?', (name,)).fetchone()
        if row is None:
            raise KeyError('missing credential reference')
        return self.cipher.decrypt(row[0], row[1], name.encode()).decode()
