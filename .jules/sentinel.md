## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2025-02-15 - Parameter Swapping and Prefix-Collision in Path Jailing
**Vulnerability:** In `PromptGuard.validate_tool_call`, calling `safe_resolve` with swapped arguments `safe_resolve(arg_value, job_dir)` instead of `(job_dir, arg_value)` incorrectly treats the untrusted tool argument as the jail directory and the trusted directory as the user-controlled path. Additionally, using `startswith` prefix checking on paths allows prefix-collision traversal bypasses (e.g. `../job_123_evil/secret.txt` matching a prefix of `/path/to/job_123`).
**Learning:** Swapped API arguments can completely invert security guardrail models. Prefix checks ignore segment boundaries, allowing relative parent traversal directories with similar prefixes to bypass security.
**Prevention:** Always follow the standardized `safe_resolve(base_dir, user_path)` signature, and employ segment-aware checks like `Path.is_relative_to()` to ensure structural path containment.
