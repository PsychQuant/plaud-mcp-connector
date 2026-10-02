#!/usr/bin/env python3
"""`cache.py find` resolves a recording by NAME, and only by name (#65, verify R1).

`plaud-to-srt` used to resolve what the user said with `cache.py search`, which is
a full-text search: a recording's name is just the heading above its hits. So a
cached recording called "Weekly sync" whose talk mentions "Budget meeting" was the
single hit for "Budget meeting", the skill took it as the recording the user meant,
and the one actually called "Budget meeting" — not cached, so never looked for —
was never reached. Reproduced in verify. The skill then produced the wrong
recording's subtitles and looked like it had succeeded.

`find` matches the name stored in the manifest and nothing else. Exit 3 when
nothing matches, so a caller can branch on "go look in Plaud" without parsing text.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
CACHE_PY = REPO / "scripts" / "cache.py"


class TestFind(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="plaud-find-")
        self.addCleanup(tmp.cleanup)
        self.env = dict(os.environ)
        self.env["PLAUD_CACHE_DIR"] = str(pathlib.Path(tmp.name) / "cache")

    def cache(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(CACHE_PY), *args], input=stdin,
                              capture_output=True, text=True, env=self.env, timeout=30)

    def put(self, rec_id: str, name: str, created: str, body: str = "hello", complete: str = "true",
            duration: str = "600000") -> None:
        proc = self.cache("put", "--id", rec_id, "--name", name, "--created-at", created,
                          "--duration", duration, "--complete", complete, "--last-cursor",
                          "" if complete == "true" else "next-page",
                          stdin=f"[00:00:01] Speaker 1: {body}\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_a_name_match_is_found_case_insensitively(self):
        self.put("r1", "Weekly Sync", "2026-09-01T09:00:00")
        proc = self.cache("find", "weekly")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("r1", proc.stdout)
        self.assertIn("Weekly Sync", proc.stdout)
        self.assertIn("2026-09-01", proc.stdout)

    def test_words_in_a_transcript_body_do_not_make_a_name_match(self):
        """The R1 scenario, verbatim."""
        self.put("r1", "Weekly sync", "2026-09-01T09:00:00", body="we should plan the Budget meeting")
        proc = self.cache("find", "Budget meeting")
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertNotIn("r1", proc.stdout)
        # ...while the full-text search the skill used to rely on DOES hit it, which
        # is exactly why it was the wrong tool for resolving a name.
        self.assertIn("1 matches", self.cache("search", "Budget meeting").stdout)

    def test_several_matches_are_all_listed_newest_first(self):
        self.put("r1", "Budget review", "2026-08-01T09:00:00")
        self.put("r2", "Budget review", "2026-09-15T09:00:00")
        proc = self.cache("find", "budget")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertLess(proc.stdout.index("r2"), proc.stdout.index("r1"))

    def test_an_incomplete_entry_is_marked_so_the_caller_can_refetch_it(self):
        self.put("r1", "Budget review", "2026-08-01T09:00:00", complete="false")
        proc = self.cache("find", "budget")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("INCOMPLETE", proc.stdout)

    def test_no_match_exits_3(self):
        self.put("r1", "Weekly sync", "2026-09-01T09:00:00")
        self.assertEqual(self.cache("find", "nothing like this").returncode, 3)

    def test_an_empty_cache_exits_3_rather_than_failing(self):
        self.assertEqual(self.cache("find", "anything").returncode, 3)

    def test_a_blank_query_is_refused_not_treated_as_match_everything(self):
        self.put("r1", "Weekly sync", "2026-09-01T09:00:00")
        proc = self.cache("find", "   ")
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("r1", proc.stdout)


if __name__ == "__main__":
    unittest.main()
