## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2024-06-25 - In-place mutation of shared settings
**Vulnerability:** Directly modifying settings dictionaries returned by a retrieval function (e.g., ) can corrupt the in-memory state of the application. If secrets are masked in-place for a GET request, subsequent operations (like background jobs or POST updates) may use the masked values, leading to authentication failures or permanent data loss if the masked values are saved back to disk.
**Learning:** Python dictionaries are passed by reference. Mutating a configuration object in a route handler affects all other modules sharing that reference.
**Prevention:** Always use `.copy()` or create a new dictionary when modifying sensitive data for presentation (masking/redaction). Centralize masking logic in helper functions that guarantee a fresh, non-mutated copy is returned.

## 2024-06-25 - In-place mutation of shared settings
**Vulnerability:** Directly modifying settings dictionaries returned by a retrieval function (e.g., `notifications.get_settings()`) can corrupt the in-memory state of the application. If secrets are masked in-place for a GET request, subsequent operations (like background jobs or POST updates) may use the masked values, leading to authentication failures or permanent data loss if the masked values are saved back to disk.
**Learning:** Python dictionaries are passed by reference. Mutating a configuration object in a route handler affects all other modules sharing that reference.
**Prevention:** Always use `.copy()` or create a new dictionary when modifying sensitive data for presentation (masking/redaction). Centralize masking logic in helper functions that guarantee a fresh, non-mutated copy is returned.
