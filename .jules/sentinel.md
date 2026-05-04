## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2024-07-05 - Plain-text Credential Exposure in API responses
**Vulnerability:** Sensitive credentials (like SMTP passwords or API keys) stored in configuration files are often returned in plain text by "get settings" endpoints, making them visible to the frontend and anyone with access to the API.
**Learning:** Returning full configuration objects directly from the database/file to the client without filtering is a common source of credential leaks.
**Prevention:** Implement a centralized masking and preservation mechanism. Use a dedicated `_mask_settings` function to replace sensitive fields with a constant mask (e.g., `********`) before sending responses. Use a matching `_preserve_secrets` function during updates to ensure that if the client sends back the mask, the original secret is kept rather than being overwritten.
