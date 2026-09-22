import os
import stat

import pytest

from meemee.onboarding import initialize


def test_initialize_secure_instance_without_secret_output(tmp_path):
    data, env = tmp_path / "data", tmp_path / "config" / ".env"
    report = initialize(data, env)
    text = env.read_text()
    assert report["status"] == "initialized"
    assert stat.S_IMODE(data.stat().st_mode) == 0o700
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert "MEEMEE_API_TOKEN=" in text and "MEEMEE_VAULT_KEY=" in text
    assert text.split("MEEMEE_API_TOKEN=", 1)[1].splitlines()[0] not in str(report)
    assert text.split("MEEMEE_VAULT_KEY=", 1)[1].splitlines()[0] not in str(report)


def test_initialize_refuses_existing_config_or_nonempty_data(tmp_path):
    data, env = tmp_path / "data", tmp_path / ".env"
    initialize(data, env)
    before = env.read_bytes()
    with pytest.raises(FileExistsError, match="overwrite"):
        initialize(data, env)
    assert env.read_bytes() == before
    other_data, other_env = tmp_path / "other", tmp_path / "other.env"
    other_data.mkdir(); (other_data / "important").write_text("keep")
    with pytest.raises(FileExistsError, match="non-empty"):
        initialize(other_data, other_env)
    assert not other_env.exists()


def test_initialize_uses_exclusive_create_under_race(tmp_path, monkeypatch):
    data, env = tmp_path / "data", tmp_path / ".env"
    real_open = os.open

    def raced(path, flags, mode=0o777):
        env.write_text("winner")
        return real_open(path, flags, mode)

    monkeypatch.setattr(os, "open", raced)
    with pytest.raises(FileExistsError):
        initialize(data, env)
    assert env.read_text() == "winner"
