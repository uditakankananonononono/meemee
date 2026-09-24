from meemee.inkling_local import (
    PLANS,
    SERVED_NAME,
    Hardware,
    assess,
    download_command,
    launch_command,
    tensor_parallel,
)


def hw(ram=32, gpu=24, n=1, disk=500):
    return Hardware(ram_gb=ram, gpu_gb=gpu, gpu_count=n, disk_free_gb=disk, system="Linux")


def test_consumer_pc_is_below_every_floor():
    r = assess(hw())
    assert r["recommended"] is None and r["runnable"] == []
    assert "cannot hold Inkling-Small" in r["verdict"]
    assert all(p["gaps"] for p in r["plans"].values())


def test_big_ram_workstation_gets_llamacpp_q4_not_a_smaller_model():
    r = assess(hw(ram=256, gpu=24, n=1, disk=400))
    assert r["recommended"] == "llamacpp-q4"
    assert all("Inkling-Small" in PLANS[n].weights for n in r["runnable"])


def test_two_h200_gets_vllm_nvfp4_tp2():
    h = hw(ram=1024, gpu=282, n=2, disk=2000)
    assert assess(h)["recommended"] == "vllm-nvfp4"
    cmd = launch_command("vllm-nvfp4", h)
    assert cmd[:3] == ["vllm", "serve", "thinkingmachines/Inkling-Small-NVFP4"]
    assert cmd[cmd.index("--served-model-name") + 1] == SERVED_NAME
    assert cmd[cmd.index("--tensor-parallel-size") + 1] == "2"
    assert "--tool-call-parser" in cmd and "inkling" in cmd


def test_eight_h200_bf16_uses_tp8():
    h = hw(ram=2048, gpu=8 * 141, n=8, disk=4000)
    assert tensor_parallel(PLANS["vllm-bf16"], h) == 8


def test_llamacpp_command_and_download():
    h = hw(ram=192, gpu=0, n=0, disk=300)
    cmd = launch_command("llamacpp-q3", h, port=9000, model_dir="m")
    assert cmd[0] == "llama-server"
    assert cmd[cmd.index("--model") + 1] == "m/UD-Q3_K_XL/Inkling-Small-UD-Q3_K_XL-00001-of-00004.gguf"
    assert cmd[cmd.index("--alias") + 1] == SERVED_NAME and "9000" in cmd
    assert "--n-gpu-layers" not in cmd
    assert download_command("llamacpp-q3", "m") == [
        "hf", "download", "unsloth/Inkling-Small-GGUF", "--local-dir", "m", "--include", "*UD-Q3_K_XL*"]
    assert download_command("vllm-nvfp4") is None


def test_disk_gap_reported():
    r = assess(hw(ram=256, gpu=0, n=0, disk=50))
    assert any("free disk" in g for g in r["plans"]["llamacpp-q4"]["gaps"])
