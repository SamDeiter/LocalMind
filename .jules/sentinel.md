## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2025-02-15 - Windows Path Traversal Bypass on POSIX Systems
**Vulnerability:** When a path-jailing function like `safe_resolve` validates paths on POSIX systems, Windows-style backslashes (`\`) are not natively recognized as directory delimiters. This allows traversal patterns like `..\\..\\` to be resolved as part of a single (highly bizarre) filename rather than directory traversal, bypassing jail checks but potentially leading to bypasses if passed to other contexts or APIs.
**Learning:** `pathlib` and typical POSIX path operations do not interpret backslash separators. To ensure jail/bounds verification, any untrusted user path must have its backslashes normalized to forward slashes before resolution.
**Prevention:** Normalize all backslashes using `user_path_str.replace("\\", "/")` at the very entry of path jailing and resolving utilities.
