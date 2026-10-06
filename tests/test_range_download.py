#!/usr/bin/env python3
"""#74 — `plaud-download` downloads a range the user names; it no longer keeps the
whole library in sync.

What these pin, and why each is here rather than in the older cache tests:

* **The incremental machinery is gone.** A cutoff, a "full sweep" marker and a
  `--single-fetch` flag existed so a re-run could stop paging early. Nothing walks
  the listing now, so each is dead weight that a future reader would have to
  understand and might wire back in. They are asserted ABSENT.
* **Old caches still load.** Anyone who ran 0.11 has `full_sweep_at` and
  `single_fetch` in their manifest. Removing the feature must not make `status` or
  `search` crash on that file.
* **`fetch_one` also caches the summary.** The old indexer did, and `plaud-search`
  searches `summaries/`. Dropping it would silently narrow what a search can find,
  with no error to notice it by.
* **`search` says what it searched.** The cache now holds only what the user chose
  to download, so "no match" means "not in the part I fetched". A bare "no match" is
  a wrong answer that looks like a correct one.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_fetch_one import (  # noqa: E402
    CACHE_PY, FETCH_ONE, SUMMARY_MARK, FetchOneTestCase,
)

REPO = pathlib.Path(__file__).resolve().parent.parent


class TestFetchOneCachesTheSummary(FetchOneTestCase):
    def test_summary_is_cached_and_found_by_search(self):
        proc = self.fetch()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        status = self.cache_py("status").stdout
        self.assertIn("summaries : 1 of 1", status)
        # `search` shells out to rg/grep, which the harness's PATH (the fake CLI and
        # nothing else) does not have, so it runs with the normal PATH.
        found = subprocess.run(
            [sys.executable, str(CACHE_PY), "search", SUMMARY_MARK], capture_output=True, text=True,
            env=dict(os.environ, PLAUD_CACHE_DIR=str(self.cache_dir)), timeout=30)
        self.assertEqual(found.returncode, 0, found.stderr)
        self.assertIn("[summary]", found.stdout)

    def test_a_summary_failure_warns_and_keeps_the_transcript(self):
        proc = self.fetch(mode="summary_fail")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("summary", proc.stderr.lower())
        self.assertEqual(self.cached_count(), "1 recordings")
        self.assertNotIn("summaries :", self.cache_py("status").stdout)


class CacheCase(unittest.TestCase):
    """A real cache directory driven through the real `cache.py`."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="plaud-range-")
        self.addCleanup(tmp.cleanup)
        self.cache_dir = pathlib.Path(tmp.name) / "cache"
        self.env = dict(os.environ, PLAUD_CACHE_DIR=str(self.cache_dir))

    def cache_py(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(CACHE_PY), *args], input=stdin,
                              capture_output=True, text=True, env=self.env, timeout=30)

    def put(self, rec_id: str, created_at: str, body: str, *extra: str) -> None:
        proc = self.cache_py("put", "--id", rec_id, "--name", f"Meeting {rec_id}",
                             "--created-at", created_at, "--duration", "1h",
                             "--complete", "true", "--pages", "1", "--last-cursor", "",
                             *extra, stdin=body)
        self.assertEqual(proc.returncode, 0, proc.stderr)


class TestSearchStatesItsScope(CacheCase):
    def setUp(self) -> None:
        super().setUp()
        self.put("recA", "2026-09-01T09:00:00.000Z", "[00:00 - 00:04] Speaker 1: the budget moved\n")
        self.put("recB", "2026-10-01T09:00:00.000Z", "[00:00 - 00:04] Speaker 1: the schedule slipped\n")

    def test_a_hit_says_how_many_recordings_were_searched_and_their_dates(self):
        out = self.cache_py("search", "budget").stdout
        self.assertIn("searched 2 cached recordings, covering 2026-09-01 → 2026-10-01", out)

    def test_no_match_says_the_same_thing(self):
        """The case that matters: 'no match' over a partial cache is not 'never said'."""
        out = self.cache_py("search", "nonexistent-term").stdout
        self.assertIn("no match", out)
        self.assertIn("searched 2 cached recordings, covering 2026-09-01 → 2026-10-01", out)

    def test_both_paths_say_the_cache_holds_only_what_was_downloaded(self):
        for term in ("budget", "nonexistent-term"):
            with self.subTest(term=term):
                self.assertIn("only what has been downloaded", self.cache_py("search", term).stdout)

    def test_incomplete_recordings_are_counted(self):
        self.put("recC", "2026-10-02T09:00:00.000Z", "[00:00 - 00:04] Speaker 1: partial\n")
        man_path = self.cache_dir / "manifest.json"
        man = json.loads(man_path.read_text(encoding="utf-8"))
        man["recordings"]["recC"]["complete"] = False
        man_path.write_text(json.dumps(man), encoding="utf-8")
        out = self.cache_py("search", "budget").stdout
        self.assertIn("1 incomplete", out)


class TestAnOldManifestStillLoads(CacheCase):
    """0.11 wrote `full_sweep_at` and a per-recording `single_fetch`."""

    def setUp(self) -> None:
        super().setUp()
        self.put("recA", "2026-09-01T09:00:00.000Z", "[00:00 - 00:04] Speaker 1: the budget moved\n")
        man_path = self.cache_dir / "manifest.json"
        man = json.loads(man_path.read_text(encoding="utf-8"))
        man["full_sweep_at"] = "2026-09-30T00:00:00Z"
        man["recordings"]["recA"]["single_fetch"] = True
        man_path.write_text(json.dumps(man), encoding="utf-8")

    def test_every_reading_command_still_works(self):
        for args in (("status",), ("status", "--ids-only"), ("search", "budget"),
                     ("find", "Meeting recA"), ("show", "recA")):
            with self.subTest(args=args):
                proc = self.cache_py(*args)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)

    def test_status_no_longer_reports_a_full_sweep(self):
        self.assertNotIn("full sweep", self.cache_py("status").stdout)

    def test_a_write_does_not_carry_the_dead_keys_forward_as_meaningful(self):
        self.put("recB", "2026-10-01T09:00:00.000Z", "[00:00 - 00:04] Speaker 1: later\n")
        man = json.loads((self.cache_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertNotIn("single_fetch", man["recordings"]["recB"])


class TestTheIncrementalMachineryIsGone(CacheCase):
    def test_removed_subcommands_and_flags_are_refused(self):
        for args in (("should-stop-paging", "--cutoff", "2026-01-01T00:00:00Z"),
                     ("mark-full-sweep",),
                     ("status", "--list-cutoff")):
            with self.subTest(args=args):
                proc = self.cache_py(*args)
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)

    def test_put_no_longer_takes_single_fetch(self):
        proc = self.cache_py("put", "--id", "recA", "--name", "n", "--created-at",
                             "2026-09-01T09:00:00.000Z", "--duration", "1h", "--single-fetch",
                             stdin="[00:00 - 00:04] Speaker 1: x\n")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)

    def test_no_source_file_still_names_the_removed_mechanism(self):
        banned = ("should-stop-paging", "should_stop_paging", "mark-full-sweep", "mark_full_sweep",
                  "list-cutoff", "list_cutoff", "full_sweep_at", "SWEEP_STALE_DAYS", "page_verdict",
                  "--single-fetch", "single_fetch")
        # An OLD manifest key may be mentioned where the code says it is tolerated and
        # ignored; that is the one legitimate reason, marked with this exact phrase.
        allowed_marker = "legacy manifest key"
        hits = []
        for rel in ("scripts/cache.py", "scripts/fetch_one.py", "scripts/to_srt.py",
                    "skills/plaud-download/SKILL.md", "skills/plaud-search/SKILL.md",
                    "skills/plaud-to-srt/SKILL.md", "README.md"):
            for n, line in enumerate((REPO / rel).read_text(encoding="utf-8").splitlines(), 1):
                if any(b in line for b in banned) and allowed_marker not in line:
                    hits.append(f"{rel}:{n}: {line.strip()[:100]}")
        self.assertEqual([], hits)


class TestTheSkillsDescribeRangeDownload(unittest.TestCase):
    def skill(self, name: str) -> str:
        return (REPO / "skills" / name / "SKILL.md").read_text(encoding="utf-8")

    def test_download_asks_for_a_range_and_confirms_before_fetching(self):
        text = self.skill("plaud-download")
        self.assertIn("fetch_one.py", text)
        for needed in ("confirm", "--days", "--since"):
            self.assertIn(needed, text, f"plaud-download SKILL.md never mentions {needed!r}")
        self.assertNotIn("--all", text, "there is no whole-library mode any more")

    def test_download_reads_the_listing_own_accounting_not_a_guess(self):
        """Measured 2026-10-06: a filtered list_files scans only the newest 500 and says
        so in `scanned_back_to` / `complete` / `note`. `complete` is false whenever
        OLDER recordings exist, even for a range that lies wholly inside the scan, so
        the test for "is my range covered" is whether the scan reached its start."""
        text = self.skill("plaud-download")
        for needed in ("scanned_back_to", "date_from"):
            self.assertIn(needed, text)
        self.assertNotIn("looks like a cap", text, "the round-number heuristic was a guess")
        self.assertNotIn("split the window", text, "splitting cannot beat a 500-recording scan budget")

    def test_download_description_leads_with_the_verb(self):
        text = self.skill("plaud-download")
        head = text.split("---", 2)[1]
        self.assertIn("download", head.lower())
        self.assertNotIn("sync", head.lower().replace("synced", ""),
                         "the name was changed because this does not keep anything in sync")

    def test_search_must_quote_the_searched_line_and_never_answer_bare_no_match(self):
        text = self.skill("plaud-search")
        self.assertIn("searched", text)
        self.assertRegex(text, r"(?i)never (just )?(say|answer|report)[^.\n]*(no match|not found|nothing)")


if __name__ == "__main__":
    unittest.main()
