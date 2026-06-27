import os
import sqlite3
import sys
import pytest

# Add current directory to path so we can import backend
sys.path.append(os.getcwd())

from backend.core.telemetry import MetricsCollector, alert_manager, init_telemetry_schema

@pytest.fixture
def empty_db(tmp_path):
    db_path = tmp_path / "empty_telemetry.db"

    # Monkeypatch DB_PATH for telemetry
    import backend.core.telemetry
    old_path = backend.core.telemetry.DB_PATH
    backend.core.telemetry.DB_PATH = str(db_path)

    init_telemetry_schema()
    yield str(db_path)

    backend.core.telemetry.DB_PATH = old_path

def test_get_metrics_summary_empty(empty_db):
    mc = MetricsCollector()
    summary = mc.get_metrics_summary()

    assert summary["llm_calls"]["total"] == 0
    assert summary["tool_calls"]["total"] == 0
    assert summary["job_completions"]["total"] == 0
    assert summary["llm_calls"]["avg_latency_ms"] == 0
    assert summary["job_completions"]["avg_duration_ms"] == 0

def test_check_thresholds_empty(empty_db):
    alerts = alert_manager.check_thresholds()
    assert alerts == []
