## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2026-07-25 - Cross-Platform Backslash Bypass in POSIX Path Jailing
**Vulnerability:** On POSIX-compliant systems (like Linux), a backslash (`\`) is considered a regular filename character rather than a directory separator. If untrusted paths use Windows-style separators (e.g., `..\..\Windows`), POSIX path resolution handles them as a single literal filename (e.g., `..\..\Windows`) inside the base jail rather than resolving upwards, thus bypassing traversal-detection checks while remaining potentially dangerous to downstream parsers/Windows targets.
**Learning:** Operating system path parsers do not treat path separators uniformly. A path string safe on Linux can become highly dangerous when passed to or interpreted by a Windows backend or other shell processors that handle backslashes as path separators.
**Prevention:** Always normalize incoming path strings by replacing all backslashes (`\`) with forward slashes (`/`) before any resolution, jailing, or validation logic.
