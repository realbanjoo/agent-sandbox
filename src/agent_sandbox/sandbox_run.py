"""sandbox-run [--name NAME] [--timeout MINUTES] [--env PORT=N] -- <command> [args...]

Run a command in the sandbox of the named install: this checkout only, without the caller's home, tokens or state
(see `sandbox`). NAME comes from --name or AGENT_SANDBOX_NAME. Through sudo, it starts the root helper that
`agent-sandbox-install --name NAME` put in a fixed, root-owned place, so the sudo rule never points at a file the
caller could change (such as a copy in a virtualenv the caller owns).
Inside a sandbox already (AGENT_SANDBOX set): the command runs as it is.
AGENT_SANDBOX_CMD replaces `sudo -n <helper> <config>` (tests).
"""
import os
import re
import shlex
import sys

LIBEXEC = "/usr/local/libexec/agent-sandbox"
CONFIG_DIR = "/etc/agent-sandbox"
NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


def helper(name):
    return "%s/%s/sandbox-root" % (LIBEXEC, name)


def command(argv, environ):
    """-> the argv to exec, or None for a usage error."""
    argv = list(argv)
    name = environ.get("AGENT_SANDBOX_NAME")
    if argv[:1] == ["--name"] and len(argv) > 1:
        name, argv = argv[1], argv[2:]
    if "--" not in argv:
        return None
    if environ.get("AGENT_SANDBOX"):
        return argv[argv.index("--") + 1:] or None
    fake = shlex.split(environ.get("AGENT_SANDBOX_CMD") or "")
    if fake:
        return fake + ["%s/%s.json" % (CONFIG_DIR, name or "test")] + argv
    if not name or not NAME.fullmatch(name):
        return None
    return ["sudo", "-n", helper(name)] + argv


def main(argv=None):
    cmd = command(sys.argv[1:] if argv is None else argv, os.environ)
    if not cmd:
        print("usage: " + __doc__.split("\n", 1)[0], file=sys.stderr)
        return 2
    os.execvp(cmd[0], cmd)


if __name__ == "__main__":
    sys.exit(main())
