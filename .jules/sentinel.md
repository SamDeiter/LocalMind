## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2025-05-15 - Security Filter Bypass via Obfuscation and Reflection
**Vulnerability:** Security filters that rely on regex patterns (like `DANGEROUS_PATTERN` or `BLOCKLIST_PATTERNS`) can be bypassed using shell-specific obfuscation (ANSI escape codes, backslashes, quotes) or language-specific reflection (Python's `getattr`, `__import__`).
**Learning:** Raw input strings must be normalized (stripping non-functional characters like ANSI escapes) before being matched against blocklists. Similarly, blocklists for code execution must account for indirect calls via reflection and internal attributes.
**Prevention:** Implement a `_normalize` step before filtering that strips shell-interpreted characters. Expand blocklists to include high-level sinks (`os.system`) and reflection methods (`getattr`, `__getattribute__`).
