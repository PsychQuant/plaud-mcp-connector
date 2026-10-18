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



class TestWhatCountsAsARewrite(RewriteCase):
    """Verify round 1: "the text changed" was a byte comparison, so a rewrite that only
    changes how the times are written marked every derived file stale, and the mark is
    sticky. What was SAID is compared, not how it was stamped."""

    RANGED = ("[00:00:01.000 - 00:00:03.000] Speaker 1: the budget is approved\n"
              "[00:00:05.000 - 00:00:07.500] Speaker 1: next item\n")

    def test_a_point_stamp_rewritten_as_a_range_is_not_a_change(self):
        self.with_all_derived()
        self.put("rec1", self.RANGED)
        self.assertEqual([], self.stale("rec1"))

    def test_line_endings_are_not_a_change(self):
        self.put("rec1", OLD.replace("\n", "\r\n"))
        self.put("rec1", "a summary\n", "summary")
        self.put("rec1", OLD.replace("\n", "\r\n"))
        self.assertEqual([], self.stale("rec1"))

    def test_the_stored_file_in_the_other_unicode_form_is_not_a_change(self):
        # Legacy caches hold NFD; the stored side is normalised too. Without that,
        # the NFC body below would differ from what is on disk.
        self.put("rec1", "[00:00:01] Speaker 1: caf\u00e9 au lait\n")
        self.put("rec1", "a summary\n", "summary")
        path = self.dir / "rec1.md"
        path.write_text(unicodedata.normalize("NFD", path.read_text()))
        self.put("rec1", "[00:00:01] Speaker 1: caf\u00e9 au lait\n")
        self.assertEqual([], self.stale("rec1"))

    def test_other_words_or_another_speaker_still_count(self):
        self.with_all_derived()
        self.put("rec1", OLD.replace("Speaker 1: next item", "Speaker 2: next item"))
        self.assertEqual(["outline", "polish", "summary"], self.stale("rec1"))

    def test_finishing_a_cut_short_fetch_stales_only_the_copy_made_from_the_short_text(self):
        # What Plaud made for the whole recording (summary, outline, polish) is not older
        # than the text just because the first fetch stopped early. A proofread copy is
        # made from the cached text, so it is.
        part = "[00:00:01] Speaker 1: the budget is approved\n"
        ns = argparse.Namespace(id="rec1", name="n", created_at="2026-10-01T00:00:00Z",
                                duration="60000", kind="transcript", complete="false")
        with mock.patch("sys.stdin", io.StringIO(part)):
            self.capture(cache.cmd_put, ns)
        self.put("rec1", "a summary\n", "summary")
        self.put("rec1", "[00:00:01] outline\n", "outline")
        self.put("rec1", "[00:00:01] Speaker 1: the budget is approved\n", "proofread")
        self.put("rec1", OLD)                       # complete, and the old text is its start
        self.assertEqual(["proofread"], self.stale("rec1"))


class TestAFileThatCannotBeReadDoesNotBlockTheRewrite(RewriteCase):
    def test_a_transcript_that_does_not_decode_can_be_rewritten(self):
        self.put("rec1", OLD)
        self.put("rec1", "a summary\n", "summary")
        with (self.dir / "rec1.md").open("ab") as fh:
            fh.write(b"\xe4\xb8")                  # a multi-byte sequence cut short
        self.put("rec1", OLD)                       # must repair, not raise
        self.assertIn("approved", (self.dir / "rec1.md").read_text())
        self.assertEqual(["summary"], self.stale("rec1"))

    def test_a_record_that_is_not_a_dict_does_not_stop_a_rewrite(self):
        self.put("rec1", OLD)
        self.put("rec1", "a summary\n", "summary")
        man = json.loads((self.dir / "manifest.json").read_text())
        man["recordings"]["rec1"] = "hand-edited"
        (self.dir / "manifest.json").write_text(json.dumps(man))
        self.put("rec1", NEW)
        self.assertIn("rejected", (self.dir / "rec1.md").read_text())


class TestShowDoesNotNeedAHealthyManifest(RewriteCase):
    def show(self, rec="rec1", kind="transcript", **extra):
        ns = argparse.Namespace(id=rec, kind=kind, fresh_only=False, **extra)
        return self.capture(cache.cmd_show, ns)

    def test_the_transcript_prints_whatever_the_manifest_looks_like(self):
        self.put("rec1", OLD)
        for broken in ("{}", "[]", "not json at all"):
            (self.dir / "manifest.json").write_text(broken)
            out, err = self.show()
            self.assertIn("approved", out, broken)
            # The transcript is never stale, so showing it has no reason to open the
            # manifest, and a corrupt one must not put a warning beside it
            self.assertEqual("", err, broken)

    def test_a_derived_file_prints_when_the_manifest_cannot_say_whether_it_is_stale(self):
        self.with_all_derived()
        (self.dir / "manifest.json").write_text("[]")
        out, _err = self.show(kind="outline")
        self.assertIn("outline of the old text", out)


class TestTheRemedyNamesTheRightStep(RewriteCase):
    def show(self, kind):
        ns = argparse.Namespace(id="rec1", kind=kind, fresh_only=False)
        return self.capture(cache.cmd_show, ns)

    def test_a_stale_proofread_copy_is_not_told_to_be_fetched(self):
        self.put("rec1", OLD)
        self.put("rec1", "[00:00:01] Speaker 1: the budget is approved\n", "proofread")
        self.put("rec1", NEW)
        _out, err = self.show("proofread")
        self.assertIn("put --kind proofread", err)
        self.assertNotIn("Fetch it again", err)

    def test_a_stale_outline_is_told_to_be_fetched_again(self):
        self.with_all_derived()
        self.put("rec1", NEW)
        _out, err = self.show("outline")
        self.assertIn("Fetch it again", err)


class TestFreshOnly(RewriteCase):
    def show(self, kind="outline"):
        ns = argparse.Namespace(id="rec1", kind=kind, fresh_only=True)
        return self.capture(cache.cmd_show, ns)

    def test_a_current_file_prints_and_exits_normally(self):
        self.with_all_derived()
        out, err = self.show()
        self.assertIn("outline of the old text", out)
        self.assertEqual("", err)

    def test_a_stale_file_prints_nothing_and_exits_3(self):
        self.with_all_derived()
        self.put("rec1", NEW)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                cache.cmd_show(argparse.Namespace(id="rec1", kind="outline", fresh_only=True))
        self.assertEqual(3, ctx.exception.code)
        self.assertEqual("", out.getvalue())
        self.assertIn("earlier version", err.getvalue())

    def test_a_missing_file_still_fails_like_before(self):
        self.put("rec1", OLD)
        with self.assertRaises(SystemExit) as ctx:
            self.show("outline")
        self.assertNotEqual(0, ctx.exception.code)


class TestTheSkillsOwnCommandLine(unittest.TestCase):
    """The warning is only worth having if the command the skill actually runs reaches it.
    Verify round 1: it ended in `2>/dev/null`, and `show` exits 0 for a stale file, so the
    fallback to fetching never ran and the old outline was printed with nothing to say so."""

    def _command(self) -> str:
        import re
        text = (REPO_ROOT / "skills" / "plaud-outline" / "SKILL.md").read_text(encoding="utf-8")
        step = text[text.index("### 2. Read the cached one, or fetch"):]
        return re.search(r"```bash\n(.*?)```", step, re.S).group(1)

    def _run(self, cache_dir, plaud_dir):
        import os
        import subprocess
        cmd = self._command().replace("${CLAUDE_PLUGIN_ROOT}", str(REPO_ROOT)).replace("<id>", "rec1")
        env = {**os.environ, "PLAUD_CACHE_DIR": str(cache_dir),
               "PATH": f"{plaud_dir}:{os.environ['PATH']}"}
        return subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, env=env)

    def _cli(self, cache_dir, *args, stdin=""):
        import os
        import subprocess
        env = {**os.environ, "PLAUD_CACHE_DIR": str(cache_dir)}
        return subprocess.run(["python3", str(REPO_ROOT / "scripts" / "cache.py"), *args],
                              input=stdin, capture_output=True, text=True, env=env)

    def _setup(self):
        tmp = tempfile.TemporaryDirectory(prefix="plaud-skillcmd-")
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        cache_dir, bin_dir = root / "cache", root / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "plaud"
        fake.write_text("#!/bin/sh\necho FETCHED-FRESH\n")
        fake.chmod(0o755)
        for args, body in ((("put", "--id", "rec1", "--name", "n", "--created-at",
                             "2026-10-01T00:00:00Z", "--duration", "60000"), OLD),
                           (("put", "--id", "rec1", "--kind", "outline"), "cached outline\n")):
            self.assertEqual(0, self._cli(cache_dir, *args, stdin=body).returncode)
        return cache_dir, bin_dir

    def test_a_current_outline_is_read_from_the_cache(self):
        cache_dir, bin_dir = self._setup()
        p = self._run(cache_dir, bin_dir)
        self.assertEqual("cached outline\n", p.stdout)

    def test_a_stale_outline_falls_through_to_fetching(self):
        cache_dir, bin_dir = self._setup()
        self.assertEqual(0, self._cli(cache_dir, "put", "--id", "rec1", "--name", "n",
                                      "--created-at", "2026-10-01T00:00:00Z", "--duration",
                                      "60000", stdin=NEW).returncode)
        p = self._run(cache_dir, bin_dir)
        self.assertEqual("FETCHED-FRESH\n", p.stdout)

if __name__ == "__main__":
    unittest.main()
