"""A polished transcript marked stale must not feed subtitles in silence (#70).

`cache.py put` now marks a polish stale when the transcript is rewritten, but the one
reader where a stale polish does real damage, `to_srt`, chose the polish whenever the
file existed. Verify round 1 reproduced it: transcript says "approved", polish made from
it, transcript rewritten to "rejected", and the subtitles came out "approved." with
nothing on stderr. `to_srt` now says so; it does not change which file it uses, because
which transcript to subtitle from is the person's preference (#22), not a guess.

Every fixture is synthetic.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
CACHE = REPO / "scripts" / "cache.py"
SRT = REPO / "scripts" / "to_srt.py"

OLD = "[00:00:01 - 00:00:03] Speaker 1: the budget is approved\n"
NEW = "[00:00:01 - 00:00:03] Speaker 1: the budget is rejected\n"
POLISH = "[00:00:01 - 00:00:03] Speaker 1: The budget is approved.\n"


class StalePolish(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="plaud-stale-polish-")
        self.addCleanup(self._tmp.cleanup)
        self.root = pathlib.Path(self._tmp.name)
        self.env = {**os.environ, "PLAUD_CACHE_DIR": str(self.root / "cache"),
                    "PLAUD_CONFIG": str(self.root / "config.json")}

    def cache(self, *args, stdin=""):
        p = subprocess.run([sys.executable, str(CACHE), *args], input=stdin,
                           capture_output=True, text=True, env=self.env)
        self.assertEqual(0, p.returncode, p.stderr)

    def put_transcript(self, body):
        self.cache("put", "--id", "rec1", "--name", "n", "--created-at",
                   "2026-10-01T00:00:00Z", "--duration", "60000", stdin=body)

    def srt(self, *args):
        return subprocess.run([sys.executable, str(SRT), "rec1", *args],
                              capture_output=True, text=True, env=self.env)

    def with_polish(self):
        self.put_transcript(OLD)
        self.cache("put", "--id", "rec1", "--kind", "polish", stdin=POLISH)


class TestTheSubtitlesSayWhenThePolishIsOld(StalePolish):
    def test_a_stale_polish_still_feeds_the_subtitles_and_stderr_says_so(self):
        self.with_polish()
        self.put_transcript(NEW)
        p = self.srt()
        self.assertEqual(0, p.returncode, p.stderr)
        self.assertIn("approved", p.stdout)
        self.assertIn("earlier version", p.stderr)
        self.assertIn("--source verbatim", p.stderr)

    def test_choosing_the_raw_transcript_gets_no_warning(self):
        self.with_polish()
        self.put_transcript(NEW)
        p = self.srt("--source", "verbatim")
        self.assertIn("rejected", p.stdout)
        self.assertNotIn("earlier version", p.stderr)

    def test_a_current_polish_gets_no_warning(self):
        self.with_polish()
        p = self.srt()
        self.assertIn("approved", p.stdout)
        self.assertNotIn("earlier version", p.stderr)

    def test_a_manifest_that_cannot_be_read_gets_no_warning_and_no_crash(self):
        self.with_polish()
        (self.root / "cache" / "manifest.json").write_text("[]")
        p = self.srt()
        self.assertEqual(0, p.returncode, p.stderr)
        self.assertIn("approved", p.stdout)


if __name__ == "__main__":
    unittest.main()
