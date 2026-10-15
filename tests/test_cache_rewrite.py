#!/usr/bin/env python3
"""Rewriting a transcript must not leave derived files describing a transcript that is gone (#70).

`cache.py put` for a transcript builds a brand-new manifest record. Before this change
that dropped `has_summary`, `has_outline` and `has_polish` (and their timestamps) while
`summaries/`, `outline/` and `polish/` stayed on disk, so:

* `status` stopped counting files that were still there;
* `show --kind outline` printed an outline of the OLD text with nothing to say so;
* `proofread/`, which is written by a plain copy and has no manifest flag at all, went
  stale in the same silent way.

The rules these tests pin (each one names a break):

* a rewrite keeps the manifest describing what is on disk: the flag of a derived file
  that exists survives, the flag of one that is gone does not (`fetch_one` removes the
  old polish BEFORE it rewrites, so a flag carried over for a missing file would lie);
* if the NEW body differs from the stored one, every derived file that exists is
  recorded as stale in ONE optional field, `stale_derived`; if the body is the same,
  nothing is stale, and a stale mark is never cleared by a later rewrite that happens
  to match the new text;
* writing a derived kind again clears that kind and no other;
* readers say so (`show --kind`, `status`, search hits) and nothing is deleted.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import pathlib
import shutil
import tempfile
import unicodedata
import unittest
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("plaud_cache_rewrite", REPO_ROOT / "scripts" / "cache.py")
cache = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(cache)

OLD = "[00:00:01] Speaker 1: the budget is approved\n[00:00:05] Speaker 1: next item\n"
NEW = "[00:00:01] Speaker 1: the budget is rejected\n[00:00:05] Speaker 1: next item\n"


def _engines():
    return [False] + ([True] if shutil.which("rg") is not None else [])


class RewriteCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="plaud-rewrite-test-")
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name) / "cache"
        for name, value in (("CACHE_DIR", self.dir), ("MANIFEST", self.dir / "manifest.json")):
            patcher = mock.patch.object(cache, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    # -- helpers -------------------------------------------------------------
    def capture(self, func, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            func(*args)
        return out.getvalue(), err.getvalue()

    def put(self, rec: str, body: str, kind: str = "transcript") -> None:
        ns = argparse.Namespace(id=rec, name="n", created_at="2026-10-01T00:00:00Z",
                                duration="60000", kind=kind)
        with mock.patch("sys.stdin", io.StringIO(body)):
            self.capture(cache.cmd_put, ns)

    def record(self, rec: str) -> dict:
        return json.loads((self.dir / "manifest.json").read_text())["recordings"][rec]

    def stale(self, rec: str) -> list:
        return sorted(self.record(rec).get("stale_derived", []))

    def with_all_derived(self, rec: str = "rec1") -> None:
        self.put(rec, OLD)
        self.put(rec, "a summary of the old text\n", "summary")
        self.put(rec, "[00:00:01] outline of the old text\n", "outline")
        self.put(rec, "[00:00:01] Speaker 1: the budget is approved.\n", "polish")


class TestFlagsSurviveARewrite(RewriteCase):
    def test_a_rewrite_with_different_text_keeps_the_flags_and_timestamps(self):
        """The break: all of these used to be dropped by the fresh record."""
        self.with_all_derived()
        before = self.record("rec1")
        self.put("rec1", NEW)
        after = self.record("rec1")
        for kind in ("summary", "outline", "polish"):
            self.assertTrue(after.get(f"has_{kind}"), f"has_{kind} was dropped by the rewrite")
            self.assertEqual(before[f"{kind}_indexed_at"], after[f"{kind}_indexed_at"])

    def test_a_flag_whose_file_is_gone_is_not_carried_over(self):
        """`fetch_one` deletes the old polish before it rewrites the transcript. A flag
        carried over for a file that is no longer there would make the manifest lie."""
        self.with_all_derived()
        (self.dir / "polish" / "rec1.md").unlink()
        self.put("rec1", NEW)
        self.assertFalse(self.record("rec1").get("has_polish"))
        self.assertTrue(self.record("rec1").get("has_summary"))

    def test_status_still_counts_the_files_that_are_still_on_disk(self):
        self.with_all_derived()
        self.put("rec1", NEW)
        out, _ = self.capture(cache.cmd_status, argparse.Namespace(ids_only=False))
        self.assertIn("summaries : 1 of 1", out)
        self.assertIn("polished  : 1 of 1", out)


class TestWhatBecomesStale(RewriteCase):
    def test_different_text_marks_every_existing_derived_kind_stale(self):
        self.with_all_derived()
        self.put("rec1", NEW)
        self.assertEqual(["outline", "polish", "summary"], self.stale("rec1"))

    def test_a_proofread_file_with_no_manifest_flag_is_marked_stale_too(self):
        """`plaud-proofread` writes `proofread/<id>.md` by plain copy; the manifest never
        heard of it, and it is the one derived file that took model work to produce."""
        self.put("rec1", OLD)
        (self.dir / "proofread").mkdir()
        (self.dir / "proofread" / "rec1.md").write_text("corrected text of the old transcript\n")
        self.put("rec1", NEW)
        self.assertEqual(["proofread"], self.stale("rec1"))

    def test_a_kind_that_never_existed_is_not_marked(self):
        self.put("rec1", OLD)
        self.put("rec1", "a summary\n", "summary")
        self.put("rec1", NEW)
        self.assertEqual(["summary"], self.stale("rec1"))

    def test_identical_text_marks_nothing_stale(self):
        """A refresh that brings the same words back (the usual `--force`) must not cry wolf."""
        self.with_all_derived()
        self.put("rec1", OLD)
        self.assertEqual([], self.stale("rec1"))
        self.assertTrue(self.record("rec1").get("has_outline"))

    def test_the_same_text_in_the_other_unicode_form_is_not_a_change(self):
        self.put("rec1", "café au lait\n")
        self.put("rec1", "a summary\n", "summary")
        self.put("rec1", unicodedata.normalize("NFD", "café au lait\n"))
        self.assertEqual([], self.stale("rec1"))

    def test_a_second_rewrite_that_matches_the_new_text_does_not_clear_the_mark(self):
        """Stale means 'written for an earlier transcript'. Rewriting the same NEW text
        again does not make the outline any newer."""
        self.with_all_derived()
        self.put("rec1", NEW)
        self.put("rec1", NEW)
        self.assertEqual(["outline", "polish", "summary"], self.stale("rec1"))

    def test_nothing_is_deleted(self):
        self.with_all_derived()
        self.put("rec1", NEW)
        for sub in ("summaries", "outline", "polish"):
            self.assertTrue((self.dir / sub / "rec1.md").is_file(), f"{sub}/ file was removed")


class TestWritingADerivedKindClearsItsMark(RewriteCase):
    def test_put_kind_clears_that_kind_and_only_that_kind(self):
        self.with_all_derived()
        self.put("rec1", NEW)
        self.put("rec1", "a fresh summary of the new text\n", "summary")
        self.assertEqual(["outline", "polish"], self.stale("rec1"))

    def test_refreshing_everything_leaves_no_field_behind(self):
        self.with_all_derived()
        self.put("rec1", NEW)
        for kind, body in (("summary", "s\n"), ("outline", "[00:00:01] o\n"), ("polish", "[00:00:01] p\n")):
            self.put("rec1", body, kind)
        self.assertNotIn("stale_derived", self.record("rec1"))


class TestProofreadHasAWritePath(RewriteCase):
    """`proofread/` was written by a plain copy, so a stale mark on it could never be cleared:
    proofreading again would leave the copy flagged for good. `put --kind proofread` is the
    write path that can clear it."""

    def test_put_kind_proofread_writes_the_file_and_sets_its_flag(self):
        self.put("rec1", OLD)
        self.put("rec1", "corrected text\n", "proofread")
        self.assertEqual("corrected text\n", (self.dir / "proofread" / "rec1.md").read_text())
        self.assertTrue(self.record("rec1").get("has_proofread"))

    def test_proofreading_again_after_a_rewrite_clears_the_mark(self):
        self.put("rec1", OLD)
        self.put("rec1", "corrected text of the old transcript\n", "proofread")
        self.put("rec1", NEW)
        self.assertEqual(["proofread"], self.stale("rec1"))
        self.put("rec1", "corrected text of the new transcript\n", "proofread")
        self.assertNotIn("stale_derived", self.record("rec1"))

    def test_a_proofread_for_a_recording_that_was_never_cached_is_refused(self):
        with self.assertRaises(SystemExit):
            self.put("rec_ghost", "corrected text\n", "proofread")

    def test_the_command_line_accepts_the_kind(self):
        env = {**__import__("os").environ, "PLAUD_CACHE_DIR": str(self.dir)}
        run = lambda *a, stdin: __import__("subprocess").run(  # noqa: E731
            [__import__("sys").executable, str(REPO_ROOT / "scripts" / "cache.py"), "put", "--id", "rec1", *a],
            input=stdin, text=True, capture_output=True, env=env)
        self.assertEqual(0, run(stdin=OLD).returncode)
        done = run("--kind", "proofread", stdin="corrected\n")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertTrue((self.dir / "proofread" / "rec1.md").is_file())


class TestReadersSayWhenSomethingIsStale(RewriteCase):
    def test_show_warns_on_stderr_and_still_prints_the_file(self):
        self.with_all_derived()
        self.put("rec1", NEW)
        out, err = self.capture(cache.cmd_show, argparse.Namespace(id="rec1", kind="outline"))
        self.assertIn("outline of the old text", out)
        self.assertIn("outline", err)
        self.assertIn("earlier version", err)

    def test_show_says_nothing_extra_for_a_current_file(self):
        self.with_all_derived()
        self.put("rec1", OLD)
        _, err = self.capture(cache.cmd_show, argparse.Namespace(id="rec1", kind="outline"))
        self.assertEqual("", err)

    def test_show_of_the_transcript_itself_never_warns(self):
        self.with_all_derived()
        self.put("rec1", NEW)
        _, err = self.capture(cache.cmd_show, argparse.Namespace(id="rec1", kind="transcript"))
        self.assertEqual("", err)

    def test_status_counts_stale_items_by_kind(self):
        self.with_all_derived()
        self.put("rec1", NEW)
        out, _ = self.capture(cache.cmd_status, argparse.Namespace(ids_only=False))
        stale_line = next((l for l in out.splitlines() if l.startswith("stale")), None)
        self.assertIsNotNone(stale_line, f"no stale line in:\n{out}")
        for kind in ("summary", "outline", "polish"):
            self.assertIn(f"{kind} 1", stale_line)

    def test_status_has_no_stale_line_when_nothing_is_stale(self):
        self.with_all_derived()
        out, _ = self.capture(cache.cmd_status, argparse.Namespace(ids_only=False))
        self.assertFalse([l for l in out.splitlines() if l.startswith("stale")])

    def _hit_line(self, out: str, needle: str) -> str:
        for line in out.splitlines():
            if line.lstrip().startswith("│") and needle in line:
                return line
        self.fail(f"no hit line containing {needle!r} in:\n{out}")

    def test_a_search_hit_from_a_stale_summary_is_marked(self):
        for rg in _engines():
            with self.subTest(ripgrep=rg):
                self.with_all_derived("recA" if not rg else "recB")
                rec = "recA" if not rg else "recB"
                self.put(rec, NEW)
                ns = argparse.Namespace(pattern="summary of the old", case_sensitive=False, max_lines=5)
                with mock.patch.object(cache, "_have_rg", return_value=rg):
                    out, _ = self.capture(cache.cmd_search, ns)
                self.assertIn("[summary, stale]", self._hit_line(out, "summary of the old"))

    def test_a_search_hit_from_a_current_summary_is_not_marked_stale(self):
        self.with_all_derived()
        self.put("rec1", OLD)
        ns = argparse.Namespace(pattern="summary of the old", case_sensitive=False, max_lines=5)
        with mock.patch.object(cache, "_have_rg", return_value=False):
            out, _ = self.capture(cache.cmd_search, ns)
        line = self._hit_line(out, "summary of the old")
        self.assertIn("[summary]", line)
        self.assertNotIn("stale", line)

    def test_a_search_hit_from_a_stale_proofread_file_is_marked(self):
        self.put("rec1", OLD)
        (self.dir / "proofread").mkdir()
        (self.dir / "proofread" / "rec1.md").write_text("corrected zebra of the old transcript\n")
        self.put("rec1", NEW)
        ns = argparse.Namespace(pattern="zebra", case_sensitive=False, max_lines=5)
        with mock.patch.object(cache, "_have_rg", return_value=False):
            out, _ = self.capture(cache.cmd_search, ns)
        self.assertIn("[corrected, stale]", self._hit_line(out, "zebra"))


class TestOlderManifestsStillWork(RewriteCase):
    def test_a_manifest_with_flags_and_no_new_field_means_nothing_is_stale(self):
        """Caches written by 0.11 / 0.12 have flags and no `stale_derived`."""
        self.with_all_derived()
        man = json.loads((self.dir / "manifest.json").read_text())
        man["recordings"]["rec1"].pop("stale_derived", None)
        (self.dir / "manifest.json").write_text(json.dumps(man))
        out, err = self.capture(cache.cmd_show, argparse.Namespace(id="rec1", kind="summary"))
        self.assertEqual("", err)
        status, _ = self.capture(cache.cmd_status, argparse.Namespace(ids_only=False))
        self.assertFalse([l for l in status.splitlines() if l.startswith("stale")])
        self.put("rec1", OLD)
        self.assertEqual([], self.stale("rec1"))

    def test_the_first_ever_put_has_no_stale_field(self):
        self.put("rec1", OLD)
        self.assertNotIn("stale_derived", self.record("rec1"))


if __name__ == "__main__":
    unittest.main()
