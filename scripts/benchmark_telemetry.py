import os
import time
import json
import sqlite3
import random
from uuid import uuid4
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys

# Add root to sys.path
sys.path.append(os.getcwd())

import backend.core.telemetry
from backend.core.telemetry import (
    init_telemetry_schema,
    metrics_collector,
    alert_manager,
    _connect
)

BENCHMARK_DB = "benchmark_telemetry.db"

def setup_benchmark_db():
    db_path = Path(os.path.abspath(BENCHMARK_DB))
    backend.core.telemetry.DB_PATH = db_path
    if db_path.exists():
        db_path.unlink()
    init_telemetry_schema()

def seed_data(count=10000):
    print(f"Seeding {count} records...")
    start = time.time()

    conn = _connect()

    # Batch insert for speed
    batch = []
    for i in range(count):
        mtype = random.choice(["llm_call", "tool_call", "job_completion"])
        mid = str(uuid4())
        recorded_at = (datetime.now(timezone.utc) - timedelta(minutes=random.randint(0, 1000))).isoformat()

        if mtype == "llm_call":
            val = {
                "model": "qwen2.5-coder:14b",
                "tokens_in": random.randint(100, 1000),
                "tokens_out": random.randint(50, 500),
                "latency_ms": random.uniform(500, 5000),
                "cost_cents": random.uniform(0, 0.1),
            }
        elif mtype == "tool_call":
            val = {
                "tool_name": random.choice(["search", "read_file", "write_file"]),
                "status": random.choice(["ok", "ok", "ok", "error"]),
                "duration_ms": random.uniform(10, 500),
            }
        else: # job_completion
            val = {
                "job_id": str(uuid4()),
                "status": random.choice(["completed", "completed", "completed", "failed"]),
                "duration_ms": random.uniform(1000, 10000),
                "node_count": random.randint(1, 10),
                "total_cost": random.uniform(0.1, 1.0),
            }

        batch.append((mid, mtype, json.dumps(val), recorded_at, None))

        if len(batch) >= 1000:
            conn.executemany(
                "INSERT INTO metrics (id, metric_type, value_json, recorded_at, trace_id) VALUES (?, ?, ?, ?, ?)",
                batch
            )
            batch = []

    if batch:
        conn.executemany(
            "INSERT INTO metrics (id, metric_type, value_json, recorded_at, trace_id) VALUES (?, ?, ?, ?, ?)",
            batch
        )

    conn.commit()
    conn.close()
    print(f"Seeding took {time.time() - start:.2f}s")

def run_benchmark():
    print("\nRunning Benchmark...")

    # Test get_metrics_summary
    start = time.time()
    summary = metrics_collector.get_metrics_summary()
    elapsed_summary = time.time() - start
    print(f"get_metrics_summary: {elapsed_summary*1000:.2f}ms")

    # Test check_thresholds
    start = time.time()
    alerts = alert_manager.check_thresholds()
    elapsed_alerts = time.time() - start
    print(f"check_thresholds: {elapsed_alerts*1000:.2f}ms")

    return elapsed_summary, elapsed_alerts

if __name__ == "__main__":
    setup_benchmark_db()
    seed_data(10000)
    run_benchmark()

    # Cleanup
    if os.path.exists(BENCHMARK_DB):
        os.remove(BENCHMARK_DB)
    if os.path.exists(f"{BENCHMARK_DB}-wal"):
        os.remove(f"{BENCHMARK_DB}-wal")
    if os.path.exists(f"{BENCHMARK_DB}-shm"):
        os.remove(f"{BENCHMARK_DB}-shm")
