import pytest
from unittest.mock import patch, MagicMock, AsyncMock

@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from backend.server import app
    return TestClient(app)

def test_hardware_status_success(client):
    """Test the /api/hardware endpoint returns correct structure."""
    with patch("backend.routes.system.get_httpx_client") as mock_get_client, \
         patch("backend.routes.system._get_memory_count") as mock_mem_count:

        mock_mem_count.return_value = 42

        # Mock Ollama API response
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"models": []}

        instance = AsyncMock()
        instance.get.return_value = mock_resp
        mock_get_client.return_value = instance

        response = client.get("/api/hardware")
        assert response.status_code == 200
        data = response.json()
        assert "system" in data
        assert "cpu_percent" in data["system"]
        assert "ram_percent" in data["system"]
        assert "models" in data
        assert "version" in data
        assert data["memory_count"] == 42
