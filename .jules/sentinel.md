## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2025-05-15 - AST-based Code Validation for Tool Safety
**Vulnerability:** Regex-based blocklists for code execution (e.g., blocking "os.remove") are easily bypassed using string concatenation (e.g., "getattr(os, 'rm' + 'ove')") or aliasing.
**Learning:** Regex only inspects the surface of the code. AST analysis allows for inspecting the semantic structure, detecting blocked function calls and attribute access even when obfuscated.
**Prevention:** Use Python's `ast` module to walk the code's parse tree. Block dangerous built-ins (`eval`, `exec`, `getattr`) and imports (`os`, `subprocess`) at the node level. Combine with a regex first-pass for performance and simple cases.
