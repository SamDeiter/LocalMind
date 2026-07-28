## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2025-02-14 - POSIX Path Traversal Bypass via Backslashes
**Vulnerability:** Under POSIX/Linux, backslashes (`\`) are treated as normal characters in file paths rather than directory separators. However, downstream consumers (or Windows environments) may still interpret them as directory separators. This mismatch allows Windows-style traversal sequences (like `..\..\etc\passwd`) to bypass jailing validation in `Path.resolve()`, because POSIX resolves the sequence as a single file name staying inside the jail.
**Learning:** File path validation must normalize path separators across operating system standards before performing security boundary checks.
**Prevention:** Normalize all backslashes (`\`) to forward slashes (`/`) in the input string before resolving or jailing the path.
