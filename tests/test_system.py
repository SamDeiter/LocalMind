import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import sqlite3
import tempfile
from pathlib import Path

@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        db_path = Path(tmp.name)
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                key_hash TEXT NOT NULL,
                name TEXT,
                role TEXT NOT NULL DEFAULT 'operator',
                rate_limit INTEGER DEFAULT 100,
                last_used_at REAL,
                revoked_at REAL,
                created_at REAL NOT NULL
            )
        """)
        conn.commit()
        conn.close()

        with patch("backend.db.DB_PATH", str(db_path)), \
             patch("backend.security.auth.DB_PATH", str(db_path)):
            from backend.server import app
            yield TestClient(app)

def test_hardware_status_success(client):
    """Test the /api/hardware endpoint returns correct structure."""
    with patch("backend.routes.system.httpx.AsyncClient") as MockClient:
        # Mock Ollama API response
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"models": []}

        instance = AsyncMock()
        instance.get.return_value = mock_resp
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = instance

        response = client.get("/api/hardware")
        assert response.status_code == 200
        data = response.json()
        assert "system" in data
        assert "cpu_percent" in data["system"]
        assert "ram_percent" in data["system"]
        assert "models" in data
