#!/usr/bin/env python3
"""Tests for scripts/cache.py (issue #12).

Stdlib-only (unittest), zero third-party dependencies — this is a Claude Code
plugin repo and must not require users to install pytest/ripgrep/etc. to run
its own test suite.

Every test isolates itself into a tmp directory and monkeypatches the
module-level cache.CACHE_DIR / cache.MANIFEST before touching anything. The
real cache at ~/.plaud-connector is never read or written. See the
"Testability note" in the issue #12 analysis: CACHE_DIR/MANIFEST are resolved
once at import time from PLAUD_CACHE_DIR, so setting the env var *after*
import has no effect on in-process calls — tests that call cache.cmd_* /
cache._* directly must monkeypatch the module attributes. Tests that invoke
the CLI as a subprocess (TestCliSmoke) instead set the env var for that fresh
process, which is the one place it actually takes effect.

Run with:
    python3 -m unittest discover -s tests -v
or:
    python3 tests/test_cache.py
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

# ---------------------------------------------------------------------------
# Load scripts/cache.py as a module under a private name (it is not part of a
# package, and "cache" is a generic enough name to risk colliding with
# something already on sys.path).
# ---------------------------------------------------------------------------
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE_PY = REPO_ROOT / "scripts" / "cache.py"

_spec = importlib.util.spec_from_file_location("plaud_cache_under_test", CACHE_PY)
cache = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(cache)

REAL_CACHE_DIR = pathlib.Path.home() / ".plaud-connector" / "cache"


class CacheTestCase(unittest.TestCase):
    """Base class: gives every test a private tmp cache dir and cleans up."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="plaud-cache-test-")
        self.addCleanup(self._tmpdir.cleanup)
        self.cache_dir = pathlib.Path(self._tmpdir.name) / "cache"

        # Guard rail: never let a test accidentally point at the real cache.
        self.assertNotEqual(self.cache_dir, REAL_CACHE_DIR)

        patch_dir = mock.patch.object(cache, "CACHE_DIR", self.cache_dir)
        patch_manifest = mock.patch.object(cache, "MANIFEST", self.cache_dir / "manifest.json")
        patch_dir.start()
        patch_manifest.start()
        self.addCleanup(patch_dir.stop)
        self.addCleanup(patch_manifest.stop)

    # -- helpers ------------------------------------------------------------

    def _capture_stdout(self, func, *args, **kwargs) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            func(*args, **kwargs)
        return buf.getvalue()

    def _put(self, rec_id: str, name: str = "", body: str = "hello\n",
              created_at: str = "", duration: str = "") -> None:
        ns = argparse.Namespace(id=rec_id, name=name, created_at=created_at, duration=duration)
        with mock.patch("sys.stdin", io.StringIO(body)):
            self._capture_stdout(cache.cmd_put, ns)

    def _hit_line(self, out: str, needle: str) -> str:
        """The result line carrying `needle`, excluding the trailing legend.

        Asserting `"[corrected]" in out` would pass on the legend alone — the
        sentence explaining the tag contains the tag. The claim being made is
        that the *hit line* is marked, so the assertion has to look there.
        """
        for line in out.splitlines():
            if line.lstrip().startswith("│") and needle in line:
                return line
        self.fail(f"no hit line containing {needle!r} in:\n{out}")

    def _search(self, pattern: str, have_rg: bool, case_sensitive: bool = False,
                max_lines: int = 5) -> str:
        ns = argparse.Namespace(pattern=pattern, case_sensitive=case_sensitive, max_lines=max_lines)
        with mock.patch.object(cache, "_have_rg", return_value=have_rg):
            return self._capture_stdout(cache.cmd_search, ns)


# ===========================================================================
# _safe_id — path-traversal defense
# ===========================================================================
class TestSafeId(unittest.TestCase):
    def test_rejects_ids_containing_a_slash(self):
        for bad in ("../../etc/passwd", "../secret", "a/b", "/etc/passwd"):
            with self.subTest(bad=bad):
                with self.assertRaises(SystemExit):
                    cache._safe_id(bad)

    def test_rejects_none(self):
        with self.assertRaises(SystemExit):
            cache._safe_id(None)

    def test_rejects_empty_string(self):
        with self.assertRaises(SystemExit):
            cache._safe_id("")

    def test_rejects_over_max_length(self):
        with self.assertRaises(SystemExit):
            cache._safe_id("a" * 129)

    def test_accepts_max_length_boundary(self):
        rec_id = "a" * 128
        self.assertEqual(cache._safe_id(rec_id), rec_id)

    def test_rejects_non_whitelisted_characters(self):
        for bad in ("中文", "a b", "a;rm -rf", "a$(whoami)", "a\nb"):
            with self.subTest(bad=bad):
                with self.assertRaises(SystemExit):
                    cache._safe_id(bad)

    def test_accepts_typical_ids(self):
        for ok in ("abc123", "rec-2026_01.01", "A.B-C_1"):
            self.assertEqual(cache._safe_id(ok), ok)

    def test_dotdot_passes_the_regex_but_stays_confined_by_path_join(self):
        # ".." fullmatches the whitelist (it contains no '/'), so _safe_id
        # alone does NOT reject it. It's only safe because every caller
        # builds the path as CACHE_DIR / f"{id}.md", which turns ".." into
        # the single literal path segment "...md", not a real ".." component.
        # This test pins that accidental safety explicitly.
        rec_id = cache._safe_id("..")
        self.assertEqual(rec_id, "..")
        candidate = pathlib.Path("/tmp/some-cache-root") / f"{rec_id}.md"
        self.assertEqual(candidate.parent, pathlib.Path("/tmp/some-cache-root"))
        self.assertEqual(candidate.name, "...md")


# ===========================================================================
# _save_manifest — atomicity
# ===========================================================================
class TestSaveManifest(CacheTestCase):
    def test_write_then_load_roundtrip(self):
        data = {"version": "1", "recordings": {"rec1": {"name": "中文 test", "chars": 10}}}
        cache._save_manifest(data)
        self.assertEqual(cache._load_manifest(), data)

    def test_no_tmp_file_left_behind_after_a_successful_save(self):
        cache._save_manifest({"version": "1", "recordings": {}})
        tmp = cache.MANIFEST.with_suffix(".json.tmp")
        self.assertTrue(cache.MANIFEST.exists())
        self.assertFalse(tmp.exists())

    def test_tmp_suffix_is_manifest_json_tmp(self):
        # Pins the assumption that MANIFEST already ends in ".json", so
        # with_suffix(".json.tmp") produces "manifest.json.tmp" and not
        # something like "manifest.tmp" that would silently break the
        # atomic-write scheme if MANIFEST were ever renamed.
        tmp = cache.MANIFEST.with_suffix(".json.tmp")
        self.assertEqual(tmp.name, "manifest.json.tmp")

    def test_preexisting_manifest_untouched_when_replace_fails(self):
        cache.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.MANIFEST.write_text('{"version": "1", "recordings": {"old": {}}}')
        original = cache.MANIFEST.read_text()

        with mock.patch.object(os, "replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                cache._save_manifest({"version": "1", "recordings": {"new": {}}})

        self.assertEqual(cache.MANIFEST.read_text(), original)
        # Documents current (not-fully-cleaned-up) behavior: the orphaned
        # .tmp file is left behind. Not corruption, but worth pinning.
        self.assertTrue(cache.MANIFEST.with_suffix(".json.tmp").exists())


# ===========================================================================
# _load_manifest — corrupt-JSON handling vs. wrong-shape-but-valid-JSON gap
# ===========================================================================
class TestLoadManifest(CacheTestCase):
    def test_missing_file_returns_default_shape(self):
        self.assertFalse(cache.MANIFEST.exists())
        self.assertEqual(cache._load_manifest(), {"version": "1", "recordings": {}})

    def test_corrupt_json_falls_back_to_default_with_stderr_warning(self):
        cache.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.MANIFEST.write_text("{not json")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            man = cache._load_manifest()
        self.assertEqual(man, {"version": "1", "recordings": {}})
        self.assertIn("corrupt", err.getvalue())

    def test_valid_json_wrong_shape_is_not_caught_here_but_breaks_the_caller(self):
        # Documents a real contract gap: _load_manifest only validates that
        # the file *parses* as JSON, not that it has the expected
        # {"recordings": {...}} shape. Valid-but-wrong-shape JSON degrades
        # ungracefully at the call site (KeyError), unlike the corrupt-JSON
        # path above which degrades cleanly.
        cache.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.MANIFEST.write_text("{}")
        with self.assertRaises(KeyError):
            cache.cmd_status(argparse.Namespace(ids_only=False))


# ===========================================================================
# cmd_status
# ===========================================================================
class TestCmdStatus(CacheTestCase):
    def test_empty_cache(self):
        out = self._capture_stdout(cache.cmd_status, argparse.Namespace(ids_only=False))
        self.assertIn("cached    : 0 recordings", out)
        self.assertNotIn("range", out)
        self.assertNotIn("transcript", out)

    def test_populated_cache_totals_and_date_range(self):
        self._put("rec1", name="One", body="hello world\n", created_at="2026-01-01T00:00:00+00:00")
        self._put("rec2", name="Two", body="foo bar baz\n", created_at="2026-03-01T00:00:00+00:00")
        out = self._capture_stdout(cache.cmd_status, argparse.Namespace(ids_only=False))
        self.assertIn("cached    : 2 recordings", out)
        self.assertIn("2026-01-01 → 2026-03-01", out)
        self.assertIn("characters indexed", out)

    def test_entry_missing_chars_field_silently_undercounts(self):
        self._put("rec1", name="One", body="hello world\n")
        man = cache._load_manifest()
        del man["recordings"]["rec1"]["chars"]
        cache._save_manifest(man)
        out = self._capture_stdout(cache.cmd_status, argparse.Namespace(ids_only=False))
        self.assertIn("0 characters indexed", out)

    def test_ids_only_flag_prints_sorted_ids(self):
        self._put("rec_b", body="x\n")
        self._put("rec_a", body="y\n")
        out = self._capture_stdout(cache.cmd_status, argparse.Namespace(ids_only=True))
        self.assertEqual(out.strip().splitlines(), ["rec_a", "rec_b"])


# ===========================================================================
# cmd_put
# ===========================================================================
class TestCmdPut(CacheTestCase):
    def test_rejects_empty_body_and_creates_no_cache_dir(self):
        ns = argparse.Namespace(id="rec1", name="", created_at="", duration="")
        with mock.patch("sys.stdin", io.StringIO("")):
            with self.assertRaises(SystemExit) as ctx:
                cache.cmd_put(ns)
        self.assertIn("empty transcript body", str(ctx.exception))
        self.assertFalse(cache.CACHE_DIR.exists())

    def test_rejects_whitespace_only_body(self):
        ns = argparse.Namespace(id="rec1", name="", created_at="", duration="")
        with mock.patch("sys.stdin", io.StringIO("   \n\n   ")):
            with self.assertRaises(SystemExit) as ctx:
                cache.cmd_put(ns)
        # Assert the message too, not just that *something* exited — otherwise a
        # refactor that raised SystemExit for an unrelated reason would still pass.
        self.assertIn("empty transcript body", str(ctx.exception))
        self.assertFalse(cache.CACHE_DIR.exists())

    def test_writes_file_and_manifest_entry(self):
        self._put("rec1", name="Meeting", body="hello world\n",
                   created_at="2026-01-01T00:00:00+00:00", duration="1000")
        path = cache.CACHE_DIR / "rec1.md"
        self.assertTrue(path.exists())
        man = cache._load_manifest()
        self.assertIn("rec1", man["recordings"])
        self.assertEqual(man["recordings"]["rec1"]["name"], "Meeting")
        self.assertEqual(man["recordings"]["rec1"]["duration_ms"], "1000")

    def test_name_with_double_quote_is_sanitized_to_single_quote(self):
        self._put("rec1", name='Say "hi"', body="hello\n")
        content = (cache.CACHE_DIR / "rec1.md").read_text()
        self.assertIn("name: \"Say 'hi'\"", content)

    def test_leading_blank_lines_in_body_survive_verbatim(self):
        self._put("rec1", name="X", body="\n\nhello\n\n")
        content = (cache.CACHE_DIR / "rec1.md").read_text()
        # front matter always ends with a "---\n" delimiter line immediately
        # followed by the body.
        body_part = content.split("---\n", 2)[-1]
        self.assertTrue(body_part.startswith("\n\nhello"))

    def test_recache_same_id_overwrites_not_appends(self):
        self._put("rec1", name="First", body="version one\n")
        self._put("rec1", name="Second", body="version two\n")
        content = (cache.CACHE_DIR / "rec1.md").read_text()
        self.assertIn("version two", content)
        self.assertNotIn("version one", content)
        man = cache._load_manifest()
        self.assertEqual(man["recordings"]["rec1"]["name"], "Second")
        self.assertEqual(len(man["recordings"]), 1)


# ===========================================================================
# cmd_search — the sharpest risk: rg / grep -rnE dual-path parity
# ===========================================================================
class TestCmdSearch(CacheTestCase):
    def test_empty_cache_exits_cleanly(self):
        ns = argparse.Namespace(pattern="x", case_sensitive=False, max_lines=5)
        with self.assertRaises(SystemExit) as ctx:
            cache.cmd_search(ns)
        self.assertIn("cache is empty", str(ctx.exception))

    def test_no_match_reports_cleanly_not_as_an_error(self):
        self._put("rec1", name="One", body="nothing relevant here\n")
        out = self._search("zzz_not_present_anywhere", have_rg=False)
        self.assertIn("no match for", out)

    def test_grep_and_rg_agree_on_alternation_pattern(self):
        """Regression test for the historical bug: BSD grep without -E treats
        '|' as a LITERAL character (basic regex), so
        search("預算|budget") against a transcript containing only "budget"
        used to silently report zero hits — a wrong answer indistinguishable
        from a correct empty result. Both engines must find the hit.

        The fixture deliberately contains only ONE arm of the alternation
        ("budget", not "預算|budget" as a literal string): a correct
        alternation match succeeds, a broken literal-'|' match finds nothing.
        """
        # name deliberately avoids either alternation arm ("預算" / "budget")
        # so the only possible hit is in the body — front matter must not
        # accidentally contribute a second match and muddy the count.
        self._put("rec1", name="Recording One", body="the budget was approved\n")
        pattern = "預算|budget"

        grep_out = self._search(pattern, have_rg=False)
        self.assertNotIn("no match for", grep_out)
        self.assertIn("1 matches in 1 recordings", grep_out)
        self.assertIn("budget", grep_out)

        if shutil.which("rg") is None:
            self.skipTest(
                "ripgrep is not installed on this machine (real, subprocess-level "
                "PATH lookup) — only the grep fallback path could be exercised. "
                "This is the required skip, not a failure: per issue #12 req #8."
            )

        rg_out = self._search(pattern, have_rg=True)
        self.assertNotIn("no match for", rg_out)
        self.assertIn("1 matches in 1 recordings", rg_out)
        self.assertIn("budget", rg_out)

    def test_grep_fallback_command_includes_dash_E_flag(self):
        """Cheap companion pin: a future refactor that accidentally drops -E
        (reintroducing the historical bug) fails this test immediately,
        without needing a real grep subprocess round-trip."""
        self._put("rec1", body="hello\n")
        fake_result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
        with mock.patch.object(cache, "_have_rg", return_value=False):
            with mock.patch.object(cache.subprocess, "run", return_value=fake_result) as mock_run:
                self._capture_stdout(
                    cache.cmd_search,
                    argparse.Namespace(pattern="hello", case_sensitive=False, max_lines=5),
                )
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[0], "grep")
        self.assertIn("-rnE", cmd)

    def test_max_lines_truncation(self):
        body = "\n".join(f"needle line {i}" for i in range(5)) + "\n"
        self._put("rec1", name="Many", body=body)
        out = self._search("needle", have_rg=False, max_lines=2)
        self.assertIn("… 3 more", out)

    def test_malformed_pattern_surfaces_as_a_real_error_not_a_silent_empty_result(self):
        self._put("rec1", body="hello world\n")
        with mock.patch.object(cache, "_have_rg", return_value=False):
            ns = argparse.Namespace(pattern="(", case_sensitive=False, max_lines=5)
            try:
                self._capture_stdout(cache.cmd_search, ns)
            except SystemExit as exc:
                self.assertIn("search failed", str(exc))
            else:
                self.skipTest(
                    "this machine's grep treats a bare '(' as a literal character "
                    "instead of an invalid-regex error; nothing to assert here"
                )

    def test_orphaned_md_file_without_manifest_entry_shows_unnamed_and_sorts_last(self):
        self._put("rec1", name="Known", body="needle here\n",
                   created_at="2026-05-01T00:00:00+00:00")
        # Simulates cmd_put's documented partial-failure gap: a .md file can
        # exist on disk with no corresponding manifest entry (e.g. if
        # _save_manifest failed after the file write already succeeded).
        (cache.CACHE_DIR / "rec2.md").write_text("---\nid: rec2\n---\nneedle here too\n")

        out = self._search("needle", have_rg=False)
        self.assertIn("(unnamed)", out)
        idx_known = out.index("Known")
        idx_unnamed = out.index("(unnamed)")
        self.assertLess(idx_known, idx_unnamed)


# ===========================================================================
# cmd_show
# ===========================================================================
class TestCmdShow(CacheTestCase):
    def test_nonexistent_id_exits_cleanly(self):
        with self.assertRaises(SystemExit) as ctx:
            cache.cmd_show(argparse.Namespace(id="ghost"))
        self.assertIn("not cached", str(ctx.exception))

    def test_unsafe_id_rejected_before_any_filesystem_check(self):
        with self.assertRaises(SystemExit) as ctx:
            cache.cmd_show(argparse.Namespace(id="../../etc/passwd"))
        self.assertIn("unsafe", str(ctx.exception))

    def test_existing_id_prints_exact_file_content(self):
        self._put("rec1", name="One", body="hello world\n")
        expected = (cache.CACHE_DIR / "rec1.md").read_text()
        out = self._capture_stdout(cache.cmd_show, argparse.Namespace(id="rec1"))
        self.assertEqual(out, expected)


# ===========================================================================
# CLI-level smoke tests — real subprocess, real PLAUD_CACHE_DIR env var
# ===========================================================================
class TestCliSmoke(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="plaud-cache-cli-test-")
        self.addCleanup(self._tmpdir.cleanup)
        cache_dir = pathlib.Path(self._tmpdir.name) / "cache"
        self.assertNotEqual(cache_dir, REAL_CACHE_DIR)
        self.env = dict(os.environ)
        self.env["PLAUD_CACHE_DIR"] = str(cache_dir)

    def _run(self, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(CACHE_PY), *args],
            input=input_text,
            capture_output=True,
            text=True,
            env=self.env,
            timeout=30,
        )

    def test_missing_required_pattern_arg_exits_nonzero_with_usage(self):
        proc = self._run("search")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("pattern", proc.stderr.lower())

    def test_status_on_a_fresh_cache_dir(self):
        proc = self._run("status")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("cached    : 0 recordings", proc.stdout)

    def test_full_round_trip_put_then_search_then_show(self):
        put = self._run("put", "--id", "rec1", "--name", "Meeting",
                         input_text="the budget was approved\n")
        self.assertEqual(put.returncode, 0, put.stderr)

        search = self._run("search", "budget")
        self.assertEqual(search.returncode, 0, search.stderr)
        self.assertIn("1 matches in 1 recordings", search.stdout)

        show = self._run("show", "rec1")
        self.assertEqual(show.returncode, 0, show.stderr)
        self.assertIn("the budget was approved", show.stdout)


# ===========================================================================
# Paging completeness (issue #8)
#
# `complete` decides whether plaud-grep warns that a search may have missed
# text. If it can be set wrongly without anything noticing, the warning never
# fires and a truncated cache looks authoritative — the same silent-wrong-answer
# shape as the grep BRE bug these tests exist for.
# ===========================================================================
class TestCursorExhaustion(unittest.TestCase):
    """The rule for 'is this cursor finished?', pinned as a function.

    Kept here rather than in the indexing skill so it is testable at all: a
    judgement made in skill prose at runtime cannot be asserted on.
    """

    def test_treats_empty_forms_as_exhausted(self) -> None:
        for raw in (None, "", "   ", "\n", "null", "NULL", "None", "undefined"):
            with self.subTest(raw=raw):
                self.assertTrue(cache._is_cursor_exhausted(raw))

    def test_falsy_looking_strings_are_still_real_cursors(self) -> None:
        # An opaque cursor may be "0" or "false". Reading those as terminators
        # would stop paging one page in and mark the result complete.
        for raw in ("0", "false", "False", "nil", "abc123", " x "):
            with self.subTest(raw=raw):
                self.assertFalse(cache._is_cursor_exhausted(raw))


class TestCompletenessTracking(CacheTestCase):
    def _put_paged(self, rec_id: str, *, complete: str = "true", pages: int = 1,
                   last_cursor=None, body: str = "hello\n", name: str = "") -> str:
        ns = argparse.Namespace(id=rec_id, name=name, created_at="", duration="",
                                complete=complete, pages=pages, last_cursor=last_cursor)
        with mock.patch("sys.stdin", io.StringIO(body)):
            return self._capture_stdout(cache.cmd_put, ns)

    def _manifest(self) -> dict:
        return cache._load_manifest()["recordings"]

    def _status(self, ids_only: bool = False) -> str:
        return self._capture_stdout(cache.cmd_status, argparse.Namespace(ids_only=ids_only))

    # -- the two meanings of "absent" ---------------------------------------

    def test_put_without_complete_attr_defaults_to_complete(self) -> None:
        # Regression guard for the AttributeError this nearly shipped with: the
        # bare `_put` helper builds a Namespace by hand, so argparse defaults are
        # not there. cmd_put must read it defensively, and absence means "an old
        # caller we trust", not "incomplete".
        self._put("rec1")
        self.assertIs(self._manifest()["rec1"]["complete"], True)

    def test_manifest_record_without_complete_key_counts_as_incomplete(self) -> None:
        # Opposite default, on purpose: a record written before paging existed
        # may stop at page one, so it must be re-fetched.
        self._put("old1")
        man = cache._load_manifest()
        del man["recordings"]["old1"]["complete"]          # simulate a v0.1.0 record
        cache._save_manifest(man)
        self.assertNotIn("old1", self._status(ids_only=True).split())
        self.assertIn("incomplete: 1", self._status())

    # -- the completeness claim is checked, not believed ---------------------

    def test_claiming_complete_with_a_live_cursor_is_downgraded(self) -> None:
        out = self._put_paged("liar", complete="true", last_cursor="still-alive")
        self.assertIs(self._manifest()["liar"]["complete"], False)
        self.assertIn("not exhausted", out)
        self.assertIn("INCOMPLETE", out)

    def test_claiming_complete_with_an_exhausted_cursor_is_honoured(self) -> None:
        for cursor in ("", None, "null"):
            with self.subTest(cursor=cursor):
                self._put_paged("ok1", complete="true", last_cursor=cursor)
                self.assertIs(self._manifest()["ok1"]["complete"], True)

    def test_incomplete_record_keeps_last_cursor_for_resume(self) -> None:
        # Without this a recording longer than the page cap can never finish:
        # every run would restart at page 1 and hit the cap again.
        self._put_paged("part1", complete="false", pages=3, last_cursor="cur-42")
        rec = self._manifest()["part1"]
        self.assertEqual(rec["last_cursor"], "cur-42")
        self.assertEqual(rec["pages"], 3)

    def test_complete_record_clears_last_cursor(self) -> None:
        self._put_paged("done1", complete="true", last_cursor="")
        self.assertIsNone(self._manifest()["done1"]["last_cursor"])

    # -- downstream consumers ------------------------------------------------

    def test_ids_only_omits_incomplete_so_the_indexer_refetches_them(self) -> None:
        self._put_paged("done1", complete="true", last_cursor="")
        self._put_paged("part1", complete="false", last_cursor="cur-1")
        self.assertEqual(self._status(ids_only=True).split(), ["done1"])

    def test_search_warns_only_for_records_marked_incomplete(self) -> None:
        self._put_paged("done1", complete="true", last_cursor="", body="budget talk\n")
        self._put_paged("part1", complete="false", last_cursor="c", body="budget again\n")
        out = self._search("budget", have_rg=False)
        self.assertEqual(out.count("partially indexed"), 1)
        # anchor the warning to the right recording, not just "it appears somewhere"
        self.assertIn("id part1", out.split("partially indexed")[0])

    def test_search_does_not_call_an_orphaned_md_partially_indexed(self) -> None:
        # No manifest entry at all is a lost manifest, not a half-fetched
        # transcript. Same falsy `.get("complete")`, different cause — labelling
        # it "partially indexed" would send the reader after the wrong problem.
        cache.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (cache.CACHE_DIR / "orphan.md").write_text("---\nid: orphan\n---\n\nbudget\n")
        out = self._search("budget", have_rg=False)
        self.assertIn("orphan", out)
        self.assertNotIn("partially indexed", out)


class TestProofreadAttribution(CacheTestCase):
    """Hits from proofread/ must be distinguishable from verbatim transcript.

    A corrected line reads exactly like a real one. If search does not say which
    it is, a caller quoting it presents an edit as testimony — the failure the
    proofreading pass itself is meant to make visible, not hide.
    """

    def _write_proofread(self, rec_id: str, body: str) -> None:
        d = cache.CACHE_DIR / "proofread"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{rec_id}.md").write_text(body)

    def test_hit_only_in_proofread_copy_still_finds_the_recording(self) -> None:
        # The whole point: ASR heard it wrong, so the raw transcript cannot match.
        self._put("rec1", name="Research", body="[00:01] A: 艾佛森 similarity\n")
        self._write_proofread("rec1", "[00:01] A: Iverson similarity\n")
        out = self._search("Iverson", have_rg=False)
        self.assertIn("Research", out)
        self.assertIn("[corrected]", self._hit_line(out, "Iverson similarity"))

    def test_the_tag_is_on_the_line_not_only_in_the_legend(self) -> None:
        # Regression guard for a false-passing assertion: dropping the per-line
        # tag left the legend behind, so a bare `assertIn` stayed green while the
        # thing it claimed to check was gone.
        self._put("rec1", name="Research", body="[00:01] A: verbatim budget\n")
        self._write_proofread("rec1", "[00:01] A: corrected budget\n")
        out = self._search("budget", have_rg=False)
        self.assertIn("[corrected]", self._hit_line(out, "corrected budget"))
        self.assertNotIn("[corrected]", self._hit_line(out, "verbatim budget"))

    def test_verbatim_hits_are_not_tagged(self) -> None:
        self._put("rec1", name="Research", body="[00:01] A: budget talk\n")
        out = self._search("budget", have_rg=False)
        self.assertNotIn("[corrected]", out)

    def test_explains_the_tag_when_one_is_shown(self) -> None:
        self._put("rec1", name="Research", body="[00:01] A: 艾佛森\n")
        self._write_proofread("rec1", "[00:01] A: Iverson\n")
        out = self._search("Iverson", have_rg=False)
        self.assertIn("not as what was said verbatim", out)

    def test_proofread_copy_groups_under_the_original_recording(self) -> None:
        # Same stem in a subdirectory must resolve to the same recording, not a
        # second "(unnamed)" entry.
        self._put("rec1", name="Research", body="[00:01] A: shared word\n")
        self._write_proofread("rec1", "[00:01] A: shared word\n")
        out = self._search("shared word", have_rg=False)
        self.assertIn("1 recordings", out)
        self.assertNotIn("(unnamed)", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)


# --------------------------------------------------------------------------
# Unicode canonical equivalence (#15)
#
# grep and ripgrep both compare bytes. `café` has two canonically-equivalent
# encodings — NFC (U+00E9) and NFD (U+0065 U+0301) — whose bytes differ, so a
# byte comparison reports "no match" on text that plainly contains the word.
# Byte equality was standing in for meaning equality; it is only ever correct
# for pure ASCII, which is why 174 tests passed over it.
#
# Both engines are exercised (`have_rg` True and False): the defect is in what
# we hand the engine, so a fix that only covers the grep path would leave every
# machine with ripgpre installed still wrong.
# --------------------------------------------------------------------------
import unicodedata  # noqa: E402

NFC = unicodedata.normalize("NFC", "café")
NFD = unicodedata.normalize("NFD", "café")


def _engines():
    """Which search engines this machine can actually exercise.

    `rg` is commonly a shell function or alias, which an interactive `command -v`
    finds and `subprocess` does not — the same trap that made an earlier
    root-cause read of the grep bug wrong. Only a real PATH executable counts;
    otherwise the ripgrep variant is reported as skipped rather than passing on
    a path that never ran.
    """
    engines = [False]
    if shutil.which("rg") is not None:
        engines.append(True)
    return engines


class TestNormalizePattern(unittest.TestCase):
    def test_ascii_pattern_is_returned_unchanged(self):
        """No pointless alternation: an ASCII pattern has one normal form, and
        rewriting it would change the user's regex for nothing."""
        self.assertEqual("budget", cache.normalize_pattern("budget"))

    def test_accented_pattern_expands_to_both_forms(self):
        out = cache.normalize_pattern(NFC)
        self.assertIn(NFC, out)
        self.assertIn(NFD, out)
        self.assertTrue(out.startswith("(") and out.endswith(")"), out)

    def test_expansion_is_the_same_whichever_form_is_given(self):
        """Someone typing NFD must get the same coverage as someone typing NFC."""
        self.assertEqual(
            sorted(cache.normalize_pattern(NFC).strip("()").split("|")),
            sorted(cache.normalize_pattern(NFD).strip("()").split("|")),
        )

    def test_regex_metacharacters_survive(self):
        """Normalization touches precomposed/decomposed characters only; it must
        not disturb ASCII metacharacters, or the user's regex changes meaning."""
        self.assertEqual("^bud.*get$", cache.normalize_pattern("^bud.*get$"))

    def test_alternation_pattern_stays_correct(self):
        """`a|b` wrapped becomes `(a|b|...)` — redundant but still correct."""
        out = cache.normalize_pattern(NFC + "|budget")
        self.assertIn("budget", out)
        self.assertIn(NFD, out)

    def test_empty_pattern_does_not_crash(self):
        self.assertEqual("", cache.normalize_pattern(""))

    def test_cjk_pattern_unchanged(self):
        """CJK here has no combining-mark decomposition; must not be rewritten."""
        self.assertEqual("預算", cache.normalize_pattern("預算"))


class TestSearchAcrossNormalizations(CacheTestCase):
    def test_nfc_query_finds_nfd_content(self):
        self._put("rec_nfd", body="on est allés au " + NFD + " hier")
        for rg in _engines():
            with self.subTest(have_rg=rg):
                self.assertIn("1 matches", self._search(NFC, have_rg=rg))

    def test_nfd_query_finds_nfc_content(self):
        self._put("rec_nfc", body="on est allés au " + NFC + " hier")
        for rg in _engines():
            with self.subTest(have_rg=rg):
                self.assertIn("1 matches", self._search(NFD, have_rg=rg))

    def test_both_forms_on_disk_are_both_found(self):
        """A cache written before this fix is a mixed bag; one query must reach
        all of it, which is why the fix is at the query end and not a migration.

        The NFD entry is written straight to disk, bypassing cmd_put, because
        cmd_put now normalises on write — going through it would produce two NFC
        files and quietly test nothing."""
        self._put("rec_a", body="le " + NFC + " du matin")
        cache.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (cache.CACHE_DIR / "rec_b.md").write_text("le " + NFD + " du soir\n")
        for rg in _engines():
            with self.subTest(have_rg=rg):
                self.assertIn("2 recordings", self._search(NFC, have_rg=rg))

    def test_unrelated_text_still_does_not_match(self):
        """The expansion must not become a catch-all."""
        self._put("rec_x", body="nothing relevant here")
        self.assertIn("no match", self._search(NFC, have_rg=False))


class TestPutNormalizesOnWrite(CacheTestCase):
    def test_body_is_stored_as_nfc(self):
        """Converging new writes on one form keeps entropy from growing; the
        query-side expansion is what keeps older mixed entries reachable."""
        self._put("rec_w", body="un " + NFD + " serré")
        stored = (cache.CACHE_DIR / "rec_w.md").read_text()
        self.assertIn(NFC, stored)


class TestRipgrepBranchGetsTheFix(CacheTestCase):
    """ripgrep is not installed here — `rg` on this machine is a shell function,
    invisible to subprocess — so the end-to-end tests above only ever exercise
    the grep branch. An acid test proved the consequence: deleting the fix from
    the ripgrep branch left the entire suite green.

    The remedy is not to install ripgrep but to assert on the property we
    actually own — the argv we hand the engine. That is verifiable anywhere,
    and it is what would be wrong if the branch were missed again.
    """

    def _captured_argv(self, pattern: str, have_rg: bool) -> list:
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

        self._put("rec_argv", body="le " + NFC + " du matin")
        ns = argparse.Namespace(pattern=pattern, case_sensitive=False, max_lines=5)
        with mock.patch.object(cache, "_have_rg", return_value=have_rg), \
             mock.patch.object(cache.subprocess, "run", side_effect=fake_run):
            self._capture_stdout(cache.cmd_search, ns)
        return captured["cmd"]

    def test_ripgrep_argv_carries_both_forms(self):
        argv = self._captured_argv(NFC, have_rg=True)
        self.assertEqual("rg", argv[0])
        joined = " ".join(argv)
        self.assertIn(NFC, joined)
        self.assertIn(NFD, joined)

    def test_grep_argv_carries_both_forms(self):
        argv = self._captured_argv(NFC, have_rg=False)
        self.assertEqual("grep", argv[0])
        joined = " ".join(argv)
        self.assertIn(NFC, joined)
        self.assertIn(NFD, joined)

    def test_ascii_argv_is_left_alone_on_both_engines(self):
        """A pattern with one normal form must reach the engine untouched."""
        for rg in (True, False):
            with self.subTest(have_rg=rg):
                self.assertIn("budget", self._captured_argv("budget", have_rg=rg))


# --------------------------------------------------------------------------
# Summaries in the cache (#20)
#
# What a person remembers is usually closer to the summary (the point) than to
# the transcript (speech with all its filler). Summaries were never cached, so
# plaud-grep could not reach them.
#
# A hit in a summary is AI-rewritten text, not something anybody said. It is
# labelled for the same reason `[corrected]` is: quoting it as verbatim speech
# would be wrong, and the reader cannot tell by looking.
# --------------------------------------------------------------------------
class TestSummaryAttribution(CacheTestCase):
    def _put_summary(self, rec_id: str, body: str) -> None:
        d = cache.CACHE_DIR / "summaries"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{rec_id}.md").write_text(body + "\n")

    def test_summary_hit_is_labelled(self):
        self._put("rec_s", body="lots of filler words here")
        self._put_summary("rec_s", "Decision: split the budget across two quarters")
        out = self._search("budget", have_rg=False)
        line = self._hit_line(out, "budget")
        self.assertIn("[summary]", line, out)

    def test_transcript_hit_is_not_labelled(self):
        self._put("rec_t", body="we should split the budget")
        out = self._search("budget", have_rg=False)
        line = self._hit_line(out, "budget")
        self.assertNotIn("[summary]", line, out)

    def test_summary_only_term_is_findable(self):
        """The whole point: a word that exists only in the summary."""
        self._put("rec_u", body="um so anyway yeah")
        self._put_summary("rec_u", "Key topic: quarterly forecasting")
        self.assertIn("1 matches", self._search("forecasting", have_rg=False))

    def test_transcript_and_summary_count_as_one_recording(self):
        """Both files carry the same stem, so the hits must group under one
        recording — otherwise every summarised recording double-counts."""
        self._put("rec_v", body="the budget question again")
        self._put_summary("rec_v", "Budget was discussed")
        out = self._search("budget", have_rg=False)
        self.assertIn("in 1 recordings", out, out)

    def test_summary_and_corrected_labels_do_not_bleed(self):
        d = cache.CACHE_DIR / "proofread"
        d.mkdir(parents=True, exist_ok=True)
        (d / "rec_w.md").write_text("Kubernetes migration meeting\n")
        self._put("rec_w", body="placeholder body")
        self._put_summary("rec_w", "Kubernetes rollout decision")
        out = self._search("Kubernetes", have_rg=False)
        for line in out.splitlines():
            if line.lstrip().startswith("│"):
                self.assertFalse("[summary]" in line and "[corrected]" in line,
                                 f"a single hit claimed two sources: {line}")

    def test_cache_without_summaries_dir_behaves_exactly_as_before(self):
        self._put("rec_x", body="plain transcript, no summary anywhere")
        out = self._search("transcript", have_rg=False)
        self.assertIn("1 matches", out)
        self.assertNotIn("[summary]", out)


class TestPutSummary(CacheTestCase):
    """Writing summaries. A summary belongs to a recording — it is not a second
    recording — so it must not create its own manifest entry or disturb the
    transcript's completeness bookkeeping."""

    def _put_kind(self, rec_id: str, body: str, kind: str = "summary") -> str:
        ns = argparse.Namespace(id=rec_id, name="", created_at="", duration="", kind=kind)
        with mock.patch("sys.stdin", io.StringIO(body)):
            return self._capture_stdout(cache.cmd_put, ns)

    def test_summary_lands_in_the_summaries_dir(self):
        self._put("rec_a", body="transcript text")
        self._put_kind("rec_a", "Decision: ship on Friday")
        self.assertTrue((cache.CACHE_DIR / "summaries" / "rec_a.md").exists())

    def test_summary_does_not_overwrite_the_transcript(self):
        self._put("rec_b", body="the actual spoken words")
        self._put_kind("rec_b", "A summary")
        self.assertIn("the actual spoken words", (cache.CACHE_DIR / "rec_b.md").read_text())

    def test_summary_does_not_create_a_second_manifest_entry(self):
        self._put("rec_c", body="transcript")
        self._put_kind("rec_c", "summary")
        man = json.loads((cache.CACHE_DIR / "manifest.json").read_text())
        self.assertEqual(["rec_c"], list(man["recordings"]))

    def test_summary_does_not_touch_completeness_or_char_count(self):
        """`chars` and `complete` describe the transcript. A summary landing must
        not move numbers that mean something else."""
        self._put("rec_d", body="transcript body here")
        before = json.loads((cache.CACHE_DIR / "manifest.json").read_text())["recordings"]["rec_d"]
        self._put_kind("rec_d", "a much much longer summary than the transcript ever was")
        after = json.loads((cache.CACHE_DIR / "manifest.json").read_text())["recordings"]["rec_d"]
        self.assertEqual(before["chars"], after["chars"])
        self.assertEqual(before["complete"], after["complete"])

    def test_summary_is_recorded_on_the_manifest_entry(self):
        self._put("rec_e", body="transcript")
        self._put_kind("rec_e", "summary text")
        man = json.loads((cache.CACHE_DIR / "manifest.json").read_text())
        self.assertTrue(man["recordings"]["rec_e"].get("has_summary"))

    def test_summary_for_an_unknown_recording_is_refused(self):
        """A summary with no transcript would be an orphan the manifest never
        mentions — invisible to status, uncountable, and impossible to refresh."""
        with self.assertRaises(SystemExit):
            self._put_kind("rec_ghost", "summary with no transcript")

    def test_kind_defaults_to_transcript_for_older_callers(self):
        ns = argparse.Namespace(id="rec_f", name="", created_at="", duration="")
        with mock.patch("sys.stdin", io.StringIO("body")):
            self._capture_stdout(cache.cmd_put, ns)
        self.assertTrue((cache.CACHE_DIR / "rec_f.md").exists())

    def test_summary_is_normalised_like_transcripts(self):
        self._put("rec_g", body="transcript")
        self._put_kind("rec_g", "un " + NFD + " serré")
        self.assertIn(NFC, (cache.CACHE_DIR / "summaries" / "rec_g.md").read_text())


# --------------------------------------------------------------------------
# Polished transcripts (#22)
#
# Plaud returns two versions of the same speech: raw, and a filler-thinned
# "polish" with identical segments and timings. Subtitles want the second;
# search wants the first, because search answers "what was said".
#
# The dividing line between polish/ and summaries/ is the point of this block:
# a summary is NEW content (words that exist nowhere else, so searching it finds
# things), while a polish is the SAME sentence said more tidily. Including
# polish in search would return every line twice — once raw, once cleaned —
# which is not extra reach, it is halved signal.
# --------------------------------------------------------------------------
class TestPolishIsExcludedFromSearch(CacheTestCase):
    def _put_polish(self, rec_id: str, body: str) -> None:
        d = cache.CACHE_DIR / "polish"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{rec_id}.md").write_text(body + "\n")

    def test_polish_hit_does_not_appear(self):
        self._put("rec_p", body="呃 那個 我們要把預算拆成兩期")
        self._put_polish("rec_p", "我們要把預算拆成兩期")
        out = self._search("預算", have_rg=False)
        self.assertIn("1 matches", out, out)   # not 2

    def test_transcript_is_still_found(self):
        self._put("rec_q", body="我們要把預算拆成兩期")
        self._put_polish("rec_q", "我們要把預算拆成兩期")
        self.assertIn("1 matches", self._search("預算", have_rg=False))

    def test_a_term_only_in_polish_is_not_findable(self):
        """Deliberate. Polish is a rewording of speech, not a source of new
        content — a word that exists only there was never actually said, and
        surfacing it as a hit would misrepresent the recording."""
        self._put("rec_r", body="something else entirely")
        self._put_polish("rec_r", "quarterly forecasting")
        self.assertIn("no match", self._search("forecasting", have_rg=False))

    def test_summaries_remain_searchable(self):
        """The contrast that makes the rule coherent — summaries stay in."""
        d = cache.CACHE_DIR / "summaries"
        d.mkdir(parents=True, exist_ok=True)
        (d / "rec_s.md").write_text("Decision: quarterly forecasting\n")
        self._put("rec_s", body="um so anyway")
        self.assertIn("1 matches", self._search("forecasting", have_rg=False))

    def test_polish_and_summary_together_behave_as_specified(self):
        self._put("rec_t", body="呃 就是 預算的事")
        self._put_polish("rec_t", "預算的事")
        (cache.CACHE_DIR / "summaries").mkdir(parents=True, exist_ok=True)
        (cache.CACHE_DIR / "summaries" / "rec_t.md").write_text("決議：預算拆兩期\n")
        out = self._search("預算", have_rg=False)
        self.assertIn("2 matches", out, out)   # transcript + summary, not polish
        self.assertIn("[summary]", out)


class TestPutPolish(CacheTestCase):
    def _put_kind(self, rec_id: str, body: str, kind: str) -> str:
        ns = argparse.Namespace(id=rec_id, name="", created_at="", duration="", kind=kind)
        with mock.patch("sys.stdin", io.StringIO(body)):
            return self._capture_stdout(cache.cmd_put, ns)

    def test_polish_lands_in_its_own_dir(self):
        self._put("rec_a", body="raw text")
        self._put_kind("rec_a", "tidy text", "polish")
        self.assertTrue((cache.CACHE_DIR / "polish" / "rec_a.md").exists())

    def test_polish_does_not_overwrite_the_transcript(self):
        self._put("rec_b", body="the actual spoken words")
        self._put_kind("rec_b", "tidied", "polish")
        self.assertIn("the actual spoken words", (cache.CACHE_DIR / "rec_b.md").read_text())

    def test_polish_does_not_move_chars_or_completeness(self):
        self._put("rec_c", body="raw body here")
        before = json.loads((cache.CACHE_DIR / "manifest.json").read_text())["recordings"]["rec_c"]
        self._put_kind("rec_c", "a very much longer tidied version than the raw ever was", "polish")
        after = json.loads((cache.CACHE_DIR / "manifest.json").read_text())["recordings"]["rec_c"]
        self.assertEqual(before["chars"], after["chars"])
        self.assertEqual(before["complete"], after["complete"])

    def test_polish_is_recorded_on_the_manifest(self):
        self._put("rec_d", body="raw")
        self._put_kind("rec_d", "tidy", "polish")
        man = json.loads((cache.CACHE_DIR / "manifest.json").read_text())
        self.assertTrue(man["recordings"]["rec_d"].get("has_polish"))

    def test_orphan_polish_is_refused(self):
        with self.assertRaises(SystemExit):
            self._put_kind("rec_ghost", "tidy with no transcript", "polish")


# ===========================================================================
# Incremental listing: where paging is allowed to stop (issue #27)
# ===========================================================================
#
# `plaud-index` used to walk every page of `list_files` on every run, even when
# three recordings were new. The saving is an early exit: stop paging once a
# whole page is older than everything already cached.
#
# The official CLI's `plaud recent` does exactly this internally — and gets it
# wrong in a way worth not copying. It compares an API timestamp
# (`created_at`, which carries no timezone suffix) against the local clock
# (`Date.now()`), so on UTC+8 every recording reads as eight hours older than
# it is. Both sides of OUR comparison come from the API, so no timezone
# question ever arises. `test_cutoff_comparison_never_reads_the_local_clock`
# is what keeps it that way.
#
# The asymmetry that shapes every choice below: reading one page too many
# costs one API call, and missing a recording costs silence — it is not
# reported, does not appear in any count, and is simply never findable again.
# Every uncertain case therefore resolves toward paging more.


class TestListCutoff(CacheTestCase):
    """The timestamp a fresh listing may stop paging at."""

    def _manifest(self, recordings: dict) -> dict:
        # Every test in this class asks "given a cutoff is allowed, what is
        # it?" — the separate question of whether one is allowed at all lives
        # in TestFullSweepCoverage. So these fixtures carry the marker.
        man = {"version": "1", "full_sweep_at": "2026-08-06T00:00:00+00:00",
               "recordings": recordings}
        cache._save_manifest(man)
        return man

    def test_cutoff_is_older_than_the_newest_cached_recording(self):
        """The margin must be SUBTRACTED. Getting the sign wrong is silent.

        A cutoff newer than what we hold stops paging before reaching the
        recordings we have not seen — and nothing anywhere reports it. This
        assertion is the only thing standing between that and a cache that
        looks healthy while quietly missing entries.
        """
        man = self._manifest({
            "a": {"created_at": "2026-08-01T00:00:00", "complete": True},
            "b": {"created_at": "2026-08-05T09:00:00", "complete": True},
        })
        cutoff = cache.list_cutoff(man)
        self.assertLess(cutoff, "2026-08-05T09:00:00", "the cutoff must reach back, not forward")
        self.assertEqual("2026-08-04T09:00:00", cutoff)

    def test_cutoff_ignores_incomplete_recordings(self):
        """A half-fetched recording does not prove we have everything up to it.

        `complete` describes the transcript, but a record that never finished
        is the weakest possible evidence about what the listing contained.
        Excluding it moves the cutoff older, which pages more — the safe
        direction, and it costs at most one extra page.
        """
        man = self._manifest({
            "done": {"created_at": "2026-08-01T00:00:00", "complete": True},
            "partial": {"created_at": "2026-08-09T00:00:00", "complete": False},
        })
        self.assertEqual("2026-07-31T00:00:00", cache.list_cutoff(man))

    def test_cutoff_is_none_on_an_empty_cache(self):
        """A first index has nothing to stop at, so it must not stop."""
        self.assertIsNone(cache.list_cutoff(
            {"version": "1", "full_sweep_at": "2026-08-06T00:00:00+00:00", "recordings": {}}))

    def test_cutoff_is_none_when_every_record_is_incomplete(self):
        man = self._manifest({
            "p": {"created_at": "2026-08-09T00:00:00", "complete": False},
        })
        self.assertIsNone(cache.list_cutoff(man))

    def test_cutoff_skips_a_timestamp_it_cannot_parse(self):
        """One malformed entry must not decide where paging stops."""
        man = self._manifest({
            "good": {"created_at": "2026-08-05T09:00:00", "complete": True},
            "junk": {"created_at": "not a date", "complete": True},
        })
        self.assertEqual("2026-08-04T09:00:00", cache.list_cutoff(man))

    def test_cutoff_is_none_when_no_timestamp_parses(self):
        man = self._manifest({"junk": {"created_at": "", "complete": True}})
        self.assertIsNone(cache.list_cutoff(man))

    def test_cutoff_survives_a_timestamp_that_is_not_a_string(self):
        """A manifest is a file on disk, and files get hand-edited.

        `fromisoformat` raises TypeError rather than ValueError on a non-string,
        which the ValueError handler would not catch — the whole indexing run
        would die on one bad entry. Found by an acid test: removing the type
        check left every existing test green, because they all pass strings.
        """
        man = self._manifest({
            "good": {"created_at": "2026-08-05T09:00:00", "complete": True},
            "numeric": {"created_at": 1786068094817, "complete": True},
        })
        self.assertEqual("2026-08-04T09:00:00", cache.list_cutoff(man))


class TestPageVerdict(CacheTestCase):
    """Whether a page of `list_files` results ends the walk."""

    CUTOFF = "2026-08-05T00:00:00"

    def test_stops_when_every_entry_is_older_than_the_cutoff(self):
        stop, reason = cache.page_verdict(
            ["2026-08-04T10:00:00", "2026-08-03T10:00:00", "2026-08-02T10:00:00"], self.CUTOFF
        )
        self.assertTrue(stop)
        self.assertIn("3", reason, "the reason should say how much it looked at")

    def test_continues_when_one_entry_is_newer(self):
        """`all`, not `any`. One unseen recording is reason enough to keep going."""
        stop, reason = cache.page_verdict(
            ["2026-08-09T10:00:00", "2026-08-03T10:00:00"], self.CUTOFF
        )
        self.assertFalse(stop)
        self.assertIn("2026-08-09T10:00:00", reason)

    def test_continues_when_the_page_is_not_descending(self):
        """Early exit assumes the listing is newest-first. That is observed, not promised.

        If `list_files` ever sorted by `start_at` instead — a recording's own
        time rather than its upload time — an early exit would stop short with
        no symptom at all. Refusing to stop on a page that is not descending
        degrades to a full walk: slower, and correct.
        """
        stop, reason = cache.page_verdict(
            ["2026-08-04T10:00:00", "2026-08-04T11:00:00"], self.CUTOFF
        )
        self.assertFalse(stop)
        self.assertIn("descending", reason)

    def test_continues_on_an_empty_page(self):
        """`all([])` is True, which would read an empty page as 'all older'."""
        stop, reason = cache.page_verdict([], self.CUTOFF)
        self.assertFalse(stop)
        self.assertIn("empty", reason)

    def test_continues_when_a_timestamp_cannot_be_parsed(self):
        stop, reason = cache.page_verdict(["2026-08-04T10:00:00", "???"], self.CUTOFF)
        self.assertFalse(stop)
        self.assertIn("???", reason)

    def test_equal_to_the_cutoff_is_not_older(self):
        """Strictly older. The boundary entry stays in, which pages more."""
        stop, _ = cache.page_verdict([self.CUTOFF], self.CUTOFF)
        self.assertFalse(stop)

    def test_cutoff_comparison_does_not_call_datetime_now(self):
        """One call, named as one call — see TestNoClockInTheCutoffPath for the property.

        This test's original name claimed the whole "never reads the local
        clock" property while mocking exactly one classmethod. `today()`,
        `utcnow()` and `time.time()` all walked past it. Renamed rather than
        deleted: the check is real, it was the claim that was too big.

        Both sides come from the API; a local clock has no business here.

        This is the defect `plaud recent` has and we do not: it parses a
        timezone-less API timestamp as local time and compares it to
        `Date.now()`. Anyone who "simplifies" this by reaching for
        `datetime.now()` reintroduces exactly that eight-hour skew.
        """
        class NoClock(cache.datetime):
            @classmethod
            def now(cls, tz=None):
                raise AssertionError("cutoff logic must not read the clock")

        man = {"version": "1", "full_sweep_at": "2026-08-06T00:00:00+00:00", "recordings": {
            "a": {"created_at": "2026-08-05T09:00:00", "complete": True}}}
        with mock.patch.object(cache, "datetime", NoClock):
            cutoff = cache.list_cutoff(man)
            stop, _ = cache.page_verdict(["2026-08-01T00:00:00"], cutoff)
        self.assertTrue(stop)


class TestListCutoffCommandWiring(CacheTestCase):
    """The flag has to reach the function — testing the function is not enough.

    #22's lesson: `subtitle_source()` was tested and correct while `main()`
    never called it, and every test stayed green. A command that recomputes
    the cutoff its own way would pass every test in TestListCutoff.
    """

    def test_status_list_cutoff_goes_through_list_cutoff(self):
        cache._save_manifest({"version": "1", "full_sweep_at": "2026-08-06T00:00:00+00:00",
                              "recordings": {
            "a": {"created_at": "2026-08-05T09:00:00", "complete": True}}})
        ns = argparse.Namespace(ids_only=False, list_cutoff=True)
        with mock.patch.object(cache, "list_cutoff", return_value="SENTINEL") as spy:
            out = self._capture_stdout(cache.cmd_status, ns)
        spy.assert_called_once()
        self.assertEqual("SENTINEL", out.strip())

    def test_status_list_cutoff_exits_3_when_there_is_nothing_to_stop_at(self):
        cache._save_manifest({"version": "1", "recordings": {}})
        ns = argparse.Namespace(ids_only=False, list_cutoff=True)
        with self.assertRaises(SystemExit) as ctx:
            self._capture_stdout(cache.cmd_status, ns)
        self.assertEqual(3, ctx.exception.code)

    def test_status_default_output_is_unchanged_by_the_new_flag(self):
        """`cmd_status` has callers that predate this flag and pass no such attribute."""
        self._put("rec_x", name="a recording", body="words", created_at="2026-08-05T09:00:00")
        legacy = argparse.Namespace(ids_only=False)  # no list_cutoff attribute at all
        out = self._capture_stdout(cache.cmd_status, legacy)
        self.assertIn("cached    : 1 recordings", out)
        self.assertNotIn("2026-08-04", out, "the cutoff must not leak into normal status")


class TestShouldStopPagingCli(unittest.TestCase):
    """The page arrives on stdin, because a page is a list, not an argument."""

    def _run(self, page: str, cutoff: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(CACHE_PY), "should-stop-paging", "--cutoff", cutoff],
            input=page, capture_output=True, text=True,
        )

    def test_prints_stop_for_a_page_that_is_entirely_older(self):
        out = self._run("2026-08-04T10:00:00\n2026-08-03T10:00:00\n", "2026-08-05T00:00:00")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertTrue(out.stdout.startswith("stop:"), out.stdout)

    def test_prints_continue_with_a_reason(self):
        out = self._run("2026-08-09T10:00:00\n", "2026-08-05T00:00:00")
        self.assertEqual(3, out.returncode, out.stderr)
        self.assertTrue(out.stdout.startswith("continue:"), out.stdout)

    def test_a_blank_line_is_an_unreadable_entry_not_an_absent_one(self):
        """Dropping it would shorten the page and make it look wrongly all-old.

        A recording whose `created_at` is empty arrives as a blank line. Found
        in verify: filtering blank lines turned "one entry we cannot read" into
        "an entry that was never there", so a page holding one old timestamp
        and one unreadable one answered `stop`.
        """
        out = self._run("\n2026-08-04T10:00:00\n", "2026-08-05T00:00:00")
        self.assertTrue(out.stdout.startswith("continue:"), out.stdout)
        self.assertEqual(3, out.returncode)

    def test_missing_cutoff_is_a_usage_error_not_a_silent_stop(self):
        out = subprocess.run(
            [sys.executable, str(CACHE_PY), "should-stop-paging"],
            input="", capture_output=True, text=True,
        )
        self.assertNotEqual(0, out.returncode)
        self.assertNotIn("stop", out.stdout)


class TestTimezoneMixing(CacheTestCase):
    """Timestamps with and without an offset must still be comparable.

    Found in verify by adversarial self-review, and the irony is worth
    recording: `_parse_api_time` translates a trailing `Z` into `+00:00` so an
    offset-carrying timestamp is understood — and that is precisely what
    creates the crash. A parsed `Z` timestamp is offset-AWARE; a parsed bare
    one is offset-NAIVE; Python refuses to order the two and raises TypeError.
    The robustness feature introduced the failure mode.

    Measured (all three raised `TypeError: can't compare offset-naive and
    offset-aware datetimes` before the fix): a manifest holding both forms,
    a cutoff carrying an offset against a page that does not, and one page
    holding both.

    The fix is not to stop parsing `Z`. The API's offset-less timestamps ARE
    UTC — measured on one recording, `start_at: …T02:01:34` against an
    auto-generated name of `10:01:34` local, an eight-hour offset. So a naive
    timestamp is read as UTC, everything is comparable, and no assumption is
    being invented to paper over a crash.
    """

    def test_manifest_mixing_offset_forms_does_not_crash(self):
        man = {"version": "1", "full_sweep_at": "2026-08-06T00:00:00+00:00", "recordings": {
            "naive": {"created_at": "2026-08-05T09:00:00", "complete": True},
            "aware": {"created_at": "2026-08-06T09:00:00Z", "complete": True},
        }}
        # The newest is the Z-suffixed one, so the cutoff is a day before it.
        self.assertEqual("2026-08-05T09:00:00", cache.list_cutoff(man))

    def test_aware_cutoff_against_a_naive_page_does_not_crash(self):
        stop, reason = cache.page_verdict(["2026-08-01T00:00:00"], "2026-08-05T00:00:00+00:00")
        self.assertTrue(stop, reason)

    def test_a_page_mixing_offset_forms_does_not_crash(self):
        stop, reason = cache.page_verdict(
            ["2026-08-04T10:00:00Z", "2026-08-03T10:00:00"], "2026-08-05T00:00:00")
        self.assertTrue(stop, reason)

    def test_a_naive_timestamp_is_read_as_utc_not_as_local_time(self):
        """The assumption is measured, so pin it — a future 'fix' must not flip it.

        Reading a naive timestamp as local time is the exact defect the official
        CLI has. Same instant written both ways must compare equal.
        """
        naive = cache._parse_api_time("2026-08-05T09:00:00")
        aware = cache._parse_api_time("2026-08-05T09:00:00+00:00")
        self.assertEqual(aware, naive)

    def test_cutoff_output_keeps_the_api_shape(self):
        """What comes out must be something `--cutoff` can read back in.

        The cutoff crosses a shell boundary as a string, so its shape is part
        of the contract, not an implementation detail.
        """
        man = {"version": "1", "full_sweep_at": "2026-08-06T00:00:00+00:00", "recordings": {
            "a": {"created_at": "2026-08-05T09:00:00Z", "complete": True}}}
        cutoff = cache.list_cutoff(man)
        self.assertNotIn("+", cutoff, "an offset would not match what list_files sends")
        stop, reason = cache.page_verdict(["2026-08-01T00:00:00"], cutoff)
        self.assertTrue(stop, reason)


class TestShouldStopPagingExitCode(unittest.TestCase):
    """The answer must survive being read as an exit code, not only as a word.

    Anyone wiring this into a shell reaches for `if cmd; then` — that is the
    idiom. With exit 0 on both branches that reads as "stop" every time, which
    is the one failure this whole change exists to prevent. So the exit code
    carries the answer too: 0 stop, 3 continue. 3 rather than 1 because 1 is
    what a crash looks like, and "keep paging" is not a failure.
    """

    def _run(self, page: str, cutoff: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(CACHE_PY), "should-stop-paging", "--cutoff", cutoff],
            input=page, capture_output=True, text=True,
        )

    def test_exit_0_means_stop(self):
        out = self._run("2026-08-04T10:00:00\n", "2026-08-05T00:00:00")
        self.assertEqual(0, out.returncode, out.stdout)

    def test_exit_3_means_continue(self):
        out = self._run("2026-08-09T10:00:00\n", "2026-08-05T00:00:00")
        self.assertEqual(3, out.returncode, out.stdout)

    def test_continue_is_never_exit_1_which_would_read_as_a_crash(self):
        out = self._run("", "2026-08-05T00:00:00")
        self.assertNotEqual(1, out.returncode)
        self.assertEqual(3, out.returncode)


class TestListCutoffRealCliWiring(unittest.TestCase):
    """Through argparse, as a subprocess — the previous class did not.

    `TestListCutoffCommandWiring` builds an `argparse.Namespace` by hand and
    calls `cmd_status` directly, so deleting `p.add_argument("--list-cutoff")`
    leaves every one of its tests green. Caught in verify by the cross-model
    reviewer, and it is the #22 error repeating inside the class named after
    guarding against it: the flag reaching the function was never tested, only
    the function.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="plaud-cli-wiring-")
        self.addCleanup(self._tmp.cleanup)
        self.cache_dir = pathlib.Path(self._tmp.name) / "cache"
        self.cache_dir.mkdir(parents=True)

    def _write_manifest(self, recordings: dict) -> None:
        (self.cache_dir / "manifest.json").write_text(
            json.dumps({"version": "1", "full_sweep_at": "2026-08-06T00:00:00+00:00",
                        "recordings": recordings}))

    def _cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(CACHE_PY), "status", *args],
            capture_output=True, text=True,
            env={**os.environ, "PLAUD_CACHE_DIR": str(self.cache_dir)},
        )

    def test_the_flag_exists_and_reaches_the_function(self):
        self._write_manifest({"a": {"created_at": "2026-08-05T09:00:00", "complete": True}})
        out = self._cli("--list-cutoff")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertEqual("2026-08-04T09:00:00", out.stdout.strip())

    def test_exit_3_reaches_the_shell_on_an_empty_cache(self):
        self._write_manifest({})
        out = self._cli("--list-cutoff")
        self.assertEqual(3, out.returncode, out.stdout)

    def test_ids_only_and_list_cutoff_together_is_refused_not_silently_ranked(self):
        self._write_manifest({"a": {"created_at": "2026-08-05T09:00:00", "complete": True}})
        out = self._cli("--ids-only", "--list-cutoff")
        self.assertNotEqual(0, out.returncode)
        self.assertIn("--ids-only", out.stderr)


class TestFullSweepCoverage(CacheTestCase):
    """A cutoff requires proof that a full listing was walked — not just that
    some recording's transcript finished.

    The distinction cost a CRITICAL in verify. `complete` says "this one's
    transcript came down whole". It says nothing about whether the listing was
    ever walked to its end, and the early exit was reading it as if it did.
    Three reproduced ways that went wrong, all silent:

    - Someone runs `--days 1` first. The cache now holds one recent recording,
      so a cutoff exists, so the next unscoped run stops on the first old page
      and the entire older library is never listed — every run after that stops
      in the same place.
    - A first index is interrupted after one `put`. Same shape: non-empty cache
      is not a finished cache.
    - `--all` was never run at all.

    So the cutoff is gated on a marker that only a completed unscoped walk
    writes. Absent marker means walk everything, which is what a tool that has
    never proved its coverage should do.
    """

    def _man(self, extra: dict = None) -> dict:
        man = {"version": "1", "recordings": {
            "a": {"created_at": "2026-08-05T09:00:00", "complete": True}}}
        man.update(extra or {})
        return man

    def test_no_cutoff_without_a_recorded_full_sweep(self):
        """The scoped-cache case: complete recordings, no proof of coverage."""
        self.assertIsNone(cache.list_cutoff(self._man()))

    def test_cutoff_once_a_full_sweep_is_recorded(self):
        man = self._man({"full_sweep_at": "2026-08-06T00:00:00+00:00"})
        self.assertEqual("2026-08-04T09:00:00", cache.list_cutoff(man))

    def test_a_full_sweep_over_an_empty_library_still_yields_no_cutoff(self):
        """Nothing to stop at, marker or not."""
        man = {"version": "1", "recordings": {},
               "full_sweep_at": "2026-08-06T00:00:00+00:00"}
        self.assertIsNone(cache.list_cutoff(man))

    def test_mark_full_sweep_writes_the_marker_and_keeps_the_recordings(self):
        cache._save_manifest(self._man())
        cache.cmd_mark_full_sweep(argparse.Namespace())
        man = json.loads((cache.CACHE_DIR / "manifest.json").read_text())
        self.assertIn("full_sweep_at", man)
        self.assertIn("a", man["recordings"], "the marker must not disturb what is cached")

    def test_marking_a_sweep_is_what_turns_the_cutoff_on(self):
        """End to end: the same manifest answers differently either side of the mark."""
        cache._save_manifest(self._man())
        self.assertIsNone(cache.list_cutoff(cache._load_manifest()))
        cache.cmd_mark_full_sweep(argparse.Namespace())
        self.assertEqual("2026-08-04T09:00:00", cache.list_cutoff(cache._load_manifest()))


class TestMarkFullSweepCli(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="plaud-sweep-")
        self.addCleanup(self._tmp.cleanup)
        self.cache_dir = pathlib.Path(self._tmp.name) / "cache"
        self.cache_dir.mkdir(parents=True)
        (self.cache_dir / "manifest.json").write_text(json.dumps(
            {"version": "1", "recordings": {
                "a": {"created_at": "2026-08-05T09:00:00", "complete": True}}}))

    def _cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(CACHE_PY), *args], capture_output=True, text=True,
            env={**os.environ, "PLAUD_CACHE_DIR": str(self.cache_dir)})

    def test_the_subcommand_exists_and_flips_the_cutoff(self):
        before = self._cli("status", "--list-cutoff")
        self.assertEqual(3, before.returncode, "no sweep recorded yet")
        marked = self._cli("mark-full-sweep")
        self.assertEqual(0, marked.returncode, marked.stderr)
        after = self._cli("status", "--list-cutoff")
        self.assertEqual(0, after.returncode, after.stderr)
        self.assertEqual("2026-08-04T09:00:00", after.stdout.strip())


class TestTimestampStrictness(CacheTestCase):
    """A date with no time is not a timestamp (issue #34).

    `datetime.fromisoformat("2026-08-05")` succeeds and silently means
    midnight. Nothing in `created_at` is ever date-only, so a value that shape
    is a manifest someone edited or an API that changed — either way it is
    unknown, and the rule for unknown is "page more", not "assume the earliest
    moment of that day".

    Assuming midnight is the wrong direction twice over: it invents precision
    that was not there, and it lands on the side that pages LESS.
    """

    def test_a_date_without_a_time_is_unreadable(self):
        self.assertIsNone(cache._parse_api_time("2026-08-05"))

    def test_a_date_only_entry_does_not_move_the_cutoff(self):
        man = {"version": "1", "full_sweep_at": "2026-08-06T00:00:00+00:00", "recordings": {
            "good": {"created_at": "2026-08-05T09:00:00", "complete": True},
            "dateonly": {"created_at": "2026-08-09", "complete": True},
        }}
        self.assertEqual("2026-08-04T09:00:00", cache.list_cutoff(man))

    def test_a_date_only_entry_on_a_page_keeps_the_walk_going(self):
        stop, reason = cache.page_verdict(["2026-08-04T10:00:00", "2026-08-03"],
                                          "2026-08-05T00:00:00")
        self.assertFalse(stop)
        self.assertIn("2026-08-03", reason)

    def test_a_space_separated_datetime_is_still_a_timestamp(self):
        """Rejecting date-only must not reject the other legal separator."""
        self.assertIsNotNone(cache._parse_api_time("2026-08-05 09:00:00"))


class TestNoClockInTheCutoffPath(CacheTestCase):
    """Two independent guards, because the first one alone did not earn its name.

    `test_cutoff_comparison_never_reads_the_local_clock` mocked
    `cache.datetime.now` and nothing else — `today()`, `utcnow()`,
    `time.time()` and a bare `.astimezone()` all walk straight past it. The
    name promised the whole property; the test checked one call.

    So: a behavioural guard that explodes on every clock-shaped classmethod,
    and a structural one that reads the two functions' own source. Either
    alone is escapable; both together are hard to slip past by accident.
    """

    MAN = {"version": "1", "full_sweep_at": "2026-08-06T00:00:00+00:00",
           "recordings": {"a": {"created_at": "2026-08-05T09:00:00", "complete": True}}}

    def test_no_clock_classmethod_is_reachable_from_the_cutoff_path(self):
        class NoClock(cache.datetime):
            @classmethod
            def now(cls, tz=None):
                raise AssertionError("cutoff path must not read the clock: now()")

            @classmethod
            def today(cls):
                raise AssertionError("cutoff path must not read the clock: today()")

            @classmethod
            def utcnow(cls):
                raise AssertionError("cutoff path must not read the clock: utcnow()")

            @classmethod
            def fromtimestamp(cls, ts, tz=None):
                raise AssertionError("cutoff path must not read the clock: fromtimestamp()")

        with mock.patch.object(cache, "datetime", NoClock):
            cutoff = cache.list_cutoff(self.MAN)
            stop, _ = cache.page_verdict(["2026-08-01T00:00:00"], cutoff)
        self.assertTrue(stop)

    def test_the_cutoff_functions_contain_no_clock_call_at_all(self):
        """Structural, because a mock only catches what it was told to catch.

        `_now()` exists in this module and is legitimate — `cmd_put` stamps
        `indexed_at` with it. This asserts it stays out of the three functions
        that decide where paging stops.
        """
        import inspect
        for fn in (cache._parse_api_time, cache.list_cutoff, cache.page_verdict):
            src = inspect.getsource(fn)
            for forbidden in ("datetime.now", "datetime.today", "datetime.utcnow",
                              "time.time", "_now("):
                self.assertNotIn(
                    forbidden, src,
                    f"{fn.__name__} reads the clock via {forbidden} — both sides of "
                    f"this comparison must come from the API, which is the whole "
                    f"reason the official CLI's eight-hour skew is not inherited",
                )


class TestCrossPageOrder(CacheTestCase):
    """One page being descending proves nothing about the next (issue #33).

    The counterexample, from cross-model verify:

        cutoff: 2026-08-05
        page 1: 2026-08-06, 2026-08-05  -> continue
        page 2: 2026-08-04, 2026-08-03  -> stop
        page 3: 2026-08-07, 2026-08-02  -> never read

    Every page descends internally; the sequence does not. With one entry per
    page the per-page check passes vacuously every time.

    So the walk carries the previous page's last timestamp forward. This
    detects a violation once it has been seen — it cannot prove the pages
    after this one stay ordered, and nothing short of reading them can.
    """

    CUTOFF = "2026-08-05T00:00:00"

    def test_a_page_starting_newer_than_the_last_one_ended_is_a_violation(self):
        stop, reason = cache.page_verdict(
            ["2026-08-04T10:00:00", "2026-08-02T00:00:00"], self.CUTOFF,
            prev_last="2026-08-03T00:00:00")
        self.assertFalse(stop)
        self.assertIn("previous page", reason)

    def test_a_properly_descending_boundary_still_stops(self):
        stop, reason = cache.page_verdict(
            ["2026-08-02T10:00:00", "2026-08-01T00:00:00"], self.CUTOFF,
            prev_last="2026-08-03T00:00:00")
        self.assertTrue(stop, reason)

    def test_single_entry_pages_are_checked_against_each_other(self):
        """The case the per-page check can never catch: nothing to compare within."""
        stop, _ = cache.page_verdict(["2026-08-04T00:00:00"], self.CUTOFF,
                                     prev_last="2026-08-03T00:00:00")
        self.assertFalse(stop, "a lone newer entry must not end the walk")

    def test_no_previous_page_means_no_boundary_check(self):
        stop, _ = cache.page_verdict(["2026-08-04T00:00:00"], self.CUTOFF)
        self.assertTrue(stop)


class TestAnomalyLatch(CacheTestCase):
    """One refused page must disable the early exit for the whole run.

    Before this, a page that came back out of order only stopped *that* page
    from ending the walk — the next orderly-looking page could still stop it.
    The SKILL.md text claiming it "degrades to a full walk" was simply false,
    and it was false in the direction that loses recordings.

    The latch state belongs to the caller (one run, many invocations), so it
    arrives as a flag. What does NOT belong to the caller is remembering to
    set it: every anomaly reason carries the instruction in its own text, so
    the thing that has to happen next is in the output rather than in prose
    somewhere else.
    """

    CUTOFF = "2026-08-05T00:00:00"

    def test_a_latched_run_never_stops(self):
        stop, reason = cache.page_verdict(["2026-08-01T00:00:00"], self.CUTOFF,
                                          anomaly_seen=True)
        self.assertFalse(stop)
        self.assertIn("earlier page", reason)

    def test_every_anomaly_reason_tells_the_caller_to_latch(self):
        anomalies = [
            (["2026-08-04T10:00:00", "2026-08-04T11:00:00"], None),   # not descending
            (["2026-08-04T10:00:00", "???"], None),                    # unreadable
            (["2026-08-04T10:00:00"], "2026-08-03T00:00:00"),          # boundary
        ]
        for dates, prev in anomalies:
            with self.subTest(dates=dates):
                stop, reason = cache.page_verdict(dates, self.CUTOFF, prev_last=prev)
                self.assertFalse(stop)
                self.assertIn("--anomaly-seen", reason,
                              "an anomaly must name the flag that latches it")

    def test_an_ordinary_continue_does_not_ask_for_the_latch(self):
        """Seeing a newer recording is normal — it is not an anomaly."""
        stop, reason = cache.page_verdict(["2026-08-09T10:00:00"], self.CUTOFF)
        self.assertFalse(stop)
        self.assertNotIn("--anomaly-seen", reason)


class TestPagingCliLatchFlags(unittest.TestCase):
    def _run(self, page: str, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(CACHE_PY), "should-stop-paging",
             "--cutoff", "2026-08-05T00:00:00", *extra],
            input=page, capture_output=True, text=True, timeout=60)

    def test_anomaly_seen_flag_forces_continue(self):
        out = self._run("2026-08-01T00:00:00\n", "--anomaly-seen")
        self.assertEqual(3, out.returncode, out.stdout)
        self.assertTrue(out.stdout.startswith("continue:"), out.stdout)

    def test_prev_last_flag_catches_a_boundary_violation(self):
        out = self._run("2026-08-04T00:00:00\n", "--prev-last", "2026-08-03T00:00:00")
        self.assertEqual(3, out.returncode, out.stdout)
        self.assertIn("previous page", out.stdout)

    def test_without_the_flags_behaviour_is_unchanged(self):
        out = self._run("2026-08-01T00:00:00\n")
        self.assertEqual(0, out.returncode, out.stdout)
