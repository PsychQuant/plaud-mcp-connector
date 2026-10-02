#!/usr/bin/env python3
"""`fetch_one.py` — land ONE recording's transcript in the cache, CLI path only (#65).

Why this exists: `plaud-to-srt` used to run on the cache alone and answer "not
cached" with "run the library indexer first". Someone who wants one recording's
subtitles does not want to index a date range. This script is the one-recording
fetch: given an id, write the raw transcript and its polished twin into the
cache, through `cache.py put`, so that `to_srt.py` can read both.

**What is faked, and what is not.** The `plaud` executable is replaced by a small
script on PATH, because the real one needs a login and a network. Everything
else is real: `fetch_one.py`, `cache.py`, `to_srt.py`, and the cache directory
(isolated through PLAUD_CACHE_DIR). The assertions read the cache back through
`to_srt.py` — the consumer that matters — rather than through the files `put`
happens to write, so a change in the on-disk layout cannot make them pass
vacuously.

**What the fake cannot tell us**, stated rather than implied: the real CLI's
wording. The one error text it imitates (`[AUTH_FAILED] ... Run \\`plaud login\\``)
was copied from a real unauthenticated run on 2026-10-02 with CLI 0.3.14; the
rest of its behaviour (output format, `--polished`, `-o`) is taken from
`skills/plaud-sync/SKILL.md`. Whether CLI 0.3.14 truncates a transcript is a
measurement against a real recording, not something a fake can establish.

Exit codes, as a contract (the skill branches on them):

    0  cached (or already cached and complete)
    3  `plaud` is not on PATH               -> skill falls back to the MCP
    4  the CLI answered but gave nothing    -> nothing was cached
    5  the CLI is not logged in             -> tell the user: `plaud login`
"""
from __future__ import annotations

import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
FETCH_ONE = SCRIPTS / "fetch_one.py"
CACHE_PY = SCRIPTS / "cache.py"
TO_SRT = SCRIPTS / "to_srt.py"

RAW = (
    "[00:00 - 00:04] Speaker 1: uh so the budget is um split in two\n"
    "[00:04 - 00:09] Speaker 2: right the first half lands in March\n"
)
POLISHED = (
    "[00:00 - 00:04] Speaker 1: the budget is split in two\n"
    "[00:04 - 00:09] Speaker 2: the first half lands in March\n"
)

# Short, wrap-proof fragments. `to_srt` folds cue lines at 42 characters, so a whole
# sentence can be split across lines; these cannot. The polished fragment is not a
# substring of the raw sentence (raw has "um" in the middle), so each one identifies
# its own source unambiguously.
RAW_MARK = "uh so the budget is um"
POLISHED_MARK = "the budget is split"

# A stand-in for `plaud`. Mode comes from FAKE_PLAUD_MODE so one script serves
# every case. It writes the transcript to the -o path exactly as the real CLI
# does, and prints its own errors to stderr.
FAKE_PLAUD = textwrap.dedent('''\
    #!{python}
    import os, sys
    mode = os.environ.get("FAKE_PLAUD_MODE", "ok")
    args = sys.argv[1:]
    out = args[args.index("-o") + 1] if "-o" in args else None
    polished = "--polished" in args
    if mode == "auth_fail":
        sys.stderr.write("\\u2717 [AUTH_FAILED] Token invalid or expired. Run `plaud login`.\\n")
        sys.exit(1)
    if mode == "polish_fail" and polished:
        sys.stderr.write("error: no polished block for this recording\\n")
        sys.exit(1)
    body = "" if mode == "empty" else ({polished!r} if polished else {raw!r})
    if out:
        open(out, "w", encoding="utf-8").write(body)
    else:
        sys.stdout.write(body)
''')


class FetchOneTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="plaud-fetch-one-")
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        self.cache_dir = root / "cache"
        self.bin_dir = root / "bin"
        self.empty_dir = root / "empty"
        self.bin_dir.mkdir()
        self.empty_dir.mkdir()
        fake = self.bin_dir / "plaud"
        fake.write_text(FAKE_PLAUD.format(python=sys.executable, raw=RAW, polished=POLISHED),
                        encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

    # -- helpers --------------------------------------------------------
    def _env(self, mode: str = "ok", with_cli: bool = True) -> dict:
        env = dict(os.environ)
        env["PLAUD_CACHE_DIR"] = str(self.cache_dir)
        env["FAKE_PLAUD_MODE"] = mode
        # PATH holds either the fake CLI or nothing at all. Never the real PATH:
        # a real `plaud` on this machine must not be reachable from the test.
        env["PATH"] = str(self.bin_dir if with_cli else self.empty_dir)
        return env

    def fetch(self, *extra: str, mode: str = "ok", with_cli: bool = True,
              rec_id: str = "rec1") -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(FETCH_ONE), "--id", rec_id, "--name", "Budget meeting",
             "--created-at", "2026-10-01T09:00:00.000Z", "--duration", "1h", *extra],
            capture_output=True, text=True, env=self._env(mode, with_cli), timeout=60)

    def cache_py(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(CACHE_PY), *args], input=stdin,
                              capture_output=True, text=True, env=self._env(), timeout=30)

    def srt(self, *args: str, rec_id: str = "rec1") -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(TO_SRT), rec_id, *args],
                              capture_output=True, text=True, env=self._env(), timeout=30)

    def cached_count(self) -> str:
        out = self.cache_py("status").stdout
        return next(ln for ln in out.splitlines() if ln.startswith("cached")).split(":", 1)[1].strip()


class TestFetchOneSuccess(FetchOneTestCase):
    def test_caches_raw_and_polished_and_to_srt_reads_both(self):
        proc = self.fetch()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.cached_count(), "1 recordings")

        polished = self.srt()  # default source is polished
        self.assertEqual(polished.returncode, 0, polished.stderr)
        self.assertIn(POLISHED_MARK, polished.stdout)
        self.assertNotIn("uh so the budget", polished.stdout)

        verbatim = self.srt("--source", "verbatim")
        self.assertEqual(verbatim.returncode, 0, verbatim.stderr)
        self.assertIn(RAW_MARK, verbatim.stdout)

    def test_success_message_names_the_id(self):
        proc = self.fetch()
        self.assertIn("rec1", proc.stdout)


class TestFetchOneRefusals(FetchOneTestCase):
    def test_cli_not_on_path_exits_3_and_writes_nothing(self):
        proc = self.fetch(with_cli=False)
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("MCP", proc.stderr)
        self.assertEqual(self.cached_count(), "0 recordings")

    def test_not_logged_in_exits_5_and_points_at_plaud_login(self):
        proc = self.fetch(mode="auth_fail")
        self.assertEqual(proc.returncode, 5, proc.stderr)
        self.assertIn("plaud login", proc.stderr)
        self.assertEqual(self.cached_count(), "0 recordings")

    def test_empty_transcript_exits_4_and_writes_nothing(self):
        proc = self.fetch(mode="empty")
        self.assertEqual(proc.returncode, 4, proc.stderr)
        self.assertEqual(self.cached_count(), "0 recordings")


class TestFetchOnePolishIsBestEffort(FetchOneTestCase):
    def test_polish_failure_warns_and_keeps_the_raw_transcript(self):
        proc = self.fetch(mode="polish_fail")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("polish", proc.stderr.lower())
        self.assertEqual(self.cached_count(), "1 recordings")
        # No polish cached: to_srt falls back to the raw transcript, which is
        # the behaviour that predates this script.
        out = self.srt()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn(RAW_MARK, out.stdout)


    def test_forced_refresh_whose_polish_fails_does_not_leave_a_stale_polish(self):
        """`to_srt` prefers a polish file whenever one exists. A refresh that
        replaces the raw transcript but cannot fetch a new polish would otherwise
        leave the OLD polish in place: subtitles from stale text, no error."""
        self.assertEqual(self.fetch().returncode, 0)
        self.assertIn(POLISHED_MARK, self.srt().stdout)  # polish is live

        proc = self.fetch("--force", mode="polish_fail")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("older polished", proc.stderr)
        out = self.srt()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn(RAW_MARK, out.stdout)
        # The polished sentence is not a substring of the raw one (raw has "um"
        # in the middle), so its presence can only mean the stale file was read.
        self.assertNotIn(POLISHED_MARK, out.stdout)


class TestFetchOneIdempotence(FetchOneTestCase):
    def test_already_cached_and_complete_is_a_noop_that_never_calls_the_cli(self):
        self.assertEqual(self.fetch().returncode, 0)
        # CLI now broken: a no-op must not notice, because it must not call it.
        again = self.fetch(mode="auth_fail")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("already cached", again.stdout)

    def test_force_with_a_failing_cli_leaves_the_existing_entry_intact(self):
        self.assertEqual(self.fetch().returncode, 0)
        before = self.srt().stdout
        failed = self.fetch("--force", mode="auth_fail")
        self.assertEqual(failed.returncode, 5, failed.stderr)
        self.assertEqual(self.srt().stdout, before)

    def test_an_incomplete_cached_entry_is_fetched_again(self):
        # What an interrupted MCP paging loop leaves behind: present, flagged incomplete.
        seeded = self.cache_py("put", "--id", "rec1", "--name", "Budget meeting",
                               "--complete", "false", stdin="[00:00:01] Speaker 1: half a transc\n")
        self.assertEqual(seeded.returncode, 0, seeded.stderr)
        proc = self.fetch()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("already cached", proc.stdout)
        self.assertIn(POLISHED_MARK, self.srt().stdout)


if __name__ == "__main__":
    unittest.main()
