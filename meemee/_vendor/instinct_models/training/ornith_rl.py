"""Ornith RL fine-tuning preflight. Real RL on even the 9B model needs a large GPU;
this refuses clearly instead of pretending. Inkling fine-tuning is likewise a paid
(Tinker) or 180GB+ GPU route and is documented, not run, here."""
from __future__ import annotations

import shutil
import subprocess


class OrnithRLUnavailable(RuntimeError):
    pass


def _gpu_mem_gb() -> float:
    if not shutil.which("nvidia-smi"):
        return 0.0
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout
        return sum(float(x) for x in out.split()) / 1024
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0


def ornith_rl_preflight(min_gpu_gb: float = 80.0, probe=_gpu_mem_gb) -> dict:
    have = probe()
    if have < min_gpu_gb:
        raise OrnithRLUnavailable(f"Ornith RL needs about {min_gpu_gb:.0f} GB of GPU memory; this machine has {have:.0f} GB. "
                                  "Use Needle LoRA for product-specific training, and Ornith as a local inference route.")
    return {"gpu_gb": have, "ok": True}
