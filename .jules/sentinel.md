## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2024-05-24 - Argument Reversal in Path Jailing Utilities
**Vulnerability:** Reversing arguments in security utilities (e.g., calling `safe_resolve(user_path, base_dir)` instead of `safe_resolve(base_dir, user_path)`) can inadvertently disable path jailing, as the utility may resolve the path relative to the current working directory or fail silently.
**Learning:** Security modules often have strict argument orders that are not enforced by the compiler; logic errors at the call site are as dangerous as vulnerabilities in the utility itself.
**Prevention:** Use type hinting and descriptive parameter names; always verify that the "jail root" is passed as the primary anchor for resolution. Use `pathlib.Path.is_relative_to()` for final boundary checks as it is more robust than string-based prefix matching.
