"""Report what compute this machine actually offers, and what we currently use.

Run on the deployment box to decide whether it's worth moving off plain
PyTorch-on-CPU:

    uv run python check_hardware.py
"""

import os
import platform
import subprocess
from pathlib import Path

import torch


def sh(command: str) -> str:
    try:
        return subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except Exception:
        return ""


def section(title: str) -> None:
    print(f"\n{'=' * 62}\n{title}\n{'=' * 62}")


def main() -> None:
    section("Machine")
    print(f"platform : {platform.platform()}")
    print(f"machine  : {platform.machine()}")

    cpu_model = sh("lscpu | grep -i 'model name' | head -1 | cut -d: -f2") or platform.processor()
    print(f"cpu      : {cpu_model.strip()}")
    print(f"cores    : {os.cpu_count()} logical")
    for field in ("Core(s) per socket", "Thread(s) per core", "CPU(s)"):
        value = sh(f"lscpu | grep -E '^{field}' | head -1 | cut -d: -f2")
        if value:
            print(f"  {field}: {value.strip()}")

    section("CPU instruction sets that matter for inference")
    flags = sh("lscpu | grep -i '^flags' | head -1").lower()
    for flag, why in [
        ("avx2", "baseline SIMD"),
        ("avx512f", "wide SIMD (usually absent on Core Ultra consumer parts)"),
        ("avx512_bf16", "fast bfloat16"),
        ("avx_vnni", "int8 acceleration — makes quantised models much faster"),
        ("amx_bf16", "matrix engine (Xeon only)"),
    ]:
        print(f"  {'yes' if flag in flags else 'no ':4} {flag:12} {why}")
    if not flags:
        print("  (couldn't read CPU flags — not Linux?)")

    section("Accelerators present")
    npu = Path("/dev/accel/accel0").exists() or "intel_vpu" in sh("lsmod")
    print(f"  {'yes' if npu else 'no ':4} NPU (Intel AI Boost)   /dev/accel/accel0, intel_vpu driver")

    render_nodes = sorted(Path("/dev/dri").glob("renderD*")) if Path("/dev/dri").exists() else []
    print(f"  {'yes' if render_nodes else 'no ':4} iGPU render node       {[p.name for p in render_nodes] or '—'}")

    vga = sh("lspci 2>/dev/null | grep -Ei 'vga|display|3d'")
    for line in vga.splitlines():
        print(f"        {line.strip()}")

    hailo = "hailo" in sh("lspci 2>/dev/null").lower() or "hailo" in sh("lsmod").lower()
    print(f"  {'yes' if hailo else 'no ':4} Hailo-8 M.2 module")

    section("What our code would actually use today")
    print(f"  torch                : {torch.__version__}")
    print(f"  cuda available       : {torch.cuda.is_available()}")
    print(f"  mps available        : {torch.backends.mps.is_available()}")
    xpu = hasattr(torch, "xpu") and torch.xpu.is_available()
    print(f"  xpu (Intel GPU)      : {xpu}")
    print(f"  -> pick_device() picks: {'mps' if torch.backends.mps.is_available() else 'cpu'}")
    print(f"  torch threads        : {torch.get_num_threads()} (of {os.cpu_count()} logical cores)")
    print(f"  interop threads      : {torch.get_num_interop_threads()}")

    section("Optimised runtimes installed")
    for module, what in [
        ("openvino", "Intel CPU/iGPU/NPU"),
        ("optimum.intel", "OpenVINO wrapper for transformers models"),
        ("intel_extension_for_pytorch", "IPEX — CPU bf16/int8 + Arc GPU"),
        ("faster_whisper", "CTranslate2 int8 CPU"),
        ("onnxruntime", "ONNX Runtime"),
    ]:
        try:
            __import__(module)
            status = "installed"
        except ImportError:
            status = "not installed"
        print(f"  {status:15} {module:32} {what}")

    section("Verdict")
    if not torch.cuda.is_available() and not torch.backends.mps.is_available():
        print("  Running plain PyTorch on CPU in float32 — the slowest available path.")
        if npu or render_nodes:
            print("  This machine has acceleration we are not touching at all.")
        print("  Baseline it with a fixed clip before changing anything:")
        print("    uv run python transcribe.py --model turbo --file <clip.wav>")


if __name__ == "__main__":
    main()
