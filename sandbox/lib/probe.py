"""Check that a sandbox holds, before trusting it: run a probe inside and report every leak found.

    problems = probe(["sandbox-run", "--timeout", "2", "--"], cwd, secret_files=[...], secret_dirs=[...],
                     secret_env=[...], outside=path_outside_the_checkout)

The probe runs `bash -c` through the given command prefix in `cwd` (a scratch folder inside the work folder) and
reports: a secret file readable, a secret folder listable, a secret variable set, a write outside the checkout that
worked, the checkout not writable, or the sandbox not running at all. -> a list of problems (empty: the sandbox holds).
"""
import shlex
import subprocess
from pathlib import Path

CREDENTIAL_ENV = ("GH_TOKEN", "GITHUB_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")


def script(secret_files, secret_dirs, outside):
    q = shlex.quote
    lines = ["env"]
    lines += ["test -r %s && echo 'PROBE-LEAK: %s is readable'" % (q(f), f) for f in secret_files]
    lines += ["ls %s >/dev/null 2>&1 && echo 'PROBE-LEAK: %s is visible'" % (q(d), d) for d in secret_dirs]
    if outside:
        lines.append("touch %s 2>/dev/null && echo 'PROBE-LEAK: a write outside the checkout worked'" % q(outside))
    lines += ["touch ./.probe-inside 2>/dev/null || echo 'PROBE-LEAK: the checkout is not writable'",
              "rm -f ./.probe-inside", "echo PROBE-DONE"]
    return "\n".join(lines) + "\n"


def probe(prefix, cwd, secret_files=(), secret_dirs=(), secret_env=CREDENTIAL_ENV, outside=None):
    Path(cwd).mkdir(parents=True, exist_ok=True)
    p = subprocess.run(list(prefix) + ["bash", "-c", script(secret_files, secret_dirs, outside)], cwd=str(cwd),
                       capture_output=True, text=True)
    out, bad = p.stdout, []
    if "PROBE-DONE" not in out:
        bad.append("the sandbox did not run: %s" % (p.stderr.strip()[-160:] or p.returncode))
    bad += [l.split(": ", 1)[1] for l in out.splitlines() if l.startswith("PROBE-LEAK: ")]
    env = dict(l.split("=", 1) for l in out.splitlines() if "=" in l and not l.startswith("PROBE"))
    bad += ["%s is set inside the sandbox" % v for v in secret_env if env.get(v)]
    if outside:
        try:
            Path(outside).unlink()
        except OSError:
            pass
    return bad


def claude_sandbox_problems(settings_file):
    """What stops Claude Code's sandbox from holding on this machine: missing tools, bubblewrap refused, settings
    missing, writable or allowing unsandboxed commands. -> a list of problems."""
    import json
    import os
    import shutil
    bad = []
    for tool in ("bwrap", "socat"):
        if not shutil.which(tool):
            bad.append("%s is missing: Claude Code cannot sandbox commands" % tool)
    if shutil.which("bwrap"):
        p = subprocess.run(["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--unshare-all", "--die-with-parent", "true"],
                           capture_output=True, text=True)
        if p.returncode != 0:
            bad.append("bubblewrap cannot create its sandbox (%s): on Ubuntu 24.04 run sandbox/setup-host"
                       % (p.stderr.strip()[:120] or p.returncode))
    st = Path(settings_file or "")
    try:
        data = json.loads(st.read_text())
        if st.stat().st_uid != 0 or os.access(str(st), os.W_OK):
            bad.append("the sandbox settings %s are not root-owned and read-only" % st)
        if (data.get("sandbox") or {}).get("allowUnsandboxedCommands") is not False:
            bad.append("the sandbox settings let commands leave the sandbox")
    except (OSError, ValueError):
        bad.append("the sandbox settings are missing (%s)" % st)
    return bad
