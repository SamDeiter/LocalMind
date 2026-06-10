import pytest
from backend.security.rbac import _is_allowed

def test_rbac_prefix_bypass():
    # 'operator' role has permission for '/api/conversations' (POST, PUT, DELETE)
    # It should NOT have permission for '/api/conversations_secrets'
    # Note: operator has ("GET", "/api/") so GET would be allowed anyway.
    assert _is_allowed("operator", "DELETE", "/api/conversations") is True

    # This was the vulnerability: '/api/conversations_secrets' starts with '/api/conversations'
    # but it is a DIFFERENT logical resource/endpoint.
    assert _is_allowed("operator", "DELETE", "/api/conversations_secrets") is False

def test_rbac_exact_match():
    assert _is_allowed("operator", "GET", "/api/conversations") is True
    assert _is_allowed("operator", "POST", "/api/chat") is True

def test_rbac_child_path():
    # '/api/conversations/123' is a child of '/api/conversations' and should be allowed
    assert _is_allowed("operator", "GET", "/api/conversations/123") is True
    assert _is_allowed("operator", "DELETE", "/api/conversations/123") is True
