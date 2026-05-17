## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2024-06-14 - RBAC Prefix Bypass via `startswith()`
**Vulnerability:** Role-based access control using `path.startswith(allowed_prefix)` allows bypassing intended restrictions if one prefix is a substring of another (e.g., `/api/chat` matching `/api/chat_admin`).
**Learning:** String prefix checks do not account for path segment boundaries.
**Prevention:** Use segment-aware matching by checking for an exact match OR ensuring the prefix is followed by a path separator (e.g., `path == prefix or path.startswith(prefix.rstrip("/") + "/")`).

## 2024-06-14 - Broken Path Jailing Logic in PromptGuard
**Vulnerability:** `PromptGuard.validate_tool_call` used swapped arguments for `safe_resolve(arg_value, job_dir)`, causing it to attempt jailing the sandbox directory inside the user-provided (potentially malicious) path. It also relied on a manual `startswith` re-check which is prone to string-prefix bypasses.
**Learning:** Security utilities must be called with correct parameter ordering, and internal jailing logic should be centralized in a single robust function (like `safe_resolve`) rather than duplicated with weaker checks.
**Prevention:** Standardize on `safe_resolve(base_dir, user_path)` and block absolute paths early if only relative paths are expected in a sandboxed context.
