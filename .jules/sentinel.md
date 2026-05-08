## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2024-05-15 - Credential Leak in POST/PUT API Responses
**Vulnerability:** Masking sensitive credentials (like SMTP passwords) in GET responses is insufficient if the same data is returned in plain text within POST/PUT responses.
**Learning:** Developers often forget that "Echoing" the updated object in a response can bypass front-end masking. Additionally, logic to preserve masked values during round-trips must handle all possible string lengths correctly.
**Prevention:** Always use a central masking utility to scrub sensitive fields from ALL API responses. Ensure that "restoration" logic (which checks if a masked value was sent back) uses a consistent marker (e.g., always starting with 4+ stars) that cannot be confused with short real passwords.
