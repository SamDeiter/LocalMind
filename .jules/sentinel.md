## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2025-02-23 - Argument Order and Backslash Normalization in Fallback/Active Path Resolution
**Vulnerability:** A signature mismatch between the real `safe_resolve` in `paths.py` (which takes `(base_dir, user_path)`) and the calling code + fallback code in `prompt_guard.py` (which expected/implemented `(user_path, base_dir)`) would cause critical path jailing checks to fail or execute on incorrect folders. Additionally, the lack of backslash normalization on POSIX (Linux) systems allowed `..\\..\\` traversal sequences to be treated as a literal directory name, bypassing path jail checks.
**Learning:** Fallback security implementations must align exactly in signature and behavior with production helpers. Normalization of OS-specific separators must occur universally before path resolution to prevent bypasses under POSIX environments when evaluating Windows-style input.
**Prevention:** Always unify core helper signatures and standardize tests checking backslash input on POSIX environments to verify alignment.
