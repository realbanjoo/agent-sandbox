# dev-tools

Small, independent tools for running AI coding agents (or any untrusted build) safely on disposable cloud VMs.
Python 3 standard library and bash only. MIT.

| Folder | What it is | Use it for |
|---|---|---|
| [`sandbox/`](#sandbox) | Run code without the caller's tokens, home or state; Claude Code sandbox settings; a leak probe | Tests, installs and dev servers of a project you don't trust, on a Linux VM |
| [`hetzner/`](#hetzner) | A Hetzner Cloud client | One VM per project, created on demand, reused, deleted when idle |

Pin a version by commit. In a workflow, to get the files (for example to send them to a VM):

```yaml
- uses: realbanjoo/dev-tools@<commit>             # steps.devtools.outputs.path
  id: devtools
```

Releases: every change goes through a pull request with a green `tests` check; merging one that changes `VERSION`
tags `vX.Y.Z`. See [CHANGELOG.md](CHANGELOG.md).

## sandbox

```
sandbox-run [--timeout MINUTES] [--env PORT=N] -- <command> [args...]
```

Run from a checkout under the configured work folder. Through a sudo rule, root starts the command in a transient
systemd unit, as the caller and never as root, in place:

- only the checkout and the sandbox's own cache folder are writable;
- the rest of `/home` is an empty tmpfs, so tokens in `~/.config` and the other checkouts are out of sight, along with
  every path under `hide`;
- the system is read-only, `/tmp` is private, nothing gains privileges, and a time limit applies;
- the environment is clean, with only `DEVTOOLS_SANDBOXED=1` and the configured `env` added;
- the network is `on`, or `off` for local connections only.

Install (root):

1. Copy this repository to `/usr/local/lib/dev-tools`, owned by root, and link `sandbox/bin/sandbox-run` into the `PATH`.
2. Write `/etc/dev-tools/sandbox.json`, owned by root:
   `{"work": "/home/me/work", "cache": "/home/me/.cache/sandbox", "hide": [...], "timeout_minutes": 30, "network": "on", "env": {}}`.
3. Add a sudo rule: `me ALL=(root) NOPASSWD: /usr/local/lib/dev-tools/sandbox/root/sandbox-root`.
4. For Claude Code's own sandbox, run `sandbox/setup-host`. It installs bubblewrap and socat, and allows bwrap's user
   namespaces on Ubuntu 24.04.

`sandbox/lib/claude_settings.py` builds the settings to pass with `claude --settings FILE`:
- sandboxed commands with no unsandboxed fallback;
- reads denied on secrets;
- credential variables removed;
- network limited to GitHub and the package registries, plus what you add;
- only `git push`/`fetch`/`pull`, `gh` and your own excluded commands run outside.

`sandbox/lib/probe.py` checks that both sandboxes hold before you trust them.

## hetzner

`hetzner/lib/hcloud.py`, standard library only:

- `Client(token)`: servers by label selector, SSH keys, deletion, retries;
- `ensure_server(...)`: reuse, power on, or create one server, with fallback types and locations, converging when
  two callers race;
- `hostkey_fingerprint`: pin the host key;
- `Ssh`: commands with a pinned `known_hosts` file and no prompts.

## Tests

`make check`. Offline; the sandbox tests run in direct mode (same user, no systemd).
