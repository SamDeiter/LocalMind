## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2024-10-24 - Fail-open Bootstrap mode via Key Revocation
**Vulnerability:** Bootstrap mode (allowing unauthenticated admin access) was triggered whenever no *active* keys were found. If an administrator revoked all existing keys (e.g., during a credential rotation or after an incident), the system would unintentionally fall back into bootstrap mode, granting full access to any unauthenticated user.
**Learning:** Security "bootstrap" or "initial setup" states must be strictly one-way. Relying on the absence of *active* state to trigger these modes is a "fail-open" pattern.
**Prevention:** Check for the existence of *any* historical configuration (e.g., any key record, even if revoked) to determine if the system has already been initialized. Once initialized, the bootstrap state should never be reachable again through standard runtime state changes like revocation.
