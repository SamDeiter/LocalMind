## 2024-05-24 - Path Traversal bypass via `.startswith()`
**Vulnerability:** In Python, verifying if a target path is inside a workspace using `str(target).startswith(str(workspace))` is vulnerable to directory traversal bypasses. For example, `/home/user/workspace_evil/file.txt` will pass the check against `/home/user/workspace` because it shares the same string prefix, even though it is in a different directory.
**Learning:** `startswith()` only checks string prefixes and does not respect path boundaries or directory separators.
**Prevention:** Use `os.path.commonpath([workspace, target]) == workspace` or `target.relative_to(workspace)` instead. These functions parse path segments properly and ensure the target is strictly a child directory or file of the workspace.

## 2024-06-12 - Command Injection bypass via Shell Operators
**Vulnerability:** Simple prefix checks like `command.startswith("rm")` are easily bypassed by chaining commands with shell operators such as `;`, `&&`, `||`, or newlines (e.g., `ls ; rm -rf /`).
**Learning:** `startswith()` only checks the beginning of the entire input string. In a shell context, one input string can contain multiple independent commands.
**Prevention:** Split the input command string by shell operators (`[;&|\n]`) and validate each resulting segment individually. Use regex with word boundaries (`\b`) to prevent false positives and bypasses (e.g., `army` vs `rm`).

## 2024-07-15 - Security bypass via Argument Swap in Utility Functions
**Vulnerability:** Reversing the order of arguments in security utility calls (e.g., passing the untrusted path as the 'base' and the jail as the 'target') can completely bypass jailing logic. In LocalMind, `safe_resolve(arg_value, job_dir)` was called instead of `safe_resolve(job_dir, arg_value)`, allowing an attacker to define their own "jail".
**Learning:** Security utilities are only as strong as their call-sites. Reversal of "trusted" vs "untrusted" parameters is a high-impact logic error.
**Prevention:** Use keyword arguments for security-sensitive functions to make the source/destination or base/target relationship explicit, and add unit tests that specifically check for argument-order sensitivity.

## 2024-07-15 - Path Traversal via lack of Backslash Normalization on POSIX
**Vulnerability:** On POSIX systems, `pathlib.Path` does not treat backslashes (`\`) as directory separators. A payload like `..\..\etc\passwd` remains a single "filename" segment and can bypass jail checks that only look for forward slashes, eventually potentially reaching the OS layer if it performs its own normalization.
**Learning:** Defense-in-depth requires normalizing all possible separators (both `/` and `\`) before any path resolution or boundary checks, regardless of the host OS.
**Prevention:** Always perform `path_str.replace("\\", "/")` before passing untrusted path strings to `pathlib.Path` or `os.path` utilities in security-critical code.
