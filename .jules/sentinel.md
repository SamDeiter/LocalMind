## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2025-05-15 - Secret Leakage in API Response via Unmasked Configuration
**Vulnerability:** Sensitive credentials (like `smtp_pass`) were being returned in plaintext in API responses (both GET and POST), exposing them to the UI and potential attackers with access to the network or browser console.
**Learning:** It's easy to focus on masking secrets in the primary 'get' endpoint while forgetting that 'update' (POST/PUT) endpoints often return the saved object as confirmation, leading to immediate re-exposure of the secret after it was updated.
**Prevention:** Always use a `.copy()` of settings objects before modification to avoid corrupting global state, and ensure masking logic is applied to ALL response paths that include sensitive data structures.
