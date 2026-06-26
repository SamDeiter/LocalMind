## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2024-06-15 - Path jailing neutered by argument swap
**Vulnerability:** In `PromptGuard.validate_tool_call`, calling `safe_resolve(user_path, base_dir)` instead of `safe_resolve(base_dir, user_path)` disables path jailing. Because `safe_resolve` treats the first argument as the jail root and resolve `user_path` relative to it, swapping them allows the LLM to provide an absolute path as the first argument, effectively making any directory the "jail".
**Learning:** Argument order in security-critical utility functions is as important as the implementation itself.
**Prevention:** Always verify that `base_dir` (the trusted boundary) is passed as the first argument to `safe_resolve`. Add unit tests that specifically check for argument-swap regressions.
