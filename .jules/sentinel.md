## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2026-04-14 - Command Injection in RunCodeTool
**Vulnerability:** The `RunCodeTool` had an incomplete blocklist that only focused on file deletion, allowing execution of arbitrary system commands via `os.system`, `os.popen`, or `subprocess`. It also lacked protection against obfuscation techniques like `getattr` or `__import__`.
**Learning:** Regex-based blocklists are easily bypassed by alternative functions or dynamic attribute access. A multi-layered defense is required.
**Prevention:** Combine strict regex blocklists for common sinks with AST (Abstract Syntax Tree) analysis to detect dangerous function calls and attribute accesses even when they are obfuscated or unusually formatted.
