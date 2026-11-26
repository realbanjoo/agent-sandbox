"""gate-branch, gate-red-green and gate-dup on a real git branch."""
import importlib.machinery, importlib.util, json, os, shutil, subprocess, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "gate/bin"
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")


def git(cwd, *a):
    return subprocess.run(["git", "-c", "init.defaultBranch=main", *a], cwd=str(cwd), check=True, capture_output=True,
                          text=True, env=GIT_ENV).stdout


class Branch(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.td, True)
        self.wt = self.td / "work/repo"; self.wt.mkdir(parents=True)
        git(self.wt, "init", "-q"); (self.wt / "README.md").write_text("x\n")
        git(self.wt, "add", "-A"); git(self.wt, "commit", "-qm", "init"); git(self.wt, "checkout", "-q", "-b", "feat/x")
        self.jd = self.td / "job"; self.jd.mkdir()

    def commit(self, files, msg):
        for p, t in files.items():
            (self.wt / p).parent.mkdir(parents=True, exist_ok=True); (self.wt / p).write_text(t)
        git(self.wt, "add", *files); git(self.wt, "commit", "-qm", msg)

    def run_(self, tool, *args, **env):
        return subprocess.run([str(BIN / tool), "--base", "main", *args], cwd=str(self.wt), capture_output=True, text=True,
                              env=dict(GIT_ENV, **env))

    def test_clean_size_trace_and_scope(self):
        spec, intake = self.jd / "spec.md", self.jd / "intake.md"
        spec.write_text("## Requirements\n| FR-001 | x |\n| FR-002 | y |\n| NFR-PERF-001 | z |\n")
        self.commit({"tests/test_a.py": "def test_FR001_x(): pass\ndef test_nfr_perf_001(): pass\n"}, "test: a")
        self.commit({"src/a.py": "x = 1\n" * 10}, "feat: a")
        r = self.run_("gate-branch", "--spec", str(spec)); self.assertEqual(r.returncode, 1)
        self.assertIn("ok: clean", r.stdout); self.assertIn("ok: size", r.stdout)
        self.assertIn("FAILED: trace", r.stdout); self.assertIn("FR-002", r.stdout); self.assertNotIn("FR-001,", r.stdout)
        self.assertEqual(self.run_("gate-branch").returncode, 0)                                  # no spec: no trace
        self.commit({"tests/test_b.py": "def test_FR002_y(): pass\n"}, "test: b")
        self.assertEqual(self.run_("gate-branch", "--spec", str(spec)).returncode, 0)
        self.assertEqual(self.run_("gate-branch", "--max-lines", "5").returncode, 1)              # too big: split
        r = self.run_("gate-branch", "--branch-prefix", "bot/"); self.assertIn("not a bot/... branch", r.stdout)
        (self.wt / "stray.txt").write_text("x")
        r = self.run_("gate-branch"); self.assertIn("FAILED: clean", r.stdout); (self.wt / "stray.txt").unlink()
        intake.write_text("DECISION: ACCEPT\nRESCOPED: yes\n\n## Dropped\nNothing.\n")
        r = self.run_("gate-branch", "--intake", str(intake)); self.assertIn("FAILED: scope", r.stdout)
        intake.write_text("DECISION: ACCEPT\nRESCOPED: yes\n\n## Dropped\n- the export\n")
        self.assertEqual(self.run_("gate-branch", "--intake", str(intake)).returncode, 0)

    def test_tests_first_must_fail_before_the_code(self):
        chk = self.td / "checks.sh"; chk.write_text("#!/bin/bash\ntest -f src/a.py\n")         # passes only with the code
        self.commit({"src/a.py": "x = 1\n", "tests/test_a.py": "t\n"}, "feat: all at once")
        r = self.run_("gate-red-green", "--checks", str(chk))
        self.assertEqual(r.returncode, 1); self.assertIn("no commit adds the new tests on their own", r.stdout)
        git(self.wt, "reset", "-q", "--hard", "main")
        self.commit({"tests/test_a.py": "t\n"}, "test: a"); self.commit({"src/a.py": "x = 1\n"}, "feat: a")
        r = self.run_("gate-red-green", "--checks", str(chk))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr); self.assertIn("ok: tests first", r.stdout)
        chk.write_text("#!/bin/bash\nexit 0\n")                                                     # tests that test nothing
        r = self.run_("gate-red-green", "--checks", str(chk))
        self.assertEqual(r.returncode, 1); self.assertIn("they test nothing", r.stdout)
        self.assertFalse((self.td / "work/repo-red").exists())

    def test_the_run_prefix_wraps_the_checks(self):
        via = self.td / "via.sh"; via.write_text('#!/bin/bash\necho wrapped >> "%s/ran"\nexec "$@"\n' % self.td); via.chmod(0o755)
        chk = self.td / "checks.sh"; chk.write_text("test -f src/a.py\n")
        self.commit({"tests/test_a.py": "t\n"}, "test: a"); self.commit({"src/a.py": "x = 1\n"}, "feat: a")
        r = self.run_("gate-red-green", "--checks", str(chk), "--run", str(via))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr); self.assertEqual((self.td / "ran").read_text(), "wrapped\n")

    def test_duplication_ratchet(self):
        loader = importlib.machinery.SourceFileLoader("gate_dup", str(BIN / "gate-dup"))
        d = importlib.util.module_from_spec(importlib.util.spec_from_loader("gate_dup", loader)); loader.exec_module(d)
        old = {"statistics": {"total": {"duplicatedLines": 10}},
               "duplicates": [{"firstFile": {"name": "a.js", "start": 1, "end": 10}, "secondFile": {"name": "b.js", "start": 5, "end": 14},
                               "lines": 10, "fragment": "old"}]}
        new = json.loads(json.dumps(old)); new["statistics"]["total"]["duplicatedLines"] = 22
        new["duplicates"].append({"firstFile": {"name": "c.js", "start": 3, "end": 14}, "secondFile": {"name": "a.js", "start": 40, "end": 51},
                                  "lines": 12, "fragment": "new"})
        ok, msg = d.compare(old, new, 0)
        self.assertFalse(ok); self.assertIn("adds 12 duplicated lines", msg[1])
        self.assertEqual([m.strip() for m in msg[2:]], ["c.js:3-14 <-> a.js:40-51 (12 lines)"])   # only the new copy
        self.assertTrue(d.compare(old, old, 0)[0]); self.assertTrue(d.compare(old, new, 12)[0])
        r = self.run_("gate-dup", PATH="/usr/bin:/bin")                                            # no Node: skipped, never blocking
        self.assertEqual(r.returncode, 0); self.assertIn("skipped", r.stdout)


class Action(unittest.TestCase):
    def test_the_actions_run_the_tools_they_ship(self):
        import yaml
        gate = yaml.safe_load((ROOT / "gate/action.yml").read_text())
        runs = "\n".join(s.get("run", "") for s in gate["runs"]["steps"])
        for tool in ("gate-tests", "gate-branch", "gate-red-green", "gate-dup"):
            self.assertIn('"$GITHUB_ACTION_PATH/bin/%s"' % tool, runs)
            self.assertTrue(os.access(BIN / tool, os.X_OK), tool)
        self.assertNotIn("${{ inputs.", runs)                     # inputs reach scripts through env only (no injection)
        top = yaml.safe_load((ROOT / "action.yml").read_text())
        self.assertIn("path", top["outputs"])


if __name__ == "__main__":
    unittest.main()
