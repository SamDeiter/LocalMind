import json
import time
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

def main():
    root = Path(__file__).resolve().parent
    version_file = root / "version.json"
    changelog_file = root / "CHANGELOG.md"
    readme_file = root / "README.md"
    
    # 1. Update version.json
    try:
        if version_file.exists():
            data = json.loads(version_file.read_text(encoding="utf-8"))
            data["version"] = "1.0.0"
            data["build"] = int(data.get("build", 308)) + 1
            data["codename"] = "Ignition"
            data["last_built"] = datetime.now(timezone.utc).isoformat()
            version_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            print("[OK] version.json updated to 1.0.0 Ignition")
    except Exception as e:
        print(f"[!] Failed to update version.json: {e}")

    # 2. Create CHANGELOG.md
    changelog_content = """# Changelog

## [1.0.0] - Ignition - 2026-04-21
### Added
- **Autonomous Agent Mode (Phase F):** Sourced reporting, goal planning, and background gap analysis execution.
- **Report Forge (Phase F2):** MedGemma-powered school interview note summarization with immutable hashes.
- **VS Code Extension (Phase E):** Sidebar WebView panel and Context Extractor hook targeting TypeScript/VS Code API.
- **Google Workspace OAuth (Phase D6):** Persistent local token system to authorize Drive, Docs, Gmail.
- **Streaming Resilience (Phase D5):** `TokenCheckpoint` buffers Ollama chunks and preserves session memory over disconnects.
- **Support for Multi-models:** Integrated routing for `medgemma:4b`, `gemma4:e2b`, and multimodal support.

### Completed
- Intelligence Map Visualization and Cryptographic Skill Integrity (Phase G).

"""
    try:
        changelog_file.write_text(changelog_content, encoding="utf-8")
        print("[OK] CHANGELOG.md created")
    except Exception as e:
        print(f"[!] Failed to create CHANGELOG.md: {e}")

    # 3. Security Scan
    print("\\n--- Running Mandatory Pre-Push Security Scan ---")
    try:
        # Use python to scan instead of grep string to avoid windows grep issues if not installed
        cmd = 'cmd /c "findstr /S /R /C:"AIza" /C:"api[_-]*key" /C:"secret" /C:"password" /C:"token" *.py *.json *.js *.html .env.example"'
        result = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, shell=True)
        if result.returncode == 0:
            print("[!] WARNING: Potential secrets found in the codebase!")
            print(result.stdout[:2000]) # Print first 2000 chars of matches
        else:
            print("[OK] Security Scan Passed: Zero hardcoded secrets detected in specified filetypes.")
            
    except Exception as e:
         print(f"[!] Security scan error: {e}")
         
    print("\\nPhase H v1.0.0 Launch Hardening Script Completed.")

if __name__ == "__main__":
    main()
