## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2026-04-19 - Shadow Security Checks and Utility Misuse
**Vulnerability:** Redundant "shadow checks" in `prompt_guard.py` re-introduced a path traversal bypass. Even though `safe_resolve` was used, a second, weaker `.startswith()` check was performed on the result without proper directory separator handling. Additionally, the `safe_resolve` call had its arguments reversed, treating the untrusted path as the jail root.
**Learning:** Redundant security checks are often weaker than the primary utility and can re-introduce vulnerabilities. Complex utility signatures (like `safe_resolve(base, untrusted)`) are prone to argument-order errors which can invert security logic.
**Prevention:** Trust and rely on centralized security utilities; avoid "shadowing" them with local checks. Use idiomatic and robust methods like `Path.is_relative_to` inside utilities to minimize internal complexity.
