import time
import json
import sqlite3
import os
from pathlib import Path
import sys

# Add backend to path
sys.path.append(os.getcwd())

import backend.core.telemetry
from backend.core.telemetry import metrics_collector, init_telemetry_schema, _connect

def benchmark():
    # Use a temporary DB for benchmarking
    test_db = Path("benchmark_metrics.db").absolute()
    if test_db.exists():
        test_db.unlink()

    # Patch DB_PATH in both telemetry and config
    backend.core.telemetry.DB_PATH = test_db

    init_telemetry_schema()

    print("Seeding 10,000 metric records...")
    conn = _connect()
    start_seed = time.time()

    # Bulk insert for speed
    metrics = []
    for i in range(4000):
        metrics.append(('llm_call', json.dumps({
            "model": "qwen2.5-coder:7b",
            "tokens_in": 100 + i % 50,
            "tokens_out": 50 + i % 20,
            "latency_ms": 1200.5 + i % 100,
            "cost_cents": 0.0
        }), "2024-01-01T00:00:00Z"))

    for i in range(4000):
        metrics.append(('tool_call', json.dumps({
            "tool_name": "web_search",
            "status": "ok" if i % 10 != 0 else "error",
            "duration_ms": 250.0 + i % 50
        }), "2024-01-01T00:01:00Z"))

    for i in range(2000):
        metrics.append(('job_completion', json.dumps({
            "job_id": f"job-{i}",
            "status": "completed" if i % 20 != 0 else "failed",
            "duration_ms": 5000.0 + i % 500,
            "node_count": 5,
            "total_cost": 0.05
        }), "2024-01-01T00:02:00Z"))

    conn.executemany(
        "INSERT INTO metrics (id, metric_type, value_json, recorded_at) VALUES (lower(hex(randomblob(16))), ?, ?, ?)",
        metrics
    )
    conn.commit()
    conn.close()
    print(f"Seed complete in {time.time() - start_seed:.2f}s")

    print("\nBenchmarking get_metrics_summary (Python-side aggregation)...")
    durations = []
    for _ in range(5):
        start = time.time()
        summary = metrics_collector.get_metrics_summary()
        durations.append(time.time() - start)

    avg_ms = (sum(durations) / len(durations)) * 1000
    print(f"Average latency: {avg_ms:.2f}ms")
    print(f"Sample summary: {json.dumps(summary, indent=2)[:200]}...")

    print("\nBenchmarking check_thresholds (Python-side aggregation)...")
    from backend.core.telemetry import alert_manager
    durations = []
    for _ in range(5):
        start = time.time()
        alerts = alert_manager.check_thresholds()
        durations.append(time.time() - start)

    avg_ms = (sum(durations) / len(durations)) * 1000
    print(f"Average latency: {avg_ms:.2f}ms")

    if test_db.exists():
        test_db.unlink()

if __name__ == "__main__":
    benchmark()
