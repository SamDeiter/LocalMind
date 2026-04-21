"""
phase_d_rca_and_git.py
======================
Phase D execution script for LocalMind v0.9.1 → v1.0.0
Performs three targeted changes:
  1. Fixes latent calibration crash in metacognition/controller.py
  2. Wires TraceAnalyzer (RCA) into the post_process failure path
  3. Adds git awareness tools (status, diff, log_short) to git_ops.py
  4. Creates backend/integrations/cross_project_hub.py
"""

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND = PROJECT_ROOT / "backend"

# ──────────────────────────────────────────────────────────────────
# 1. Fix controller.py — calibration crash + RCA wiring
# ──────────────────────────────────────────────────────────────────

CONTROLLER_PATH = BACKEND / "metacognition" / "controller.py"

def patch_controller():
    print("Patching metacognition/controller.py ...")
    src = CONTROLLER_PATH.read_text(encoding="utf-8")
    original = src

    # ── Fix 1: Uncomment CalibrationTracker and wire RCA import ──
    old_calibration_commented = "         # # self.calibration = CalibrationTracker()"
    new_calibration = "        self.calibration = CalibrationTracker()\n\n        # AgentFixer RCA — wired into post_process failure path\n        from backend.validation.root_cause import TraceAnalyzer\n        self._rca = TraceAnalyzer(ollama_url)"
    if old_calibration_commented in src:
        src = src.replace(old_calibration_commented, new_calibration)
        print("  [OK] Fixed commented-out CalibrationTracker + injected TraceAnalyzer")
    else:
        print("  [WARN] Could not find commented CalibrationTracker — checking alternate pattern")
        # Try alternate whitespace
        alt = "# # self.calibration = CalibrationTracker()"
        if alt in src:
            src = src.replace(alt, new_calibration)
            print("  [OK] Fixed (alternate pattern) CalibrationTracker + injected TraceAnalyzer")
        else:
            print("  [SKIP] CalibrationTracker pattern not found — manual review needed")

    # ── Fix 2: Wire RCA trace logging into the self_check FAIL branch ──
    old_fail_log = (
        "        self._emit(\"thinking\",\n"
        "                    f\"Self-check: {'PASS' if check.passed else 'FAIL'} — {len(check.issues)} issues\",\n"
        "                    thinking_type=\"self_check\",\n"
        "                    check=check.to_dict())"
    )
    new_fail_log = (
        "        self._emit(\"thinking\",\n"
        "                    f\"Self-check: {'PASS' if check.passed else 'FAIL'} — {len(check.issues)} issues\",\n"
        "                    thinking_type=\"self_check\",\n"
        "                    check=check.to_dict())\n\n"
        "        # ── RCA: Log failure trace for post-mortem analysis ──\n"
        "        if not check.passed:\n"
        "            from backend.validation.root_cause import TraceEntry\n"
        "            from backend.validation.base import ValidationResult, Severity\n"
        "            import time\n"
        "            failure_result = ValidationResult(\n"
        "                passed=False,\n"
        "                message=\"; \".join(check.issues[:3]),\n"
        "                severity=Severity.MODERATE,\n"
        "            )\n"
        "            entry = TraceEntry(\n"
        "                stage=\"post_process\",\n"
        "                validator_name=\"SelfChecker\",\n"
        "                result=failure_result,\n"
        "                timestamp=time.time(),\n"
        "            )\n"
        "            trace_id = f\"{conversation_id}:{session.turn_number}\"\n"
        "            self._rca.log_failure(trace_id, entry)\n"
        "            logger.info(f\"RCA trace logged for turn {trace_id} — {len(check.issues)} issues\")"
    )
    if old_fail_log in src:
        src = src.replace(old_fail_log, new_fail_log)
        print("  [OK] Wired RCA trace logging into self_check FAIL path")
    else:
        print("  [WARN] SKIP: self_check emit block not matched exactly — RCA wiring skipped")

    if src != original:
        CONTROLLER_PATH.write_text(src, encoding="utf-8")
        print(f"  ✓ Saved: {CONTROLLER_PATH.name}")
    else:
        print("  [WARN] No changes written to controller.py")

# ──────────────────────────────────────────────────────────────────
# 2. Add git awareness tools to git_ops.py
# ──────────────────────────────────────────────────────────────────

GIT_OPS_PATH = BACKEND / "git_ops.py"

GIT_AWARENESS_BLOCK = '''

# ── Git Awareness Tools (Phase D) ────────────────────────────────────────────

def git_status() -> dict:
    """Return the current working tree status as structured data.
    
    Returns a dict with:
      - branch: current branch name
      - staged: list of staged file paths
      - unstaged: list of unstaged modified file paths
      - untracked: list of untracked file paths
      - is_clean: bool
    """
    raw = git_run(["status", "--porcelain=v1", "--branch"])
    branch = ""
    staged, unstaged, untracked = [], [], []

    for line in raw.splitlines():
        if line.startswith("## "):
            # ## main...origin/main [ahead 1]
            branch_part = line[3:].split("...")[0].split(" ")[0]
            branch = branch_part
            continue
        if len(line) < 3:
            continue
        xy = line[:2]
        path = line[3:].strip()
        # Index (staged) status
        if xy[0] in ("M", "A", "D", "R", "C"):
            staged.append(path)
        # Working tree (unstaged) status
        if xy[1] in ("M", "D"):
            unstaged.append(path)
        # Untracked
        if xy == "??":
            untracked.append(path)

    return {
        "branch": branch,
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
        "is_clean": not staged and not unstaged and not untracked,
    }


def git_diff_staged() -> str:
    """Return the diff for all staged changes (what would be committed).
    
    Caps output at 8000 chars to avoid overwhelming LLM context.
    """
    diff = git_run(["diff", "--cached"])
    if len(diff) > 8000:
        diff = diff[:8000] + "\\n... [diff truncated at 8000 chars]"
    return diff or "(no staged changes)"


def git_log_short(n: int = 20) -> list[dict]:
    """Return the last N commits as a list of dicts.
    
    Each dict has: sha (short), message, author, date_relative
    """
    raw = git_run([
        "log", f"-{n}",
        "--pretty=format:%h|||%s|||%an|||%ar"
    ])
    commits = []
    for line in raw.splitlines():
        parts = line.split("|||")
        if len(parts) == 4:
            commits.append({
                "sha": parts[0],
                "message": parts[1],
                "author": parts[2],
                "date_relative": parts[3],
            })
    return commits


def git_commit_staged(message: str) -> dict:
    """Commit all currently staged files with the given message.
    
    Returns dict with: success, sha, message, error
    IMPORTANT: Callers should present staged filelist to user before calling.
    """
    if not message or not message.strip():
        return {"success": False, "error": "Commit message cannot be empty", "sha": "", "message": ""}
    
    result_raw = git_run(["commit", "-m", f"{message}"])
    if result_raw:
        # Extract SHA from "main abc1234 message" or "[branch abc1234] message"
        sha_match = __import__("re").search(r"[\\[\\s]([0-9a-f]{7})[\\]\\s]", result_raw)
        sha = sha_match.group(1) if sha_match else ""
        return {"success": True, "sha": sha, "message": message, "error": ""}
    return {"success": False, "sha": "", "message": message, "error": "git commit returned no output — check git status"}
'''

def patch_git_ops():
    print("Patching backend/git_ops.py ...")
    src = GIT_OPS_PATH.read_text(encoding="utf-8")

    if "def git_status()" in src:
        print("  [OK] SKIP: git_status already present")
        return

    src += GIT_AWARENESS_BLOCK
    GIT_OPS_PATH.write_text(src, encoding="utf-8")
    print(f"  ✓ Added git_status, git_diff_staged, git_log_short, git_commit_staged → {GIT_OPS_PATH.name}")


# ──────────────────────────────────────────────────────────────────
# 3. Create backend/integrations/cross_project_hub.py
# ──────────────────────────────────────────────────────────────────

INTEGRATIONS_DIR = BACKEND / "integrations"
HUB_PATH = INTEGRATIONS_DIR / "cross_project_hub.py"

CROSS_PROJECT_HUB_CONTENT = '''"""
cross_project_hub.py — Cross-Project Context Hub (Phase D)
============================================================
Detects the tech stack and project metadata for any local repository,
and exposes a /api/hub/context endpoint so the agent can reason 
across multiple projects.

Tech stack detection is done by scanning for signature files:
  - package.json      → Node.js / web stack
  - pyproject.toml    → Python (modern)
  - requirements.txt  → Python (legacy)
  - Cargo.toml        → Rust
  - go.mod            → Go
  - pom.xml           → Java/Maven
  - build.gradle      → Java/Gradle
  - Gemfile           → Ruby
  - composer.json     → PHP
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("localmind.integrations.cross_project_hub")

# Map: filename → (language/framework, display_name)
STACK_SIGNATURES: dict[str, tuple[str, str]] = {
    "package.json":      ("node",    "Node.js / JavaScript"),
    "pyproject.toml":    ("python",  "Python (pyproject)"),
    "requirements.txt":  ("python",  "Python (pip)"),
    "Cargo.toml":        ("rust",    "Rust"),
    "go.mod":            ("go",      "Go"),
    "pom.xml":           ("java",    "Java (Maven)"),
    "build.gradle":      ("java",    "Java (Gradle)"),
    "Gemfile":           ("ruby",    "Ruby"),
    "composer.json":     ("php",     "PHP"),
    "tsconfig.json":     ("typescript", "TypeScript"),
    "Dockerfile":        ("docker",  "Docker"),
    "docker-compose.yml": ("docker", "Docker Compose"),
    ".firebase.json":    ("firebase", "Firebase"),
    "firebase.json":     ("firebase", "Firebase"),
}


def detect_tech_stack(project_path: Path) -> list[dict]:
    """Scan a directory for tech stack signature files.
    
    Returns a list of detected stacks, each with:
      - language: str
      - display_name: str
      - signature_file: str
    """
    detected = []
    seen_langs = set()

    for filename, (language, display_name) in STACK_SIGNATURES.items():
        candidate = project_path / filename
        if candidate.exists() and language not in seen_langs:
            detected.append({
                "language": language,
                "display_name": display_name,
                "signature_file": filename,
            })
            seen_langs.add(language)

    return detected


def get_project_meta(project_path: Path) -> dict:
    """Collect project metadata from standard config files.
    
    Reads name/version from package.json or pyproject.toml if present.
    Returns a dict with: name, version, description, license
    """
    meta = {"name": project_path.name, "version": "", "description": "", "license": ""}

    # Node.js
    pkg = project_path / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            meta["name"] = data.get("name", meta["name"])
            meta["version"] = data.get("version", "")
            meta["description"] = data.get("description", "")
            meta["license"] = data.get("license", "")
            return meta
        except (json.JSONDecodeError, OSError):
            pass

    # Python (pyproject.toml — basic TOML parse without dependency)
    pyproject = project_path / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text(encoding="utf-8")
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("name ="):
                    meta["name"] = line.split("=", 1)[1].strip().strip(\'"\\'\\' \\')
                elif line.startswith("version ="):
                    meta["version"] = line.split("=", 1)[1].strip().strip(\'"\\'\\' \\')
                elif line.startswith("description ="):
                    meta["description"] = line.split("=", 1)[1].strip().strip(\'"\\'\\' \\')
        except OSError:
            pass

    return meta


def build_hub_context(project_path: Optional[str] = None) -> dict:
    """Build the full hub context payload for a given project directory.
    
    If project_path is None, uses the LocalMind project root itself.
    
    Returns a dict suitable for /api/hub/context:
      - project_path: absolute path string
      - meta: name, version, description, license
      - stack: list of detected tech stacks
      - git_remote: git remote URL(s) if available
    """
    if project_path:
        root = Path(project_path).resolve()
    else:
        # Default: LocalMind project root (two levels up from this file)
        root = Path(__file__).resolve().parent.parent.parent

    if not root.exists():
        return {"error": f"Path does not exist: {root}"}

    meta = get_project_meta(root)
    stack = detect_tech_stack(root)

    # Try to get git remote URLs
    git_remotes = []
    try:
        import subprocess
        result = subprocess.run(
            "cmd /c git remote -v",
            cwd=str(root),
            capture_output=True, text=True, timeout=10, shell=True,
        )
        if result.returncode == 0:
            seen_remotes = set()
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 2 and "(fetch)" in line:
                    url = parts[1]
                    if url not in seen_remotes:
                        git_remotes.append({"name": parts[0], "url": url})
                        seen_remotes.add(url)
    except Exception as exc:
        logger.debug(f"Could not read git remotes: {exc}")

    logger.info(f"Hub context built for: {root.name} — stack: {[s['language'] for s in stack]}")

    return {
        "project_path": str(root),
        "meta": meta,
        "stack": stack,
        "git_remotes": git_remotes,
    }
'''

def create_cross_project_hub():
    print("Creating backend/integrations/cross_project_hub.py ...")
    INTEGRATIONS_DIR.mkdir(parents=True, exist_ok=True)

    init_path = INTEGRATIONS_DIR / "__init__.py"
    if not init_path.exists():
        init_path.write_text("# LocalMind integrations package\n", encoding="utf-8")
        print(f"  [OK] Created {init_path.name}")

    if HUB_PATH.exists():
        print("  [OK] SKIP: cross_project_hub.py already exists")
        return

    HUB_PATH.write_text(CROSS_PROJECT_HUB_CONTENT, encoding="utf-8")
    print(f"  [OK] Created: {HUB_PATH.name}")


# ──────────────────────────────────────────────────────────────────
# 4. Register /api/hub/context in server.py
# ──────────────────────────────────────────────────────────────────

SERVER_PATH = BACKEND / "server.py"

def patch_server():
    print("Patching backend/server.py for /api/hub/context ...")
    src = SERVER_PATH.read_text(encoding="utf-8")

    if "/api/hub/context" in src:
        print("  [OK] SKIP: /api/hub/context already registered")
        return

    # Find the end of imports block (first @app.route or first async def handle)
    hub_route = '''

# ── Cross-Project Hub (Phase D) ──────────────────────────────────
@app.get("/api/hub/context")
async def hub_context(project_path: str = ""):
    """Return tech stack + metadata for a local project directory.
    
    Query param: project_path (optional) — absolute path to scan.
    Defaults to the LocalMind project root if omitted.
    """
    from backend.integrations.cross_project_hub import build_hub_context
    context = build_hub_context(project_path or None)
    return context

'''

    # Insert before the first @app route definition
    insert_marker = "@app.get(\"/\")"
    if insert_marker not in src:
        # Try a fallback marker
        insert_marker = "async def root("
    
    if insert_marker in src:
        src = src.replace(insert_marker, hub_route + insert_marker, 1)
        SERVER_PATH.write_text(src, encoding="utf-8")
        print("  [OK] Registered GET /api/hub/context in server.py")
    else:
        # Just append before the last line
        src = src.rstrip() + "\n" + hub_route
        SERVER_PATH.write_text(src, encoding="utf-8")
        print("  [OK] Appended /api/hub/context route to server.py")


# ──────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print(" LocalMind Phase D — RCA + Git Awareness + Cross-Project Hub")
    print("=" * 60)

    errors = []

    try:
        patch_controller()
    except Exception as e:
        errors.append(f"controller.py: {e}")
        print(f"  [!!] ERROR: {e}")

    try:
        patch_git_ops()
    except Exception as e:
        errors.append(f"git_ops.py: {e}")
        print(f"  [!!] ERROR: {e}")

    try:
        create_cross_project_hub()
    except Exception as e:
        errors.append(f"cross_project_hub.py: {e}")
        print(f"  [!!] ERROR: {e}")

    try:
        patch_server()
    except Exception as e:
        errors.append(f"server.py: {e}")
        print(f"  [!!] ERROR: {e}")

    print("=" * 60)
    if errors:
        print(f"COMPLETED WITH {len(errors)} ERROR(S):")
        for err in errors:
            print(f"  [!!] {err}")
        sys.exit(1)
    else:
        print("Phase D COMPLETE — all 4 changes applied successfully.")
        print("Next: Run pytest tests/ to verify no regressions.")
