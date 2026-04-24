import pytest
from unittest.mock import patch, MagicMock, AsyncMock

@pytest.fixture
def client(seeded_db):
    from fastapi.testclient import TestClient
    from backend.server import app
    import backend.db
    with patch("backend.db.DB_PATH", seeded_db), \
         patch("backend.db.get_db", lambda: seeded_db):
        return TestClient(app)

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
