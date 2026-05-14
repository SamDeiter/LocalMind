## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2026-05-14 - Python Sandbox Escape via dynamic attribute access and broad imports
**Vulnerability:** Simple blocklists for `os.remove` or specific dangerous functions are insufficient to prevent arbitrary code execution in a Python sandbox. Attackers can use `getattr(os, 'system')` or `__import__('os').system('...')` to bypass static string checks.
**Learning:** Python's dynamic nature allows accessing restricted functionality through multiple indirect methods. A security blocklist must include fundamental primitives like `getattr` and `__import__` if they are not strictly required.
**Prevention:** Harden sandbox blocklists by including `getattr`, `__import__`, and all process execution entry points (`os.system`, `subprocess.*`, `pty.spawn`). For robust security, prefer AST-based analysis or containerized isolation over regex blocklists.
