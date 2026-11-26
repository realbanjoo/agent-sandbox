# Changelog

## 0.1.0

First version, extracted from factory-bootstrap 0.21.0 and made independent of it:

- `gate/`: `gate-tests`, `gate-branch`, `gate-red-green`, `gate-dup`, and a GitHub Action for any pull request.
- `sandbox/`: `sandbox-run` (systemd, configured by a root-owned `/etc/dev-tools/sandbox.json`), Claude Code sandbox
  settings, the leak probe, `setup-host` (bubblewrap, socat, the AppArmor rule for bwrap).
- `hetzner/`: the Hetzner Cloud client (`ensure_server` with fallbacks, host key pinning, SSH).
- The root `action.yml` makes a pinned version available to another action (`outputs.path`).
