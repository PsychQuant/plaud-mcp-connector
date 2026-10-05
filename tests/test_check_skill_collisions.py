"""Tests for scripts/check_skill_collisions.py (issue #76).

The question the script answers is "does a skill name in this plugin also exist in
another plugin the same person has installed?" — which depends on the machine, so
nothing in the repo can answer it statically. These tests build a fake plugin
cache and a fake repo and pin what the script reports against them, so the
answer on a real machine is trustworthy.
"""

import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "check_skill_collisions.py"


def write_skill(root: pathlib.Path, name: str, declared: str | None = None) -> None:
    """A skill directory `name` whose frontmatter declares `declared` (default: name)."""
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {declared or name}\ndescription: test fixture\n---\n\nbody\n",
        encoding="utf-8",
    )


def make_plugin(cache: pathlib.Path, marketplace: str, plugin: str, version: str, skills):
    """cache/<marketplace>/<plugin>/<version>/skills/<skill>/SKILL.md — the layout
    Claude Code writes for an installed plugin."""
    base = cache / marketplace / plugin / version / "skills"
    base.mkdir(parents=True, exist_ok=True)
    for s in skills:
        write_skill(base, s)


class CollisionChecker(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = pathlib.Path(self._tmp.name)
        self.cache = tmp / "cache"
        self.cache.mkdir()
        self.repo = tmp / "repo"
        (self.repo / "skills").mkdir(parents=True)
        for name in ("plaud-download", "plaud-search"):
            write_skill(self.repo / "skills", name)

    def run_script(self, *extra):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(self.repo),
             "--plugins-dir", str(self.cache), *extra],
            capture_output=True, text=True,
        )

    def test_reports_a_name_shared_with_another_plugin(self):
        make_plugin(self.cache, "mkt", "other-plugin", "1.0.0", ["plaud-download", "unrelated"])
        r = self.run_script()
        self.assertEqual(1, r.returncode, r.stdout + r.stderr)
        self.assertIn("plaud-download", r.stdout)
        self.assertIn("other-plugin", r.stdout)
        self.assertNotIn("unrelated", r.stdout)

    def test_clean_when_no_name_is_shared(self):
        make_plugin(self.cache, "mkt", "other-plugin", "1.0.0", ["something-else"])
        r = self.run_script()
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)

    def test_our_own_plugin_is_not_compared_with_itself(self):
        # An installed copy of this very plugin has the same skill names by definition.
        make_plugin(self.cache, "mkt", "plaud-mcp-connector", "0.11.0", ["plaud-download"])
        r = self.run_script()
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)

    def test_only_the_newest_installed_version_counts(self):
        # 1.0.0 still has the clashing skill on disk; 1.1.0 dropped it. Claude Code
        # loads the newest, so reporting 1.0.0 would be a false alarm.
        make_plugin(self.cache, "mkt", "other-plugin", "1.0.0", ["plaud-download"])
        make_plugin(self.cache, "mkt", "other-plugin", "1.1.0", ["something-else"])
        r = self.run_script()
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)

    def test_versions_compare_numerically_not_alphabetically(self):
        # "1.10.0" sorts before "1.9.0" as a string; it is the newer release.
        make_plugin(self.cache, "mkt", "other-plugin", "1.9.0", ["plaud-download"])
        make_plugin(self.cache, "mkt", "other-plugin", "1.10.0", ["something-else"])
        r = self.run_script()
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)

    def test_a_name_comes_from_frontmatter_not_the_directory(self):
        base = self.cache / "mkt" / "other-plugin" / "1.0.0" / "skills"
        base.mkdir(parents=True)
        write_skill(base, "dir-name-differs", declared="plaud-search")
        r = self.run_script()
        self.assertEqual(1, r.returncode, r.stdout + r.stderr)
        self.assertIn("plaud-search", r.stdout)

    def test_missing_plugins_dir_is_a_clear_non_failure(self):
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(self.repo),
             "--plugins-dir", str(self.cache / "does-not-exist")],
            capture_output=True, text=True,
        )
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertIn("nothing to compare", (r.stdout + r.stderr).lower())

    def test_the_repo_itself_has_no_collisions_among_its_own_skills(self):
        # Run against the real repo with an empty cache: catches two of OUR skills
        # sharing a name, which a directory listing would not.
        empty = self.cache / "empty"
        empty.mkdir()
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(REPO), "--plugins-dir", str(empty)],
            capture_output=True, text=True,
        )
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
