"""
D4 Model Cleanup — Remove non-router Ollama models (~70 GB)
Run: python scripts/d4_model_cleanup.py
"""
import subprocess
import sys

REMOVE = [
    "ue5-slides:latest",
    "ue5-slides:v2",
    "ue5-slides:q8",
    "ue5-slides:q8v2",
    "ue5-slides:q4",
    "ue5-slides:q4v2",
    "deepseek-r1:14b",
    "deepseek-r1:7b",
    "qwen3:8b",
    "qwen2.5:7b-instruct",
    "gemma3:4b",
]

print("=== LocalMind D4 Model Cleanup ===")
print(f"Removing {len(REMOVE)} models...\n")

success, failed = [], []

for model in REMOVE:
    print(f"  Removing {model}...", end=" ", flush=True)
    result = subprocess.run(
        ["ollama", "rm", model],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print("OK")
        success.append(model)
    else:
        err = result.stderr.strip() or result.stdout.strip()
        print(f"SKIP ({err[:60]})")
        failed.append((model, err))

print(f"\n=== Done: {len(success)} removed, {len(failed)} skipped ===")
if failed:
    print("Skipped (may not have been installed):")
    for m, e in failed:
        print(f"  {m}: {e[:80]}")

print("\nRunning ollama list to confirm...")
subprocess.run(["ollama", "list"])
