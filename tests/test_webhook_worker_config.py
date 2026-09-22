from pathlib import Path


def test_webhook_worker_uses_api_payload_and_encryption_configuration():
    text = (Path(__file__).parents[1] / "meemee/cli.py").read_text()
    block = text[text.index('@app.command("webhook-worker")'):text.index('@app.command("webhook-verify")')]
    assert "settings.webhook_max_payload_bytes" in block
    assert "settings.vault_key" in block
    assert "dispatch_forever" in block
