## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2025-05-15 - Path Jailing bypass via Argument Reversal
**Vulnerability:** A critical logic bug was found in `PromptGuard.validate_tool_call` where `safe_resolve` was called with reversed arguments: `safe_resolve(user_path, base_dir)`. Because the function attempted to strip prefixes from the first argument and join it to the second, this effectively disabled path jailing and allowed arbitrary path resolution.
**Learning:** Security utilities with multiple positional arguments are prone to transposition errors that can silently negate their protection.
**Prevention:** Use keyword arguments for security-critical functions or add internal assertions to verify that the 'base' directory argument is indeed an absolute path and the 'user' path is relative.
