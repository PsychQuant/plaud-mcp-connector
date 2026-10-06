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
        self.assertIn("searched 2 cached recordings, covering 2026-09 → 2026-10 (2)", out)

    def test_no_match_says_the_same_thing(self):
        """The case that matters: 'no match' over a partial cache is not 'never said'."""
        out = self.cache_py("search", "nonexistent-term").stdout
        self.assertIn("no match", out)
        self.assertIn("searched 2 cached recordings, covering 2026-09 → 2026-10 (2)", out)

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


class TestTheScopeLineShowsGaps(CacheCase):
    """R3: under range download a cache is a few separate pieces, so one min-to-max span
    reads as continuous coverage and a search that missed March looks like it covered it."""

    def setUp(self) -> None:
        super().setUp()
        for rid, when in (("recJan", "2026-01-05"), ("recSep", "2026-09-20"), ("recOct", "2026-10-01")):
            self.put(rid, f"{when}T09:00:00.000Z", f"[00:00 - 00:04] Speaker 1: word in {rid}\n")

    def test_separate_months_are_listed_separately_and_the_gap_is_not_covered(self):
        out = self.cache_py("search", "word").stdout
        self.assertIn("2026-01 (1)", out)
        self.assertIn("2026-09 → 2026-10 (2)", out)
        for absent in ("2026-02", "2026-03", "2026-05", "2026-08"):
            self.assertNotIn(absent, out, "a month nothing was downloaded for must not appear")
        self.assertIn("months not listed have nothing downloaded", out)

    def test_status_shows_the_same_pieces_not_a_single_span(self):
        out = self.cache_py("status").stdout
        self.assertIn("2026-01 (1)", out)
        self.assertNotIn("2026-01 → 2026-10", out)


class TestTheScopeLineCountsWhatWasSearched(CacheCase):
    def test_a_manifest_entry_whose_file_is_gone_is_not_counted_as_searched(self):
        self.put("recA", "2026-09-01T09:00:00.000Z", "[00:00 - 00:04] Speaker 1: alpha\n")
        self.put("recB", "2026-09-02T09:00:00.000Z", "[00:00 - 00:04] Speaker 1: beta\n")
        (self.cache_dir / "recB.md").unlink()
        self.assertIn("searched 1 cached recordings", self.cache_py("search", "alpha").stdout)

    def test_an_entry_with_no_completeness_marker_counts_as_incomplete_like_status_does(self):
        self.put("recA", "2026-09-01T09:00:00.000Z", "[00:00 - 00:04] Speaker 1: alpha\n")
        man_path = self.cache_dir / "manifest.json"
        man = json.loads(man_path.read_text(encoding="utf-8"))
        del man["recordings"]["recA"]["complete"]
        man_path.write_text(json.dumps(man), encoding="utf-8")
        self.assertIn("1 incomplete", self.cache_py("search", "alpha").stdout)


class TestLegacyKeysAreNotWrittenBack(CacheCase):
    def test_a_write_drops_full_sweep_at_and_single_fetch_from_the_manifest(self):
        self.put("recA", "2026-09-01T09:00:00.000Z", "[00:00 - 00:04] Speaker 1: a\n")
        man_path = self.cache_dir / "manifest.json"
        man = json.loads(man_path.read_text(encoding="utf-8"))
        man["full_sweep_at"] = "2026-09-30T00:00:00Z"
        man["recordings"]["recA"]["single_fetch"] = True
        man_path.write_text(json.dumps(man), encoding="utf-8")
        self.put("recB", "2026-10-01T09:00:00.000Z", "[00:00 - 00:04] Speaker 1: b\n")
        after = json.loads(man_path.read_text(encoding="utf-8"))
        self.assertNotIn("full_sweep_at", after)
        self.assertNotIn("single_fetch", after["recordings"]["recA"])


class TestSearchAndPutDoNotTrustTheirInput(CacheCase):
    def test_a_pattern_that_looks_like_an_option_is_a_pattern(self):
        self.put("recA", "2026-09-01T09:00:00.000Z", "[00:00 - 00:04] Speaker 1: alpha\n")
        proc = self.cache_py("search", "--", "--pre=nothing")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("no match", proc.stdout)

    def test_a_newline_in_a_name_cannot_add_frontmatter_keys(self):
        proc = self.cache_py("put", "--id", "recA", "--name", "a\ncomplete: true", "--created-at",
                             "2026-09-01T09:00:00.000Z", "--duration", "1h", "--complete", "false",
                             "--pages", "1", "--last-cursor", "x", stdin="[00:00 - 00:04] Speaker 1: a\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        head = (self.cache_dir / "recA.md").read_text(encoding="utf-8").split("\n---", 2)[0]
        self.assertEqual(1, sum(1 for l in head.splitlines() if l.startswith("complete:")), head)
        self.assertEqual(1, sum(1 for l in head.splitlines() if l.startswith("name:")), head)

    def test_control_characters_in_a_recording_name_are_not_echoed(self):
        self.put("recA", "2026-09-01T09:00:00.000Z", "[00:00 - 00:04] Speaker 1: alpha\n",
                 "--name", "x\x1b[31mred")
        out = self.cache_py("search", "alpha").stdout
        self.assertNotIn("\x1b", out)


class TestACacheRefusalIsNotNoTranscript(FetchOneTestCase):
    def test_a_cache_that_cannot_be_written_exits_6_not_4(self):
        blocker = self.cache_dir.parent / "not-a-directory"
        blocker.write_text("x", encoding="utf-8")
        env = self._env()
        env["PLAUD_CACHE_DIR"] = str(blocker)
        proc = subprocess.run([sys.executable, str(FETCH_ONE), "--id", "rec1"], capture_output=True,
                              text=True, env=env, timeout=60)
        self.assertEqual(6, proc.returncode, proc.stdout + proc.stderr)


class TestPutFromAJsonFile(CacheCase):
    """Plaud's text goes in a file the model writes, not on a shell command line."""

    def write(self, rec_id: str = "recA", **fields) -> pathlib.Path:
        folder = self.cache_dir.parent / "incoming"
        folder.mkdir(exist_ok=True)
        path = folder / f"{rec_id}.json"
        path.write_text(json.dumps(fields), encoding="utf-8")
        return path

    def test_every_field_is_taken_from_the_file_and_the_file_is_removed(self):
        path = self.write(name="Budget $(touch pwned) `x` 'q' \"d\"", created_at="2026-09-01T09:00:00.000Z",
                          duration=3600000, complete=True, pages=2, last_cursor=None,
                          body="[00:00 - 00:04] Speaker 1: alpha\n")
        proc = self.cache_py("put", "--id", "recA", "--json", str(path))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        man = json.loads((self.cache_dir / "manifest.json").read_text(encoding="utf-8"))["recordings"]["recA"]
        self.assertIn("$(touch pwned)", man["name"])
        self.assertEqual(man["pages"], 2)
        self.assertIs(man["complete"], True)
        self.assertIn("alpha", (self.cache_dir / "recA.md").read_text(encoding="utf-8"))
        self.assertFalse(path.exists(), "the incoming file holds third-party speech and must not linger")

    def test_a_refused_put_leaves_the_file_and_the_cache_alone(self):
        full = dict(complete=True, last_cursor=None)
        for label, fields in (("unknown key", dict(body="x", evil="1", **full)), ("no body", dict(name="n", **full)),
                              ("bad pages", dict(body="x", pages="two", **full)),
                              ("bool pages", dict(body="x", pages=True, **full)),
                              ("no complete", dict(body="x", last_cursor=None)),
                              ("no last_cursor", dict(body="x", complete=True))):
            with self.subTest(label):
                path = self.write(**fields)
                proc = self.cache_py("put", "--id", "recA", "--json", str(path))
                self.assertNotEqual(proc.returncode, 0)
                self.assertTrue(path.exists())
                self.assertFalse((self.cache_dir / "recA.md").exists())

    def test_the_file_must_be_named_for_the_id_and_live_in_incoming(self):
        """Otherwise --json is a way to delete or read any JSON file, and a wrong file name
        stores recording B's text under recording A and then deletes B's file."""
        full = dict(body="x", complete=True, last_cursor=None)
        other = self.write("recB", **full)
        proc = self.cache_py("put", "--id", "recA", "--json", str(other))
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(other.exists())
        stray = self.cache_dir.parent / "recA.json"
        stray.write_text(json.dumps(full), encoding="utf-8")
        proc = self.cache_py("put", "--id", "recA", "--json", str(stray))
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(stray.exists())

    def test_a_missing_file_with_the_right_name_reaches_the_unreadable_branch(self):
        (self.cache_dir.parent / "incoming").mkdir(exist_ok=True)
        proc = self.cache_py("put", "--id", "recA", "--json", str(self.cache_dir.parent / "incoming" / "recA.json"))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("No such file", proc.stderr)

    def test_only_the_one_real_incoming_folder_is_accepted(self):
        """R6: a name check alone accepted any folder called incoming and, through a symlink,
        read and then deleted a file somewhere else."""
        full = dict(body="x", complete=True, last_cursor=None)
        root = self.cache_dir.parent
        elsewhere = root / "elsewhere" / "incoming"
        elsewhere.mkdir(parents=True)
        (elsewhere / "recA.json").write_text(json.dumps(full), encoding="utf-8")
        real = root / "realdir"
        real.mkdir()
        (real / "recA.json").write_text(json.dumps(full), encoding="utf-8")
        (root / "incoming").symlink_to(real, target_is_directory=True)     # the right folder name, a link
        target = root / "target.json"
        target.write_text(json.dumps(full), encoding="utf-8")
        for label, path, survivor in (("another incoming folder", elsewhere / "recA.json", elsewhere / "recA.json"),
                                      ("symlinked folder", root / "incoming" / "recA.json", real / "recA.json"),
                                      ("dotdot", root / "elsewhere" / ".." / "incoming" / "recA.json", real / "recA.json")):
            with self.subTest(label):
                proc = self.cache_py("put", "--id", "recA", "--json", str(path))
                self.assertNotEqual(proc.returncode, 0, proc.stdout)
                self.assertTrue(survivor.exists(), "a refused put must not delete anything")
                self.assertFalse((self.cache_dir / "recA.md").exists())
        (root / "incoming").unlink()
        (root / "incoming").mkdir()
        (root / "incoming" / "recA.json").symlink_to(target)               # the right name, a link
        proc = self.cache_py("put", "--id", "recA", "--json", str(root / "incoming" / "recA.json"))
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse((self.cache_dir / "recA.md").exists())

    def test_deeply_nested_json_is_an_error_not_a_traceback(self):
        folder = self.cache_dir.parent / "incoming"
        folder.mkdir(exist_ok=True)
        (folder / "recA.json").write_text("[" * 100000, encoding="utf-8")
        proc = self.cache_py("put", "--id", "recA", "--json", str(folder / "recA.json"))
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("Traceback", proc.stderr)

    def test_a_lone_surrogate_is_refused_before_an_existing_file_is_touched(self):
        self.put("recA", "2026-09-01T09:00:00.000Z", "[00:00 - 00:04] Speaker 1: keep me\n")
        path = self.write("recA", body="bad \ud800 text", complete=True, last_cursor=None)
        proc = self.cache_py("put", "--id", "recA", "--json", str(path))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("keep me", (self.cache_dir / "recA.md").read_text(encoding="utf-8"))

    def test_a_missing_or_malformed_file_is_an_error_not_a_traceback(self):
        bad = self.cache_dir.parent / "incoming"
        bad.mkdir(exist_ok=True)
        bad = bad / "recA.json"
        bad.write_text("{not json", encoding="utf-8")
        for target in (str(bad), str(self.cache_dir.parent / "incoming" / "recB.json")):
            proc = self.cache_py("put", "--id", "recA", "--json", target)
            self.assertNotEqual(proc.returncode, 0)
            self.assertNotIn("Traceback", proc.stderr)

    def test_a_summary_comes_from_the_file_too(self):
        self.put("recA", "2026-09-01T09:00:00.000Z", "[00:00 - 00:04] Speaker 1: alpha\n")
        path = self.write("recA", body="the summary text")
        proc = self.cache_py("put", "--id", "recA", "--kind", "summary", "--json", str(path))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("the summary text", (self.cache_dir / "summaries" / "recA.md").read_text(encoding="utf-8"))
        self.assertFalse(path.exists())


class TestTheScopeLineIsHonestAboutWhatItCannotSay(CacheCase):
    def test_a_recording_that_survives_only_as_a_summary_is_counted(self):
        self.put("recA", "2026-09-01T09:00:00.000Z", "[00:00 - 00:04] Speaker 1: alpha\n")
        self.cache_py("put", "--id", "recA", "--kind", "summary", stdin="only the summary says zebra")
        (self.cache_dir / "recA.md").unlink()
        out = self.cache_py("search", "zebra").stdout
        self.assertIn("zebra", out)
        self.assertIn("searched 1 cached recordings", out)

    def test_undated_recordings_are_counted_rather_than_dropped(self):
        self.put("recA", "2026-09-01T09:00:00.000Z", "[00:00 - 00:04] Speaker 1: alpha\n")
        self.put("recB", "", "[00:00 - 00:04] Speaker 1: beta\n")
        self.put("recC", "2026-13-01T09:00:00.000Z", "[00:00 - 00:04] Speaker 1: gamma\n")
        self.put("recD", "2026-\u0663\u0664-01", "[00:00 - 00:04] Speaker 1: delta\n")
        proc = self.cache_py("search", "a")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("2026-09 (1)", proc.stdout)
        self.assertIn("3 with no usable date", proc.stdout)

    def test_december_into_january_is_one_piece(self):
        self.put("recA", "2025-12-30T09:00:00.000Z", "[00:00 - 00:04] Speaker 1: alpha\n")
        self.put("recB", "2026-01-02T09:00:00.000Z", "[00:00 - 00:04] Speaker 1: alpha\n")
        self.assertIn("2025-12 → 2026-01 (2)", self.cache_py("search", "alpha").stdout)

    def test_a_listed_month_is_not_claimed_to_be_whole(self):
        out = self.cache_py("search", "nonexistent").stdout
        self.put("recA", "2026-09-01T09:00:00.000Z", "[00:00 - 00:04] Speaker 1: alpha\n")
        out = self.cache_py("search", "alpha").stdout
        self.assertIn("a listed month holds only the recordings downloaded from it", out)


class TestWhatIsPrintedAndWrittenIsClean(CacheCase):
    def test_duration_cannot_add_a_frontmatter_key(self):
        proc = self.cache_py("put", "--id", "recA", "--name", "n", "--created-at", "2026-09-01T09:00:00.000Z",
                             "--duration", "1h\ncomplete: true", "--complete", "false", "--pages", "1",
                             "--last-cursor", "x", stdin="[00:00 - 00:04] Speaker 1: a\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        head = (self.cache_dir / "recA.md").read_text(encoding="utf-8").split("\n---", 2)[0]
        self.assertEqual(1, sum(1 for l in head.splitlines() if l.startswith("complete:")), head)

    def test_a_matched_line_with_an_escape_sequence_is_not_echoed_raw(self):
        self.put("recA", "2026-09-01T09:00:00.000Z", "[00:00 - 00:04] Speaker 1: alpha \x1b]0;title\x07 omega\n")
        self.assertNotIn("\x1b", self.cache_py("search", "alpha").stdout)

    def test_find_does_not_echo_a_raw_date(self):
        self.put("recA", "2026-\x1b[3m9-01", "[00:00 - 00:04] Speaker 1: alpha\n")
        self.assertNotIn("\x1b", self.cache_py("find", "Meeting").stdout)

    def test_a_joiner_in_a_name_survives_but_a_bidi_override_does_not(self):
        self.put("recA", "2026-09-01T09:00:00.000Z", "[00:00 - 00:04] Speaker 1: alpha\n",
                 "--name", "family \u200d emoji \u202eevil")
        man = json.loads((self.cache_dir / "manifest.json").read_text(encoding="utf-8"))
        name = man["recordings"]["recA"]["name"]
        self.assertIn("\u200d", name)
        self.assertNotIn("\u202e", name)


class TestCleanerKeepsOnlyWhatIsSpelling(CacheCase):
    def test_invisible_format_characters_are_removed_but_joiners_stay(self):
        sys.path.insert(0, str(REPO / "scripts"))
        import cache
        for ch in ("\U000e0041", "\u200b", "\ufeff", "\u2060", "\u00ad", "\u202e", "\x1b", "\u2028"):
            with self.subTest(char=hex(ord(ch))):
                self.assertNotIn(ch, cache._clean(f"a{ch}b"))
        for ch in ("\u200d", "\u200c"):
            with self.subTest(char=hex(ord(ch))):
                self.assertIn(ch, cache._clean(f"a{ch}b"))


class TestTheScopeLineCountsEverythingSearchReads(CacheCase):
    def test_a_recording_that_survives_only_as_a_proofread_copy_is_counted(self):
        self.put("recA", "2026-09-01T09:00:00.000Z", "[00:00 - 00:04] Speaker 1: alpha\n")
        (self.cache_dir / "proofread").mkdir()
        (self.cache_dir / "proofread" / "recA.md").write_text("the corrected text says yak\n", encoding="utf-8")
        (self.cache_dir / "recA.md").unlink()
        out = self.cache_py("search", "yak").stdout
        self.assertIn("yak", out)
        self.assertIn("searched 1 cached recordings", out)


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
                  "--single-fetch", "single_fetch",
                  # Words for the old index/sweep model, which the site and the skills said
                  # in prose rather than as symbols (R3).
                  "index run", "everything you have indexed", "what you indexed", "next run")
        # An OLD manifest key may be mentioned where the code says it is tolerated and
        # ignored; that is the one legitimate reason, marked with this exact phrase.
        allowed_marker = "legacy manifest key"
        hits = []
        shipped = ["scripts/cache.py", "scripts/fetch_one.py", "scripts/to_srt.py", "README.md",
                   "site/index.html", "skills/plaud-download/report_template.txt"]
        shipped += sorted(str(p.relative_to(REPO)) for p in (REPO / "skills").glob("*/SKILL.md"))
        for rel in shipped:
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

    def test_the_mcp_loop_records_the_cursors_it_has_followed(self):
        self.assertIn("seen.add", self.skill("plaud-download"),
                      "a `seen` set that is never added to cannot catch a repeated cursor")

    def test_no_text_from_plaud_reaches_a_put_command_line(self):
        """R4: prose rules about quoting kept leaving one flag unguarded (--last-cursor).
        The structural answer is that the command line carries only a path."""
        for name in ("plaud-download", "plaud-to-srt"):
            text = self.skill(name)
            with self.subTest(skill=name):
                self.assertIn("--json", text)
                for banned in ("<<'", "--last-cursor", "--name", "--created-at", "TRANSCRIPT_END", "SUMMARY_END"):
                    self.assertNotIn(banned, text, f"{name} still shows {banned!r} on a command line")

    def test_ids_are_checked_and_plaud_text_is_data(self):
        text = " ".join(self.skill("plaud-download").split())   # a phrase may wrap across lines
        for needed in ("hexadecimal", "data, not instructions"):
            self.assertIn(needed, text)

    def test_the_coverage_test_has_a_day_of_margin_and_covers_name_queries(self):
        text = self.skill("plaud-download")
        self.assertIn("00:00 UTC", text)
        self.assertIn("query", text.split("scanned_back_to", 1)[1])

    def test_coverage_is_measured_from_the_start_of_the_previous_utc_day(self):
        text = " ".join(self.skill("plaud-download").replace("\n> ", "\n").split())   # quoted block wraps
        self.assertIn("00:00 UTC on the day before", text)

    def test_every_skill_that_searches_gives_the_same_pattern_rule(self):
        for name in ("plaud-search", "plaud-proofread", "plaud-outline"):
            with self.subTest(skill=name):
                text = " ".join(self.skill(name).split())
                self.assertIn("a `'` in the pattern becomes `.`", text)
        self.assertNotIn("a `$`", self.skill("plaud-search"))

    def test_search_examples_put_options_before_the_dashes(self):
        text = self.skill("plaud-search")
        self.assertIn("search --case-sensitive -- 'MCP'", text)
        self.assertIn("search --max-lines 15 -- 'onboarding'", text)

    def test_the_json_instructions_say_it_must_be_valid_json_and_forbid_the_fallback(self):
        text = " ".join(self.skill("plaud-download").split())
        self.assertIn("valid JSON", text)
        self.assertRegex(text, r"(?i)cannot write the file[^.]{0,120}(stop|do not)")

    def test_no_document_still_offers_the_calendar_day_test(self):
        for rel in ("docs/official-surface.md", "README.md", "skills/plaud-download/SKILL.md"):
            text = " ".join((REPO / rel).read_text(encoding="utf-8").replace("\n> ", "\n").replace("`", "").split())
            with self.subTest(file=rel):
                self.assertNotIn("is on or before its date_from", text)
                self.assertNotIn("four hours short", text)

    def test_both_mcp_skills_say_valid_json_and_stop_without_the_write_tool(self):
        for name in ("plaud-download", "plaud-to-srt"):
            text = " ".join(self.skill(name).split())
            with self.subTest(skill=name):
                self.assertIn("valid JSON", text)
                self.assertRegex(text, r"(?i)cannot write the file[^.]{0,120}(stop|do not)")

    def test_the_end_of_the_library_is_covered_not_unproven(self):
        text = " ".join(self.skill("plaud-download").split())
        self.assertIn("empty page", text)
        self.assertRegex(text, r"empty page[^.]{0,200}(library|everything)[^.]{0,120}covered")
        self.assertNotRegex(text, r"stopped for any of those reasons")

    def test_both_skills_that_call_fetch_one_know_exit_6(self):
        for name in ("plaud-download", "plaud-to-srt"):
            with self.subTest(skill=name):
                text = self.skill(name)
                self.assertRegex(text, r"\|\s*`?6`?\s*\|")

    def test_the_search_templates_put_the_pattern_after_dashes(self):
        self.assertIn("search -- '<pattern>'", self.skill("plaud-search"))

    def test_the_fallback_walk_says_created_at_is_utc_too(self):
        text = " ".join(self.skill("plaud-download").split())
        self.assertRegex(text, r"created_at[^.]{0,80}UTC[^.]{0,200}(day|margin)")

    def test_the_cli_only_extras_and_the_older_summary_gap_are_said_plainly(self):
        text = self.skill("plaud-download")
        self.assertRegex(text, r"(?is)polished.{0,200}only.{0,60}CLI|only.{0,60}CLI.{0,200}polished")
        self.assertIn("--force", text.split("What the extra two are for", 1)[1])

    def test_search_does_not_claim_the_scope_line_comes_before_everything(self):
        self.assertNotIn("before\nanything else", self.skill("plaud-search"))
        self.assertNotIn("plugin path it names", (REPO / "docs" / "naming.md").read_text(encoding="utf-8"))
        self.assertNotIn("before anything else", self.skill("plaud-search"))

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
