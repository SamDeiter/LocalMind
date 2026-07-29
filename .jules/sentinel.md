## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2025-02-14 - Swapped Parameters in safe_resolve validation
**Vulnerability:** Inconsistent fallback signatures or mismatched parameter orders when calling a security utility (like calling `safe_resolve(user_path, base_dir)` instead of `safe_resolve(base_dir, user_path)`) can completely neutralize path jailing protections, turning the untrusted input itself into the trusted root directory.
**Learning:** Security APIs must have highly descriptive parameter names, strong type signatures, or unit tests specifically asserting that incorrect calling conventions or fallbacks do not silently bypass the jail or crash the validator.
**Prevention:** Always write unit tests asserting correct argument ordering and behavior for both default and fallback security utility functions. Use keyword arguments or rigid type-checking/signatures when available.
