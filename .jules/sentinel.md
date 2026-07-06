## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2024-07-25 - Path Jailing failure via Argument Reversal
**Vulnerability:** In `backend/security/prompt_guard.py`, the `safe_resolve` utility was called with reversed arguments (user-provided path before the base directory). This effectively disabled path jailing because the utility expected the jail root as the first argument.
**Learning:** Security utilities with multiple arguments are brittle if their signature is not strictly followed or if they are shadowed by incorrect fallbacks.
**Prevention:** Standardize security utility signatures across the codebase and use type hints (`Path` vs `str`) to catch argument swaps. Always verify that the fallback implementation in circular-dependency guards matches the primary implementation's signature and logic.
