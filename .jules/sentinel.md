## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2026-07-10 - Path Jailing and Command Injection Robustness
**Vulnerability:** Path traversal via backslash normalization bypass and command injection via newline chaining. Also, a critical bug where safe_resolve arguments were reversed, disabling path jailing.
**Learning:** Manual prefix-based path checks (e.g. .startswith()) are fragile and should be replaced with Path.is_relative_to(). Shell metacharacter sets must include newlines (\n) and carriage returns (\r) to prevent command chaining in tools that use shell=True.
**Prevention:** Always use Path.is_relative_to() for boundary checks. Ensure security utility arguments (base_dir, user_path) are never reversed. Validate that shell metacharacter sets are comprehensive.
