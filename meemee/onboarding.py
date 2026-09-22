from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

from .vault import SecretVault


def initialize(instance_dir: Path, env_file: Path) -> dict:
    """Atomically initialize one secure local instance without printing secrets."""
    if env_file.exists():
        raise FileExistsError(f"refusing to overwrite existing configuration: {env_file}")
    if instance_dir.exists() and any(instance_dir.iterdir()):
        raise FileExistsError(f"refusing to initialize non-empty data directory: {instance_dir}")
    instance_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(instance_dir, 0o700)
    env_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    api_token = secrets.token_urlsafe(48)
    vault_key = SecretVault.generate_key()
    content = (
        f"MEEMEE_DATA_DIR={instance_dir.resolve()}\n"
        f"MEEMEE_API_TOKEN={api_token}\n"
        f"MEEMEE_VAULT_KEY={vault_key}\n"
        "MEEMEE_MODEL_BASE_URL=http://127.0.0.1:11434/v1\n"
        "MEEMEE_MODEL_NAME=qwen2.5-coder:14b\n"
        "MEEMEE_MODEL_API_KEY=local\n"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(env_file, flags, 0o600)
    try:
        os.write(descriptor, content.encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    mode = stat.S_IMODE(env_file.stat().st_mode)
    if mode != 0o600:
        env_file.unlink(missing_ok=True)
        raise RuntimeError("could not enforce owner-only configuration permissions")
    return {
        "status": "initialized",
        "data_dir": str(instance_dir.resolve()),
        "env_file": str(env_file.resolve()),
        "env_mode": oct(mode),
        "next": [
            f"set -a; . {env_file.resolve()}; set +a",
            "meemee preflight --require-model",
            "meemee serve --host 127.0.0.1 --port 8787",
        ],
    }
