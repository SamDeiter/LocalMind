## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2025-05-15 - Command Injection via `shell=True` and `cmd /c`
**Vulnerability:** Functions in `backend/git_ops.py` used `subprocess.run` with `shell=True` and string-joined arguments (e.g., `f'cmd /c "git {" ".join(args)}"'`), allowing arbitrary command execution via shell operators in `args`.
**Learning:** Using `shell=True` even with a list of arguments joined by spaces is insecure. The `cmd /c` prefix also introduced a hard dependency on Windows, which failed on POSIX systems.
**Prevention:** Always use `shell=False` and pass arguments as a list. Use `sys.executable` to refer to the current Python interpreter for cross-platform reliability. Avoid `cmd /c` or other shell-specific wrappers unless absolutely necessary and sanitized.
