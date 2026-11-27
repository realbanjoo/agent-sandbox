"""agent-sandbox-install: root, install or remove one named sandbox (Ubuntu 24.04 tested). Safe to run again.

    agent-sandbox-install --name NAME --user USER [--no-packages] [--root DIR]
    agent-sandbox-install --name NAME --remove [--root DIR]

  - copies this version's `sandbox.py` to /usr/local/libexec/agent-sandbox/NAME/ (root-owned) and writes
    `sandbox-root` there, which runs it with `/usr/bin/python3 -I` and the fixed configuration
    /etc/agent-sandbox/NAME.json. The sudo rule names this fixed path, never the package's own location (a virtualenv
    the user owns would let the user change what root runs). Installs with different names never share a file, so
    several versions can sit side by side;
  - writes /etc/sudoers.d/agent-sandbox-NAME letting USER (only) run that helper, checked with `visudo -cf` before it
    is put in place;
  - unless --no-packages: bubblewrap and socat (Claude Code's sandbox), and where AppArmor forbids unprivileged user
    namespaces (Ubuntu 24.04 and later) a profile allowing them for /usr/bin/bwrap only. Machine-wide by nature, and the
    same for every version;
  - --remove takes away the helper and the sudo rule (the configuration is the caller's to remove).
The configuration stays yours to write, root-owned: /etc/agent-sandbox/NAME.json (see `sandbox`), with "user": USER.
--root DIR installs under DIR instead of / and skips packages and AppArmor (tests; root not needed).
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import sandbox_run

HERE = Path(__file__).resolve().parent
HELPER = """#!/bin/sh
# agent-sandbox %(name)s: the sandbox's root side, run through a sudo rule (agent-sandbox-install). Root-owned.
exec /usr/bin/python3 -I %(dir)s/sandbox.py %(config)s "$@"
"""
PROFILE = """abi <abi/4.0>,
include <tunables/global>

# agent-sandbox: Ubuntu 24.04 forbids unprivileged user namespaces by default; bubblewrap (Claude Code's sandbox) needs
# them. Only bwrap gets the exception.
profile bwrap /usr/bin/bwrap flags=(unconfined) {
  userns,
  include if exists <local/bwrap>
}
"""


def say(msg, err=False):
    print("agent-sandbox-install: " + msg, file=sys.stderr if err else sys.stdout)


def write(path, text, mode):
    if not path.parent.is_dir():
        path.parent.mkdir(parents=True)
        os.chmod(path.parent, 0o755)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def paths(root, name):
    return {"dir": root / sandbox_run.LIBEXEC.lstrip("/") / name,
            "sudoers": root / "etc/sudoers.d" / ("agent-sandbox-" + name.replace(".", "_"))}


def install(root, name, user):
    p = paths(root, name)
    write(p["dir"] / "sandbox.py", (HERE / "sandbox.py").read_text(), 0o644)
    write(p["dir"] / "sandbox-root", HELPER % {"name": name, "dir": "%s/%s" % (sandbox_run.LIBEXEC, name),
                                               "config": "%s/%s.json" % (sandbox_run.CONFIG_DIR, name)}, 0o755)
    text = ("# agent-sandbox %s: %s may run this sandbox's root helper (agent-sandbox-install)\n"
            "%s ALL=(root) NOPASSWD: %s\n" % (name, user, user, sandbox_run.helper(name)))
    new = p["sudoers"].with_name(p["sudoers"].name + ".new")     # sudo skips names with a dot: never read half-written
    write(new, text, 0o440)
    if shutil.which("visudo") and subprocess.run(["visudo", "-cf", str(new)], capture_output=True).returncode != 0:
        new.unlink()
        raise SystemExit("agent-sandbox-install: the sudo rule did not pass visudo: nothing changed")
    os.replace(new, p["sudoers"])
    return p["dir"]


def remove(root, name):
    p = paths(root, name)
    shutil.rmtree(p["dir"], ignore_errors=True)
    if p["sudoers"].exists():
        p["sudoers"].unlink()


def packages():
    env = {"DEBIAN_FRONTEND": "noninteractive", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"}
    subprocess.run(["apt-get", "install", "-y", "-q", "bubblewrap", "socat"], check=True, env=env)
    prof = Path("/etc/apparmor.d/bwrap")
    if not Path("/etc/apparmor.d").is_dir():
        say("no AppArmor: nothing to allow")
        return 0
    if not prof.exists() or prof.read_text() != PROFILE:
        prof.write_text(PROFILE)
        if subprocess.run(["apparmor_parser", "-r", str(prof)]).returncode != 0:
            say("WARNING: the AppArmor profile for bwrap could not be loaded", err=True)
            return 1
    say("bwrap allowed")
    return 0


def valid_user(u):
    return bool(u) and u.replace("-", "").replace("_", "").isalnum() and not u[0].isdigit()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    opts = {"--name": None, "--user": None, "--root": None}
    flags = set()
    while argv:
        a = argv.pop(0)
        if a in opts and argv:
            opts[a] = argv.pop(0)
        elif a in ("--no-packages", "--remove"):
            flags.add(a)
        else:
            print(__doc__.split("\n\n")[1], file=sys.stderr)
            return 2
    name = opts["--name"]
    if not name or not sandbox_run.NAME.fullmatch(name):
        say("--name must match %s" % sandbox_run.NAME.pattern, err=True)
        return 2
    if not opts["--root"] and os.geteuid() != 0:
        say("run it as root", err=True)
        return 2
    root = Path(opts["--root"] or "/")
    if "--remove" in flags:
        remove(root, name)
        say("%s removed" % name)
        return 0
    if not valid_user(opts["--user"]):
        say("--user must be a Linux user name", err=True)
        return 2
    say("%s installed in %s, for %s" % (name, install(root, name, opts["--user"]), opts["--user"]))
    if opts["--root"] or "--no-packages" in flags:
        return 0
    return packages()


if __name__ == "__main__":
    sys.exit(main())
