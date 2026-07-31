## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2025-05-15 - RBAC Route Prefix Collision Bypass & Swapped Argument Jailing
**Vulnerability:** RBAC checks using `.startswith()` on URLs can allow authorization bypasses (e.g., `/api/chat-admin` matches allowed prefix `/api/chat`). Additionally, PromptGuard tool path jailing passed arguments to `safe_resolve(arg_value, job_dir)` backwards (relative path as base, and base as relative), and used insecure string prefix `.startswith()` checks.
**Learning:** Checking URL route prefixes with string-based `startswith()` allows partial name collision bypasses unless trailing slashes are strictly handled. Reversing path jailing arguments disables security boundary checks.
**Prevention:** Ensure route prefix matching requires either exact matching or matching of complete path segments (e.g., followed by a trailing slash). Standardize `safe_resolve` argument signatures and enforce path checks strictly via `.is_relative_to()`.
