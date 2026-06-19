## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2026-06-12 - Path Jailing Bypass via Argument Swap and Weak Prefix Check
**Vulnerability:** A path jailing mechanism was bypassed due to two flaws:
1. Argument Swap: `safe_resolve` was called with `(user_path, base_dir)` instead of `(base_dir, user_path)`, causing the jail root to be treated as an untrusted relative path.
2. Weak Prefix Check: Using `str(resolved).startswith(str(jail))` is vulnerable to prefix collisions (e.g., `/tmp/jail_evil` starts with `/tmp/jail`).
**Learning:** Security-critical functions with similar argument types are prone to swap bugs. String-based path comparisons are inherently fragile.
**Prevention:** Use type-safe path objects and robust boundary checks like `Path.is_relative_to()`. Always verify the argument order of security-sensitive utility functions.
