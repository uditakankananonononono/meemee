import socket

import pytest
from cryptography.exceptions import InvalidTag

from meemee.key_rotation import rotate_keys
from meemee.vault import SecretVault
from meemee.webhooks import WebhookStore


def test_rotation_reencrypts_vault_and_webhooks(tmp_path,monkeypatch):
    monkeypatch.setattr(socket,"getaddrinfo",lambda *a:[(2,1,6,"",("93.184.216.34",443))])
    old,new=SecretVault.generate_key(),SecretVault.generate_key()
    vault=SecretVault(tmp_path/"vault.sqlite3",old); vault.put("model","top-secret")
    hooks=WebhookStore(tmp_path/"webhooks.sqlite3",encryption_key=old); ident,secret=hooks.subscribe("u","https://x.example/h",{"*"}); hooks.db.close()
    assert rotate_keys(tmp_path,old,new)=={"vault_secrets":1,"webhook_secrets":1}
    assert SecretVault(tmp_path/"vault.sqlite3",new).get("model")=="top-secret"
    rotated=WebhookStore(tmp_path/"webhooks.sqlite3",encryption_key=new)
    stored=rotated.db.execute("SELECT secret FROM webhook_subscriptions WHERE id=?",(ident,)).fetchone()[0]
    assert rotated._decrypt_secret(ident,stored)==secret
    with pytest.raises(InvalidTag): SecretVault(tmp_path/"vault.sqlite3",old).get("model")


def test_wrong_old_key_leaves_all_ciphertext_unchanged(tmp_path,monkeypatch):
    monkeypatch.setattr(socket,"getaddrinfo",lambda *a:[(2,1,6,"",("93.184.216.34",443))])
    old=SecretVault.generate_key(); SecretVault(tmp_path/"vault.sqlite3",old).put("x","y")
    hooks=WebhookStore(tmp_path/"webhooks.sqlite3",encryption_key=old); hooks.subscribe("u","https://x.example/h",{"*"}); hooks.db.close()
    import sqlite3
    before_v=sqlite3.connect(tmp_path/"vault.sqlite3").execute("SELECT name,nonce,ciphertext FROM secrets").fetchall()
    before_h=sqlite3.connect(tmp_path/"webhooks.sqlite3").execute("SELECT id,secret FROM webhook_subscriptions").fetchall()
    with pytest.raises(InvalidTag): rotate_keys(tmp_path,SecretVault.generate_key(),SecretVault.generate_key())
    after_v=sqlite3.connect(tmp_path/"vault.sqlite3").execute("SELECT name,nonce,ciphertext FROM secrets").fetchall()
    after_h=sqlite3.connect(tmp_path/"webhooks.sqlite3").execute("SELECT id,secret FROM webhook_subscriptions").fetchall()
    assert after_v==before_v and after_h==before_h
    assert not (tmp_path/".key-rotation-in-progress").exists()
