import os
import json
import time
from pathlib import Path
import pytest
import backend.proposals as prop_mod
from backend.proposals import ProposalManager

@pytest.fixture
def manager(tmp_path):
    prop_mod.PROPOSALS_DIR = tmp_path / "proposals"
    prop_mod.PROPOSALS_DIR.mkdir()
    prop_mod.ARCHIVE_DIR = tmp_path / "archive"
    return ProposalManager()

def test_count_active(manager):
    # Setup some files
    for i in range(5):
        status = "proposed" if i % 2 == 0 else "completed"
        proposal = {
            "id": f"test_prop_{i}",
            "title": f"Test Proposal {i}",
            "status": status,
            "created_at": time.time()
        }
        fp = prop_mod.PROPOSALS_DIR / f"test_prop_{i}_misc.json"
        fp.write_text(json.dumps(proposal, indent=2), encoding="utf-8")

    assert manager.count_active() == 3 # 0, 2, 4 are proposed

    # Test cache is working by not writing through manager
    proposal = {
        "id": f"test_prop_5",
        "title": f"Test Proposal 5",
        "status": "proposed",
        "created_at": time.time()
    }
    fp = prop_mod.PROPOSALS_DIR / f"test_prop_5_misc.json"
    fp.write_text(json.dumps(proposal, indent=2), encoding="utf-8")

    # Mtime changes so cache invalidated
    assert manager.count_active() == 4

    # Update through manager, should invalidate cache
    manager._update_status("test_prop_0", "completed")
    assert manager.count_active() == 3
