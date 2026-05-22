import os
import time
import json
import sqlite3
import random
from uuid import uuid4
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Mock DB_PATH for benchmark
os.environ["DB_PATH"] = "benchmark_telemetry.db"
from backend.core.telemetry import MetricsCollector, init_telemetry_schema

def seed_data(count=5000):
    collector = MetricsCollector()
    init_telemetry_schema()

    print(f"Seeding {count} metrics...")
    start_time = time.time()

    # Batch insertion for speed in seeding
    conn = sqlite3.connect("benchmark_telemetry.db")

    # We'll use the collector's _insert logic but batch it for the benchmark setup
    metrics_to_insert = []

    for i in range(count):
        mtype = random.choice(["llm_call", "tool_call", "job_completion"])
        recorded_at = (datetime.now(timezone.utc) - timedelta(minutes=random.randint(0, 1000))).isoformat()

        if mtype == "llm_call":
            val = {
                "model": "qwen2.5-coder:14b",
                "tokens_in": random.randint(100, 1000),
                "tokens_out": random.randint(50, 500),
                "latency_ms": random.uniform(200, 2000),
                "cost_cents": random.uniform(0, 0.5)
            }
        elif mtype == "tool_call":
            val = {
                "tool_name": "web_search",
                "status": random.choice(["ok", "ok", "ok", "error"]),
                "duration_ms": random.uniform(50, 500)
            }
        else: # job_completion
            val = {
                "job_id": str(uuid4()),
                "status": random.choice(["completed", "completed", "failed"]),
                "duration_ms": random.uniform(1000, 10000),
                "total_cost": random.uniform(0.1, 2.0)
            }

        metrics_to_insert.append((str(uuid4()), mtype, json.dumps(val), recorded_at, None))

    conn.executemany(
        "INSERT INTO metrics (id, metric_type, value_json, recorded_at, trace_id) VALUES (?, ?, ?, ?, ?)",
        metrics_to_insert
    )
    conn.commit()
    conn.close()

    print(f"Seed complete in {time.time() - start_time:.2f}s")

def run_benchmark():
    collector = MetricsCollector()

    # Warm up
    collector.get_metrics_summary()

    iterations = 50
    start_time = time.time()
    for _ in range(iterations):
        summary = collector.get_metrics_summary()
    end_time = time.time()

    avg_time_ms = ((end_time - start_time) / iterations) * 1000
    print(f"Average get_metrics_summary execution time: {avg_time_ms:.2f}ms")
    return avg_time_ms

if __name__ == "__main__":
    if os.path.exists("benchmark_telemetry.db"):
        os.remove("benchmark_telemetry.db")

    seed_data(50000) # Use 50k for more noticeable difference
    run_benchmark()
