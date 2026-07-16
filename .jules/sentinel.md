## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2025-05-15 - RBAC Prefix Collision Bypass
**Vulnerability:** Authorization logic used `path.startswith(allowed_prefix)`, allowing a user with access to `/api/chat` to access `/api/chat-admin` because the string prefix matched.
**Learning:** String-based prefix matching for URL paths is dangerous without enforcing path segment boundaries.
**Prevention:** Ensure the path matches the prefix exactly or is followed by a path separator (e.g., `path == prefix or path.startswith(prefix.rstrip("/") + "/")`).

## 2025-05-15 - Path Jailing Robustness & POSIX Backslash Traversal
**Vulnerability:** Path jailing using `startswith` on resolved strings is susceptible to partial prefix collisions (e.g. `/data/jail` matching `/data/jail_evil`). Additionally, backslash traversal sequences (`..\..`) may not be correctly resolved on POSIX systems if not normalized, potentially bypassing jail checks if passed to a component that does interpret them as separators.
**Learning:** `pathlib.Path.is_relative_to()` is the robust standard for jail validation as it respects path segments. Normalizing backslashes before resolution is necessary for cross-platform safety.
**Prevention:** Always use `Path.is_relative_to()` for jail checks. Normalize input paths by replacing `\` with `/` before calling `.resolve()` on POSIX systems.
