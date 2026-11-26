"""A branch as the gate sees it: which files changed against its base, and which of them are tests or source code.

Shared by gate-tests, gate-branch, gate-red-green and gate-dup.
"""
import re
import subprocess

TEST_DIRS = {"test", "tests", "__tests__", "e2e", "cypress"}
TEST_NAME = re.compile(r"(\.test\.|\.spec\.|^test_.*\.py$|_test\.py$|_test\.go$)")
CODE_EXT = {".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".go", ".rs", ".java", ".rb", ".php", ".cs", ".kt", ".swift",
            ".c", ".cc", ".cpp", ".h", ".vue", ".svelte"}


def is_test(path):
    parts = path.split("/")
    return bool(TEST_DIRS & set(parts[:-1])) or bool(TEST_NAME.search(parts[-1]))


def git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("gate: `git %s` failed: %s" % (" ".join(args), r.stderr.strip()))
    return r.stdout


def base_ref(cwd, given):
    if given:
        return given
    r = subprocess.run(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=cwd, capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    for ref in ("origin/main", "origin/master", "main", "master"):
        if subprocess.run(["git", "rev-parse", "--verify", "-q", ref], cwd=cwd, capture_output=True).returncode == 0:
            return ref
    raise SystemExit("gate: no base branch found; pass --base")


def merge_base(cwd, base):
    return git(cwd, "merge-base", base, "HEAD").strip()


def changes(cwd, base):
    """-> list of (status letter, old path, new path) for the working tree against the merge base, untracked files as A."""
    out = []
    for line in git(cwd, "diff", "--name-status", "-M", merge_base(cwd, base)).splitlines():
        s, *p = line.split("\t")
        out.append((s[0], p[0], p[-1]))
    for p in git(cwd, "ls-files", "--others", "--exclude-standard").splitlines():
        out.append(("A", p, p))
    return out
