## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2024-06-25 - Shell and Code Execution Filter Hardening
**Vulnerability:** Security filters using simple word-boundary regexes can be bypassed using backslash escaping (e.g., `r\m`) or varied whitespace (e.g., `pip  install`). In Python code execution, dangerous operations can be hidden via reflection (`getattr`) or obfuscated imports.
**Learning:** Shells like `/bin/sh` ignore backslashes that don't escape a special character, but regex word boundaries (`\b`) treat them as word breaks or literal characters, causing matches to fail.
**Prevention:** Use regexes that account for optional backslashes and flexible whitespace between command segments. Block reflection primitives like `getattr`, `__getattribute__`, and `__import__` in sandboxed Python environments to prevent dynamic bypasses of function blocklists.
