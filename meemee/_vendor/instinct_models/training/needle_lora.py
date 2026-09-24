"""LoRA fine-tune of Needle on a product dataset, using the documented CLI:

    needle finetune data.jsonl --epochs N --out adapter.pkl
    needle build checkpoints/needle2.pkl --lora adapter.pkl --out tuned.cact

Runs locally (JAX on CPU/GPU/Metal). Never uploads: ``--upload`` is not passed and
NEEDLE_HF_REPO is removed from the child env. The adapter registry records dataset
hash, command, and output hash so a product can pin exactly what it serves.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

Runner = Callable[[list[str], dict], subprocess.CompletedProcess]


def _run(cmd: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=6 * 3600)


@dataclass
class NeedleLoRAJob:
    product: str
    dataset_jsonl: str
    out_dir: str
    base_checkpoint: str = "checkpoints/needle2.pkl"
    epochs: int = 10
    val_split: float = 0.1


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def train_needle_lora(job: NeedleLoRAJob, runner: Runner = _run, cli: str = "needle") -> dict:
    data = Path(job.dataset_jsonl)
    if not data.is_file():
        raise FileNotFoundError(job.dataset_jsonl)
    manifest_path = Path(str(data) + ".manifest.json")
    ds_manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    if ds_manifest and ds_manifest.get("sha256") != _sha(data):
        raise ValueError("dataset changed since its manifest was written; rebuild it")
    if runner is _run and shutil.which(cli) is None:
        raise RuntimeError("needle CLI not found; pip install cactus-needle")
    out = Path(job.out_dir); out.mkdir(parents=True, exist_ok=True)
    adapter, tuned = out / f"{job.product}-adapter.pkl", out / f"{job.product}-tuned.cact"
    env = {k: v for k, v in os.environ.items() if k not in ("NEEDLE_HF_REPO", "OPENROUTER_API_KEY")}
    steps = [[cli, "finetune", str(data), "--epochs", str(job.epochs), "--val-split", str(job.val_split), "--out", str(adapter)],
             [cli, "build", job.base_checkpoint, "--lora", str(adapter), "--out", str(tuned)]]
    logs = []
    for cmd in steps:
        res = runner(cmd, env)
        logs.append({"cmd": cmd, "returncode": res.returncode, "stdout_tail": (res.stdout or "")[-2000:],
                     "stderr_tail": (res.stderr or "")[-2000:]})
        if res.returncode != 0:
            raise RuntimeError(f"{cmd[1]} failed (exit {res.returncode}): {(res.stderr or '')[-500:]}")
    if not tuned.is_file():
        raise RuntimeError("needle build reported success but no .cact was written")
    record = {"product": job.product, "dataset_sha256": _sha(data), "dataset_rows": ds_manifest.get("rows"),
              "train_locally_only": ds_manifest.get("train_locally_only", True), "adapter": str(adapter),
              "tuned_weights": str(tuned), "tuned_sha256": _sha(tuned), "epochs": job.epochs,
              "trained_at": datetime.now(timezone.utc).isoformat(), "logs": logs}
    reg = out / "registry.jsonl"
    with reg.open("a") as f:
        f.write(json.dumps(record) + "\n")
    return record
