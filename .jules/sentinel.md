## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2025-05-14 - Path Traversal in PromptGuard via reversed arguments
**Vulnerability:** The `PromptGuard.validate_tool_call` method called `safe_resolve(arg_value, job_dir)` instead of `safe_resolve(job_dir, arg_value)`. Because `safe_resolve` only checks if the *second* argument is inside the *first*, this effectively disabled path jailing and allowed any absolute path to be used as a tool argument if the job directory happened to be a child of it (or just by nature of the reversed check). Additionally, it used a weak string-based `.startswith()` check.
**Learning:** Security utilities with positional arguments are prone to "argument reversal" bugs that completely bypass their protection. String-based path checks are vulnerable to prefix bypasses (e.g., `/app/data_secret` starting with `/app/data`).
**Prevention:** Use `pathlib.Path.is_relative_to()` for all jail checks. Always verify the argument order of security-critical functions. Include a redundant check at the call site for defense-in-depth.
