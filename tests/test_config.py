"""Preferences: read them, apply them, and never let them stop the real work.

The config file holds preferences, not decisions with a right answer — which
version of a transcript your subtitles come from, how wide a subtitle line
should be. Everything here follows from that: a preference that cannot be read
is a smaller problem than a subtitle file you did not get, so every failure
path in this module degrades to the default and keeps going.

The one thing it must NOT do is fail silently. A typo'd key that quietly does
nothing is worse than either alternative: you believe a preference is in effect,
the output disagrees, and nothing anywhere says why.

Isolation note: every test here points PLAUD_CONFIG at a temp file. Without
that, the suite would read — and save_preference would WRITE — the developer's
real preferences.
"""

import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location("plaud_config", REPO / "scripts" / "config.py")
config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(config)


class ConfigTestCase(unittest.TestCase):
    """Base: an isolated config path, so no test can touch the real one."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = pathlib.Path(self._tmp.name) / "config.json"

    def _write(self, data) -> None:
        self.path.write_text(
            data if isinstance(data, str) else json.dumps(data), encoding="utf-8"
        )


class TestConfigPath(ConfigTestCase):
    def test_env_var_wins(self) -> None:
        with mock.patch.dict(os.environ, {"PLAUD_CONFIG": "/somewhere/else.json"}):
            self.assertEqual(pathlib.Path("/somewhere/else.json"), config.config_path())

    def test_default_is_beside_the_cache_not_inside_it(self) -> None:
        """Asserts the computed path WITHOUT reading it.

        Touching the real default path in a test is how a suite ends up
        depending on — or worse, rewriting — whatever the developer happens to
        have configured. The location is also deliberately not under the cache
        directory: a cache is disposable and gets rebuilt, preferences are not.
        """
        env = {k: v for k, v in os.environ.items() if k != "PLAUD_CONFIG"}
        with mock.patch.dict(os.environ, env, clear=True):
            path = config.config_path()
        self.assertEqual("config.json", path.name)
        self.assertEqual(".plaud-connector", path.parent.name)
        self.assertNotIn("cache", path.parts)


class TestLoadConfig(ConfigTestCase):
    def test_missing_file_gives_defaults_silently(self) -> None:
        """No config file is the normal state, not an error worth mentioning."""
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            cfg = config.load_config(self.path)
        self.assertEqual("polished", cfg["subtitle_source"])
        self.assertEqual({"latin": 42, "cjk": 20}, cfg["srt_line_limits"])
        self.assertEqual("", err.getvalue())

    def test_file_values_override_defaults(self) -> None:
        self._write({"subtitle_source": "verbatim"})
        cfg = config.load_config(self.path)
        self.assertEqual("verbatim", cfg["subtitle_source"])
        # Untouched keys keep their defaults rather than disappearing.
        self.assertEqual({"latin": 42, "cjk": 20}, cfg["srt_line_limits"])

    def test_partial_line_limits_merge_rather_than_replace(self) -> None:
        """Setting one script's limit must not delete the other's."""
        self._write({"srt_line_limits": {"cjk": 14}})
        cfg = config.load_config(self.path)
        self.assertEqual({"latin": 42, "cjk": 14}, cfg["srt_line_limits"])

    def test_unknown_key_warns_loudly_and_keeps_going(self) -> None:
        """A typo must be audible, but must not cost you the subtitles.

        Same reasoning as a failed write: the preference is the small thing
        here, the work is the big thing.
        """
        self._write({"subtitle_soruce": "verbatim", "srt_line_limits": {"cjk": 16}})
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            cfg = config.load_config(self.path)
        msg = err.getvalue()
        self.assertIn("subtitle_soruce", msg)
        self.assertIn("subtitle_source", msg, "the warning must name the valid keys")
        self.assertIn("srt_line_limits", msg)
        # The typo did not take effect, and the other setting still did.
        self.assertEqual("polished", cfg["subtitle_source"])
        self.assertEqual(16, cfg["srt_line_limits"]["cjk"])

    def test_broken_json_warns_and_falls_back(self) -> None:
        """'No config' and 'broken config' must not look the same from outside."""
        self._write("{ this is not json")
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            cfg = config.load_config(self.path)
        self.assertIn(str(self.path), err.getvalue())
        self.assertEqual("polished", cfg["subtitle_source"])

    def test_non_object_json_warns_and_falls_back(self) -> None:
        self._write("[1, 2, 3]")
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            cfg = config.load_config(self.path)
        self.assertNotEqual("", err.getvalue())
        self.assertEqual("polished", cfg["subtitle_source"])

    def test_invalid_subtitle_source_value_warns_and_falls_back(self) -> None:
        """A key we know with a value we do not is still a typo."""
        self._write({"subtitle_source": "clean"})
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            cfg = config.load_config(self.path)
        self.assertIn("clean", err.getvalue())
        self.assertEqual("polished", cfg["subtitle_source"])

    def test_non_integer_line_limit_warns_and_falls_back(self) -> None:
        self._write({"srt_line_limits": {"cjk": "twenty"}})
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            cfg = config.load_config(self.path)
        self.assertNotEqual("", err.getvalue())
        self.assertEqual(20, cfg["srt_line_limits"]["cjk"])


class TestSavePreference(ConfigTestCase):
    def test_creates_the_file_and_its_directory(self) -> None:
        nested = pathlib.Path(self._tmp.name) / "deep" / "config.json"
        self.assertTrue(config.save_preference("subtitle_source", "verbatim", nested))
        self.assertEqual("verbatim", json.loads(nested.read_text())["subtitle_source"])

    def test_preserves_other_keys(self) -> None:
        self._write({"srt_line_limits": {"cjk": 16}})
        config.save_preference("subtitle_source", "verbatim", self.path)
        saved = json.loads(self.path.read_text())
        self.assertEqual("verbatim", saved["subtitle_source"])
        self.assertEqual({"cjk": 16}, saved["srt_line_limits"])

    def test_unwritable_path_warns_and_returns_false_without_raising(self) -> None:
        """Not being able to remember a preference must never abort the run.

        Read-only home, full disk, wrong permissions — all real. Losing the
        preference is an inconvenience; losing the subtitles is the failure.
        """
        unwritable = pathlib.Path(self._tmp.name) / "locked"
        unwritable.mkdir()
        unwritable.chmod(0o500)
        self.addCleanup(unwritable.chmod, 0o700)
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            ok = config.save_preference("subtitle_source", "verbatim",
                                        unwritable / "config.json")
        self.assertFalse(ok)
        self.assertNotEqual("", err.getvalue(), "a silent failed write is the worst case")

    def test_corrupt_existing_file_does_not_block_the_write(self) -> None:
        """A broken file should be replaced, not treated as a reason to give up."""
        self._write("{ broken")
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            ok = config.save_preference("subtitle_source", "verbatim", self.path)
        self.assertTrue(ok)
        self.assertEqual("verbatim", json.loads(self.path.read_text())["subtitle_source"])


if __name__ == "__main__":
    unittest.main()


class TestConfigCLI(ConfigTestCase):
    """A tiny command surface, because the skill has to reach these settings.

    The interaction contract lives in SKILL.md — Claude asks the question, since
    AskUserQuestion is an agent tool no Python process can call. But Claude then
    has to (a) find out whether a preference was ever recorded and (b) record
    one. Hand-writing JSON from prose is exactly the kind of step that works
    until the day it writes a stray comma, so it gets a command instead.

    `get` distinguishes "explicitly chosen" from "falling back to the default" —
    that distinction IS the ask-once rule. Without it the skill cannot tell a
    user who picked polished from a user who has never been asked.
    """

    def _cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(REPO / "scripts" / "config.py"), *args],
            capture_output=True, text=True,
            env={**os.environ, "PLAUD_CONFIG": str(self.path)},
        )

    def test_get_exits_3_when_the_key_was_never_chosen(self) -> None:
        out = self._cli("get", "subtitle_source")
        self.assertEqual(3, out.returncode)
        self.assertIn("polished", out.stdout, "still report the effective value")

    def test_get_exits_0_once_a_choice_is_recorded(self) -> None:
        self._write({"subtitle_source": "verbatim"})
        out = self._cli("get", "subtitle_source")
        self.assertEqual(0, out.returncode)
        self.assertIn("verbatim", out.stdout)

    def test_set_records_the_choice(self) -> None:
        out = self._cli("set", "subtitle_source", "verbatim")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertEqual("verbatim", json.loads(self.path.read_text())["subtitle_source"])

    def test_set_rejects_an_unknown_key_rather_than_writing_it(self) -> None:
        """Warning about a typo you already saved is too late to help."""
        out = self._cli("set", "subtitle_soruce", "verbatim")
        self.assertNotEqual(0, out.returncode)
        self.assertIn("subtitle_source", out.stderr)
        self.assertFalse(self.path.exists(), "nothing should have been written")

    def test_set_rejects_an_invalid_value(self) -> None:
        out = self._cli("set", "subtitle_source", "clean")
        self.assertNotEqual(0, out.returncode)
        self.assertIn("clean", out.stderr)
        self.assertFalse(self.path.exists())

    def test_show_prints_the_effective_settings_and_the_path(self) -> None:
        out = self._cli("show")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn(str(self.path), out.stdout)
        self.assertIn("polished", out.stdout)
