from meemee.secret_cipher import SecretCipher
from meemee.vault import SecretVault


def test_cipher_roundtrip_is_randomized():
    cipher=SecretCipher(SecretVault.generate_key())
    first=cipher.encrypt("secret","webhook:one"); second=cipher.encrypt("secret","webhook:one")
    assert first!=second and "secret" not in first
    assert cipher.decrypt(first,"webhook:one")=="secret"


def test_context_is_authenticated():
    import pytest
    cipher = SecretCipher(SecretVault.generate_key())
    encrypted = cipher.encrypt("secret", "webhook:one")
    from cryptography.exceptions import InvalidTag
    with pytest.raises(InvalidTag):
        cipher.decrypt(encrypted, "webhook:two")


def test_webhook_store_requires_key_and_migrates_plaintext(tmp_path, monkeypatch):
    import socket

    import pytest

    from meemee.webhooks import WebhookStore
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a: [(2,1,6,"",("93.184.216.34",443))])
    with pytest.raises(ValueError, match="MEEMEE_VAULT_KEY"):
        WebhookStore(tmp_path / "missing.db")
    key = SecretVault.generate_key()
    db = tmp_path / "legacy.db"
    first = WebhookStore(db, encryption_key=key)
    ident, visible = first.subscribe("u", "https://hooks.example/a", {"*"})
    first.db.execute("UPDATE webhook_subscriptions SET secret=? WHERE id=?", ("legacy-plaintext", ident))
    first.db.close()
    upgraded = WebhookStore(db, encryption_key=key)
    stored = upgraded.db.execute("SELECT secret FROM webhook_subscriptions WHERE id=?", (ident,)).fetchone()[0]
    assert stored.startswith("enc:v1:") and "legacy-plaintext" not in stored
    assert upgraded._decrypt_secret(ident, stored) == "legacy-plaintext"
    assert visible != "legacy-plaintext"


def test_wrong_key_fails_closed(tmp_path, monkeypatch):
    import socket

    import pytest
    from cryptography.exceptions import InvalidTag

    from meemee.webhooks import WebhookStore
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a: [(2,1,6,"",("93.184.216.34",443))])
    db = tmp_path / "encrypted.db"
    store = WebhookStore(db, encryption_key=SecretVault.generate_key())
    store.subscribe("u", "https://hooks.example/a", {"*"})
    store.db.close()
    with pytest.raises(InvalidTag):
        WebhookStore(db, encryption_key=SecretVault.generate_key())
