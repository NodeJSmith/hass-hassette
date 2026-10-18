"""Shared helpers for the CI gate scripts in this directory.

The scripts run as standalone executables (``./tools/check_*.py`` puts this directory on
``sys.path``), so a bare ``from lint_helpers import ...`` resolves.
"""

import ast
import re
import subprocess
from pathlib import Path

#: Repo root, shared so every checker resolves paths against the same anchor rather than
#: each recomputing ``Path(__file__).resolve().parent.parent`` on its own.
REPO_ROOT = Path(__file__).resolve().parent.parent

#: Directory components that are never first-party source: virtualenvs, caches, and build output.
#: Without this filter, a stray nested virtualenv inside a scan dir pulls third-party
#: site-packages into rglob and reports them as findings.
EXCLUDED_PARTS = frozenset({".venv", "site-packages", "__pycache__", ".nox", ".git"})

#: A unified-diff hunk header, e.g. "@@ -a,b +c,d @@" — b/d default to 1 when omitted (git's
#: convention for a single-line hunk). Used by git_changed_line_ranges() below.
_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,(?P<count>\d+))? @@")

#: git's own wording for "the path isn't in that tree" (as opposed to "the ref itself doesn't
#: resolve", e.g. an unfetched commit) — verified directly against git's actual stderr output,
#: not guessed. Both phrasings appear depending on whether the path exists in the working tree.
#: Used by git_file_line_count_at() below.
_GIT_SHOW_MISSING_PATH_MARKERS = ("does not exist in", "exists on disk, but not in")


def docstring_spans(tree: ast.AST) -> list[tuple[int, int]]:
    """Return (start, end) 1-based line spans of every docstring in the tree."""
    spans: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            spans.append((first.value.lineno, first.value.end_lineno or first.value.lineno))
    return spans


def iter_py_files(repo_root: Path, scan_dirs: list[str]) -> list[Path]:
    """Return every first-party .py file under the given repo-relative directories, sorted.

    Skips the dirs in EXCLUDED_PARTS so the linters never scan installed third-party packages
    sitting in a stray nested virtualenv.
    """
    # relative_to(repo_root) scopes the check to repo-internal components, so an ancestor directory
    # sharing an excluded name (e.g. a checkout under /opt/node_modules/) can't blank the scan.
    return sorted(
        path
        for scan_dir in scan_dirs
        for path in (repo_root / scan_dir).rglob("*.py")
        if EXCLUDED_PARTS.isdisjoint(path.relative_to(repo_root).parts)
    )


# Git-diff helpers for "new code" CI gates — checkers that must flag only a violation this PR's
# own commits introduced, not pre-existing debt the PR happens to sit near. Shared here (rather
# than duplicated per gate) because both the file-size and duplicate-code gates need the merge-base
# and rename primitives; the file-size gate also reads which lines changed and what a file looked
# like before the PR.


def git_merge_base(repo_root: Path, base_ref: str, head_ref: str) -> str:
    """Return the commit SHA where ``head_ref`` diverged from ``base_ref``.

    This, not ``base_ref``'s current tip, is the correct comparison point for a "did this PR
    make things worse" gate: using the live tip would blame a PR for unrelated commits that
    landed on the base branch after the PR branched, or hide the PR's own regressions behind an
    unrelated improvement on the base branch.
    """
    result = subprocess.run(
        ["git", "merge-base", base_ref, head_ref], cwd=repo_root, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def git_changed_line_ranges(repo_root: Path, base_ref: str, head_ref: str) -> dict[Path, set[int]]:
    """Return ``{repo-relative path: {new-side line numbers added or modified}}`` in the diff.

    ``--find-renames`` matters here: without it, a renamed file with a handful of real edits
    shows as a full delete-and-add pair, and every one of its lines — not just the edited ones —
    would count as "changed", making an untouched block that merely moved files look brand new.
    A pure rename with zero content change produces no hunks at all and so contributes no
    entries, which is the desired outcome (nothing about that file's content is new).

    Deleted files are skipped (there is no new-side content for a gate about newly-introduced
    lines to point at). Uses ``--unified=0`` so hunk ranges cover only actually-changed lines,
    not surrounding context.
    """
    result = subprocess.run(
        ["git", "diff", "--unified=0", "--find-renames", base_ref, head_ref],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    changed: dict[Path, set[int]] = {}
    current_file: Path | None = None
    for line in result.stdout.splitlines():
        if line.startswith("+++ "):
            target = line[4:]
            current_file = None if target == "/dev/null" else Path(target.removeprefix("b/"))
            continue
        match = _HUNK_HEADER_RE.match(line)
        if match and current_file is not None:
            start = int(match.group("start"))
            count = int(match.group("count") or "1")
            if count:
                changed.setdefault(current_file, set()).update(range(start, start + count))
    return changed


def git_renamed_from(repo_root: Path, base_ref: str, head_ref: str) -> dict[Path, Path]:
    """Return ``{new repo-relative path: old repo-relative path}`` for files git detects as renamed."""
    result = subprocess.run(
        ["git", "diff", "--find-renames", "--name-status", base_ref, head_ref],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    renames: dict[Path, Path] = {}
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].startswith("R"):
            renames[Path(parts[2])] = Path(parts[1])
    return renames


def git_file_line_count_at(repo_root: Path, ref: str, path: Path) -> int:
    """Return the line count of ``path`` at ``ref``, or 0 if it doesn't exist there.

    0 for a missing file is deliberate, not a fallback default: a file with no prior line count
    to compare against should read as "born at its current size", which is exactly the case a
    new-code size gate needs to catch. That deliberate 0 must not also catch a *different*
    failure, though — an unresolvable `ref` (e.g. a commit `fetch-depth: 0` should have pulled in
    but didn't) would otherwise silently read as "file didn't exist", reporting a pre-existing
    file's entire current size as 100% new growth. So only git's own "path missing from this
    tree" wording maps to 0; anything else re-raises as the same `CalledProcessError` a sibling
    `check=True` call here would have produced.
    """
    result = subprocess.run(["git", "show", f"{ref}:{path.as_posix()}"], cwd=repo_root, capture_output=True, text=True)
    if result.returncode != 0:
        if any(marker in result.stderr for marker in _GIT_SHOW_MISSING_PATH_MARKERS):
            return 0
        raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)
    return len(result.stdout.splitlines())
