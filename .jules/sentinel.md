## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2025-05-15 - Authorization Bypass via Insecure Prefix Matching
**Vulnerability:** RBAC and path jailing logic used simple string prefix matching with `.startswith()`. This allowed an attacker to bypass authorization by requesting resources with names that shared a common prefix but were distinct from the intended scope (e.g., `/api/chat_admin` bypassing a check for `/api/chat`).
**Learning:** String-based prefix matching does not respect logical boundaries (like directory separators).
**Prevention:** Always use segment-aware matching. For URLs or file paths, ensure the prefix ends with a separator: `path == allowed or path.startswith(allowed.rstrip("/") + "/")`. For file operations, delegate to a robust `safe_resolve` utility that canonicalizes paths and enforces strict containment.
