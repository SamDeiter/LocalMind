## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2026-04-11 - Reversed Argument Order in safe_resolve Fallback & Invocation
**Vulnerability:** In `prompt_guard.py`, invoking `safe_resolve(arg_value, job_dir)` on the standard `(base_dir, user_path)` signature causes reversed parameter binding. This binds the untrusted user value as the jail base and the trusted jail directory as the user path, rendering the security check entirely ineffective.
**Learning:** Inconsistent parameter ordering between APIs and fallback/mock definitions can silently break security boundaries, allowing unauthorized paths to be treated as valid jail bases.
**Prevention:** Enforce consistent function signatures across all real and fallback/mock modules. Utilize static analysis or type annotation checks (`mypy`) to detect argument type or name mismatches early. Always use segment-aware `is_relative_to` for robust verification.
