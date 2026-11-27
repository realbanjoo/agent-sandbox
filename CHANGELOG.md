# Changelog

## 0.1.0

First version, extracted from factory-bootstrap and made independent of it:

- `sandbox-run`: untrusted code in place in a checkout, in a systemd unit, without the caller's home, tokens or state.
- Named installs (`agent-sandbox-install --name NAME --user USER`): each has its own root-owned helper, sudo rule and
  configuration `/etc/agent-sandbox/NAME.json`, used only by its own user, so several versions can sit side by side.
- `agent-sandbox-claude-settings`: strict settings for Claude Code's own sandbox.
- `agent-sandbox-probe`: checks that both sandboxes hold.
- A standard Python package (no dependencies), installed from a git tag or commit with any pip-compatible tool.
