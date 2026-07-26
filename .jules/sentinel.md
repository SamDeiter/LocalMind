## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2025-02-13 - Path Traversal bypass via Windows-style Backslashes on POSIX Systems
**Vulnerability:** On POSIX-compliant operating systems, `pathlib.Path` treats backslashes (`\`) as literal characters in a filename rather than path separators. A malicious relative path sequence using backslashes, such as `..\\..\\Windows`, is therefore resolved as a single file or folder named `..\..\Windows` within the base directory. This bypasses typical `relative_to` or prefix-based jail checks because it stays inside the directory tree, but is highly vulnerable if the path is later processed by Windows systems, shell commands, or databases that interpret backslashes as directory separators.
**Learning:** Checking relative path escape using only POSIX rules fails to recognize backslashes as separators, allowing directory traversal sequences to slip through validation.
**Prevention:** Always normalize incoming user paths by replacing backslashes with forward slashes (`.replace('\\', '/')`) before performing any path resolution or jail verification.
