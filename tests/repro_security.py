
import pytest
import posixpath
from pathlib import PurePosixPath
from backend.security.rbac import _is_allowed
import asyncio

def test_rbac_prefix_collision():
    # operator is allowed /api/chat
    # should NOT match /api/chat_evil
    assert _is_allowed("operator", "POST", "/api/chat") is True
    assert _is_allowed("operator", "POST", "/api/chat_evil") is False
    assert _is_allowed("operator", "POST", "/api/chat/123") is True

def test_rbac_normalization():
    # traversal should be normalized
    assert _is_allowed("operator", "POST", "/api/chat/../jobs") is True # /api/jobs
    assert _is_allowed("operator", "POST", "/api/../secret") is False # /secret (not starting with /api)

def test_auth_middleware_logic():
    # Verify the segment-aware matching logic
    _AUTH_SKIP_PREFIXES = ("/health", "/docs", "/static/")

    def check_skip(path):
        normalized_path = posixpath.normpath(path)
        path_obj = PurePosixPath(normalized_path)
        for prefix in _AUTH_SKIP_PREFIXES:
            try:
                if path_obj.is_relative_to(prefix):
                    return True
            except ValueError:
                continue
        return False

    assert check_skip("/health/ready") is True
    assert check_skip("/static/main.js") is True
    assert check_skip("/static_secret") is False
    assert check_skip("/health_secret") is False

    def check_api(path):
        normalized_path = posixpath.normpath(path)
        path_obj = PurePosixPath(normalized_path)
        try:
            return path_obj.is_relative_to("/api")
        except ValueError:
            return False

    assert check_api("/api/chat") is True
    assert check_api("/api_secret") is False
    assert check_api("/api/../secret") is False
