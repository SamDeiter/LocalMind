"""
Build Version Manager for LocalMind
Auto-increments build number in version.json.
Optional: --patch, --minor, --major to bump semver.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# version.json lives at the project root (one level up from scripts/)
VERSION_FILE = Path(__file__).parent.parent / "version.json"

def bump(level=None):
    data = json.loads(VERSION_FILE.read_text())
    data["build"] = data.get("build", 0) + 1
    data["last_built"] = datetime.now(timezone.utc).isoformat()

    if level:
        parts = data.get("version", "0.1.0").split(".")
        major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
        if level == "major":
            major += 1; minor = 0; patch = 0
        elif level == "minor":
            minor += 1; patch = 0
        elif level == "patch":
            patch += 1
        data["version"] = f"{major}.{minor}.{patch}"

    VERSION_FILE.write_text(json.dumps(data, indent=2) + "\n")
    print(f"  v{data['version']} build #{data['build']}")
    return data

if __name__ == "__main__":
    lvl = None
    if len(sys.argv) > 1:
        arg = sys.argv[1].lstrip("-")
        if arg in ("patch", "minor", "major"):
            lvl = arg
    bump(lvl)
