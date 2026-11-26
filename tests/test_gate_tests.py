"""gate-tests: rules on changes to test files."""
import shutil, subprocess, tempfile, unittest
from pathlib import Path

RULES = Path(__file__).resolve().parent.parent / "gate/bin/gate-tests"


def git(cwd, *a):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], cwd=cwd, check=True, capture_output=True)


class TestRulesTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.td, True)
        self.main = self.td / "main"; self.wt = self.main / ".worktrees" / "001-login"
        (self.main / "tests").mkdir(parents=True); (self.main / "src").mkdir()
        (self.main / "tests/a.test.js").write_text("old a\n"); (self.main / "tests/test_b.py").write_text("old b\n")
        (self.main / "src/a.js").write_text("code\n"); (self.main / "README.md").write_text("docs\n")
        git(self.td, "init", "-q", "-b", "main", str(self.main)); git(self.main, "add", "-A"); git(self.main, "commit", "-qm", "base")
        git(self.main, "worktree", "add", "-q", "-b", "feat/001-login", str(self.wt))
        self.jobdir = self.td / "specs"; self.jobdir.mkdir(parents=True)

    def spec(self, test_changes):
        (self.jobdir / "spec.md").write_text("# x\n\n## Requirements\n- FR-001\n\n## Test changes\n%s\n\n## Out of scope\nnone\n" % test_changes)

    def run_(self, *args):
        spec = ["--spec", str(self.jobdir / "spec.md")] if (self.jobdir / "spec.md").exists() else []
        return subprocess.run(["python3", str(RULES), "--base", "main", *spec, *args], cwd=self.wt, capture_output=True,
                              text=True)

    def commit(self):
        git(self.wt, "add", "-A"); git(self.wt, "commit", "-qm", "work")

    def test_nothing_changed_passes(self):
        r = self.run_(); self.assertEqual(r.returncode, 0, r.stdout); self.assertIn("ok: test rules", r.stdout)

    def test_adding_tests_needs_no_authorisation(self):
        (self.wt / "tests/new.test.js").write_text("new\n"); (self.wt / "tests/test_c.py").write_text("c\n")
        self.assertEqual(self.run_().returncode, 0)                                       # untracked
        self.commit(); self.assertEqual(self.run_().returncode, 0)                        # committed

    def test_modifying_an_unlisted_existing_test_fails_and_names_it(self):
        self.spec("None.")
        (self.wt / "tests/a.test.js").write_text("weakened\n")
        for committed in (False, True):
            if committed: self.commit()
            r = self.run_(); self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("tests/a.test.js", r.stdout); self.assertIn("HOW TO FIX", r.stdout)

    def test_deleting_and_renaming_existing_tests_fail(self):
        self.spec("None.")
        (self.wt / "tests/a.test.js").unlink(); git(self.wt, "mv", "tests/test_b.py", "tests/test_renamed.py")
        self.commit(); r = self.run_()
        self.assertEqual(r.returncode, 1); self.assertIn("tests/a.test.js", r.stdout); self.assertIn("tests/test_b.py", r.stdout)

    def test_a_listed_test_may_change_but_only_that_one(self):
        self.spec("- `tests/a.test.js` (FR-001): asserts the old behaviour.")
        (self.wt / "tests/a.test.js").write_text("updated\n"); self.commit()
        self.assertEqual(self.run_().returncode, 0)
        (self.wt / "tests/test_b.py").write_text("weakened\n"); self.commit()
        r = self.run_(); self.assertEqual(r.returncode, 1); self.assertIn("tests/test_b.py", r.stdout); self.assertNotIn("a.test.js", r.stdout)

    def test_a_listed_deletion_is_allowed(self):
        self.spec("- `tests/test_b.py` (FR-001): replaced.")
        (self.wt / "tests/test_b.py").unlink(); self.commit(); self.assertEqual(self.run_().returncode, 0)

    def test_without_a_spec_the_list_is_empty(self):
        (self.wt / "tests/a.test.js").write_text("edited\n")
        r = self.run_(); self.assertEqual(r.returncode, 1); self.assertIn("no spec was given", r.stdout)
        self.spec("- `tests/a.test.js` (FR-001): x")
        self.assertEqual(self.run_().returncode, 0)
        self.assertEqual(self.run_("--spec", str(self.td / "missing.md")).returncode, 1)

    def test_source_code_needs_a_test_with_the_option(self):
        (self.wt / "src/a.js").write_text("changed\n")
        self.assertEqual(self.run_().returncode, 0)                                       # without the option: not required
        r = self.run_("--require-new-tests"); self.assertEqual(r.returncode, 1); self.assertIn("src/a.js", r.stdout)
        (self.wt / "tests/new.test.js").write_text("t\n")
        self.assertEqual(self.run_("--require-new-tests").returncode, 0)

    def test_docs_and_markup_do_not_need_tests(self):
        (self.wt / "README.md").write_text("more docs\n"); (self.wt / "page.html").write_text("<p>x</p>\n"); (self.wt / "style.css").write_text("a{}\n")
        self.assertEqual(self.run_("--require-new-tests").returncode, 0)

    def test_what_counts_as_a_test_file(self):
        import sys; sys.path.insert(0, str(RULES.parent.parent / "lib")); import gate_branch as m
        for p in ("tests/a.js", "test/x.py", "src/__tests__/a.ts", "e2e/login.js", "cypress/e2e/a.cy.js", "a.test.ts", "b.spec.js",
                  "test_x.py", "x_test.py", "pkg/x_test.go", "web/tests/deep/a.txt"):
            self.assertTrue(m.is_test(p), p)
        for p in ("src/a.js", "README.md", "latest/a.js", "contest.py", "src/testing_helpers.js", "docs/spec.md"):
            self.assertFalse(m.is_test(p), p)

    def test_usage_error(self):
        self.assertEqual(self.run_("--nope").returncode, 2)


if __name__ == "__main__":
    unittest.main()
