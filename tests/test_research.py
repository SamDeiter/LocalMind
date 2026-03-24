"""
Tests for ArXiv research endpoints.
Uses httpx.AsyncClient with the FastAPI test client.
"""
import json
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    from backend.server import app
    return TestClient(app)


# ── GET /api/research/arxiv ──────────────────────────────────────


class TestArxivSearch:
    """Tests for the arXiv search endpoint."""

    def test_search_returns_papers(self, client):
        """Search should return papers when arXiv responds."""
        mock_papers = [
            {
                "title": "Test Paper",
                "authors": "Author A",
                "abstract": "A test abstract",
                "url": "https://arxiv.org/abs/1234",
                "published": "2024-01-01",
                "source": "arxiv",
            }
        ]

        with patch(
            "backend.routes.research_routes._get_researcher"
        ) as mock_get:
            mock_researcher = MagicMock()
            mock_researcher.search_arxiv = AsyncMock(return_value=mock_papers)
            mock_get.return_value = mock_researcher

            resp = client.get("/api/research/arxiv?q=machine+learning&max=3")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["papers"][0]["title"] == "Test Paper"
        assert data["query"] == "machine learning"

    def test_search_empty_results(self, client):
        """Should return empty list when no papers found."""
        with patch(
            "backend.routes.research_routes._get_researcher"
        ) as mock_get:
            mock_researcher = MagicMock()
            mock_researcher.search_arxiv = AsyncMock(return_value=[])
            mock_get.return_value = mock_researcher

            resp = client.get("/api/research/arxiv?q=xyznonexistent")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["papers"] == []

    def test_search_pagination(self, client):
        """Page parameter should be passed through."""
        with patch(
            "backend.routes.research_routes._get_researcher"
        ) as mock_get:
            mock_researcher = MagicMock()
            mock_researcher.search_arxiv = AsyncMock(return_value=[])
            mock_get.return_value = mock_researcher

            resp = client.get("/api/research/arxiv?q=test&page=2&max=5")

        assert resp.status_code == 200
        data = resp.json()
        assert data["page"] == 2
        # Verify start offset was calculated correctly
        mock_researcher.search_arxiv.assert_called_once_with(
            "test", max_results=5, start=10,
        )

    def test_search_handles_exception(self, client):
        """Should return error gracefully on exception."""
        with patch(
            "backend.routes.research_routes._get_researcher"
        ) as mock_get:
            mock_researcher = MagicMock()
            mock_researcher.search_arxiv = AsyncMock(
                side_effect=Exception("Network error")
            )
            mock_get.return_value = mock_researcher

            resp = client.get("/api/research/arxiv?q=test")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert "error" in data


# ── POST /api/research/apply-paper ───────────────────────────────


class TestApplyPaper:
    """Tests for the apply-paper endpoint."""

    def test_apply_paper_success(self, client):
        """Should generate and save a proposal."""
        mock_proposal = {
            "id": "test-123",
            "title": "Apply attention mechanism to model router",
            "category": "feature",
            "description": "Use transformer attention",
            "source": "arxiv",
        }

        with patch(
            "backend.routes.research_routes.generate_paper_proposal",
            new_callable=AsyncMock,
            return_value={"proposal": mock_proposal, "error": None},
        ):
            resp = client.post(
                "/api/research/apply-paper",
                json={
                    "title": "Attention Is All You Need",
                    "abstract": "We propose a new architecture...",
                    "url": "https://arxiv.org/abs/1706.03762",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["proposal"]["title"] == "Apply attention mechanism to model router"
        assert data["error"] is None

    def test_apply_paper_bad_json(self, client):
        """Should handle LLM returning invalid JSON."""
        with patch(
            "backend.routes.research_routes.generate_paper_proposal",
            new_callable=AsyncMock,
            return_value={
                "proposal": None,
                "error": "AI response was not valid JSON — try again",
            },
        ):
            resp = client.post(
                "/api/research/apply-paper",
                json={
                    "title": "Some Paper",
                    "abstract": "Some abstract",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["proposal"] is None
        assert "JSON" in data["error"]

    def test_apply_paper_missing_fields(self, client):
        """Should reject request with missing required fields."""
        resp = client.post(
            "/api/research/apply-paper",
            json={"url": "https://example.com"},  # missing title + abstract
        )
        assert resp.status_code == 422  # Pydantic validation error
