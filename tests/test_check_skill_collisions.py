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


class InstalledStateNotJustCache(unittest.TestCase):
    """The cache holds every version Claude Code ever downloaded; what is LOADED is
    decided by the install record and the enabled flag. A check that reads only the
    cache reports plugins that are not installed and misses ones that are (#76 verify)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = pathlib.Path(self._tmp.name)
        self.cache = self.tmp / "cache"
        self.cache.mkdir()
        self.repo = self.tmp / "repo"
        (self.repo / "skills").mkdir(parents=True)
        write_skill(self.repo / "skills", "plaud-search")
        self.installed_file = self.tmp / "installed_plugins.json"
        self.settings_file = self.tmp / "settings.json"

    def record(self, entries: dict):
        import json
        self.installed_file.write_text(json.dumps({"version": 2, "plugins": entries}), encoding="utf-8")

    def run_script(self, *extra, with_record=True, with_settings=False):
        cmd = [sys.executable, str(SCRIPT), "--repo", str(self.repo), "--plugins-dir", str(self.cache)]
        if with_record:
            cmd += ["--installed-file", str(self.installed_file)]
        if with_settings:
            cmd += ["--settings", str(self.settings_file)]
        return subprocess.run(cmd + list(extra), capture_output=True, text=True)

    def test_the_installed_version_wins_over_a_newer_cached_one(self):
        make_plugin(self.cache, "mkt", "other", "1.0.0", ["plaud-search"])
        make_plugin(self.cache, "mkt", "other", "1.1.0", ["unrelated"])
        self.record({"other@mkt": [{"scope": "user", "version": "1.0.0",
                                    "installPath": str(self.cache / "mkt" / "other" / "1.0.0")}]})
        r = self.run_script()
        self.assertEqual(1, r.returncode, r.stdout + r.stderr)
        self.assertIn("plaud-search", r.stdout)

    def test_a_cached_plugin_with_no_install_record_is_ignored(self):
        make_plugin(self.cache, "mkt", "downloaded-once", "1.0.0", ["plaud-search"])
        self.record({})
        r = self.run_script()
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)

    def test_without_an_install_record_it_says_it_is_guessing_from_the_cache(self):
        make_plugin(self.cache, "mkt", "other", "1.0.0", ["plaud-search"])
        r = self.run_script(with_record=False)
        self.assertEqual(1, r.returncode, r.stdout + r.stderr)
        self.assertIn("cache only", (r.stdout + r.stderr).lower())

    def test_a_disabled_plugin_is_skipped(self):
        import json
        make_plugin(self.cache, "mkt", "other", "1.0.0", ["plaud-search"])
        self.record({"other@mkt": [{"scope": "user", "version": "1.0.0",
                                    "installPath": str(self.cache / "mkt" / "other" / "1.0.0")}]})
        self.settings_file.write_text(json.dumps({"enabledPlugins": {"other@mkt": False}}), encoding="utf-8")
        r = self.run_script(with_settings=True)
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)

    def test_a_quoted_name_in_frontmatter_still_collides(self):
        for declared in ('"plaud-search"', "'plaud-search'", "plaud-search  # trailing comment"):
            with self.subTest(declared=declared):
                base = self.cache / "mkt" / "quoted" / "1.0.0" / "skills" / "dir-differs"
                base.mkdir(parents=True, exist_ok=True)
                (base / "SKILL.md").write_text(f"---\nname: {declared}\n---\nbody\n", encoding="utf-8")
                r = self.run_script(with_record=False)
                self.assertEqual(1, r.returncode, r.stdout + r.stderr)

    def test_names_taken_from_the_cache_are_not_echoed_as_raw_control_characters(self):
        make_plugin(self.cache, "mkt", "evil\x1b[31mplugin", "1.0.0", ["plaud-search"])
        r = self.run_script(with_record=False)
        self.assertEqual(1, r.returncode, r.stdout + r.stderr)
        self.assertNotIn("\x1b", r.stdout)

    def test_a_dangling_symlink_in_the_cache_does_not_crash_the_check(self):
        make_plugin(self.cache, "mkt", "other", "1.0.0", ["unrelated"])
        (self.cache / "mkt" / "other" / "2.0.0").symlink_to(self.tmp / "gone")
        r = self.run_script(with_record=False)
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)


class AGateThatCannotReadItsInputMustNotSayClean(unittest.TestCase):
    """A collision check that exits 0 when it understood nothing is worse than no check:
    it reads as "no collisions". And a crash must not look like a collision either, since
    1 means "collision found" (#76 verify R2)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = pathlib.Path(self._tmp.name)
        self.cache = self.tmp / "cache"
        self.cache.mkdir()
        self.repo = self.tmp / "repo"
        (self.repo / "skills").mkdir(parents=True)
        write_skill(self.repo / "skills", "plaud-search")
        self.installed_file = self.tmp / "installed_plugins.json"

    def run_with_record(self, text, *extra):
        if text is not None:
            self.installed_file.write_text(text, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(self.repo), "--plugins-dir", str(self.cache),
             "--installed-file", str(self.installed_file), *extra],
            capture_output=True, text=True)

    def test_a_record_with_no_plugins_key_is_not_clean(self):
        for body in ('{"version": 3, "installed": {}}', '{}', '{"plugins": []}', '[]'):
            with self.subTest(body=body):
                r = self.run_with_record(body)
                self.assertEqual(2, r.returncode, r.stdout + r.stderr)
                self.assertIn("install record", (r.stdout + r.stderr).lower())

    def test_an_entry_that_is_not_a_list_of_objects_is_not_clean(self):
        for body in ('{"plugins": {"a@m": {"installPath": "/x"}}}', '{"plugins": {"a@m": ["not-an-object"]}}',
                     '{"plugins": {"a@m": [{"installPath": 7}]}}'):
            with self.subTest(body=body):
                r = self.run_with_record(body)
                self.assertEqual(2, r.returncode, r.stdout + r.stderr)

    def test_an_explicit_installed_file_that_is_missing_names_the_file(self):
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(self.repo), "--plugins-dir", str(self.cache),
             "--installed-file", str(self.tmp / "nope.json")], capture_output=True, text=True)
        self.assertEqual(2, r.returncode, r.stdout + r.stderr)
        self.assertIn("nope.json", r.stdout + r.stderr)

    def test_an_explicit_installed_file_that_is_not_json_is_an_error_not_a_fallback(self):
        r = self.run_with_record("{not json")
        self.assertEqual(2, r.returncode, r.stdout + r.stderr)
        self.assertNotIn("cache only", (r.stdout + r.stderr).lower())

    def test_an_empty_plugins_map_is_legitimately_clean(self):
        r = self.run_with_record('{"version": 2, "plugins": {}}')
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)

    def test_the_clean_line_says_how_many_installed_plugins_were_compared(self):
        make_plugin(self.cache, "mkt", "other", "1.0.0", ["unrelated"])
        entry = {"scope": "user", "version": "1.0.0", "installPath": str(self.cache / "mkt" / "other" / "1.0.0")}
        r = self.run_with_record(__import__("json").dumps({"version": 2, "plugins": {"other@mkt": [entry]}}))
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"compared against 1 installed plugin")

    def test_an_install_path_that_does_not_exist_is_reported_not_skipped_silently(self):
        entry = {"scope": "user", "version": "1.0.0", "installPath": str(self.tmp / "gone")}
        r = self.run_with_record(__import__("json").dumps({"version": 2, "plugins": {"other@mkt": [entry]}}))
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertIn("other@mkt", r.stdout + r.stderr)

    def test_one_plugin_recorded_twice_is_reported_once(self):
        make_plugin(self.cache, "mkt", "other", "1.0.0", ["plaud-search"])
        entry = {"scope": "user", "version": "1.0.0", "installPath": str(self.cache / "mkt" / "other" / "1.0.0")}
        project = dict(entry, scope="project", projectPath="/somewhere")
        r = self.run_with_record(__import__("json").dumps({"version": 2, "plugins": {"other@mkt": [entry, project]}}))
        self.assertEqual(1, r.returncode, r.stdout + r.stderr)
        self.assertEqual(1, r.stdout.count("`plaud-search` is also shipped by"), r.stdout)

    def test_the_cache_fallback_also_honours_a_disabled_plugin(self):
        import json
        make_plugin(self.cache, "mkt", "other", "1.0.0", ["plaud-search"])
        settings = self.tmp / "settings.json"
        settings.write_text(json.dumps({"enabledPlugins": {"other@mkt": False}}), encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(self.repo), "--plugins-dir", str(self.cache),
             "--settings", str(settings)], capture_output=True, text=True)
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)

    def test_a_comment_only_name_value_is_not_taken_as_the_name(self):
        base = self.cache / "mkt" / "other" / "1.0.0" / "skills" / "plaud-search"
        base.mkdir(parents=True)
        (base / "SKILL.md").write_text("---\nname: # just a comment\n---\nbody\n", encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(self.repo), "--plugins-dir", str(self.cache)],
            capture_output=True, text=True)
        # falls back to the DIRECTORY name, which is plaud-search -> collision, rather than
        # treating "# just a comment" as the declared name and missing it
        self.assertEqual(1, r.returncode, r.stdout + r.stderr)

    def test_bidi_and_line_separator_characters_are_not_echoed(self):
        make_plugin(self.cache, "mkt", "evil\u202eplugin\u2028x", "1.0.0", ["plaud-search"])
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(self.repo), "--plugins-dir", str(self.cache)],
            capture_output=True, text=True)
        self.assertEqual(1, r.returncode, r.stdout + r.stderr)
        self.assertNotIn("\u202e", r.stdout)
        self.assertNotIn("\u2028", r.stdout)

    def test_an_unexpected_failure_is_exit_2_not_the_collision_code(self):
        # --repo is a file, so reading skills/ cannot succeed in any ordinary way
        bogus = self.tmp / "afile"
        bogus.write_text("x", encoding="utf-8")
        r = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(bogus), "--plugins-dir", str(self.cache)],
                           capture_output=True, text=True)
        self.assertEqual(2, r.returncode, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)


if __name__ == "__main__":
    unittest.main()
