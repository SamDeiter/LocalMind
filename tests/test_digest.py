"""
Tests for the daily digest autonomy component.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.digest import generate_digest, get_latest_digest, get_digest_by_date


@pytest.fixture
def mock_dirs(tmp_path):
    """Setup mock directories for proposals and digests."""
    proposals_dir = tmp_path / "proposals"
    archive_dir = proposals_dir / "archive"
    digests_dir = tmp_path / "digests"

    proposals_dir.mkdir(parents=True)
    archive_dir.mkdir(parents=True)
    digests_dir.mkdir(parents=True)

    with patch('backend.digest.PROPOSALS_DIR', proposals_dir), \
         patch('backend.digest.ARCHIVE_DIR', archive_dir), \
         patch('backend.digest.DIGESTS_DIR', digests_dir):
        yield {"proposals": proposals_dir, "archive": archive_dir, "digests": digests_dir}


def create_proposal_mock(directory: Path, proposal_id: str, status: str,
                         title: str, timestamp: float, **kwargs):
    """Helper to create a mock proposal JSON file."""
    data = {
        "id": proposal_id,
        "status": status,
        "title": title,
        "created_at": timestamp,
        "execution_finished_at": timestamp,
    }
    data.update(kwargs)

    file_path = directory / f"{proposal_id}.json"
    file_path.write_text(json.dumps(data), encoding="utf-8")
    return file_path


class TestDigest:

    def test_generate_digest_happy_path(self, mock_dirs):
        """Test generating a digest with various proposal statuses."""
        mock_time = 1234567890.0
        # Create proposals within the 24h window (mock_time - 3600 = 1 hour ago)
        recent_time = mock_time - 3600

        create_proposal_mock(mock_dirs["proposals"], "p1", "completed", "Test Completed", recent_time, files_edited=["file1.py"])
        create_proposal_mock(mock_dirs["archive"], "p2", "failed", "Test Failed", recent_time, error="An error occurred")
        create_proposal_mock(mock_dirs["proposals"], "p3", "skipped", "Test Skipped", recent_time)
        create_proposal_mock(mock_dirs["proposals"], "p4", "proposed", "Test Proposed", recent_time)
        create_proposal_mock(mock_dirs["proposals"], "p5", "approved", "Test Approved", recent_time)
        create_proposal_mock(mock_dirs["archive"], "p6", "completed", "Old Completed", mock_time - (48 * 3600)) # Outside 24h

        with patch('backend.digest.time.time', return_value=mock_time), \
             patch('backend.digest.time.strftime', return_value="2009-02-13 23:31"):

            digest = generate_digest()

        assert "Daily Digest — 2009-02-13 23:31" in digest
        assert "**Total Activity**: 5 proposals" in digest

        # Verify counts in sections
        assert "## ✅ Completed (1)" in digest
        assert "## ❌ Failed (1)" in digest
        assert "## ⏭️ Skipped (1)" in digest
        assert "## ⏳ Pending (2)" in digest # Proposed and approved

        # Verify proposal titles appear
        assert "**Test Completed**" in digest
        assert "**Test Failed**" in digest
        assert "Test Skipped" in digest
        assert "Test Proposed" in digest
        assert "Test Approved" in digest
        assert "Old Completed" not in digest

        # Verify success rate
        # 1 completed, 1 failed = 50%
        assert "**Success Rate**: 50% (1/2)" in digest

        # Verify file creation
        digest_file = mock_dirs["digests"] / "2009-02-13.md" # Note: strftime mock returns 2009-02-13 23:31, but generate_digest uses time.strftime("%Y-%m-%d") as well
        # Let's adjust our expectation or the mock to handle multiple calls if needed.
        # Actually generate_digest does: time.strftime('%Y-%m-%d %H:%M') and time.strftime("%Y-%m-%d")
        # If we patch time.strftime with a single return value, it will return "2009-02-13 23:31" for BOTH.
        # The file will be named "2009-02-13 23:31.md" in this mock setup.
        digest_file = mock_dirs["digests"] / "2009-02-13 23:31.md"
        assert digest_file.exists()
        assert digest_file.read_text(encoding="utf-8") == digest


    def test_generate_digest_empty(self, mock_dirs):
        """Test generating a digest when there's no activity."""
        mock_time = 1234567890.0

        with patch('backend.digest.time.time', return_value=mock_time):
            digest = generate_digest()

        assert digest == "# Daily Digest\n\nNo autonomy activity in the last 24 hours.\n"

        # Verify no file is saved
        assert not list(mock_dirs["digests"].glob("*.md"))


    def test_generate_digest_error_handling(self, mock_dirs):
        """Test resilience against malformed files."""
        mock_time = 1234567890.0
        recent_time = mock_time - 3600

        # Valid proposal
        create_proposal_mock(mock_dirs["proposals"], "p1", "completed", "Test Completed", recent_time)

        # Invalid JSON
        bad_json = mock_dirs["proposals"] / "bad.json"
        bad_json.write_text("not json")

        with patch('backend.digest.time.time', return_value=mock_time), \
             patch('backend.digest.time.strftime', side_effect=lambda fmt: "2009-02-13" if "%H" not in fmt else "2009-02-13 23:31"):

            digest = generate_digest()

        # Should successfully process p1 and ignore bad.json
        assert "## ✅ Completed (1)" in digest
        assert "**Test Completed**" in digest

        digest_file = mock_dirs["digests"] / "2009-02-13.md"
        assert digest_file.exists()


    def test_get_latest_digest_exists(self, mock_dirs):
        """Test retrieving the most recent digest when files exist."""
        d1 = mock_dirs["digests"] / "2024-01-01.md"
        d1.write_text("Digest from Jan 1", encoding="utf-8")

        d2 = mock_dirs["digests"] / "2024-01-02.md"
        d2.write_text("Digest from Jan 2", encoding="utf-8")

        # get_latest_digest sorts by filename descending
        result = get_latest_digest()
        assert result == "Digest from Jan 2"


    def test_get_latest_digest_empty(self, mock_dirs):
        """Test generating a new digest if none exists when calling get_latest_digest."""
        mock_time = 1234567890.0
        with patch('backend.digest.time.time', return_value=mock_time):
            result = get_latest_digest()

        assert result == "# Daily Digest\n\nNo autonomy activity in the last 24 hours.\n"


    def test_get_digest_by_date_exists(self, mock_dirs):
        """Test retrieving a specific digest by date."""
        d1 = mock_dirs["digests"] / "2024-05-15.md"
        d1.write_text("Expected content", encoding="utf-8")

        result = get_digest_by_date("2024-05-15")
        assert result == "Expected content"


    def test_get_digest_by_date_not_found(self, mock_dirs):
        """Test retrieving a missing digest returns None."""
        result = get_digest_by_date("2024-05-15")
        assert result is None
