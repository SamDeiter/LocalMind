
from backend.security.rbac import _is_allowed

def test_rbac_prefix_collision_bypass():
    # 'operator' role has permission for ('POST', '/api/chat')
    # This should NOT allow 'POST' on '/api/chat_evil'
    assert _is_allowed("operator", "POST", "/api/chat") is True
    assert _is_allowed("operator", "POST", "/api/chat/123") is True
    assert _is_allowed("operator", "POST", "/api/chat_evil") is False, "RBAC bypass detected: /api/chat_evil should not be allowed via /api/chat prefix"

def test_rbac_path_traversal_bypass():
    # '/api/chat' should not allow '/api/chat/../admin'
    assert _is_allowed("operator", "POST", "/api/chat/../admin") is False, "RBAC bypass detected: path traversal allowed"

def test_rbac_malformed_path():
    # Verify that malformed paths don't crash the logic
    assert _is_allowed("operator", "POST", "invalid_path") is False
    assert _is_allowed("operator", "POST", "") is False
