## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2024-06-25 - Cross-Platform Backslash Traversal and Swapped Argument Vulnerabilities
**Vulnerability:** (1) POSIX filesystems treat backslashes (`\`) as literal characters in a filename rather than path separators. This allows Windows-style traversal sequences (e.g., `..\..\etc\passwd`) to bypass standard jail resolution checks on POSIX systems while remaining executable on Windows environments. (2) Mismatched argument signatures between fallback and primary utilities can lead to critical validation swaps, such as passing untrusted user input as the jail root.
**Learning:** Always normalize backslashes to forward slashes before any resolution or boundary check. Ensure strict alignment of security function signatures across fallbacks and primary versions.
**Prevention:** Use `str(path).replace("\\", "/")` universally before resolving path limits, and enforce argument symmetry using standardized automated tests.
