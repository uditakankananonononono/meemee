from pathlib import Path


def test_worker_passes_required_webhook_encryption_and_payload_settings():
    text = (Path(__file__).parents[1] / "meemee/worker.py").read_text()
    assert "settings.webhook_max_payload_bytes" in text
    assert "settings.vault_key" in text
    assert 'WebhookStore(settings.data_dir / "webhooks.sqlite3")' not in text
