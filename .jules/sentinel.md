## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2024-05-29 - Command Injection via shell=True and String Concatenation
**Vulnerability:** Passing a single string to `subprocess.run(..., shell=True)` is highly vulnerable to command injection if any part of the string is derived from untrusted input. Even using a list of arguments but joining them with spaces before passing to `shell=True` is dangerous (e.g., `cmd /c "git " + " ".join(args)`).
**Learning:** `shell=True` invokes a shell to interpret the command string, allowing shell operators like `;`, `&&`, `||`, and `|` to execute arbitrary additional commands.
**Prevention:** Always use list-based arguments with `shell=False`. If a command must be parsed from a string, use `shlex.split()` with `posix=(os.name != 'nt')` to safely tokenize it before passing it to `subprocess.run(..., shell=False)`.
