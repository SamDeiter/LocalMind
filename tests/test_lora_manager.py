"""
Tests for backend.inference.lora_manager — LoRA adapter registry.

Uses a real in-memory SQLite database (patched via _get_conn) so that
actual SQL queries run against the same schema the production code uses.
"""

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.inference.lora_manager import (
    LoRAAdapter,
    LoRAManager,
    _ensure_table,
    _get_conn,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_in_memory_conn() -> sqlite3.Connection:
    """Create an in-memory SQLite connection with the same config as prod."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _sample_adapter(**overrides) -> LoRAAdapter:
    """Return a default LoRAAdapter, with optional field overrides."""
    defaults = dict(
        adapter_id="research-lora-v1",
        base_model="qwen3:8b",
        task_types=["research", "writing"],
        path="/data/lora_adapters/research-lora-v1",
        vram_overhead_mb=128,
        enabled=True,
    )
    defaults.update(overrides)
    return LoRAAdapter(**defaults)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_path(tmp_path):
    """Create a temp SQLite file and initialise the lora_adapters table."""
    p = tmp_path / "lora_test.db"
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    _ensure_table(conn)
    conn.close()
    return p


@pytest.fixture()
def patched_conn(db_path):
    """Patch _get_conn so every call returns a connection to our temp DB.

    Each call gets a fresh connection (just like prod), but they all
    point at the same temp file so data persists across calls within
    a single test.
    """
    def _fake_get_conn():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    with patch("backend.inference.lora_manager._get_conn", side_effect=_fake_get_conn):
        yield db_path


@pytest.fixture()
def manager(patched_conn):
    """A LoRAManager wired to the temp DB with no adapters_dir."""
    return LoRAManager(adapters_dir=None)


@pytest.fixture()
def manager_with_dir(patched_conn, tmp_path):
    """A LoRAManager wired to the temp DB with a real adapters_dir."""
    return LoRAManager(adapters_dir=str(tmp_path / "adapters"))


# ===================================================================
# LoRAAdapter dataclass tests
# ===================================================================


class TestLoRAAdapterToDict:
    def test_returns_all_keys(self):
        adapter = _sample_adapter()
        d = adapter.to_dict()
        assert set(d.keys()) == {
            "adapter_id", "base_model", "task_types",
            "path", "vram_overhead_mb", "enabled",
        }

    def test_values_match_fields(self):
        adapter = _sample_adapter(adapter_id="abc", enabled=False)
        d = adapter.to_dict()
        assert d["adapter_id"] == "abc"
        assert d["enabled"] is False
        assert d["task_types"] == ["research", "writing"]

    def test_task_types_is_list(self):
        adapter = _sample_adapter(task_types=["code"])
        d = adapter.to_dict()
        assert isinstance(d["task_types"], list)
        assert d["task_types"] == ["code"]


class TestLoRAAdapterFromRow:
    def _row(self, **overrides):
        """Build a sqlite3.Row-like dict backed by a real Row object."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        defaults = dict(
            adapter_id="a1",
            base_model="qwen3:8b",
            task_types_json='["research"]',
            path="/tmp/a1",
            vram_overhead_mb=64,
            enabled=1,
        )
        defaults.update(overrides)
        cols = ", ".join(defaults.keys())
        placeholders = ", ".join("?" for _ in defaults)
        conn.execute(f"CREATE TABLE _tmp ({cols})")
        conn.execute(f"INSERT INTO _tmp VALUES ({placeholders})", tuple(defaults.values()))
        row = conn.execute("SELECT * FROM _tmp").fetchone()
        conn.close()
        return row

    def test_basic_construction(self):
        row = self._row()
        adapter = LoRAAdapter.from_row(row)
        assert adapter.adapter_id == "a1"
        assert adapter.base_model == "qwen3:8b"
        assert adapter.task_types == ["research"]
        assert adapter.path == "/tmp/a1"
        assert adapter.vram_overhead_mb == 64
        assert adapter.enabled is True

    def test_enabled_false(self):
        row = self._row(enabled=0)
        adapter = LoRAAdapter.from_row(row)
        assert adapter.enabled is False

    def test_malformed_json_returns_empty_list(self):
        row = self._row(task_types_json="{not json at all")
        adapter = LoRAAdapter.from_row(row)
        assert adapter.task_types == []

    def test_none_task_types_json_returns_empty_list(self):
        row = self._row(task_types_json=None)
        adapter = LoRAAdapter.from_row(row)
        assert adapter.task_types == []

    def test_empty_string_task_types_json_returns_empty_list(self):
        row = self._row(task_types_json="")
        adapter = LoRAAdapter.from_row(row)
        assert adapter.task_types == []

    def test_multiple_task_types(self):
        row = self._row(task_types_json='["code", "research", "writing"]')
        adapter = LoRAAdapter.from_row(row)
        assert adapter.task_types == ["code", "research", "writing"]


# ===================================================================
# LoRAManager.__init__ tests
# ===================================================================


class TestLoRAManagerInit:
    def test_creates_adapters_dir(self, patched_conn, tmp_path):
        target = tmp_path / "new_dir" / "nested"
        assert not target.exists()
        LoRAManager(adapters_dir=str(target))
        assert target.is_dir()

    def test_none_adapters_dir_no_error(self, patched_conn):
        mgr = LoRAManager(adapters_dir=None)
        assert mgr._adapters_dir is None

    def test_table_exists_after_init(self, patched_conn):
        LoRAManager(adapters_dir=None)
        conn = sqlite3.connect(str(patched_conn))
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='lora_adapters'"
        ).fetchall()
        conn.close()
        assert len(tables) == 1


# ===================================================================
# LoRAManager.register_adapter tests
# ===================================================================


class TestRegisterAdapter:
    def test_insert_new_adapter(self, manager):
        adapter = _sample_adapter()
        manager.register_adapter(adapter)

        result = manager.get_adapter_by_id(adapter.adapter_id)
        assert result is not None
        assert result.base_model == "qwen3:8b"

    def test_upsert_existing_adapter(self, manager):
        adapter = _sample_adapter(vram_overhead_mb=100)
        manager.register_adapter(adapter)

        updated = _sample_adapter(vram_overhead_mb=200)
        manager.register_adapter(updated)

        result = manager.get_adapter_by_id("research-lora-v1")
        assert result.vram_overhead_mb == 200

    def test_raises_file_not_found_when_path_missing(self, manager_with_dir):
        adapter = _sample_adapter(path="/nonexistent/path/weights")
        with pytest.raises(FileNotFoundError, match="Adapter weights not found"):
            manager_with_dir.register_adapter(adapter)

    def test_succeeds_when_adapters_dir_none(self, manager):
        adapter = _sample_adapter(path="/totally/fake/path")
        manager.register_adapter(adapter)
        assert manager.get_adapter_by_id(adapter.adapter_id) is not None

    def test_succeeds_when_path_exists(self, manager_with_dir, tmp_path):
        adapter_path = tmp_path / "adapters" / "my_adapter"
        adapter_path.mkdir(parents=True, exist_ok=True)
        adapter = _sample_adapter(path=str(adapter_path))
        manager_with_dir.register_adapter(adapter)
        assert manager_with_dir.get_adapter_by_id(adapter.adapter_id) is not None

    def test_task_types_stored_as_json(self, manager):
        adapter = _sample_adapter(task_types=["code", "research"])
        manager.register_adapter(adapter)

        result = manager.get_adapter_by_id(adapter.adapter_id)
        assert result is not None
        assert result.task_types == ["code", "research"]


# ===================================================================
# LoRAManager.get_adapter tests
# ===================================================================


class TestGetAdapter:
    def test_finds_matching_enabled_adapter(self, manager):
        manager.register_adapter(_sample_adapter())
        result = manager.get_adapter("research", "qwen3:8b")
        assert result is not None
        assert result.adapter_id == "research-lora-v1"

    def test_returns_none_when_no_match(self, manager):
        manager.register_adapter(_sample_adapter())
        result = manager.get_adapter("nonexistent_task", "qwen3:8b")
        assert result is None

    def test_returns_none_for_wrong_base_model(self, manager):
        manager.register_adapter(_sample_adapter())
        result = manager.get_adapter("research", "llama3:70b")
        assert result is None

    def test_ignores_disabled_adapters(self, manager):
        manager.register_adapter(_sample_adapter(enabled=False))
        result = manager.get_adapter("research", "qwen3:8b")
        assert result is None

    def test_matches_second_task_type(self, manager):
        manager.register_adapter(_sample_adapter(task_types=["code", "writing"]))
        result = manager.get_adapter("writing", "qwen3:8b")
        assert result is not None

    def test_returns_none_on_empty_db(self, manager):
        result = manager.get_adapter("research", "qwen3:8b")
        assert result is None


# ===================================================================
# LoRAManager.get_adapter_by_id tests
# ===================================================================


class TestGetAdapterById:
    def test_finds_by_id(self, manager):
        manager.register_adapter(_sample_adapter(adapter_id="xyz"))
        result = manager.get_adapter_by_id("xyz")
        assert result is not None
        assert result.adapter_id == "xyz"

    def test_returns_none_for_missing_id(self, manager):
        result = manager.get_adapter_by_id("does-not-exist")
        assert result is None


# ===================================================================
# LoRAManager.list_adapters tests
# ===================================================================


class TestListAdapters:
    def test_returns_all(self, manager):
        manager.register_adapter(_sample_adapter(adapter_id="a1"))
        manager.register_adapter(_sample_adapter(adapter_id="a2", enabled=False))
        result = manager.list_adapters()
        assert len(result) == 2

    def test_enabled_only(self, manager):
        manager.register_adapter(_sample_adapter(adapter_id="a1", enabled=True))
        manager.register_adapter(_sample_adapter(adapter_id="a2", enabled=False))
        result = manager.list_adapters(enabled_only=True)
        assert len(result) == 1
        assert result[0].adapter_id == "a1"

    def test_empty_db(self, manager):
        result = manager.list_adapters()
        assert result == []

    def test_returns_lora_adapter_instances(self, manager):
        manager.register_adapter(_sample_adapter())
        result = manager.list_adapters()
        assert all(isinstance(a, LoRAAdapter) for a in result)

    def test_ordered_by_adapter_id(self, manager):
        manager.register_adapter(_sample_adapter(adapter_id="z-last"))
        manager.register_adapter(_sample_adapter(adapter_id="a-first"))
        result = manager.list_adapters()
        assert result[0].adapter_id == "a-first"
        assert result[1].adapter_id == "z-last"


# ===================================================================
# LoRAManager.remove_adapter tests
# ===================================================================


class TestRemoveAdapter:
    def test_removes_existing(self, manager):
        manager.register_adapter(_sample_adapter(adapter_id="del-me"))
        assert manager.remove_adapter("del-me") is True
        assert manager.get_adapter_by_id("del-me") is None

    def test_returns_false_for_missing(self, manager):
        assert manager.remove_adapter("no-such-id") is False

    def test_only_removes_target(self, manager):
        manager.register_adapter(_sample_adapter(adapter_id="keep"))
        manager.register_adapter(_sample_adapter(adapter_id="remove"))
        manager.remove_adapter("remove")
        assert manager.get_adapter_by_id("keep") is not None
        assert manager.get_adapter_by_id("remove") is None


# ===================================================================
# LoRAManager.enable_adapter / disable_adapter tests
# ===================================================================


class TestEnableDisableAdapter:
    def test_disable_adapter(self, manager):
        manager.register_adapter(_sample_adapter(adapter_id="toggle", enabled=True))
        result = manager.disable_adapter("toggle")
        assert result is True
        adapter = manager.get_adapter_by_id("toggle")
        assert adapter.enabled is False

    def test_enable_adapter(self, manager):
        manager.register_adapter(_sample_adapter(adapter_id="toggle", enabled=False))
        result = manager.enable_adapter("toggle")
        assert result is True
        adapter = manager.get_adapter_by_id("toggle")
        assert adapter.enabled is True

    def test_disable_returns_false_for_missing(self, manager):
        assert manager.disable_adapter("nope") is False

    def test_enable_returns_false_for_missing(self, manager):
        assert manager.enable_adapter("nope") is False

    def test_disable_then_enable_roundtrip(self, manager):
        manager.register_adapter(_sample_adapter(adapter_id="rt"))
        manager.disable_adapter("rt")
        assert manager.get_adapter_by_id("rt").enabled is False
        manager.enable_adapter("rt")
        assert manager.get_adapter_by_id("rt").enabled is True


# ===================================================================
# LoRAManager.get_adapters_for_task tests
# ===================================================================


class TestGetAdaptersForTask:
    def test_returns_all_matching_across_base_models(self, manager):
        manager.register_adapter(_sample_adapter(
            adapter_id="r1", base_model="qwen3:8b", task_types=["research"],
        ))
        manager.register_adapter(_sample_adapter(
            adapter_id="r2", base_model="llama3:70b", task_types=["research"],
        ))
        result = manager.get_adapters_for_task("research")
        assert len(result) == 2
        ids = {a.adapter_id for a in result}
        assert ids == {"r1", "r2"}

    def test_ignores_disabled(self, manager):
        manager.register_adapter(_sample_adapter(
            adapter_id="d1", task_types=["code"], enabled=False,
        ))
        result = manager.get_adapters_for_task("code")
        assert len(result) == 0

    def test_returns_empty_for_no_match(self, manager):
        manager.register_adapter(_sample_adapter(task_types=["writing"]))
        result = manager.get_adapters_for_task("code")
        assert result == []

    def test_does_not_include_wrong_task(self, manager):
        manager.register_adapter(_sample_adapter(
            adapter_id="w1", task_types=["writing"],
        ))
        manager.register_adapter(_sample_adapter(
            adapter_id="c1", task_types=["code"],
        ))
        result = manager.get_adapters_for_task("writing")
        assert len(result) == 1
        assert result[0].adapter_id == "w1"
