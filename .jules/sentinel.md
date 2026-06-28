## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2025-05-14 - Path Jailing bypass via Argument Swap
**Vulnerability:** The `safe_resolve(base_dir, user_path)` utility was called with swapped arguments: `safe_resolve(user_path, base_dir)`. This caused the utility to treat the untrusted user path as the "jail root" and the intended jail root as the relative path, effectively bypassing all security checks and allowing access to any file on the system.
**Learning:** Security utilities with multiple path arguments are prone to positional errors.
**Prevention:** Use keyword arguments for security-critical functions (e.g., `safe_resolve(base=job_dir, path=arg_value)`) or add type-level assertions to distinguish between trusted base directories and untrusted user input.

## 2025-05-14 - Multi-stage Command Injection via Newlines
**Vulnerability:** Shell injection filters that only block `;`, `&`, and `|` are bypassed by using newlines (`\n`) or carriage returns (`\r`). In many shell environments, a newline is a valid command separator, allowing an attacker to execute arbitrary commands by appending them after a newline.
**Learning:** Shell metacharacter blocklists must include all whitespace-based separators, not just punctuation.
**Prevention:** Always include `\n` and `\r` in shell metacharacter filters. Prefer using `shlex.quote` or subprocess with an argument list instead of shell strings whenever possible.
