## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2024-07-25 - Inverted Argument Order in Path Jailing Validation
**Vulnerability:** A mismatched function signature between the actual `safe_resolve(base_dir, user_path)` definition and its invocation `safe_resolve(arg_value, job_dir)` in `prompt_guard.py` caused the path jailing check to swap the jail directory with the untrusted user input, resulting in bypassed path validation or exceptions.
**Learning:** Standardizing security utility signatures across the codebase is vital, and fallback implementations must mirror the exact production signature to prevent argument-swapping vulnerabilities.
**Prevention:** Enforce strict type checking and match interface contracts explicitly; write unit tests validating that path jailing successfully flags escapes in tool argument validations.
