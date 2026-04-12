# Sentinel Security Journal

## 2025-05-15 - Masking & Preservation of Sensitive Settings
**Vulnerability:** API endpoints returning sensitive configuration (SMTP passwords, API keys) in plaintext allows any user with read access to the settings UI (or anyone intercepting the API response) to steal credentials.
**Learning:** Simply masking the field on the way out is insufficient if the UI sends the mask back during a save operation, as it would overwrite the real secret with asterisks.
**Prevention:** Implement a "mask-on-read, preserve-on-write" pattern. GET endpoints redact secrets with a fixed placeholder (e.g., `****`). POST endpoints check for this placeholder; if found, they restore the existing secret from backend storage instead of overwriting.

## 2025-05-15 - Security Test Failures due to Graceful Fallbacks
**Vulnerability:** `MemoryEncryption` fell back to Base64 when `cryptography` was missing. While this allowed the app to "work", it meant that a test providing a "wrong key" still successfully "decrypted" the data (because Base64 doesn't use a key), leading to a silent security failure.
**Learning:** Security modules that provide graceful fallbacks for development can mask critical failures in security-themed unit tests.
**Prevention:** Ensure `cryptography` is a mandatory dependency for environments running security tests. Validate that "wrong key" scenarios actually raise errors or return `None` only when true encryption is active.
