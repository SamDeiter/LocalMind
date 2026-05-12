## 2025-05-15 - Command Obfuscation Bypass in TerminalTool
**Vulnerability:** Shell commands could bypass `DANGEROUS_PATTERN` regex checks using backslashes (e.g., `r\m`) or quotes (e.g., `'r'm`).
**Learning:** Regex word boundaries (`\b`) are effective against common sub-word matches but fail when shell-specific escape characters are interspersed in the command string, as shells strip these characters before execution.
**Prevention:** Normalize raw shell commands by stripping backslashes, quotes, and line continuations before evaluating them against security patterns.
