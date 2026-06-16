## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2025-05-15 - Auth Bootstrap Fail-Open via Key Revocation
**Vulnerability:** The system's "bootstrap mode" (no-auth admin access) was triggered whenever no *active* API keys existed. Revoking all keys would inadvertently revert the system to an unauthenticated state, allowing anyone to gain admin access.
**Learning:** Bootstrap checks should verify if the system has *ever* been initialized, not just if it currently has active credentials.
**Prevention:** In `_has_any_keys()`, check for the existence of ANY key record in the database, including revoked ones, to ensure the system stays in a "fail-closed" state after initial setup.

## 2025-05-15 - RBAC Path Prefix Bypass
**Vulnerability:** RBAC path matching using `path.startswith(allowed_prefix)` allowed access to sensitive sub-resources if the allowed prefix was a substring of the sensitive one (e.g., `/api/chat` accidentally allowing access to `/api/chat-admin`).
**Learning:** Simple string prefix matching does not respect resource boundaries in URL paths.
**Prevention:** Use boundary-aware matching: `path == prefix or path.startswith(prefix.rstrip("/") + "/")`. This ensures that a match only occurs if the path is an exact match or a subdirectory of the allowed prefix.
