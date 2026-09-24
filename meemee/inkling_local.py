"""Self-hosted Inkling-Small: hardware floor checks and exact launch commands.

This is real Inkling-Small (276B MoE, 12B active) in every plan. The plans differ only in
the serving engine and weight quantization, never in the model. Floors come from the vLLM
recipe (https://recipes.vllm.ai/thinkingmachines/Inkling-Small) and the measured Unsloth
GGUF file sizes (https://huggingface.co/unsloth/Inkling-Small-GGUF).
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

SERVED_NAME = "thinkingmachines/Inkling-Small"
GB = 1_000_000_000


@dataclass(frozen=True)
class Plan:
    name: str
    engine: str
    weights: str
    min_gpu_gb: float  # aggregated NVIDIA VRAM required (0 = not required)
    min_total_gb: float  # RAM + VRAM required
    disk_gb: float
    note: str


PLANS: dict[str, Plan] = {
    p.name: p
    for p in [
        Plan("vllm-nvfp4", "vllm", "thinkingmachines/Inkling-Small-NVFP4", 180, 180, 180,
             "Fastest. NVIDIA server: TP1 on B300/GB300, TP2 on B200/GB200/H200."),
        Plan("vllm-bf16", "vllm", "thinkingmachines/Inkling-Small", 600, 600, 600,
             "Full precision. TP4 on B300/GB300, TP8 on B200/H200."),
        Plan("llamacpp-q4", "llama.cpp", "unsloth/Inkling-Small-GGUF:UD-Q4_K_XL", 0, 175, 164,
             "Workstation or Mac Studio with ~175GB+ combined RAM/VRAM. 163.3GB of weights."),
        Plan("llamacpp-q3", "llama.cpp", "unsloth/Inkling-Small-GGUF:UD-Q3_K_XL", 0, 130, 120,
             "Same model, heavier compression. 119.6GB of weights, ~130GB+ memory."),
        Plan("llamacpp-q2", "llama.cpp", "unsloth/Inkling-Small-GGUF:UD-Q2_K_XL", 0, 100, 88,
             "Same model, strongest compression and lowest quality. 87.9GB of weights, ~100GB+ memory."),
    ]
}
PREFERENCE = ["vllm-nvfp4", "vllm-bf16", "llamacpp-q4", "llamacpp-q3", "llamacpp-q2"]


@dataclass(frozen=True)
class Hardware:
    ram_gb: float
    gpu_gb: float
    gpu_count: int
    disk_free_gb: float
    system: str


def _ram_bytes() -> int:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        pass
    if platform.system() == "Darwin":
        out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=False)
        if out.returncode == 0 and out.stdout.strip().isdigit():
            return int(out.stdout.strip())
    return 0


def _nvidia_gpus_mib() -> list[int]:
    if shutil.which("nvidia-smi") is None:
        return []
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, check=False, timeout=20,
    )
    if out.returncode != 0:
        return []
    return [int(x.strip()) for x in out.stdout.splitlines() if x.strip().isdigit()]


def detect_hardware(model_dir: Path | None = None) -> Hardware:
    gpus = _nvidia_gpus_mib()
    target = model_dir or Path.home()
    while not target.exists():
        target = target.parent
    return Hardware(
        ram_gb=round(_ram_bytes() / GB, 1),
        gpu_gb=round(sum(gpus) * 1024 * 1024 / GB, 1),
        gpu_count=len(gpus),
        disk_free_gb=round(shutil.disk_usage(target).free / GB, 1),
        system=platform.system(),
    )


def gaps(plan: Plan, hw: Hardware) -> list[str]:
    out = []
    if plan.min_gpu_gb and hw.gpu_gb < plan.min_gpu_gb:
        out.append(f"needs {plan.min_gpu_gb:.0f}GB aggregated NVIDIA VRAM, found {hw.gpu_gb:.0f}GB")
    if hw.ram_gb + hw.gpu_gb < plan.min_total_gb:
        out.append(f"needs {plan.min_total_gb:.0f}GB RAM+VRAM, found {hw.ram_gb + hw.gpu_gb:.0f}GB")
    if hw.disk_free_gb < plan.disk_gb:
        out.append(f"needs {plan.disk_gb:.0f}GB free disk, found {hw.disk_free_gb:.0f}GB")
    return out


def assess(hw: Hardware) -> dict:
    plans = {name: {**asdict(PLANS[name]), "gaps": gaps(PLANS[name], hw)} for name in PREFERENCE}
    runnable = [n for n in PREFERENCE if not plans[n]["gaps"]]
    return {
        "model": SERVED_NAME,
        "hardware": asdict(hw),
        "recommended": runnable[0] if runnable else None,
        "runnable": runnable,
        "plans": plans,
        "verdict": (
            f"This machine can run Inkling-Small with {runnable[0]}."
            if runnable
            else "This machine cannot hold Inkling-Small. Smallest floor is ~100GB RAM+VRAM "
            "(llamacpp-q2); fast serving needs 180GB+ NVIDIA VRAM. Use the HF-router 'inkling' profile meanwhile."
        ),
    }


def tensor_parallel(plan: Plan, hw: Hardware) -> int:
    if hw.gpu_count <= 1:
        return 1
    per_gpu = hw.gpu_gb / hw.gpu_count
    need = max(1, -(-int(plan.min_gpu_gb) // max(int(per_gpu), 1)))
    tp = 1
    while tp < need:
        tp *= 2
    return min(tp, hw.gpu_count)


def launch_command(plan_name: str, hw: Hardware, port: int = 8000, model_dir: str = "models") -> list[str]:
    plan = PLANS[plan_name]
    if plan.engine == "vllm":
        return [
            "vllm", "serve", plan.weights,
            "--served-model-name", SERVED_NAME,
            "--host", "127.0.0.1", "--port", str(port),
            "--tokenizer-mode", "inkling",
            "--reasoning-parser", "inkling",
            "--tool-call-parser", "inkling",
            "--enable-auto-tool-choice",
            "--tensor-parallel-size", str(tensor_parallel(plan, hw)),
            "--kernel-config.enable_flashinfer_autotune=False",
            "--trust-remote-code",
        ]
    quant = plan.weights.split(":")[1]
    first = f"{model_dir}/{quant}/Inkling-Small-{quant}-00001-of-{_shards(quant):05d}.gguf"
    cmd = [
        "llama-server", "--model", first,
        "--alias", SERVED_NAME,
        "--host", "127.0.0.1", "--port", str(port),
        "--jinja", "--ctx-size", "32768",
        "--threads", str(os.cpu_count() or 8),
    ]
    if hw.gpu_count:
        cmd += ["--n-gpu-layers", "99", "-ot", ".ffn_.*_exps.=CPU"]
    return cmd


def download_command(plan_name: str, model_dir: str = "models") -> list[str] | None:
    plan = PLANS[plan_name]
    if plan.engine != "llama.cpp":
        return None  # vLLM downloads the checkpoint itself on first start
    repo, quant = plan.weights.split(":")
    return ["hf", "download", repo, "--local-dir", model_dir, "--include", f"*{quant}*"]


def _shards(quant: str) -> int:
    return {"UD-Q2_K_XL": 3, "UD-Q3_K_XL": 4, "UD-Q4_K_XL": 5}[quant]


VLLM_ENV = {"VLLM_USE_V2_MODEL_RUNNER": "1", "FLASH_ATTENTION_CUTE_DSL_CACHE_ENABLED": "1"}
