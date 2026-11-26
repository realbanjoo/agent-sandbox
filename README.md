# dev-tools

Small, independent tools for running AI coding agents (or any untrusted build) safely and keeping their pull requests
reviewable. Python 3 standard library and bash only. MIT.

| Folder | What it is | Use it for |
|---|---|---|
| [`gate/`](#gate) | Checks on a branch against its base, and a GitHub Action | Any pull request: test-file rules, size, requirement traceability, tests first, no new duplicated code |
| [`sandbox/`](#sandbox) | Run code without the caller's tokens, home or state; Claude Code sandbox settings; a leak probe | Tests, installs and dev servers of a project you don't trust, on a Linux VM |
| [`hetzner/`](#hetzner) | A Hetzner Cloud client | One VM per project, created on demand, reused, deleted when idle |

Pin a version by commit. In a workflow:

```yaml
- uses: realbanjoo/dev-tools/gate@<commit>        # the gate on a pull request
- uses: realbanjoo/dev-tools@<commit>             # just the files: steps.<id>.outputs.path
  id: devtools
```

Releases: every change goes through a pull request with a green `tests` check; merging one that changes `VERSION`
tags `vX.Y.Z`. See [CHANGELOG.md](CHANGELOG.md).

## gate

```
gate-tests      [--base REF] [--spec FILE] [--require-new-tests]
gate-branch     [--base REF] [--max-lines N] [--branch-prefix P] [--spec FILE] [--intake FILE]
gate-red-green  --checks FILE [--base REF] [--run COMMAND]
gate-dup        [--base REF] [--tolerance LINES] [--run COMMAND] [--cache DIR]
```

Run in the checkout of the branch. Each prints `ok: <check>` or `FAILED: <check>` with how to fix it, and exits 1 when
a check failed.

- **gate-tests**: an existing test file may change only when the spec lists it, backticked, under `## Test changes`;
  with `--require-new-tests`, source changes need a test added or changed. New test files are always fine.
- **gate-branch**:
  - `clean`: nothing uncommitted or untracked;
  - `size`: at most N changed lines of source (tests, lock files and generated folders don't count);
  - `trace` (with `--spec`): every `FR-`/`NFR-` ID under `## Requirements` is named by a test the branch adds or changes;
  - `scope` (with `--intake`): `RESCOPED: yes` needs a non-empty `## Dropped`.
- **gate-red-green**: when the branch changes tests and code, the last commit that adds tests on their own must *fail*
  the checks (a bash script), in a throwaway checkout. Tests that pass without the code test nothing.
- **gate-dup**: jscpd (pinned) on the branch and on its base; fails when the branch adds duplicated lines. A ratchet:
  existing copies don't block. Skipped without Node.js.

`--run` runs project code through a prefix, for example `sandbox-run --`. Pass `--spec` and `--intake` from a place the
branch cannot change (not from the branch's own checkout).

The Action (`gate/action.yml`) runs all four on a pull request. Its inputs are `max-lines`, `spec`,
`require-new-tests`, `checks`, `duplication` and `duplication-tolerance`.

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
