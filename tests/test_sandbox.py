"""sandbox: in place in the checkout, no tokens, its own home and caches; the Claude Code settings; the probe."""
import importlib, json, os, pwd, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "sandbox/lib"
SANDBOX = LIB / "sandbox.py"
TOKENS = dict(GH_TOKEN="ghp_SECRET", ANTHROPIC_API_KEY="sk-ant-SECRET", CLAUDE_CODE_OAUTH_TOKEN="sk-ant-oat-SECRET")
sys.path.insert(0, str(LIB))


class Base(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.td, True)
        self.work = self.td / "work"; self.src = self.work / "r"; self.src.mkdir(parents=True)
        (self.src / "a.txt").write_text("project file\n")
        self.cache = self.td / "cache"
        self.cfg = self.td / "sandbox.json"
        self.config()
        self.env = dict(os.environ, DEVTOOLS_SANDBOX_RUNNER="direct", DEVTOOLS_SANDBOX_CONFIG=str(self.cfg), **TOKENS)
        self.env.pop("DEVTOOLS_SANDBOXED", None)

    def config(self, **extra):
        self.cfg.write_text(json.dumps(dict({"work": str(self.work), "cache": str(self.cache), "hide": ["/var/lib/app"],
                                             "env": {"APP_HELPERS": "/opt/helpers", "bad name": "x"}}, **extra)))

    def box(self, *args, cwd=None, **env):
        return subprocess.run(["python3", str(SANDBOX), *args], cwd=str(cwd or self.src), capture_output=True, text=True,
                              env=dict(self.env, **env), timeout=120)


class Sandbox(Base):
    def test_in_place_in_the_checkout_with_the_exit_code(self):
        r = self.box("--", "bash", "-c", "echo changed > a.txt; echo new > b.txt; pwd; exit 3")
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertEqual(r.stdout.strip(), str(self.src))
        self.assertEqual((self.src / "a.txt").read_text(), "changed\n"); self.assertTrue((self.src / "b.txt").exists())

    def test_no_token_and_its_own_home_and_caches(self):
        r = self.box("--", "env")
        self.assertNotIn("SECRET", r.stdout)
        env = dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)
        self.assertEqual(env["HOME"], str(self.cache / "home"))
        self.assertEqual((env["CI"], env["DEVTOOLS_SANDBOXED"], env["APP_HELPERS"]), ("true", "1", "/opt/helpers"))
        self.assertNotIn("bad name", env)

    def test_only_the_port_may_be_passed_in(self):
        self.assertIn("port=3007", self.box("--env", "PORT=3007", "--", "bash", "-c", 'echo "port=$PORT"').stdout)
        self.assertEqual(self.box("--env", "GH_TOKEN=x", "--", "true").returncode, 2)
        self.assertEqual(self.box("--env", "PORT=3;rm", "--", "true").returncode, 2)

    def test_refused_outside_the_work_folder_without_config_and_usage(self):
        other = self.td / "elsewhere"; other.mkdir()
        r = self.box("--", "true", cwd=other)
        self.assertEqual(r.returncode, 2); self.assertIn("refused", r.stderr)
        self.assertEqual(self.box("true").returncode, 2)                                       # no `--`
        self.cfg.write_text("{}")
        self.assertIn("must set `work` and `cache`", self.box("--", "true").stderr)

    def test_time_limit(self):
        r = self.box("--timeout", "1", "--", "sleep", "5", DEVTOOLS_SANDBOX_TIMEOUT_SECONDS="1")
        self.assertEqual(r.returncode, 124)

    def test_the_unit_hides_home_state_and_tokens_and_runs_as_the_caller(self):
        old = dict(os.environ); self.addCleanup(lambda: (os.environ.clear(), os.environ.update(old)))
        os.environ.update(DEVTOOLS_SANDBOX_RUNNER="direct", DEVTOOLS_SANDBOX_CONFIG=str(self.cfg))
        sys.modules.pop("sandbox", None); sb = importlib.import_module("sandbox")
        me = pwd.getpwuid(os.getuid())
        props = sb.properties(me, self.src, 15, sb.config())
        for p in ("ProtectHome=tmpfs", "BindPaths=%s" % self.src, "BindPaths=%s" % self.cache, "ProtectSystem=strict",
                  "NoNewPrivileges=yes", "PrivateTmp=yes", "RuntimeMaxSec=900", "KillMode=control-group",
                  "InaccessiblePaths=-/var/lib/app"):
            self.assertIn(p, props)
        self.assertIn("--uid=%s" % me.pw_name, props)
        self.assertFalse(any("IPAddressDeny" in p for p in props))                            # installs need the network
        self.config(network="off")
        off = sb.properties(me, self.src, 15, sb.config())
        self.assertIn("IPAddressDeny=any", off); self.assertIn("IPAddressAllow=localhost", off)
        os.environ["SUDO_UID"] = "0"
        with self.assertRaises(SystemExit):
            sb.caller()

    def test_root_mode_reads_only_the_fixed_config(self):
        env = dict(os.environ, DEVTOOLS_SANDBOX_CONFIG=str(self.cfg)); env.pop("DEVTOOLS_SANDBOX_RUNNER", None)
        r = subprocess.run(["python3", "-c", "import sys; sys.path.insert(0, %r); import sandbox; print(sandbox.CONFIG)" % str(LIB)],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.stdout.strip(), "/etc/dev-tools/sandbox.json")

    def test_the_wrapper(self):
        run = ROOT / "sandbox/bin/sandbox-run"
        r = subprocess.run(["bash", str(run), "--timeout", "5", "--", "echo", "hi"], capture_output=True, text=True,
                           env=dict(os.environ, DEVTOOLS_SANDBOXED="1", DEVTOOLS_SANDBOX_CMD="false"))
        self.assertEqual((r.returncode, r.stdout), (0, "hi\n"))                                 # inside already: as it is
        r = subprocess.run(["bash", str(run), "--", "pwd"], cwd=str(self.src), capture_output=True, text=True,
                           env=dict(self.env, DEVTOOLS_SANDBOX_CMD="python3 %s" % SANDBOX))
        self.assertEqual(r.stdout.strip(), str(self.src))
        root = (ROOT / "sandbox/root/sandbox-root").read_text()
        self.assertIn('/usr/bin/python3 -I "$here/lib/sandbox.py"', root)


class Probe(Base):
    def test_a_holding_sandbox_and_a_leaking_one(self):
        import probe
        secret = self.td / "secret"; secret.write_text("x")
        via = ["python3", str(SANDBOX), "--"]
        env_keep = dict(os.environ); self.addCleanup(lambda: (os.environ.clear(), os.environ.update(env_keep)))
        os.environ.update(DEVTOOLS_SANDBOX_RUNNER="direct", DEVTOOLS_SANDBOX_CONFIG=str(self.cfg), GH_TOKEN="leak")
        bad = probe.probe(via, self.work / ".probe", secret_files=[str(secret)], outside=str(self.td / "outside"))
        self.assertIn("%s is readable" % secret, bad)                       # direct mode hides nothing: the probe sees it
        self.assertIn("a write outside the checkout worked", bad)
        self.assertNotIn("GH_TOKEN is set inside the sandbox", bad)          # the clean environment holds even here
        self.assertFalse((self.td / "outside").exists())
        self.assertEqual(probe.probe(["false"], self.work / ".probe")[0][:24], "the sandbox did not run:")

    def test_claude_sandbox_problems(self):
        import probe
        bad = probe.claude_sandbox_problems(str(self.td / "missing.json"))
        self.assertTrue(any("settings are missing" in b for b in bad))
        f = self.td / "s.json"; f.write_text('{"sandbox": {"allowUnsandboxedCommands": true}}')
        bad = probe.claude_sandbox_problems(str(f))
        self.assertIn("the sandbox settings let commands leave the sandbox", bad)
        self.assertTrue(any("not root-owned" in b for b in bad))


class ClaudeSettings(unittest.TestCase):
    def test_strict_and_additive(self):
        import claude_settings as C
        s = C.settings(deny_read=["/var/lib/app"], excluded=["app-gate *"], allowed_domains=["api.example.com", " "],
                       credential_env=["APP_TOKEN"])
        sb = s["sandbox"]
        self.assertEqual((sb["enabled"], sb["allowUnsandboxedCommands"], sb["failIfUnavailable"]), (True, False, True))
        self.assertEqual(sb["excludedCommands"][:5], C.DEFAULT_EXCLUDED); self.assertIn("app-gate *", sb["excludedCommands"])
        self.assertNotIn("npm *", sb["excludedCommands"]); self.assertNotIn("bash *", sb["excludedCommands"])
        self.assertIn("~/.config", sb["filesystem"]["denyRead"]); self.assertIn("/var/lib/app", sb["filesystem"]["denyRead"])
        self.assertIn("api.example.com", sb["network"]["allowedDomains"]); self.assertNotIn(" ", sb["network"]["allowedDomains"])
        self.assertIn({"name": "APP_TOKEN", "mode": "deny"}, sb["credentials"]["envVars"])
        self.assertIn({"name": "GH_TOKEN", "mode": "deny"}, sb["credentials"]["envVars"])
        for p in ("Read(~/.config/**)", "Write(~/.local/bin/**)", "Read(/var/lib/app/**)", "Read(//proc/*/environ)"):
            self.assertIn(p, s["permissions"]["deny"])

    def test_setup_host_profile_is_for_bwrap_only(self):
        t = (ROOT / "sandbox/setup-host").read_text()
        for s in ("profile bwrap /usr/bin/bwrap", "userns,", '"bubblewrap", "socat"'):
            self.assertIn(s, t)


if __name__ == "__main__":
    unittest.main()
