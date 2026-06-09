
import pytest
from fastapi import HTTPException
from backend.security.rbac import _is_allowed, check_permission

def test_rbac_prefix_bypass():
    # 'operator' has access to '/api/chat'
    # It should NOT have access to '/api/chat_evil'

    assert _is_allowed("operator", "POST", "/api/chat") is True
    assert _is_allowed("operator", "POST", "/api/chat/messages") is True

    # This is the vulnerability:
    # If this returns True, it's a bypass.
    # We WANT it to be False.
    assert _is_allowed("operator", "POST", "/api/chat_evil") is False

def test_rbac_check_permission_bypass():
    # Should raise HTTPException for bypass attempt
    with pytest.raises(HTTPException) as excinfo:
        check_permission("operator", "POST", "/api/chat_evil")
    assert excinfo.value.status_code == 403
