# agent-sandbox

Run untrusted code — tests, installs, dev servers, an AI agent's shell commands — on a Linux host without giving it
your tokens, your home or your state. Also: strict settings for Claude Code's own sandbox, and a probe that checks both
sandboxes hold before you trust them. Python 3 standard library only. MIT.

Several **named installs**, even of different versions, can sit side by side on one host without sharing a file, so
each user or each service gets its own sandbox.

## Install

Root installs it into a root-owned virtualenv, then sets up one named sandbox per user:

```sh
sudo python3 -m venv /opt/agent-sandbox
sudo /opt/agent-sandbox/bin/pip install "agent-sandbox @ git+https://github.com/realbanjoo/agent-sandbox@v0.1.0"
sudo /opt/agent-sandbox/bin/agent-sandbox-install --name me --user me
sudo ln -s /opt/agent-sandbox/bin/sandbox-run /usr/local/bin/sandbox-run
```

Then write its configuration, root-owned, `/etc/agent-sandbox/me.json`:

```json
{"user": "me", "work": "/home/me/code", "cache": "/home/me/.cache/agent-sandbox",
 "hide": [], "timeout_minutes": 30, "network": "on", "env": {}}
```

Any pip-compatible tool works the same way (`uv pip install`, a `requirements.txt` line, Poetry or PDM with the git
URL), and so does a plain clone: `PYTHONPATH=src python3 -m agent_sandbox.install ...`. Pin a tag or a commit.

`agent-sandbox-install --name NAME --user USER`:

- copies this version's root side to `/usr/local/libexec/agent-sandbox/NAME/`, owned by root;
- writes `/etc/sudoers.d/agent-sandbox-NAME`, letting USER (only) run it, checked with `visudo -cf` first;
- installs bubblewrap and socat for Claude Code's sandbox, and on Ubuntu 24.04+ allows user namespaces for
  `/usr/bin/bwrap` only (skip with `--no-packages`).

The sudo rule always points at that fixed root-owned copy, never into a virtualenv: a venv the user owns would let the
user change what root runs. `--remove` takes a named install away again.

## sandbox-run

```
sandbox-run [--name NAME] [--timeout MINUTES] [--env PORT=N] -- <command> [args...]
```

Run from a checkout under the configured `work` folder (NAME defaults to `$AGENT_SANDBOX_NAME`). Root starts the
command in a transient systemd unit, as you and never as root, in place:

- only the checkout and the sandbox's own `cache` folder are writable;
- the rest of `/home` is an empty tmpfs, so tokens in `~/.config` and the other checkouts are out of sight, along with
  every path under `hide`;
- the system is read-only, `/tmp` is private, nothing gains privileges, and the time limit applies;
- the environment is clean: no token, `AGENT_SANDBOX=1`, plus the configured `env`;
- the network is `on`, or `off` for local connections only.

Only the configuration's `user` may use a named install. Inside a sandbox, `sandbox-run` runs the command as it is.

## agent-sandbox-claude-settings

```
agent-sandbox-claude-settings [--deny-read PATH]... [--exclude COMMAND]... [--allow-domain DOMAIN]...
                              [--allow-write PATH]... [--credential-env NAME]...   > settings.json
```

Prints settings for `claude --settings settings.json`, which a repository's own settings cannot loosen:

- commands sandboxed, with no unsandboxed fallback;
- reads denied on tokens and credentials, plus your paths;
- credential variables removed from commands;
- network limited to GitHub and the package registries, plus your domains;
- only `git push`/`fetch`/`pull`, `gh` and your excluded commands run outside.

## agent-sandbox-probe

```
agent-sandbox-probe --run "sandbox-run --name NAME --timeout 2 --" --dir DIR [--secret-file F]...
                    [--secret-dir D]... [--secret-env V]... [--outside PATH] [--claude-settings FILE]
```

Runs a probe through the sandbox and prints one problem per line, exiting 1 if there is any. It checks for secret
files readable, folders visible, variables set, writes outside the checkout, a checkout that isn't writable, a sandbox
that didn't start, and Claude Code's sandbox not holding. Exit 0 means both sandboxes hold.

## Tests

`make check`, offline: the sandbox tests run in direct mode (same user, no systemd), installs under a temporary root.

Releases: every change goes through a pull request with a green `tests` check; merging one that changes `VERSION`
tags `vX.Y.Z`. See [CHANGELOG.md](CHANGELOG.md).
