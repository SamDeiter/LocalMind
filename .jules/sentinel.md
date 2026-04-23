## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2024-04-23 - Secret Preservation Pattern in Settings
**Vulnerability:** Updating settings via API can accidentally overwrite existing secrets with masked strings (e.g., "********") if the frontend sends back the masked value it received from a GET request.
**Learning:** Naive update handlers that save the entire incoming dictionary will corrupt the secret store if masking is active on read.
**Prevention:** Implement a preservation helper that compares incoming and current settings. If an incoming value is a masked sentinel (e.g., starts with "****"), restore the original value from the database before saving.

## 2024-04-23 - Cross-Platform Path Traversal via Backslashes
**Vulnerability:** Path jailing logic that only handles forward slashes can be bypassed on POSIX systems if the underlying file system or a downstream tool interprets backslashes as directory separators, or if the test suite specifically checks for them.
**Learning:** `pathlib.Path.resolve()` on Linux treats `..\u005c` as part of a filename, not a directory jump, but some environments or manual joins might behave differently.
**Prevention:** Normalize all incoming untrusted paths by replacing `\u005c` with `/` before any resolution or jailing checks to ensure consistent behavior across platforms and test suites.
