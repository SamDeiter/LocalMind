"""
Cross-Project Pattern Mining Engine — Sprint 5: Cross-Project Hub.

Analyses registered projects to find shared patterns, dependencies,
and architectural insights across the user's entire portfolio.
"""

import json
import logging
import os
import re
import sqlite3
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path

from backend.config import DB_PATH

logger = logging.getLogger("localmind.research.cross_project")

SKIP_DIRS = {".git", "node_modules", "__pycache__", "venv", ".venv",
             ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
             ".eggs", "*.egg-info"}

# File extensions we consider when scanning names/structure
_CODE_EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs",
                    ".java", ".kt", ".rb", ".c", ".cpp", ".h", ".cs"}

# Architectural markers — directory names that signal a pattern
_ARCH_MARKERS = {
    "backend/frontend split": ({"backend", "frontend"}, 0.9),
    "MVC structure": ({"models", "views", "controllers"}, 0.85),
    "has Docker setup": ({"Dockerfile", "docker-compose.yml", "docker-compose.yaml"}, 0.95),
    "uses FastAPI routes pattern": ({"routes", "routers"}, 0.7),
    "has tests/ directory": ({"tests", "test"}, 0.9),
    "has CI/CD config": ({".github", ".gitlab-ci.yml", ".circleci"}, 0.85),
    "monorepo structure": ({"packages", "apps", "libs"}, 0.75),
    "has documentation setup": ({"docs", "documentation"}, 0.8),
}


class CrossProjectMiner:
    """Analyses registered projects to find shared patterns across them."""

    def __init__(self):
        self.db_path = str(DB_PATH)

    # ── DB helper ───────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        """Short-lived SQLite connection (WAL, FK ON, row_factory=Row)."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

    # ── Main entry point ────────────────────────────────────────────

    def mine_all(self) -> dict:
        """Run all mining methods across all active projects.

        Returns ``{"patterns_found": N, "insights": [...]}``.
        """
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM project_registry WHERE active = 1"
            ).fetchall()
        finally:
            conn.close()

        projects = [dict(r) for r in rows]
        if len(projects) < 2:
            return {"patterns_found": 0,
                    "insights": ["Need at least 2 active projects to mine patterns."]}

        all_patterns: list[dict] = []
        all_patterns.extend(self._mine_shared_dependencies(projects))
        all_patterns.extend(self._mine_naming_patterns(projects))
        all_patterns.extend(self._mine_architecture_patterns(projects))
        all_patterns.extend(self._mine_common_issues(projects))

        for pat in all_patterns:
            self._upsert_pattern(pat)

        insights = self.get_insights()
        return {"patterns_found": len(all_patterns),
                "insights": insights.get("suggestions", [])}

    # ── Dependency mining ───────────────────────────────────────────

    def _mine_shared_dependencies(self, projects: list[dict]) -> list[dict]:
        """Find dependencies shared across 2+ projects."""
        dep_files = {
            "requirements.txt": self._parse_requirements_txt,
            "package.json": self._parse_package_json,
            "go.mod": self._parse_go_mod,
            "Cargo.toml": self._parse_cargo_toml,
        }

        # project_id -> set of dependency names
        project_deps: dict[str, set[str]] = {}
        # dep_name -> list of project_ids that use it
        dep_to_projects: dict[str, list[str]] = defaultdict(list)

        for proj in projects:
            proj_path = Path(proj["path"])
            if not proj_path.is_dir():
                continue
            deps: set[str] = set()
            for fname, parser in dep_files.items():
                fpath = proj_path / fname
                if fpath.is_file():
                    try:
                        deps.update(parser(fpath))
                    except Exception as exc:
                        logger.debug("Failed to parse %s: %s", fpath, exc)
            project_deps[proj["id"]] = deps
            for dep in deps:
                dep_to_projects[dep].append(proj["id"])

        patterns: list[dict] = []
        for dep, pids in dep_to_projects.items():
            if len(pids) < 2:
                continue
            confidence = min(1.0, len(pids) / len(projects))
            patterns.append({
                "pattern_type": "dependency",
                "title": f"Shared dependency: {dep}",
                "description": f"{dep} is used in {len(pids)} of {len(projects)} projects.",
                "source_project_id": pids[0],
                "related_project_ids": pids,
                "confidence": round(confidence, 2),
                "occurrences": len(pids),
                "metadata": {"dependency_name": dep},
            })

        patterns.sort(key=lambda p: p["occurrences"], reverse=True)
        return patterns

    @staticmethod
    def _parse_requirements_txt(path: Path) -> set[str]:
        deps: set[str] = set()
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            # Strip version specifiers: requests>=2.0 -> requests
            name = re.split(r"[><=!~;\[]", line)[0].strip()
            if name:
                deps.add(name.lower())
        return deps

    @staticmethod
    def _parse_package_json(path: Path) -> set[str]:
        deps: set[str] = set()
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
            for key in ("dependencies", "devDependencies", "peerDependencies"):
                if isinstance(data.get(key), dict):
                    deps.update(data[key].keys())
        except (json.JSONDecodeError, KeyError):
            pass
        return deps

    @staticmethod
    def _parse_go_mod(path: Path) -> set[str]:
        deps: set[str] = set()
        in_require = False
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if stripped.startswith("require ("):
                in_require = True
                continue
            if in_require:
                if stripped == ")":
                    in_require = False
                    continue
                parts = stripped.split()
                if parts:
                    deps.add(parts[0])
            elif stripped.startswith("require "):
                parts = stripped.split()
                if len(parts) >= 2:
                    deps.add(parts[1])
        return deps

    @staticmethod
    def _parse_cargo_toml(path: Path) -> set[str]:
        deps: set[str] = set()
        in_deps = False
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if re.match(r"\[(.*dependencies.*)\]", stripped):
                in_deps = True
                continue
            if stripped.startswith("[") and in_deps:
                in_deps = False
                continue
            if in_deps and "=" in stripped:
                name = stripped.split("=")[0].strip()
                if name:
                    deps.add(name)
        return deps

    # ── Naming patterns ─────────────────────────────────────────────

    def _mine_naming_patterns(self, projects: list[dict]) -> list[dict]:
        """Find naming conventions shared across projects."""
        # Track conventions per project
        project_conventions: dict[str, set[str]] = {}

        for proj in projects:
            proj_path = Path(proj["path"])
            if not proj_path.is_dir():
                continue
            conventions: set[str] = set()

            # Scan top-level directories
            top_dirs = set()
            try:
                for entry in proj_path.iterdir():
                    if entry.is_dir() and entry.name not in SKIP_DIRS and not entry.name.startswith("."):
                        top_dirs.add(entry.name)
            except OSError:
                continue

            # Check for known directory conventions
            for dname in top_dirs:
                if dname in ("routes", "routers"):
                    conventions.add("has routes/ directory")
                elif dname in ("tests", "test"):
                    conventions.add("has tests/ directory")
                elif dname in ("models",):
                    conventions.add("has models/ directory")
                elif dname in ("utils", "helpers"):
                    conventions.add("has utils/helpers directory")
                elif dname in ("config", "configs", "settings"):
                    conventions.add("has config directory")
                elif dname in ("docs", "documentation"):
                    conventions.add("has docs directory")
                elif dname in ("scripts",):
                    conventions.add("has scripts directory")
                elif dname in ("src",):
                    conventions.add("uses src/ layout")

            # Scan Python/JS filenames for naming style
            snake_count = 0
            camel_count = 0
            total_files = 0
            for f in self._iter_code_files(proj_path, max_files=200):
                total_files += 1
                stem = f.stem
                if "_" in stem and stem.islower():
                    snake_count += 1
                elif re.match(r"^[a-z]+[A-Z]", stem):
                    camel_count += 1

            if total_files > 0:
                if snake_count / max(1, total_files) > 0.5:
                    conventions.add("uses snake_case modules")
                if camel_count / max(1, total_files) > 0.5:
                    conventions.add("uses camelCase modules")

            project_conventions[proj["id"]] = conventions

        # Find conventions shared across 2+ projects
        convention_to_projects: dict[str, list[str]] = defaultdict(list)
        for pid, convs in project_conventions.items():
            for conv in convs:
                convention_to_projects[conv].append(pid)

        patterns: list[dict] = []
        for conv, pids in convention_to_projects.items():
            if len(pids) < 2:
                continue
            confidence = min(1.0, len(pids) / len(projects))
            patterns.append({
                "pattern_type": "naming",
                "title": conv,
                "description": (f"Convention '{conv}' found in {len(pids)} of "
                                f"{len(projects)} projects."),
                "source_project_id": pids[0],
                "related_project_ids": pids,
                "confidence": round(confidence, 2),
                "occurrences": len(pids),
                "metadata": {"convention": conv},
            })

        return patterns

    # ── Architecture patterns ───────────────────────────────────────

    def _mine_architecture_patterns(self, projects: list[dict]) -> list[dict]:
        """Compare directory structures across projects to find shared architectural patterns."""
        project_markers: dict[str, set[str]] = {}

        for proj in projects:
            proj_path = Path(proj["path"])
            if not proj_path.is_dir():
                continue

            # Collect all top-level names (dirs + files)
            top_names: set[str] = set()
            try:
                for entry in proj_path.iterdir():
                    name = entry.name
                    if name in SKIP_DIRS or name.startswith("."):
                        # Still include dotfiles that are arch markers
                        if name in (".github", ".gitlab-ci.yml", ".circleci"):
                            top_names.add(name)
                        continue
                    top_names.add(name.lower() if entry.is_dir() else name)
            except OSError:
                continue

            found: set[str] = set()
            for label, (markers, _conf) in _ARCH_MARKERS.items():
                # A pattern matches if ANY marker is present in top-level names
                if markers & top_names:
                    found.add(label)

            # Check for Dockerfile anywhere (might be nested)
            if "has Docker setup" not in found:
                for candidate in ("Dockerfile", "docker-compose.yml", "docker-compose.yaml"):
                    if (proj_path / candidate).exists():
                        found.add("has Docker setup")
                        break

            project_markers[proj["id"]] = found

        # Find patterns shared across 2+ projects
        marker_to_projects: dict[str, list[str]] = defaultdict(list)
        for pid, markers in project_markers.items():
            for m in markers:
                marker_to_projects[m].append(pid)

        patterns: list[dict] = []
        for label, pids in marker_to_projects.items():
            if len(pids) < 2:
                continue
            _, base_conf = _ARCH_MARKERS.get(label, (set(), 0.7))
            confidence = round(base_conf * min(1.0, len(pids) / len(projects)), 2)
            patterns.append({
                "pattern_type": "architecture",
                "title": label,
                "description": (f"Pattern '{label}' present in {len(pids)} of "
                                f"{len(projects)} projects."),
                "source_project_id": pids[0],
                "related_project_ids": pids,
                "confidence": confidence,
                "occurrences": len(pids),
                "metadata": {"architecture_label": label},
            })

        return patterns

    # ── Common issues (TODO/FIXME/HACK) ─────────────────────────────

    def _mine_common_issues(self, projects: list[dict]) -> list[dict]:
        """Scan for TODO/FIXME/HACK comments across all projects and find common themes."""
        _MARKERS = ("TODO", "FIXME", "HACK")
        _MARKER_RE = re.compile(r"\b(TODO|FIXME|HACK)\b[:\s]*(.*)", re.IGNORECASE)

        # project_id -> Counter of normalised theme words
        project_themes: dict[str, Counter] = {}
        # project_id -> total issue count
        project_issue_counts: dict[str, int] = {}

        for proj in projects:
            proj_path = Path(proj["path"])
            if not proj_path.is_dir():
                continue
            themes: Counter = Counter()
            issue_count = 0
            for fpath in self._iter_code_files(proj_path, max_files=300):
                try:
                    for line in fpath.read_text(encoding="utf-8", errors="ignore").splitlines():
                        m = _MARKER_RE.search(line)
                        if m:
                            issue_count += 1
                            marker = m.group(1).upper()
                            rest = m.group(2).strip()[:80]
                            themes[marker] += 1
                            # Extract broad theme words
                            for word in re.findall(r"[a-z]{4,}", rest.lower()):
                                themes[word] += 1
                except OSError:
                    continue
            project_themes[proj["id"]] = themes
            project_issue_counts[proj["id"]] = issue_count

        # Find themes that appear in 2+ projects
        theme_to_projects: dict[str, list[str]] = defaultdict(list)
        for pid, themes in project_themes.items():
            for theme, cnt in themes.most_common(20):
                if theme in _MARKERS:
                    continue  # Skip the marker keywords themselves
                if cnt >= 2:
                    theme_to_projects[theme].append(pid)

        patterns: list[dict] = []

        # High-level issue patterns: projects with many TODOs
        projects_with_issues = [pid for pid, cnt in project_issue_counts.items() if cnt > 0]
        if len(projects_with_issues) >= 2:
            total_issues = sum(project_issue_counts.values())
            patterns.append({
                "pattern_type": "issue",
                "title": "TODO/FIXME markers across projects",
                "description": (f"{total_issues} TODO/FIXME/HACK markers found across "
                                f"{len(projects_with_issues)} projects."),
                "source_project_id": projects_with_issues[0],
                "related_project_ids": projects_with_issues,
                "confidence": 0.95,
                "occurrences": total_issues,
                "metadata": {"counts_by_project": {
                    pid: project_issue_counts[pid]
                    for pid in projects_with_issues
                }},
            })

        # Shared theme words
        for theme, pids in theme_to_projects.items():
            if len(pids) < 2:
                continue
            confidence = min(1.0, len(pids) / len(projects)) * 0.7
            patterns.append({
                "pattern_type": "issue",
                "title": f"Common issue theme: {theme}",
                "description": (f"The theme '{theme}' appears in TODO/FIXME comments "
                                f"across {len(pids)} projects."),
                "source_project_id": pids[0],
                "related_project_ids": pids,
                "confidence": round(confidence, 2),
                "occurrences": len(pids),
                "metadata": {"theme_word": theme},
            })

        return patterns

    # ── Persistence ─────────────────────────────────────────────────

    def _upsert_pattern(self, pattern: dict):
        """INSERT or UPDATE a pattern (matched by pattern_type + title)."""
        now = time.time()
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT id, occurrences FROM cross_project_patterns "
                "WHERE pattern_type = ? AND title = ?",
                (pattern["pattern_type"], pattern["title"]),
            ).fetchone()

            if row:
                conn.execute(
                    "UPDATE cross_project_patterns SET "
                    "description = ?, related_project_ids = ?, confidence = ?, "
                    "occurrences = ?, last_seen_at = ?, metadata = ? "
                    "WHERE id = ?",
                    (
                        pattern.get("description", ""),
                        json.dumps(pattern.get("related_project_ids", [])),
                        pattern.get("confidence", 0.0),
                        pattern.get("occurrences", dict(row)["occurrences"] + 1),
                        now,
                        json.dumps(pattern.get("metadata", {})),
                        dict(row)["id"],
                    ),
                )
            else:
                conn.execute(
                    "INSERT INTO cross_project_patterns "
                    "(id, pattern_type, title, description, source_project_id, "
                    "related_project_ids, confidence, occurrences, first_seen_at, "
                    "last_seen_at, metadata) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()),
                        pattern["pattern_type"],
                        pattern["title"],
                        pattern.get("description", ""),
                        pattern.get("source_project_id"),
                        json.dumps(pattern.get("related_project_ids", [])),
                        pattern.get("confidence", 0.0),
                        pattern.get("occurrences", 1),
                        now,
                        now,
                        json.dumps(pattern.get("metadata", {})),
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    # ── Query methods ───────────────────────────────────────────────

    def get_patterns(self, pattern_type: str | None = None,
                     project_id: str | None = None,
                     limit: int = 50) -> list[dict]:
        """SELECT patterns, optionally filtered by type or project."""
        clauses: list[str] = []
        params: list = []

        if pattern_type:
            clauses.append("pattern_type = ?")
            params.append(pattern_type)
        if project_id:
            clauses.append(
                "(source_project_id = ? OR related_project_ids LIKE ?)"
            )
            params.extend([project_id, f"%{project_id}%"])

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (f"SELECT * FROM cross_project_patterns{where} "
               f"ORDER BY occurrences DESC, confidence DESC LIMIT ?")
        params.append(limit)

        conn = self._conn()
        try:
            rows = conn.execute(sql, params).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                # Parse JSON fields for convenience
                for jf in ("related_project_ids", "metadata"):
                    if isinstance(d.get(jf), str):
                        try:
                            d[jf] = json.loads(d[jf])
                        except (json.JSONDecodeError, TypeError):
                            pass
                results.append(d)
            return results
        finally:
            conn.close()

    def get_insights(self) -> dict:
        """Generate high-level insights from mined patterns.

        Returns::

            {
                "summary": {...},
                "suggestions": [...],
                "by_type": {...},
            }
        """
        conn = self._conn()
        try:
            # Counts by type
            type_rows = conn.execute(
                "SELECT pattern_type, COUNT(*) as cnt, AVG(confidence) as avg_conf "
                "FROM cross_project_patterns GROUP BY pattern_type"
            ).fetchall()
            by_type = {
                dict(r)["pattern_type"]: {
                    "count": dict(r)["cnt"],
                    "avg_confidence": round(dict(r)["avg_conf"], 2),
                }
                for r in type_rows
            }

            # Most shared dependencies
            dep_rows = conn.execute(
                "SELECT title, occurrences, related_project_ids "
                "FROM cross_project_patterns "
                "WHERE pattern_type = 'dependency' "
                "ORDER BY occurrences DESC LIMIT 10"
            ).fetchall()
            top_deps = []
            for r in dep_rows:
                d = dict(r)
                try:
                    d["related_project_ids"] = json.loads(d["related_project_ids"])
                except (json.JSONDecodeError, TypeError):
                    pass
                top_deps.append(d)

            # Projects with most patterns
            proj_counts: Counter = Counter()
            all_rows = conn.execute(
                "SELECT source_project_id, related_project_ids "
                "FROM cross_project_patterns"
            ).fetchall()
            for r in all_rows:
                d = dict(r)
                if d.get("source_project_id"):
                    proj_counts[d["source_project_id"]] += 1
                try:
                    pids = json.loads(d.get("related_project_ids", "[]"))
                    for pid in pids:
                        proj_counts[pid] += 1
                except (json.JSONDecodeError, TypeError):
                    pass

            top_projects = proj_counts.most_common(5)

            # Resolve project names
            project_names = {}
            if top_projects:
                pids_str = ",".join(f"'{p[0]}'" for p in top_projects)
                name_rows = conn.execute(
                    f"SELECT id, name FROM project_registry WHERE id IN ({pids_str})"
                ).fetchall()
                project_names = {dict(r)["id"]: dict(r)["name"] for r in name_rows}

        finally:
            conn.close()

        # Build suggestions
        suggestions: list[str] = []

        # Suggestion: shared framework
        for dep in top_deps:
            name = dep["title"].replace("Shared dependency: ", "")
            n_projects = dep["occurrences"]
            if n_projects >= 2:
                if name.lower() in ("fastapi", "flask", "django", "express", "next"):
                    suggestions.append(
                        f"{n_projects} projects use {name} -- consider sharing middleware or utilities."
                    )
                elif name.lower() in ("pytest", "jest", "mocha"):
                    suggestions.append(
                        f"{n_projects} projects use {name} -- consider a shared test config or fixtures."
                    )

        # Suggestion: architecture alignment
        arch_count = by_type.get("architecture", {}).get("count", 0)
        if arch_count >= 3:
            suggestions.append(
                f"{arch_count} shared architectural patterns detected -- "
                "your projects have consistent structure."
            )

        # Suggestion: issue cleanup
        issue_count = by_type.get("issue", {}).get("count", 0)
        if issue_count >= 3:
            suggestions.append(
                f"{issue_count} common issue themes found -- "
                "consider a cross-project TODO cleanup sprint."
            )

        # Suggestion: projects with high pattern overlap
        if len(top_projects) >= 2:
            top_name = project_names.get(top_projects[0][0], top_projects[0][0])
            suggestions.append(
                f"Project '{top_name}' has the most cross-project patterns "
                f"({top_projects[0][1]}) -- it may be a good candidate for shared libraries."
            )

        if not suggestions:
            suggestions.append("Continue registering projects to discover more patterns.")

        summary = {
            "total_patterns": sum(v["count"] for v in by_type.values()),
            "pattern_types": len(by_type),
            "top_dependencies": [d["title"] for d in top_deps[:5]],
            "top_projects": [
                {"id": pid, "name": project_names.get(pid, pid), "patterns": cnt}
                for pid, cnt in top_projects
            ],
        }

        return {
            "summary": summary,
            "suggestions": suggestions,
            "by_type": by_type,
        }

    def search_patterns(self, query: str, limit: int = 20) -> list[dict]:
        """Simple LIKE search on title + description."""
        conn = self._conn()
        try:
            like_q = f"%{query}%"
            rows = conn.execute(
                "SELECT * FROM cross_project_patterns "
                "WHERE title LIKE ? OR description LIKE ? "
                "ORDER BY occurrences DESC LIMIT ?",
                (like_q, like_q, limit),
            ).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                for jf in ("related_project_ids", "metadata"):
                    if isinstance(d.get(jf), str):
                        try:
                            d[jf] = json.loads(d[jf])
                        except (json.JSONDecodeError, TypeError):
                            pass
                results.append(d)
            return results
        finally:
            conn.close()

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _iter_code_files(root: Path, max_files: int = 500):
        """Yield code files under *root*, skipping ignored directories.

        Lightweight: only yields Path objects, does not read contents.
        Caps at *max_files* to keep scans fast.
        """
        count = 0
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                # Prune ignored directories in-place
                dirnames[:] = [
                    d for d in dirnames
                    if d not in SKIP_DIRS and not d.startswith(".")
                ]
                for fname in filenames:
                    ext = os.path.splitext(fname)[1]
                    if ext in _CODE_EXTENSIONS:
                        yield Path(dirpath) / fname
                        count += 1
                        if count >= max_files:
                            return
        except OSError:
            return


# ── Module singleton ────────────────────────────────────────────────

_miner: CrossProjectMiner | None = None


def get_miner() -> CrossProjectMiner:
    global _miner
    if _miner is None:
        _miner = CrossProjectMiner()
    return _miner
