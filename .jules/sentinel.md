## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2025-05-15 - State Corruption via In-Place Redaction
**Vulnerability:** Redacting secrets in a dictionary returned by a shared utility (like `get_settings()`) without creating a copy modifies the underlying state. This replaces actual credentials with asterisks in the application's memory/cache, breaking features that rely on those secrets.
**Learning:** Functions returning configuration often return references to shared objects. Direct mutation impacts the entire runtime.
**Prevention:** Always use `.copy()` or `deepcopy()` before applying masks or redactions to data retrieved from shared modules.

## 2025-05-15 - RCE/XSS via `eval()` on API Data
**Vulnerability:** Calling `eval()` on a `code_snippet` field fetched from a backend API allows arbitrary JavaScript execution in the user's browser. If the backend or the communication channel is compromised, an attacker can execute malicious code.
**Learning:** `eval()` is inherently dangerous and should never be used on data from external sources, even internal APIs.
**Prevention:** Remove `eval()` usage. Display code using safe DOM APIs like `.textContent` and use syntax highlighters for visualization only. Never provide "Execute" functionality for arbitrary API-provided snippets.
