#!/usr/bin/env python3
"""Tests for the auto-update-disabled notice (#16).

Claude Code installs third-party marketplaces with auto-update **off**. A user
who installs this plugin and walks away stays on the version they installed —
including versions with the search bugs since fixed (#8, #15). Nothing tells
them, because the plugin they are running is the stale thing that would have
told them.

The notice is the only part of this we own. Writing to Claude Code's own
`known_marketplaces.json` is not ours to do — it is another program's runtime
state, it is rewritten by that program, and the documented toggle is its UI.
"""
import json
import pathlib
import subprocess
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "hooks" / "autoupdate-notice.sh"

MARKETPLACE = "plaud-mcp-connector"


def run(known_marketplaces: dict | None, env_extra: dict | None = None):
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "known_marketplaces.json"
        if known_marketplaces is not None:
            path.write_text(json.dumps(known_marketplaces))
        env = {"PATH": "/usr/bin:/bin", "PLAUD_KNOWN_MARKETPLACES": str(path)}
        env.update(env_extra or {})
        return subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env)


class TestAutoUpdateNotice(unittest.TestCase):
    def test_missing_autoupdate_key_warns(self):
        """The real failing shape: our entry exists with no autoUpdate field."""
        out = run({MARKETPLACE: {"source": {"repo": "x"}}}).stdout
        self.assertIn("auto-update", out.lower())
        self.assertIn("/plugin", out)

    def test_autoupdate_false_warns(self):
        self.assertIn("auto-update", run({MARKETPLACE: {"autoUpdate": False}}).stdout.lower())

    def test_autoupdate_true_is_silent(self):
        """Nagging someone who already fixed it is how a notice gets ignored."""
        self.assertEqual("", run({MARKETPLACE: {"autoUpdate": True}}).stdout.strip())

    def test_marketplace_not_installed_is_silent(self):
        """Running from a clone rather than an install — nothing to say."""
        self.assertEqual("", run({"some-other-marketplace": {"autoUpdate": False}}).stdout.strip())

    def test_missing_file_is_silent(self):
        """No Claude Code state at all. Silence beats guessing."""
        self.assertEqual("", run(None).stdout.strip())

    def test_malformed_file_is_silent_and_succeeds(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "km.json"
            p.write_text("{not json")
            r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True,
                               env={"PATH": "/usr/bin:/bin", "PLAUD_KNOWN_MARKETPLACES": str(p)})
            self.assertEqual(0, r.returncode)
            self.assertEqual("", r.stdout.strip())

    def test_opt_out_env_var_silences_it(self):
        out = run({MARKETPLACE: {"autoUpdate": False}},
                  {"PLAUD_CONNECTOR_NO_UPDATE_CHECK": "1"}).stdout
        self.assertEqual("", out.strip())

    def test_never_exits_nonzero(self):
        """A SessionStart hook that fails is a broken session, over a notice."""
        for state in (None, {MARKETPLACE: {}}, {MARKETPLACE: {"autoUpdate": True}}, {}):
            self.assertEqual(0, run(state).returncode, state)

    def test_notice_says_why_it_matters(self):
        """A notice that only says 'auto-update is off' gets dismissed. It has to
        say what going stale costs."""
        out = run({MARKETPLACE: {}}).stdout.lower()
        self.assertTrue(any(w in out for w in ("stale", "fixed", "version")), out)


if __name__ == "__main__":
    unittest.main()
