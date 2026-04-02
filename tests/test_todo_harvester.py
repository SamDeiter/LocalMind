import pytest
from backend.todo_harvester import harvest_todos, PROJECT_ROOT
import tempfile
import os
from pathlib import Path

def test_harvest_todos_priority(monkeypatch):
    # Create temporary files with various TODOs
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        monkeypatch.setattr("backend.todo_harvester.PROJECT_ROOT", temp_path)

        file1 = temp_path / "file1.py"
        file1.write_text("""
# TODO: this is a regular todo
# FIXME: this is a regular fixme
# HACK: this is a regular hack
# XXX: this is a regular xxx
# TODO(P0): this is a P0 todo
# FIXME(P3): this is a P3 fixme
# HACK[critical]: this is a critical hack
# TODO[low]: this is a low todo
        """)

        todos = harvest_todos(100)

        # Expected sort order:
        # 1. P0/critical: # TODO(P0), # HACK[critical]
        # 2. P1/high: None
        # 3. P2/medium: None
        # 4. P3/low: # FIXME(P3), # TODO[low]
        # 5. Default priorities: # FIXME, # HACK, # XXX, # TODO

        # Expected priorities based on tags for same-level priorities:
        # P0/critical level: HACK[critical] vs TODO(P0) -> HACK (1) < TODO (3) -> HACK[critical], TODO(P0)
        # P3/low level: FIXME(P3) vs TODO[low] -> FIXME (0) < TODO (3) -> FIXME(P3), TODO[low]
        # Default level: FIXME, HACK, XXX, TODO

        # We need to test the harvester output, but the logic in harvester
        # will define the exact rules.
        assert len(todos) == 8

        tags_and_comments = [(t["tag"], t["comment"]) for t in todos]

        expected = [
            ("HACK", "this is a critical hack"),
            ("TODO", "this is a P0 todo"),
            ("FIXME", "this is a P3 fixme"),
            ("TODO", "this is a low todo"),
            ("FIXME", "this is a regular fixme"),
            ("HACK", "this is a regular hack"),
            ("XXX", "this is a regular xxx"),
            ("TODO", "this is a regular todo"),
        ]

        assert tags_and_comments == expected
