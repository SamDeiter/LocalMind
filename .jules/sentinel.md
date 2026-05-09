## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2025-05-15 - Path Traversal bypass in Auth/RBAC Middleware
**Vulnerability:** Authentication and RBAC checks using `startswith()` on raw request URLs are vulnerable to path traversal (e.g., `/health/../api/secret`) and prefix collisions (e.g., `/api_secret` matching `/api`).
**Learning:** Raw `request.url.path` must be normalized using `posixpath.normpath()` before any security checks. Furthermore, `PurePosixPath.is_relative_to()` should be used instead of string `startswith()` to ensure matches occur at path segment boundaries.
**Prevention:** Always normalize incoming URL paths and use component-aware path libraries for prefix validation.
