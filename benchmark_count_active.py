import time
import os
import shutil
import json
from pathlib import Path
from backend.proposals import ProposalManager, PROPOSALS_DIR

def setup_mock_proposals(num_proposals=1000):
    if PROPOSALS_DIR.exists():
        shutil.rmtree(PROPOSALS_DIR)
    PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Creating {num_proposals} mock proposals...")
    for i in range(num_proposals):
        status = "proposed" if i % 2 == 0 else "completed"
        proposal = {
            "id": f"test_prop_{i}",
            "title": f"Test Proposal {i}",
            "status": status,
            "created_at": time.time()
        }
        fp = PROPOSALS_DIR / f"test_prop_{i}_misc.json"
        fp.write_text(json.dumps(proposal, indent=2), encoding="utf-8")
    print("Done setting up mock proposals.")

def benchmark_count_active(iterations=50):
    manager = ProposalManager()

    print(f"Benchmarking count_active over {iterations} iterations...")
    start_time = time.time()
    for _ in range(iterations):
        manager.count_active()
    end_time = time.time()

    total_time = end_time - start_time
    avg_time = total_time / iterations

    print(f"Total time: {total_time:.4f}s")
    print(f"Average time per call: {avg_time:.4f}s")
    print(f"Result count: {manager.count_active()}")

if __name__ == "__main__":
    setup_mock_proposals(500)
    benchmark_count_active(100)
