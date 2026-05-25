## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2024-05-25 - RunCodeTool Blocklist Bypass via Missing Dangerous Functions
**Vulnerability:** The `RunCodeTool` blocklist was missing critical dangerous Python functions like `os.system`, `os.popen`, `getattr`, and `__builtins__`. This allowed an agent or an attacker to execute arbitrary shell commands or bypass other blocklist entries using reflection (e.g., `getattr(os, 'system')('rm -rf /')`).
**Learning:** A static blocklist for code execution must be comprehensive and include reflection-based bypasses (`getattr`, `setattr`, `eval`, etc.) and internal machinery (`__builtins__`, `sys.modules`).
**Prevention:** Regularly audit blocklists against known bypass techniques. Prefer a whitelist-based sandbox if possible, but if using a blocklist, ensure it covers all common execution and reflection vectors.
