"""Minimal Modal smoke-test app.

Spins up one H100 container, prints `nvidia-smi`, returns.
Per-token cost is well under $0.01 — safe to run across the whole pool to
verify the orchestrator works end-to-end before swapping in your real workload.

Run via the orchestrator:

    python -m modal_orchestrator run \
        --tokens tokens.csv \
        --config examples/smoke-h100.py \
        --max-parallel 5

Or directly (single workspace, for one-off testing):

    modal run examples/smoke-h100.py
"""
import subprocess

import modal

app = modal.App("orchestrator-smoke")

image = modal.Image.debian_slim(python_version="3.11")


@app.function(gpu="H100", image=image, timeout=120)
def check_gpu() -> str:
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
         "--format=csv,noheader"],
        capture_output=True,
        text=True,
        check=False,
    )
    line = result.stdout.strip() or result.stderr.strip() or "<no nvidia-smi output>"
    print(f"GPU info: {line}")
    return line


@app.local_entrypoint()
def main() -> None:
    info = check_gpu.remote()
    print(f"check_gpu returned: {info}")
