import os
import sys
from pathlib import Path

# Add backend to path so we can import modules
sys.path.append(os.path.abspath("."))

from backend.security.prompt_guard import PromptGuard
from backend.security.rbac import _is_allowed

def test_prompt_guard_prefix_bypass():
    print("Testing PromptGuard prefix bypass...")
    pg = PromptGuard(level="strict")

    # Setup job_dir and evil path
    job_dir = Path("/tmp/job1").resolve()
    evil_path = "/tmp/job1_evil/secret.txt"

    # Mock tool call
    call = {
        "name": "read_file",
        "args": {"path": evil_path}
    }

    # In strict mode, PromptGuard.validate_tool_call should block this
    # because it resolves to /tmp/job1_evil/secret.txt which is NOT in /tmp/job1/
    result = pg.validate_tool_call(call, ["read_file"], job_dir)

    if not result.valid and any("escapes job directory" in issue for issue in result.issues):
        print("✅ PromptGuard correctly blocked prefix bypass!")
    else:
        print("❌ PromptGuard FAILED to block prefix bypass!")
        print(f"Result valid: {result.valid}")
        print(f"Issues: {result.issues}")

def test_rbac_prefix_bypass():
    print("\nTesting RBAC prefix bypass...")

    # Test cases: (role, method, path, expected_allowed)
    test_cases = [
        ("operator", "POST", "/api/chat", True),         # Exact match
        ("operator", "POST", "/api/chat/123", True),     # Child path
        ("operator", "POST", "/api/chat_evil", False),   # Prefix bypass attempt
        ("viewer", "GET", "/api/conversations", True),    # Child of /api/
        ("viewer", "POST", "/api/chat", False),          # Disallowed method
    ]

    all_passed = True
    for role, method, path, expected in test_cases:
        allowed = _is_allowed(role, method, path)
        status = "✅" if allowed == expected else "❌"
        print(f"{status} {role} {method} {path} -> {allowed} (expected {expected})")
        if allowed != expected:
            all_passed = False

    if all_passed:
        print("✅ All RBAC test cases passed!")
    else:
        print("❌ Some RBAC test cases FAILED!")

if __name__ == "__main__":
    try:
        test_prompt_guard_prefix_bypass()
    except Exception as e:
        print(f"Error in PromptGuard test: {e}")
        import traceback
        traceback.print_exc()

    try:
        test_rbac_prefix_bypass()
    except Exception as e:
        print(f"Error in RBAC test: {e}")
        import traceback
        traceback.print_exc()
