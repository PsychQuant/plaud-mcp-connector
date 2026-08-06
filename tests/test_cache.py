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


if __name__ == "__main__":
    unittest.main(verbosity=2)
