"""Check publishable source or the exact Git index without displaying secrets.

Run ``python scripts/check_publication.py`` before staging and add ``--staged``
before committing. Positional paths limit a working-tree check. The default
manifest intentionally excludes local environments, build output and user data.
This is a targeted publication guard, not a guarantee that arbitrary secrets can
be recognized; review the staged diff as well.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
import subprocess


SOURCE_DIRECTORIES = frozenset({
    "core", "config", "database", "ui", "scripts", "tests", "packaging", ".github",
    "campus-login-cli", "docs",
})
ROOT_FILES = frozenset({
    ".gitignore", ".gitattributes", "main.py", "app_lifecycle.py", "requirements.txt", "README.md",
    "CLAUDE.md", "LICENSE", "LICENSE.md", "pyproject.toml",
})
EXCLUDED_DIRECTORIES = frozenset({
    ".git", ".venv", "venv", "env", "build", "dist", "__pycache__", ".tmp",
    ".agents", ".claude", ".codex", ".idea", ".vscode", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".config", "campus_login", "userdata",
    "user_data", "localdata", "secrets", "logs", "reports", "htmlcov",
})
PRIVATE_SUFFIXES = (
    ".db", ".db-wal", ".db-shm", ".db-journal", ".sqlite", ".sqlite-wal",
    ".sqlite-shm", ".sqlite-journal", ".sqlite3", ".sqlite3-wal",
    ".sqlite3-shm", ".sqlite3-journal", ".key", ".pem", ".p12", ".pfx",
    ".log", ".dmp", ".har", ".pyc", ".pyo", ".exe", ".dll", ".zip",
)
PRIVATE_NAMES = frozenset({"accounts.json", "settings.json", ".coverage", "coverage.xml"})
PATTERNS = (
    ("personal profile path", re.compile(
        r"(?i)\b[A-Z]:[\\/]+users[\\/]+"
        r"(?!(?:public|default|username|user_name|example)(?:[\\/\s\"'<>]|$)|<)"
        r"[^\\/\s\"'<>]+"
    )),
    ("personal profile path", re.compile(
        r"/(?:home|Users)/(?!(?:user|username|example)(?:[/\s\"'<>]|$)|<)"
        r"[A-Za-z0-9_.-]+"
    )),
    ("email address", re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")),
    ("access token", re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
        r"|sk-[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16})\b"
    )),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("phone number", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
)
CREDENTIAL_LITERAL = re.compile(
    r'''(?i)["']?(?:password|passwd|access_token|api_key|client_secret)["']?'''
    r'''\s*[:=]\s*["']([^"'\r\n]{6,})["']'''
)
PLACEHOLDER = re.compile(r"(?i)^(?:<[^>]+>|(?:your|example|sample|dummy|test|fake)[_ -].*|changeme)$")
EXAMPLE_DOMAINS = frozenset({
    "example.com", "example.org", "example.net", "localhost.invalid",
    "users.noreply.github.com",
})


@dataclass(frozen=True)
class Finding:
    path: str
    category: str
    line: int | None = None


def excluded_directory(name: str) -> bool:
    return (name.lower() in EXCLUDED_DIRECTORIES
            or name.lower().endswith((".build", ".dist"))
            or name.startswith((".storage-test-", ".publication-test-")))


def path_problem(path: str) -> str | None:
    """Classify only relative repository paths; never include file contents."""
    relative = PurePosixPath(path.replace("\\", "/"))
    if (not relative.parts or relative.is_absolute() or ".." in relative.parts
            or ":" in relative.parts[0]):
        return "path outside repository"
    name = relative.name.lower()
    if (any(excluded_directory(part) for part in relative.parts[:-1])
            or name in PRIVATE_NAMES or name.endswith(PRIVATE_SUFFIXES)
            or (name.startswith(".env") and name != ".env.example")
            or ".log." in name or name.endswith("-crash-report.xml")):
        return "local/private or generated artifact"
    if not (relative.parts[0] in SOURCE_DIRECTORIES
            or len(relative.parts) == 1 and (relative.name in ROOT_FILES or name.endswith(".spec"))):
        return "outside publication manifest"
    return None


def scan_content(path: str, data: bytes) -> list[Finding]:
    findings = []
    try:
        content = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        # ICO/PNG assets are part of the source manifest. Binary database/key
        # files are rejected by their paths before reaching this point.
        return findings
    for category, pattern in PATTERNS:
        for match in pattern.finditer(content):
            if category == "email address" and match.group(1).lower() in EXAMPLE_DOMAINS:
                continue
            findings.append(Finding(path, category, content.count("\n", 0, match.start()) + 1))
    # Tests deliberately use synthetic passwords to verify encryption and log
    # redaction. Token/private-key/profile patterns still apply to test sources.
    if "tests" not in PurePosixPath(path).parts:
        for match in CREDENTIAL_LITERAL.finditer(content):
            if not PLACEHOLDER.fullmatch(match.group(1)):
                findings.append(Finding(path, "credential literal", content.count("\n", 0, match.start()) + 1))
    return findings


def working_paths(root: Path, supplied: list[str]) -> list[str]:
    if supplied:
        selected = [root / path for path in supplied]
    else:
        selected = [root / folder for folder in sorted(SOURCE_DIRECTORIES)]
        selected += [path for path in root.iterdir()
                     if path.is_file() and (path.name in ROOT_FILES or path.suffix == ".spec")]
    paths = set()
    for selected_path in selected:
        try:
            selected_path.resolve().relative_to(root.resolve())
        except ValueError:
            raise ValueError("A supplied path resolves outside the repository.") from None
        candidates = selected_path.rglob("*") if selected_path.is_dir() else [selected_path]
        for path in candidates:
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if not supplied and any(excluded_directory(part) for part in PurePosixPath(relative).parts[:-1]):
                continue
            if path.is_symlink():
                raise ValueError("Symbolic links require a separate publication review.")
            paths.add(relative)
    return sorted(paths)


def git_output(root: Path, *arguments: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *arguments], capture_output=True, check=False)
    if result.returncode:
        raise ValueError("Git index could not be read; initialize the repository and stage source files first.")
    return result.stdout


def check(root: Path, supplied: list[str] | None = None, *, staged: bool = False) -> tuple[int, list[Finding]]:
    if staged:
        names = git_output(root, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
        paths = [name.decode("utf-8") for name in names.split(b"\0") if name]
    else:
        paths = working_paths(root, supplied or [])
    findings = []
    for path in paths:
        problem = path_problem(path)
        if problem:
            findings.append(Finding(path, problem))
            continue
        data = git_output(root, "show", f":{path}") if staged else (root / path).read_bytes()
        findings.extend(scan_content(path, data))
    return len(paths), findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="Optional source paths relative to --root")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--staged", action="store_true", help="Inspect exact staged contents instead of working files")
    args = parser.parse_args(argv)
    if args.staged and args.paths:
        parser.error("--staged cannot be combined with working-tree paths")
    try:
        count, findings = check(args.root.resolve(), args.paths, staged=args.staged)
    except (OSError, ValueError) as error:
        print(f"Publication check could not complete: {type(error).__name__}.")
        return 2
    for finding in findings:
        location = f"{finding.path}:{finding.line}" if finding.line else finding.path
        print(f"{location}: {finding.category}")
    print(f"Publication check: {count} files, {len(findings)} findings. No matched values are displayed.")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
