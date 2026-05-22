import pytest
import json
import sqlite3
import os
import time
from uuid import uuid4
from datetime import datetime, timezone, timedelta
from backend.core.telemetry import MetricsCollector, init_telemetry_schema

@pytest.fixture
def mock_db(tmp_path):
    db_path = tmp_path / "test_telemetry.db"
    # Ensure we are starting fresh
    if db_path.exists():
        os.remove(db_path)

    # We must patch DB_PATH in the telemetry module
    import backend.core.telemetry
    old_db_path = backend.core.telemetry.DB_PATH
    backend.core.telemetry.DB_PATH = db_path

    init_telemetry_schema()
    yield db_path

    # Restore
    backend.core.telemetry.DB_PATH = old_db_path

def test_get_metrics_summary_correctness(mock_db):
    collector = MetricsCollector()

    # Record some metrics
    collector.record_llm_call("model-1", 100, 50, 1000.0, 0.1)
    collector.record_llm_call("model-1", 200, 100, 2000.0, 0.2)

    collector.record_tool_call("tool-1", "ok", 500.0)
    collector.record_tool_call("tool-1", "error", 100.0)

    collector.record_job_completion("job-1", "completed", 5000.0, 1, 1.5)
    collector.record_job_completion("job-2", "failed", 1000.0, 1, 0.5)

    summary = collector.get_metrics_summary()

    # Verify LLM calls
    assert summary["llm_calls"]["total"] == 2
    assert summary["llm_calls"]["tokens_in"] == 300
    assert summary["llm_calls"]["tokens_out"] == 150
    assert summary["llm_calls"]["avg_latency_ms"] == 1500.0
    assert summary["llm_calls"]["total_cost_cents"] == 0.3

    # Verify Tool calls
    assert summary["tool_calls"]["total"] == 2
    assert summary["tool_calls"]["success"] == 1
    assert summary["tool_calls"]["error"] == 1
    assert summary["tool_calls"]["avg_duration_ms"] == 300.0

    # Verify Job completions
    assert summary["job_completions"]["total"] == 2
    assert summary["job_completions"]["completed"] == 1
    assert summary["job_completions"]["failed"] == 1
    assert summary["job_completions"]["avg_duration_ms"] == 3000.0
    assert summary["job_completions"]["total_cost_cents"] == 2.0

def test_get_metrics_summary_since(mock_db):
    collector = MetricsCollector()

    old_time = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    # Ensure now_time is definitely after old_time but before we record the new one
    time.sleep(0.01)
    now_time = datetime.now(timezone.utc).isoformat()
    time.sleep(0.01)

    # Manually insert an old metric
    conn = sqlite3.connect(str(mock_db))
    conn.execute(
        "INSERT INTO metrics (id, metric_type, value_json, recorded_at) VALUES (?, ?, ?, ?)",
        (str(uuid4()), "llm_call", json.dumps({"tokens_in": 10, "tokens_out": 5, "latency_ms": 100, "cost_cents": 0.01}), old_time)
    )
    conn.commit()
    conn.close()

    # Record a new metric
    collector.record_llm_call("model-1", 100, 50, 1000.0, 0.1)

    # Summary since now should only have 1 metric
    summary = collector.get_metrics_summary(since=now_time)
    assert summary["llm_calls"]["total"] == 1
    assert summary["llm_calls"]["tokens_in"] == 100

    # Summary for all time should have 2 metrics
    summary_all = collector.get_metrics_summary()
    assert summary_all["llm_calls"]["total"] == 2
    assert summary_all["llm_calls"]["tokens_in"] == 110
