## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2025-05-15 - Directory Traversal via Argument Swap and Prefix Bypass
**Vulnerability:** A critical security utility `safe_resolve(base, path)` was called with swapped arguments `safe_resolve(path, base)` in `PromptGuard`, effectively reversing the jail-breaking protection. Additionally, both `paths.py` and `prompt_guard.py` relied on string-based `.startswith()` checks for path validation, which is vulnerable to prefix-collision bypasses (e.g., `/app/jail_evil` passing a check against `/app/jail`).
**Learning:** Manual string manipulation of paths for security boundaries is fragile and prone to logic errors. Reversing argument order in security-critical functions can silently disable protections.
**Prevention:** Always use `pathlib.Path.is_relative_to()` for boundary checks to ensure proper segment-aware validation. Use descriptive parameter names and type hints to minimize argument swap risks, and maintain centralized, well-tested security primitives.
