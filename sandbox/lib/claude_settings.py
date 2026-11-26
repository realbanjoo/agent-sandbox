"""Settings for Claude Code's own sandbox around an agent's shell commands, passed with `claude --settings FILE`.

Settings given on the command line win over the repository's own `.claude/settings*.json`, so a repository cannot
loosen them. Commands run sandboxed (bubblewrap on Linux; needs `bwrap` and `socat`), with no unsandboxed fallback:
  - reads denied: every path in `deny_read` (tokens, credentials, state); file tools denied the same paths;
  - writes: the project folder, plus `allow_write`;
  - network: only `allowed_domains` (GitHub and the package registries by default);
  - credential variables removed from the commands' environment;
  - `excluded` commands run outside the sandbox: those that need a token and only talk to a known host (git push,
    gh), or that start a sandbox of their own.
"""

DEFAULT_DOMAINS = ["github.com", "*.github.com", "*.githubusercontent.com", "registry.npmjs.org", "*.npmjs.org",
                   "registry.yarnpkg.com", "pypi.org", "files.pythonhosted.org", "proxy.golang.org", "sum.golang.org",
                   "crates.io", "*.crates.io"]
DEFAULT_EXCLUDED = ["git push *", "git fetch *", "git pull *", "git ls-remote *", "gh *"]
DEFAULT_DENY_READ = ["~/.config", "~/.claude/.credentials.json"]
CREDENTIAL_ENV = ["CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "GH_TOKEN", "GITHUB_TOKEN"]
PROTECTED_FILES = ["~/.config/**", "~/.claude/.credentials.json", "~/.claude/settings*.json", "~/.ssh/**", "~/.profile",
                   "~/.bashrc", "~/.local/bin/**"]


def settings(deny_read=(), excluded=(), allowed_domains=(), allow_write=("~/.npm", "~/.cache"), credential_env=()):
    """-> the settings dict. The arguments add to the defaults above."""
    deny = []
    for p in PROTECTED_FILES + ["%s/**" % d.rstrip("/") for d in deny_read]:
        deny += ["Read(%s)" % p, "Edit(%s)" % p, "Write(%s)" % p]
    deny.append("Read(//proc/*/environ)")
    domains = DEFAULT_DOMAINS + [d for d in allowed_domains if isinstance(d, str) and d.strip()]
    return {"sandbox": {"enabled": True, "allowUnsandboxedCommands": False, "failIfUnavailable": True,
                        "autoAllowBashIfSandboxed": True,
                        "excludedCommands": list(dict.fromkeys(DEFAULT_EXCLUDED + list(excluded))),
                        "filesystem": {"denyRead": list(dict.fromkeys(DEFAULT_DENY_READ + list(deny_read))),
                                       "allowWrite": list(allow_write)},
                        "network": {"allowedDomains": list(dict.fromkeys(domains))},
                        "credentials": {"envVars": [{"name": v, "mode": "deny"}
                                                    for v in dict.fromkeys(CREDENTIAL_ENV + list(credential_env))]}},
            "permissions": {"deny": list(dict.fromkeys(deny))}}
