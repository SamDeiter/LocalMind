## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2025-06-01 - Command Injection via `shell=True` and Import Ambiguity
**Vulnerability:** Using `subprocess.run(..., shell=True)` with string-formatted commands allows arbitrary command execution. Additionally, the coexistence of `backend/tools.py` and `backend/tools/` package caused import ambiguity, complicating security verification.
**Learning:** `shell=True` should be avoided in favor of list-based arguments with `shell=False`. When verifying fixes, be aware of package/module name collisions that might mask the code being tested.
**Prevention:** Always use list-based `subprocess.run` and `shlex.split` for dynamic commands. Maintain clear separation between package names and module names to avoid shadowing imports.
