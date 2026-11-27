"""Run untrusted project code (tests, installs, dev servers) without the caller's tokens, home or state.

    sandbox-run [--name NAME] [--timeout MINUTES] [--env PORT=N] -- <command> [args...]

This module is the root side of one named install. `agent-sandbox-install --name NAME` copies it to
/usr/local/libexec/agent-sandbox/NAME/sandbox.py (root-owned), where `sandbox-root` runs it with `python3 -I` and the
install's fixed configuration path as its first argument. It imports nothing but the standard library, so installs of
different versions never share code.

Run from a checkout under the configured work folder. Root (through `sandbox-root` and a sudo rule) starts the command
in a transient systemd unit, as the calling user (never root), in place - no copy:
  - the checkout is the only writable project folder; its git data is readable; a cache folder of its own persists
    (HOME, package caches, virtualenvs);
  - the rest of /home is an empty tmpfs: the caller's home, its tokens (`~/.config`, `~/.claude`) and the other
    checkouts are out of sight, and so is every path listed under `hide`;
  - read-only system (ProtectSystem=strict), private /tmp, no privilege gain, a time limit (RuntimeMaxSec), every
    process stopped when the command ends;
  - a clean environment: no token, CI=true, AGENT_SANDBOX=1, plus the variables listed under `env`;
  - network: "on" by default (installs need it); "off" keeps only local connections (tests and dev servers on
    localhost still work).

Configuration: /etc/agent-sandbox/NAME.json, root-owned (the caller cannot change it). Only `user` may use it:
    {"user": "me", "work": "/home/me/work", "cache": "/home/me/.cache/sandbox", "hide": ["/var/lib/myapp"],
     "timeout_minutes": 30, "network": "on", "env": {"NAME": "value"}}
Exit code: the command's; 124 at the time limit; 2 when refused.
Tests: AGENT_SANDBOX_RUNNER=direct (same user, no systemd, no `user` check) reads AGENT_SANDBOX_CONFIG instead, and
AGENT_SANDBOX_TIMEOUT_SECONDS overrides the limit.
"""
import json
import os
import pwd
import re
import subprocess
import sys
from pathlib import Path

DIRECT = os.environ.get("AGENT_SANDBOX_RUNNER") == "direct"
CONFIG_DIR = Path("/etc/agent-sandbox")
NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
PATH = "/usr/local/bin:/usr/bin:/bin"
PASS = re.compile(r"(PORT)=([0-9]{1,5})")       # the only values a caller may pass in
ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
PATH_DIR = re.compile(r"/[A-Za-z0-9._/+-]{0,250}")  # extra PATH folders, before the system's (root-owned config)


def usage():
    print("usage: " + __doc__.strip().splitlines()[2].strip(), file=sys.stderr)
    return 2


def parse(argv):
    timeout, extra, i = None, {}, 0
    while i < len(argv) and argv[i] != "--":
        if argv[i] == "--timeout" and i + 1 < len(argv) and argv[i + 1].isdigit():
            timeout = int(argv[i + 1])
            i += 1
        elif argv[i] == "--env" and i + 1 < len(argv) and PASS.fullmatch(argv[i + 1]):
            k, v = argv[i + 1].split("=", 1)
            extra[k] = v
            i += 1
        else:
            return None
        i += 1
    cmd = argv[i + 1:]
    return (timeout, extra, cmd) if cmd else None


def config_path(arg):
    """The install's configuration: the fixed path `sandbox-root` passes (never chosen by the caller)."""
    if DIRECT:
        return Path(os.environ.get("AGENT_SANDBOX_CONFIG") or arg)
    p = Path(arg)
    if p.parent != CONFIG_DIR or p.suffix != ".json" or not NAME.fullmatch(p.stem):
        raise SystemExit("sandbox: refused: bad configuration path %s" % arg)
    return p


def config(path):
    """The settings, with defaults; refuses a configuration without a user, a work folder or a cache."""
    try:
        c = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise SystemExit("sandbox: no usable configuration at %s (%s)" % (path, e))
    need = ("work", "cache") if DIRECT else ("user", "work", "cache")
    if not all(c.get(k) for k in need):
        raise SystemExit("sandbox: %s must set %s" % (path, ", ".join("`%s`" % k for k in need)))
    env = {k: str(v) for k, v in (c.get("env") or {}).items() if ENV_NAME.fullmatch(str(k))}
    path = [str(d) for d in c.get("path") or []]
    bad = [d for d in path if not PATH_DIR.fullmatch(d)]
    if bad:
        raise SystemExit("sandbox: %s: `path` takes absolute folders only (%s)" % (path, ", ".join(bad)))
    return {"user": c.get("user"), "work": Path(c["work"]), "cache": Path(c["cache"]),
            "hide": [str(h) for h in c.get("hide") or []], "timeout_minutes": int(c.get("timeout_minutes") or 30),
            "network": c.get("network") or "on", "env": env, "path": path}


def git_dir(src, work):
    """The checkout's git data (a worktree's `.git` is a file pointing into the main checkout), if under work."""
    p = subprocess.run(["git", "-C", str(src), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                       capture_output=True, text=True)
    d = Path(p.stdout.strip()) if p.returncode == 0 and p.stdout.strip() else None
    return d if d and (work in d.parents) else None


def environment(cfg):
    cache = cfg["cache"]
    env = dict(cfg["env"])
    env.update({"HOME": str(cache / "home"), "XDG_CACHE_HOME": str(cache / "xdg"), "npm_config_cache": str(cache / "npm"),
                "PIP_CACHE_DIR": str(cache / "pip"), "CI": "true", "LANG": "C.UTF-8",
                "PATH": ":".join(list(cfg.get("path") or []) + [PATH]),
                "GIT_CONFIG_NOSYSTEM": "1", "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0", "AGENT_SANDBOX": "1"})
    return env


def properties(user, src, minutes, cfg):
    cache = cfg["cache"]
    props = ["--uid=%s" % user.pw_name, "--gid=%d" % user.pw_gid, "-p", "WorkingDirectory=%s" % src,
             "-p", "RuntimeMaxSec=%d" % (minutes * 60), "-p", "NoNewPrivileges=yes", "-p", "PrivateTmp=yes",
             "-p", "ProtectSystem=strict", "-p", "ProtectHome=tmpfs", "-p", "BindPaths=%s" % src,
             "-p", "BindPaths=%s" % cache, "-p", "KillMode=control-group"]
    g = git_dir(src, cfg["work"])
    if g and g != src / ".git":
        props += ["-p", "BindReadOnlyPaths=%s" % g]
    for h in cfg["hide"]:
        props += ["-p", "InaccessiblePaths=-%s" % h]
    if cfg["network"] == "off":
        props += ["-p", "IPAddressDeny=any", "-p", "IPAddressAllow=localhost"]
    return props


def caller(allowed):
    """The user the command runs as: the one sudo was called by, never root, and only the install's own user."""
    uid = int(os.environ.get("SUDO_UID") or os.getuid())
    if uid == 0:
        raise SystemExit("sandbox: refused: never as root")
    user = pwd.getpwuid(uid)
    if user.pw_name != allowed:
        raise SystemExit("sandbox: refused: this sandbox is %s's, not %s's" % (allowed, user.pw_name))
    return user


def prepare_cache(cache, user=None):
    created = [d for d in [cache] + list(cache.parents) if not d.exists()]    # root creates them the first time
    for d in ("home", "xdg", "npm", "pip"):
        (cache / d).mkdir(parents=True, exist_ok=True)
    if user is None:
        return
    for d in created:                                                          # the caller owns what root created
        os.chown(d, user.pw_uid, user.pw_gid)
    for dp, dns, _ in os.walk(cache):
        for n in dns:
            os.lchown(os.path.join(dp, n), user.pw_uid, user.pw_gid)


def main(argv):
    """argv: the configuration path (from `sandbox-root`), then the caller's arguments."""
    if not argv:
        return usage()
    path = config_path(argv[0])
    p = parse(argv[1:])
    if not p:
        return usage()
    timeout, extra, cmd = p
    cfg = config(path)
    minutes = timeout or cfg["timeout_minutes"]
    work = cfg["work"]
    src = Path(os.getcwd()).resolve()
    if src != work and work not in src.parents:
        print("sandbox: refused: %s is not under %s" % (src, work), file=sys.stderr)
        return 2
    env = dict(environment(cfg), **extra)
    if DIRECT:                                    # tests: same user, no systemd; still a clean environment
        prepare_cache(cfg["cache"])
        env["PATH"] = os.environ.get("PATH", PATH)
        try:
            secs = float(os.environ.get("AGENT_SANDBOX_TIMEOUT_SECONDS") or minutes * 60)
            return subprocess.run(cmd, cwd=str(src), env=env, timeout=secs).returncode
        except subprocess.TimeoutExpired:
            print("sandbox: time limit of %d minutes reached: stopped" % minutes, file=sys.stderr)
            return 124
    user = caller(cfg["user"])
    prepare_cache(cfg["cache"], user)
    props = properties(user, src, minutes, cfg) + ["--setenv=%s=%s" % kv for kv in env.items()]
    return subprocess.run(["systemd-run", "--wait", "--pipe", "--collect", "--quiet"] + props + ["--"] + cmd).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
