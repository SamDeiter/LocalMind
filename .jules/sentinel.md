## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2025-05-15 - Sensitive Credential Exposure in Settings API
**Vulnerability:** API endpoints that return configuration settings (like SMTP passwords or API keys) for user review in the UI will leak these secrets in plaintext unless explicitly masked. Partial masking (e.g., revealing last 4 characters) still leaks information like secret length and character patterns.
**Learning:** Returning secrets in any form to the UI increases the attack surface. Furthermore, updating settings requires a preservation mechanism so the UI doesn't overwrite a secret with a placeholder.
**Prevention:** Implement a centralized masking helper that replaces sensitive values with a static placeholder (e.g., `****`). Implement "preservation logic" in POST/PUT handlers: if the incoming value is the placeholder, restore the actual secret from storage before saving.
