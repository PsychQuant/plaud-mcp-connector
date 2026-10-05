#!/usr/bin/env python3
"""A one-recording fetch must not move the incremental listing cutoff (#65, verify R1).

`list_cutoff()` answers "where may a fresh listing stop paging?" from the newest
`complete` record, and only once a full sweep was recorded. That reading assumes
every `complete` record was reached BY WALKING THE LISTING. `fetch_one.py` breaks
the assumption: it fetches one recording that is not in a walked listing at all.

Reproduced during verify, with nothing but `cache.py`: a cache whose newest
record is from March and which had a full sweep reports a cutoff of 2026-02-28;
after one single-recording fetch of an October recording it reports 2026-10-01.
The next incremental `plaud-download` stops at the first page older than that, and
every recording between March and October is skipped — no error, no count.
`list_cutoff`'s own docstring records the same trap: `complete` says one
transcript came down whole, and says nothing about whether the listing was walked.

The fix is a flag on the record, `single_fetch`, set by `put --single-fetch` and
ignored when computing the cutoff. A later `put` WITHOUT the flag (a sync that
did walk the listing and reached that recording) replaces the record and counts
again — correct, because that walk did cover it.

These run the real `cache.py` as a subprocess against an isolated cache dir.
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


class SingleFetchCutoffTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="plaud-single-fetch-")
        self.addCleanup(tmp.cleanup)
        self.env = dict(os.environ)
        self.env["PLAUD_CACHE_DIR"] = str(pathlib.Path(tmp.name) / "cache")

    def cache(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(CACHE_PY), *args], input=stdin,
                              capture_output=True, text=True, env=self.env, timeout=30)

    def put(self, rec_id: str, created_at: str, *extra: str) -> None:
        proc = self.cache("put", "--id", rec_id, "--created-at", created_at,
                          "--complete", "true", "--last-cursor", "", *extra,
                          stdin="[00:00:01] Speaker 1: hi\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def cutoff(self) -> str:
        return self.cache("status", "--list-cutoff").stdout.strip()

    def swept_cache_with_old_record(self) -> str:
        self.put("old1", "2026-03-01T09:00:00")
        self.assertEqual(self.cache("mark-full-sweep").returncode, 0)
        before = self.cutoff()
        self.assertEqual(before, "2026-02-28T09:00:00")  # March minus the safety margin
        return before


class TestSingleFetchDoesNotMoveTheCutoff(SingleFetchCutoffTestCase):
    def test_a_single_fetch_record_is_ignored_when_computing_the_cutoff(self):
        before = self.swept_cache_with_old_record()
        self.put("new1", "2026-10-02T09:00:00", "--single-fetch")
        self.assertEqual(self.cutoff(), before)

    def test_an_ordinary_put_still_moves_the_cutoff(self):
        """Guards the fix against over-reach: records written by a listing walk count."""
        self.swept_cache_with_old_record()
        self.put("new1", "2026-10-02T09:00:00")
        self.assertEqual(self.cutoff(), "2026-10-01T09:00:00")

    def test_a_later_walk_that_reaches_the_recording_makes_it_count(self):
        """A sync that listed its way to this recording replaces the record without
        the flag — and that walk really did cover everything up to its date."""
        self.swept_cache_with_old_record()
        self.put("new1", "2026-10-02T09:00:00", "--single-fetch")
        self.put("new1", "2026-10-02T09:00:00")  # what plaud-download's put writes
        self.assertEqual(self.cutoff(), "2026-10-01T09:00:00")

    def test_a_cache_holding_only_single_fetch_records_has_no_cutoff(self):
        """No listing evidence at all -> None -> the next sync walks everything."""
        self.put("only1", "2026-10-02T09:00:00", "--single-fetch")
        self.assertEqual(self.cache("mark-full-sweep").returncode, 0)
        self.assertEqual(self.cutoff(), "")


if __name__ == "__main__":
    unittest.main()
