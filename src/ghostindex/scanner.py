"""Project walking for GhostIndex.

Mirrors GhostSlice's scanner discipline (ghostslice/scanner.py):
the same default ignore dirs, the same binary extension set, and the
same extension-to-language map, so the index sees the projects the
way Slice sees them. On top of that it honours each root's
``.gitignore`` files and the ``ignore`` list of a root's
``.ghostslice.json`` profile when one exists.

The .gitignore matcher is a pragmatic subset of git's rules
(patterns, ``!`` negations, trailing-slash directory patterns,
leading-slash anchoring, ``*`` / ``?`` / ``**`` globs, per-directory
.gitignore files applying to their subtree, last match wins). It is
not a full wildmatch reimplementation; the difference is stated in
PLAN.md under Risks.
"""
from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# Mirrored from ghostslice/scanner.py (v1.0.6).
DEFAULT_IGNORE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "dist", "build", "out",
    "__pycache__", ".venv", "venv", "env", ".tox", ".mypy_cache",
    ".pytest_cache", ".next", ".nuxt", "coverage", ".cache", "target",
    ".idea", ".vscode", ".ghostslice_bundle", ".claude_context",
    ".ghostslice-cache",
}

DEFAULT_IGNORE_FILES = {".ghostslice-history.jsonl"}

BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".bmp", ".svgz",
    ".pdf", ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".o", ".a", ".class", ".wasm",
    ".mp3", ".mp4", ".mov", ".avi", ".woff", ".woff2", ".ttf", ".eot",
    ".pyc", ".pyo", ".db", ".sqlite", ".sqlite3",
}

LANGUAGE_BY_EXT = {
    ".py": "python",
    ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript",
    ".mjs": "javascript", ".cjs": "javascript",
    ".md": "markdown", ".markdown": "markdown", ".txt": "text",
    ".json": "json",
    ".go": "go", ".rs": "rust", ".java": "java", ".cs": "csharp",
    ".php": "php", ".rb": "ruby", ".kt": "kotlin", ".kts": "kotlin",
    ".swift": "swift",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".scala": "scala", ".sc": "scala",
    ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml",
    ".sql": "sql",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell",
    ".ps1": "powershell", ".psm1": "powershell",
    ".ipynb": "notebook",
    ".html": "html", ".css": "css", ".xml": "xml",
}


def language_for(path: str) -> str:
    return LANGUAGE_BY_EXT.get(Path(path).suffix.lower(), "text")


def _glob_to_regex(pattern: str) -> re.Pattern:
    """Translate one gitignore glob body to a regex (full match)."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if pattern[i:i + 2] == "**":
                # ** crosses directories; absorb a following slash.
                out.append(".*")
                i += 2
                if i < len(pattern) and pattern[i] == "/":
                    i += 1
                continue
            out.append("[^/]*")
            i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        elif ch == "[":
            j = pattern.find("]", i + 1)
            if j == -1:
                out.append(re.escape(ch))
                i += 1
            else:
                out.append(pattern[i:j + 1])
                i = j + 1
        else:
            out.append(re.escape(ch))
            i += 1
    return re.compile("^" + "".join(out) + "$")


@dataclass
class _Rule:
    base: str          # posix rel dir the .gitignore lives in ("" = root)
    regex: re.Pattern
    negated: bool
    dir_only: bool
    anchored: bool


class GitignoreMatcher:
    """Last-match-wins matcher over all .gitignore files in a root."""

    def __init__(self) -> None:
        self._rules: list[_Rule] = []

    def load_dir(self, dirpath: Path, base_rel: str) -> None:
        gi = dirpath / ".gitignore"
        try:
            lines = gi.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            negated = line.startswith("!")
            if negated:
                line = line[1:]
            dir_only = line.endswith("/")
            if dir_only:
                line = line[:-1]
            anchored = "/" in line
            if line.startswith("/"):
                line = line[1:]
            if not line:
                continue
            self._rules.append(_Rule(base_rel, _glob_to_regex(line),
                                     negated, dir_only, anchored))

    def ignored(self, rel: str, is_dir: bool = False) -> bool:
        result = False
        for rule in self._rules:
            if rule.dir_only and not is_dir:
                continue
            if rule.base:
                if rel == rule.base or not rel.startswith(rule.base + "/"):
                    continue
                sub = rel[len(rule.base) + 1:]
            else:
                sub = rel
            if rule.anchored:
                matched = bool(rule.regex.match(sub))
            else:
                matched = bool(rule.regex.match(sub.rsplit("/", 1)[-1]))
            if matched:
                result = not rule.negated
        return result


def load_profile_ignores(root: Path) -> list[str]:
    """The ``ignore`` glob list from a root's .ghostslice.json, if any."""
    profile = root / ".ghostslice.json"
    try:
        data = json.loads(profile.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(data, dict) and isinstance(data.get("ignore"), list):
        return [str(p) for p in data["ignore"]]
    return []


def _profile_ignored(patterns: list[str], rel: str) -> bool:
    for pattern in patterns:
        pat = pattern.strip()
        if not pat:
            continue
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel, pat.rstrip("/")):
            return True
        if "/" not in pat and fnmatch.fnmatch(rel.rsplit("/", 1)[-1], pat):
            return True
    return False


@dataclass
class Candidate:
    rel: str           # posix path relative to the root
    abspath: Path
    size: int
    mtime: float


@dataclass
class SkipStats:
    ignored_dirs: int = 0
    ignored_files: int = 0
    binary_ext: int = 0
    default_files: int = 0
    symlinks: int = 0
    unreadable: int = 0

    def as_dict(self) -> dict:
        return {
            "ignored_dirs": self.ignored_dirs,
            "ignored_files": self.ignored_files,
            "binary_ext": self.binary_ext,
            "default_files": self.default_files,
            "symlinks": self.symlinks,
            "unreadable": self.unreadable,
        }


@dataclass
class WalkResult:
    candidates: list[Candidate] = field(default_factory=list)
    skips: SkipStats = field(default_factory=SkipStats)


def walk(root: Path, extra_skip_paths: set[Path] | None = None) -> WalkResult:
    """Collect indexable files under root, honouring all ignore rules.

    extra_skip_paths holds resolved absolute paths to never index
    (used to keep an index database that lives inside a root out of
    its own index).
    """
    root = root.resolve()
    matcher = GitignoreMatcher()
    profile_ignores = load_profile_ignores(root)
    skip_paths = extra_skip_paths or set()
    result = WalkResult()

    for dirpath, dirnames, filenames in _sorted_walk(root):
        rel_dir = dirpath.relative_to(root).as_posix()
        if rel_dir == ".":
            rel_dir = ""
        matcher.load_dir(dirpath, rel_dir)

        kept_dirs: list[str] = []
        for name in dirnames:
            rel = f"{rel_dir}/{name}" if rel_dir else name
            full = dirpath / name
            if name in DEFAULT_IGNORE_DIRS:
                result.skips.ignored_dirs += 1
                continue
            if full.is_symlink():
                result.skips.symlinks += 1
                continue
            if matcher.ignored(rel, is_dir=True) or _profile_ignored(profile_ignores, rel):
                result.skips.ignored_dirs += 1
                continue
            kept_dirs.append(name)
        dirnames[:] = kept_dirs

        for name in filenames:
            rel = f"{rel_dir}/{name}" if rel_dir else name
            full = dirpath / name
            if name in DEFAULT_IGNORE_FILES:
                result.skips.default_files += 1
                continue
            if skip_paths:
                try:
                    if full.resolve() in skip_paths:
                        result.skips.default_files += 1
                        continue
                except OSError:
                    pass
            if Path(name).suffix.lower() in BINARY_EXTS:
                result.skips.binary_ext += 1
                continue
            if matcher.ignored(rel) or _profile_ignored(profile_ignores, rel):
                result.skips.ignored_files += 1
                continue
            if full.is_symlink():
                try:
                    full.resolve().relative_to(root)
                except (ValueError, OSError):
                    result.skips.symlinks += 1
                    continue
            try:
                stat = full.stat()
            except OSError:
                result.skips.unreadable += 1
                continue
            result.candidates.append(Candidate(rel, full, stat.st_size, stat.st_mtime))
    return result


def _sorted_walk(root: Path):
    import os

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        yield Path(dirpath), dirnames, filenames
