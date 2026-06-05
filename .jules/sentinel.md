## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2024-05-22 - Path Traversal bypass via Backslashes on POSIX
**Vulnerability:** On POSIX systems, `pathlib` does not treat backslashes (`\`) as path separators. A security filter that only checks for forward slashes or uses `Path.relative_to` without normalization may fail to detect traversal attempts using backslashes if the path is later used in a context that does interpret them (like a Windows-compatible shell or certain libraries).
**Learning:** Path normalization must account for both forward and backward slashes regardless of the host OS to ensure consistent security policy enforcement.
**Prevention:** Normalize all backslashes to forward slashes before performing jail checks on POSIX systems.

## 2024-05-22 - Security Fallback "Fail-Open" Risk
**Vulnerability:** Moving security logic into a centralized utility and removing it from callers can create a "fail-open" vulnerability if the utility's fallback implementation (e.g., in case of import errors) does not maintain the same level of security.
**Learning:** Centralized security utilities must have robust, secure fallback implementations or explicitly fail closed (e.g., by raising an exception) if their primary logic cannot be loaded.
**Prevention:** Always implement basic security checks in fallback functions or ensure they raise errors for unsafe operations.
