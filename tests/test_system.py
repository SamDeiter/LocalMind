import pytest
from unittest.mock import patch, MagicMock, AsyncMock

@pytest.fixture
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    # Use a temporary database to avoid schema errors in middleware
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))

    from backend.db import init_db
    from backend.core.schema import init_phase0_schema
    init_db()
    init_phase0_schema()

    from backend.server import app
    return TestClient(app)

def test_hardware_status_success(client):
    """Test the /api/hardware endpoint returns correct structure."""
    with patch("backend.routes.system.get_async_client") as mock_get_client:
        # Mock Ollama API response
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"models": []}

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_get_client.return_value = mock_client

        response = client.get("/api/hardware")
        assert response.status_code == 200
        data = response.json()
        assert "system" in data
        assert "cpu_percent" in data["system"]
        assert "ram_percent" in data["system"]
        assert "models" in data
