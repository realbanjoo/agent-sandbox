"""agent-sandbox: the sandbox in place without tokens, named installs side by side, the commands."""
import importlib, io, json, os, pwd, shutil, subprocess, sys, tempfile, unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "src/agent_sandbox"
SANDBOX = PKG / "sandbox.py"
TOKENS = dict(GH_TOKEN="ghp_SECRET", ANTHROPIC_API_KEY="sk-ant-SECRET", CLAUDE_CODE_OAUTH_TOKEN="sk-ant-oat-SECRET")
sys.path.insert(0, str(ROOT / "src"))
from agent_sandbox import claude_settings as C, install, probe, sandbox_run  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.td, True)
        self.work = self.td / "work"; self.src = self.work / "r"; self.src.mkdir(parents=True)
        (self.src / "a.txt").write_text("project file\n")
        self.cache = self.td / "cache"
        self.cfg = self.td / "sandbox.json"
        self.config()
        self.env = dict(os.environ, AGENT_SANDBOX_RUNNER="direct", AGENT_SANDBOX_CONFIG=str(self.cfg), **TOKENS)
        self.env.pop("AGENT_SANDBOX", None)

    def config(self, **extra):
        self.cfg.write_text(json.dumps(dict({"user": "someone", "work": str(self.work), "cache": str(self.cache),
                                             "hide": ["/var/lib/app"], "env": {"APP_HELPERS": "/opt/helpers", "bad name": "x"}},
                                            **extra)))

    def box(self, *args, cwd=None, **env):
        return subprocess.run(["python3", str(SANDBOX), "/etc/agent-sandbox/x.json", *args], cwd=str(cwd or self.src),
                              capture_output=True, text=True, env=dict(self.env, **env), timeout=120)

    def module(self, **env):
        old = dict(os.environ); self.addCleanup(lambda: (os.environ.clear(), os.environ.update(old)))
        os.environ.update(env)
        sys.modules.pop("agent_sandbox.sandbox", None)
        return importlib.import_module("agent_sandbox.sandbox")


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
        self.assertEqual((env["CI"], env["AGENT_SANDBOX"], env["APP_HELPERS"]), ("true", "1", "/opt/helpers"))
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
        self.assertIn("must set `work`, `cache`", self.box("--", "true").stderr)

    def test_time_limit(self):
        r = self.box("--timeout", "1", "--", "sleep", "5", AGENT_SANDBOX_TIMEOUT_SECONDS="1")
        self.assertEqual(r.returncode, 124)

    def test_the_unit_hides_home_state_and_tokens_and_runs_as_its_own_user_only(self):
        sb = self.module(AGENT_SANDBOX_RUNNER="direct", AGENT_SANDBOX_CONFIG=str(self.cfg))
        me = pwd.getpwuid(os.getuid())
        cfg = sb.config(self.cfg)
        props = sb.properties(me, self.src, 15, cfg)
        for p in ("ProtectHome=tmpfs", "BindPaths=%s" % self.src, "BindPaths=%s" % self.cache, "ProtectSystem=strict",
                  "NoNewPrivileges=yes", "PrivateTmp=yes", "RuntimeMaxSec=900", "KillMode=control-group",
                  "InaccessiblePaths=-/var/lib/app"):
            self.assertIn(p, props)
        self.assertIn("--uid=%s" % me.pw_name, props)
        self.assertFalse(any("IPAddressDeny" in p for p in props))                            # installs need the network
        self.config(network="off")
        off = sb.properties(me, self.src, 15, sb.config(self.cfg))
        self.assertIn("IPAddressDeny=any", off); self.assertIn("IPAddressAllow=localhost", off)
        os.environ["SUDO_UID"] = str(os.getuid())
        if os.getuid() != 0:
            self.assertEqual(sb.caller(me.pw_name).pw_name, me.pw_name)
            with self.assertRaises(SystemExit) as e:
                sb.caller("someone-else")                                                  # not this install's user
            self.assertIn("is someone-else's", str(e.exception))
        os.environ["SUDO_UID"] = "0"
        with self.assertRaises(SystemExit):
            sb.caller("root")

    def test_root_mode_reads_only_its_own_fixed_config(self):
        sb = self.module(AGENT_SANDBOX_CONFIG=str(self.cfg))
        os.environ.pop("AGENT_SANDBOX_RUNNER", None)
        sys.modules.pop("agent_sandbox.sandbox", None); sb = importlib.import_module("agent_sandbox.sandbox")
        self.assertEqual(sb.config_path("/etc/agent-sandbox/fb-x1.json"), Path("/etc/agent-sandbox/fb-x1.json"))
        for bad in (str(self.cfg), "/etc/agent-sandbox/../shadow.json", "/etc/agent-sandbox/x.txt", "/tmp/x.json"):
            with self.assertRaises(SystemExit):
                sb.config_path(bad)
        self.cfg.write_text(json.dumps({"work": "/w", "cache": "/c"}))
        with self.assertRaises(SystemExit) as e:
            sb.config(self.cfg)                                                             # no `user`: refused
        self.assertIn("`user`", str(e.exception))


class ConfiguredPath(Base):
    def test_path_folders_come_before_the_system_and_only_absolute_ones(self):                 # 0.2.0
        sb = self.module(AGENT_SANDBOX_CONFIG=str(self.cfg))
        self.config(path=["/opt/inst/tools/node/bin"])
        env = sb.environment(sb.config(self.cfg))
        self.assertEqual(env["PATH"], "/opt/inst/tools/node/bin:/usr/local/bin:/usr/bin:/bin")
        self.config()
        self.assertEqual(sb.environment(sb.config(self.cfg))["PATH"], "/usr/local/bin:/usr/bin:/bin")
        for bad in (["relative/bin"], ["/opt/x:/tmp"], ["/opt/$HOME"]):
            self.config(path=bad)
            with self.assertRaises(SystemExit):
                sb.config(self.cfg)


class Command(Base):
    def test_sandbox_run(self):
        self.assertEqual(sandbox_run.command(["--timeout", "5", "--", "echo", "hi"], {"AGENT_SANDBOX": "1"}), ["echo", "hi"])
        self.assertEqual(sandbox_run.command(["--name", "fb-a", "--", "pwd"], {}),
                         ["sudo", "-n", "/usr/local/libexec/agent-sandbox/fb-a/sandbox-root", "--", "pwd"])
        self.assertEqual(sandbox_run.command(["--", "pwd"], {"AGENT_SANDBOX_NAME": "me"})[2],
                         "/usr/local/libexec/agent-sandbox/me/sandbox-root")
        self.assertIsNone(sandbox_run.command(["--", "pwd"], {}))                              # no name
        self.assertIsNone(sandbox_run.command(["--name", "../x", "--", "pwd"], {}))
        r = subprocess.run(["python3", "-m", "agent_sandbox.sandbox_run", "--", "pwd"], cwd=str(self.src), capture_output=True,
                           text=True, env=dict(self.env, PYTHONPATH=str(ROOT / "src"), AGENT_SANDBOX_CMD="python3 %s" % SANDBOX))
        self.assertEqual(r.stdout.strip(), str(self.src), r.stderr)

    def test_claude_settings_command(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(C.main(["--deny-read", "/var/lib/app", "--exclude", "app-gate *", "--allow-domain", "api.x.com"]), 0)
        s = json.loads(out.getvalue())
        self.assertIn("/var/lib/app", s["sandbox"]["filesystem"]["denyRead"])
        self.assertIn("app-gate *", s["sandbox"]["excludedCommands"]); self.assertIn("api.x.com", s["sandbox"]["network"]["allowedDomains"])
        self.assertEqual(s["sandbox"]["filesystem"]["allowWrite"], ["~/.npm", "~/.cache"])
        self.assertEqual(C.main(["--nope", "x"]), 2)

    def test_probe_command(self):
        secret = self.td / "secret"; secret.write_text("x")
        env_keep = dict(os.environ); self.addCleanup(lambda: (os.environ.clear(), os.environ.update(env_keep)))
        os.environ.update(AGENT_SANDBOX_RUNNER="direct", AGENT_SANDBOX_CONFIG=str(self.cfg))
        out = io.StringIO()
        with redirect_stdout(out):
            rc = probe.main(["--run", "python3 %s /etc/agent-sandbox/x.json --" % SANDBOX, "--dir", str(self.work / ".probe"),
                             "--secret-file", str(secret)])
        self.assertEqual(rc, 1); self.assertIn("%s is readable" % secret, out.getvalue())   # direct mode hides nothing
        self.assertEqual(probe.main(["--dir", "x"]), 2)


class Probe(Base):
    def test_a_holding_sandbox_and_a_leaking_one(self):
        secret = self.td / "secret"; secret.write_text("x")
        via = ["python3", str(SANDBOX), "/etc/agent-sandbox/x.json", "--"]
        env_keep = dict(os.environ); self.addCleanup(lambda: (os.environ.clear(), os.environ.update(env_keep)))
        os.environ.update(AGENT_SANDBOX_RUNNER="direct", AGENT_SANDBOX_CONFIG=str(self.cfg), GH_TOKEN="leak")
        bad = probe.probe(via, self.work / ".probe", secret_files=[str(secret)], outside=str(self.td / "outside"))
        self.assertIn("%s is readable" % secret, bad)
        self.assertIn("a write outside the checkout worked", bad)
        self.assertNotIn("GH_TOKEN is set inside the sandbox", bad)          # the clean environment holds even here
        self.assertFalse((self.td / "outside").exists())
        self.assertEqual(probe.probe(["false"], self.work / ".probe")[0][:24], "the sandbox did not run:")

    def test_claude_sandbox_problems(self):
        bad = probe.claude_sandbox_problems(str(self.td / "missing.json"))
        self.assertTrue(any("settings are missing" in b for b in bad))
        f = self.td / "s.json"; f.write_text('{"sandbox": {"allowUnsandboxedCommands": true}}')
        bad = probe.claude_sandbox_problems(str(f))
        self.assertIn("the sandbox settings let commands leave the sandbox", bad)
        if os.getuid() != 0:
            self.assertTrue(any("not root-owned" in b for b in bad))


class Install(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.td, True)

    def files(self):
        return {str(p.relative_to(self.td)) for p in self.td.rglob("*") if p.is_file()}

    def run_(self, *args):
        with redirect_stdout(io.StringIO()):
            return install.main(["--root", str(self.td), *args])

    def test_named_installs_side_by_side_share_no_file(self):
        self.assertEqual(self.run_("--name", "fb-a1", "--user", "fb-a1"), 0)
        a = self.files()
        self.assertEqual(self.run_("--name", "fb-b2", "--user", "fb-b2"), 0)
        b = self.files() - a
        self.assertFalse(a & b)
        lx = self.td / "usr/local/libexec/agent-sandbox/fb-a1"
        self.assertEqual((lx / "sandbox.py").read_text(), SANDBOX.read_text())
        root = (lx / "sandbox-root").read_text()
        self.assertIn("exec /usr/bin/python3 -I /usr/local/libexec/agent-sandbox/fb-a1/sandbox.py /etc/agent-sandbox/fb-a1.json", root)
        self.assertTrue(os.access(lx / "sandbox-root", os.X_OK))
        rule = (self.td / "etc/sudoers.d/agent-sandbox-fb-a1").read_text()
        self.assertIn("fb-a1 ALL=(root) NOPASSWD: /usr/local/libexec/agent-sandbox/fb-a1/sandbox-root\n", rule)
        self.assertEqual(self.run_("--name", "fb-a1", "--remove"), 0)
        self.assertEqual(self.files(), b)                                       # removing one leaves the other intact

    def test_refusals(self):
        self.assertEqual(self.run_("--name", "../x", "--user", "u"), 2)
        self.assertEqual(self.run_("--name", "ok", "--user", "x ALL=(ALL) ALL"), 2)
        self.assertEqual(self.run_("--name", "ok"), 2)                          # no user
        self.assertIn("profile bwrap /usr/bin/bwrap", install.PROFILE); self.assertIn("userns,", install.PROFILE)


class Wheel(unittest.TestCase):
    def test_the_wheel_installs_the_commands(self):
        td = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, td, True)
        if subprocess.run(["python3", "-c", "import setuptools, venv"], capture_output=True).returncode != 0:
            self.skipTest("setuptools or venv missing")
        tree = td / "tree"                                                 # build from a copy: nothing left in the repo
        shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns(".git", "build", "dist", "*.egg-info", "__pycache__"))
        r = subprocess.run(["python3", "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "-q", "-w", str(td / "dist"),
                            str(tree)], capture_output=True, text=True)
        if r.returncode != 0:
            self.skipTest("cannot build here: %s" % r.stderr[-200:])
        clean = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}     # the venv sees only the wheel
        subprocess.run(["python3", "-m", "venv", str(td / "v")], check=True)
        wheel = next((td / "dist").glob("*.whl"))
        subprocess.run([str(td / "v/bin/pip"), "install", "-q", "--no-index", "--no-deps", str(wheel)], check=True, env=clean)
        for c in ("sandbox-run", "agent-sandbox-claude-settings", "agent-sandbox-probe", "agent-sandbox-install"):
            self.assertTrue((td / "v/bin" / c).exists(), c)
        out = subprocess.run([str(td / "v/bin/agent-sandbox-claude-settings")], capture_output=True, text=True, env=clean).stdout
        self.assertTrue(json.loads(out)["sandbox"]["enabled"])


if __name__ == "__main__":
    unittest.main()
