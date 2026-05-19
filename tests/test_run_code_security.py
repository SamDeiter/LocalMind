import pytest
from backend.tools.run_code import _safety_check

@pytest.mark.parametrize("code", [
    "import os; os.system('rm -rf /')",
    "import os; os.popen('rm -rf /')",
    "import subprocess; subprocess.call(['rm', '-rf', '/'])",
    "getattr(os, 'system')('rm -rf /')",
    "getattr(__import__('os'), 'system')('rm -rf /')",
    "__import__('os').system('rm -rf /')",
    "import os; getattr(os, 'remove')('file')",
    "import os; os.__getattribute__('system')('ls')",
])
def test_run_code_bypass_blocked(code):
    result = _safety_check(code)
    assert result is not None, f"Security bypass: Code was not blocked: {code}"
    assert "BLOCKED" in result
