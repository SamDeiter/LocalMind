## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2024-05-24 - Masking Sensitive Settings with Preservation
**Vulnerability:** Exposing raw credentials (API keys, SMTP passwords) in GET responses to the frontend increases the risk of credential theft via XSS or shoulder surfing.
**Learning:** Simply masking the output is insufficient if the user needs to update OTHER settings in the same form without re-entering the secret. The backend must recognize a placeholder (e.g., `****`) and preserve the existing secret.
**Prevention:** Use a centralized `_mask_settings` helper for all configuration GET routes and a `_preserve_secrets` helper for POST routes. This ensures secrets are redacted in transit to the UI but maintained in storage unless explicitly changed by the user.
