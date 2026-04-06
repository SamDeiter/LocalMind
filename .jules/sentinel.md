## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-05-25 - Terminal Command Injection Bypass via `.startswith()`
**Vulnerability:** Using `command.startswith(dangerous_cmd)` to block shell commands is easily bypassed by adding leading whitespace or chaining commands (e.g., `ls; rm -rf /`).
**Learning:** Shell command validation must account for shell operators (`;`, `&&`, `||`, `|`), newlines, and word boundaries to prevent bypasses and false positives.
**Prevention:** Use regex with word boundaries and shell operator detection: `(?:^|[;&|]|\n)\s*\b(dangerous_cmd)\b`. Always trim input before validation.
