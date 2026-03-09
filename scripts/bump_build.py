"""
Build Version Bumper — auto-increments the build number in version.json.

Usage:
    python bump_build.py              # Increment build number
    python bump_build.py --patch      # Bump patch (0.3.0 -> 0.3.1)
    python bump_build.py --minor      # Bump minor (0.3.0 -> 0.4.0)
    python bump_build.py --major      # Bump major (0.3.0 -> 1.0.0)

Can be used as a git pre-commit hook or called manually.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

VERSION_FILE = Path(__file__).parent / "version.json"


def bump(level: str = "build"):
    with open(VERSION_FILE, "r") as f:
        data = json.load(f)

    # Always increment build
    data["build"] = data.get("build", 0) + 1
    data["last_built"] = datetime.now().astimezone().isoformat()

    # Optionally bump semantic version
    if level in ("patch", "minor", "major"):
        parts = data["version"].split(".")
        major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])

        if level == "patch":
            patch += 1
        elif level == "minor":
            minor += 1
            patch = 0
        elif level == "major":
            major += 1
            minor = 0
            patch = 0

        data["version"] = f"{major}.{minor}.{patch}"

    with open(VERSION_FILE, "w") as f:
        json.dump(data, f, indent=4)
        f.write("\n")

    print(f"✅ v{data['version']} build #{data['build']} — {data['last_built']}")
    return data


if __name__ == "__main__":
    level = "build"
    if "--patch" in sys.argv:
        level = "patch"
    elif "--minor" in sys.argv:
        level = "minor"
    elif "--major" in sys.argv:
        level = "major"
    bump(level)
