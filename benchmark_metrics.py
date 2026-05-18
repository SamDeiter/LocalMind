
import time
import sqlite3
import json
import uuid
import os
from datetime import datetime, timezone

# Ensure we use the same DB path as telemetry.py might use if imported
os.environ["DB_PATH"] = "data/localmind.db"
DB_PATH = "data/localmind.db"

def setup_large_db(n=10000):
    if not os.path.exists("data"):
        os.makedirs("data")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        DROP TABLE IF EXISTS metrics;
        DROP TABLE IF EXISTS jobs;
        DROP TABLE IF EXISTS eval_runs;
        CREATE TABLE metrics (
            id TEXT PRIMARY KEY,
            metric_type TEXT NOT NULL,
            value_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            trace_id TEXT
        );
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY,
            workspace_id TEXT,
            status TEXT,
            cost_cents REAL,
            created_at TEXT
        );
        CREATE TABLE eval_runs (
            id TEXT PRIMARY KEY,
            score REAL,
            duration_ms INTEGER
        );
    """)

    # Fill metrics
    metrics = []
    for i in range(n):
        metrics.append((
            str(uuid.uuid4()),
            "llm_call",
            json.dumps({"tokens_in": 100, "tokens_out": 200, "latency_ms": 500.5, "cost_cents": 0.01}),
            datetime.now(timezone.utc).isoformat(),
            None
        ))
        metrics.append((
            str(uuid.uuid4()),
            "tool_call",
            json.dumps({"tool_name": "test", "status": "ok", "duration_ms": 50.0}),
            datetime.now(timezone.utc).isoformat(),
            None
        ))
        metrics.append((
            str(uuid.uuid4()),
            "job_completion",
            json.dumps({"job_id": "job1", "status": "completed", "duration_ms": 1000.0, "total_cost": 0.5}),
            datetime.now(timezone.utc).isoformat(),
            None
        ))

    conn.executemany("INSERT INTO metrics VALUES (?, ?, ?, ?, ?)", metrics)

    # Fill jobs
    jobs = []
    for i in range(n):
        jobs.append((str(uuid.uuid4()), "default", "completed", 0.5, datetime.now(timezone.utc).isoformat()))
        jobs.append((str(uuid.uuid4()), "default", "failed", 0.1, datetime.now(timezone.utc).isoformat()))
        jobs.append((str(uuid.uuid4()), "default", "running", 0.0, datetime.now(timezone.utc).isoformat()))
    conn.executemany("INSERT INTO jobs (id, workspace_id, status, cost_cents, created_at) VALUES (?, ?, ?, ?, ?)", jobs)

    # Fill eval_runs
    evals = []
    for i in range(n):
        evals.append((str(uuid.uuid4()), 0.85, 1200))
    conn.executemany("INSERT INTO eval_runs (id, score, duration_ms) VALUES (?, ?, ?)", evals)

    conn.commit()
    conn.close()

def benchmark_current_telemetry():
    from backend.core.telemetry import metrics_collector
    start = time.time()
    summary = metrics_collector.get_metrics_summary()
    end = time.time()
    print(f"Current Telemetry Summary: {end - start:.4f}s")
    return summary

def benchmark_new_telemetry():
    conn = sqlite3.connect(DB_PATH)
    start = time.time()
    cursor = conn.execute("""
        SELECT
            metric_type,
            count(*) as total,
            sum(json_extract(value_json, '$.tokens_in')) as tokens_in,
            sum(json_extract(value_json, '$.tokens_out')) as tokens_out,
            sum(json_extract(value_json, '$.latency_ms')) as latency_sum,
            sum(json_extract(value_json, '$.cost_cents')) as cost_sum,
            sum(CASE WHEN json_extract(value_json, '$.status') = 'ok' THEN 1 ELSE 0 END) as success_count,
            sum(CASE WHEN json_extract(value_json, '$.status') = 'completed' THEN 1 ELSE 0 END) as completed_count,
            sum(json_extract(value_json, '$.duration_ms')) as duration_sum,
            sum(json_extract(value_json, '$.total_cost')) as total_cost_sum
        FROM metrics
        GROUP BY metric_type
    """)
    rows = cursor.fetchall()

    # Process results into the same format
    summary = {
        "llm_calls": {"total": 0, "tokens_in": 0, "tokens_out": 0, "avg_latency_ms": 0, "total_cost_cents": 0},
        "tool_calls": {"total": 0, "success": 0, "error": 0, "avg_duration_ms": 0},
        "job_completions": {"total": 0, "completed": 0, "failed": 0, "avg_duration_ms": 0, "total_cost_cents": 0}
    }

    for row in rows:
        mtype, total, tokens_in, tokens_out, lat_sum, cost_sum, succ, comp, dur_sum, tot_cost = row
        if mtype == "llm_call":
            summary["llm_calls"] = {
                "total": total,
                "tokens_in": int(tokens_in or 0),
                "tokens_out": int(tokens_out or 0),
                "avg_latency_ms": round(lat_sum / total, 1) if total else 0,
                "total_cost_cents": round(cost_sum or 0, 4)
            }
        elif mtype == "tool_call":
            summary["tool_calls"] = {
                "total": total,
                "success": int(succ or 0),
                "error": total - int(succ or 0),
                "avg_duration_ms": round(dur_sum / total, 1) if total else 0
            }
        elif mtype == "job_completion":
            summary["job_completions"] = {
                "total": total,
                "completed": int(comp or 0),
                "failed": total - int(comp or 0),
                "avg_duration_ms": round(dur_sum / total, 1) if total else 0,
                "total_cost_cents": round(tot_cost or 0, 4)
            }

    end = time.time()
    conn.close()
    print(f"New SQL Telemetry Summary: {end - start:.4f}s")
    return summary

def benchmark_current_system_metrics():
    # Mocking what's in backend/routes/system.py
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    start = time.time()
    total_jobs = 0
    completed_jobs = 0
    failed_jobs = 0
    running_jobs = 0
    total_cost_cents = 0.0

    rows = conn.execute("SELECT status, cost_cents FROM jobs").fetchall()
    for row in rows:
        total_jobs += 1
        s = row["status"]
        if s == "completed":
            completed_jobs += 1
        elif s == "failed":
            failed_jobs += 1
        elif s == "running":
            running_jobs += 1
        total_cost_cents += row["cost_cents"] or 0.0

    eval_total = 0
    eval_avg_score = None
    eval_avg_duration_ms = 0.0
    eval_rows = conn.execute("SELECT score, duration_ms FROM eval_runs").fetchall()
    scores = []
    durations = []
    for r in eval_rows:
        eval_total += 1
        if r["score"] is not None:
            scores.append(r["score"])
        if r["duration_ms"] is not None:
            durations.append(r["duration_ms"])

    if scores:
        eval_avg_score = round(sum(scores) / len(scores), 3)
    if durations:
        eval_avg_duration_ms = round(sum(durations) / len(durations), 1)

    end = time.time()
    conn.close()
    print(f"Current System Metrics: {end - start:.4f}s")

def benchmark_new_system_metrics():
    conn = sqlite3.connect(DB_PATH)
    start = time.time()

    job_stats = conn.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
            SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) as running,
            SUM(cost_cents) as total_cost
        FROM jobs
    """).fetchone()

    eval_stats = conn.execute("""
        SELECT
            COUNT(*) as total,
            AVG(score) as avg_score,
            AVG(duration_ms) as avg_duration
        FROM eval_runs
    """).fetchone()

    end = time.time()
    conn.close()
    print(f"New SQL System Metrics: {end - start:.4f}s")

if __name__ == "__main__":
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    setup_large_db(10000) # 30k metrics, 30k jobs, 10k evals
    benchmark_current_telemetry()
    benchmark_new_telemetry()
    benchmark_current_system_metrics()
    benchmark_new_system_metrics()
