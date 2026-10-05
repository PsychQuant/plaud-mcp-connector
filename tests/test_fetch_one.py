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
`skills/plaud-download/SKILL.md`. Whether CLI 0.3.14 truncates a transcript is a
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
import shutil
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
    import time
    mode = os.environ.get("FAKE_PLAUD_MODE", "ok")
    args = sys.argv[1:]
    out = args[args.index("-o") + 1] if "-o" in args else None
    polished = "--polished" in args
    if args and args[0] == "file":
        # what the real `plaud file <id>` prints: a header, then `key: value` lines
        if mode == "auth_fail":
            sys.stderr.write("\\u2717 [AUTH_FAILED] Token invalid or expired. Run `plaud login`.\\n")
            sys.exit(1)
        sys.stdout.write("- Fetching file...\\n\\nFile Details:\\n\\n")
        sys.stdout.write("  id:           " + args[1] + "\\n")
        sys.stdout.write("  name:         " + os.environ.get("FAKE_PLAUD_NAME", "Named by the CLI") + "\\n")
        sys.stdout.write("  created_at:   2026-10-01T09:00:00\\n")
        sys.stdout.write("  duration:     " + os.environ.get("FAKE_PLAUD_DURATION", "1h02m03s") + "\\n")
        sys.exit(0)
    if mode == "hang" or (mode == "polish_hang" and polished):
        time.sleep(30)
    if mode == "auth_fail":
        sys.stderr.write("\\u2717 [AUTH_FAILED] Token invalid or expired. Run `plaud login`.\\n")
        sys.exit(1)
    if mode == "polish_fail" and polished:
        sys.stderr.write("error: no polished block for this recording\\n")
        sys.exit(1)
    body = "" if mode == "empty" else ({polished!r} if polished else {raw!r})
    body = body + os.environ.get("FAKE_PLAUD_TAIL", "")
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


class TestFetchOneMetadata(FetchOneTestCase):
    """The skill passes only `--id`. Recording names come from Plaud and can contain
    anything, so they must not be pasted into a shell command line; the script
    reads `name`, `created_at` and `duration` from `plaud file <id>` itself."""

    def fetch_id_only(self, **kw) -> subprocess.CompletedProcess:
        env = self._env(kw.pop("mode", "ok"))
        env.update(kw.pop("extra_env", {}))
        return subprocess.run([sys.executable, str(FETCH_ONE), "--id", "rec1", *kw.pop("args", [])],
                              capture_output=True, text=True, env=env, timeout=60)

    def shown(self) -> str:
        return self.cache_py("show", "rec1").stdout

    def test_metadata_is_read_from_plaud_file_when_not_given(self):
        proc = self.fetch_id_only(extra_env={"FAKE_PLAUD_NAME": "Budget review"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        shown = self.shown()
        self.assertIn('name: "Budget review"', shown)
        self.assertIn("created_at: 2026-10-01T09:00:00", shown)
        self.assertIn("duration_ms: 3723000", shown)  # 1h02m03s

    def test_a_shell_hostile_name_is_stored_verbatim_and_runs_nothing(self):
        hostile = 'x"; touch PWNED; echo "$(id)'
        proc = self.fetch_id_only(extra_env={"FAKE_PLAUD_NAME": hostile})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(pathlib.Path("PWNED").exists())
        self.assertIn("touch PWNED", self.shown())

    def test_explicit_flags_win_over_what_plaud_file_says(self):
        proc = subprocess.run(
            [sys.executable, str(FETCH_ONE), "--id", "rec1", "--name", "Given name"],
            capture_output=True, text=True, env=self._env(), timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('name: "Given name"', self.shown())

    def test_a_logged_out_cli_fails_on_plaud_file_with_exit_5(self):
        proc = self.fetch_id_only(mode="auth_fail")
        self.assertEqual(proc.returncode, 5, proc.stderr)
        self.assertEqual(self.cached_count(), "0 recordings")


class TestFetchOneRobustness(FetchOneTestCase):
    """What verify R1 found outside the happy path."""

    def run_fetch(self, *args: str, mode: str = "ok", timeout_s: str = "300") -> subprocess.CompletedProcess:
        env = self._env(mode)
        env["PLAUD_FETCH_TIMEOUT"] = timeout_s
        return subprocess.run([sys.executable, str(FETCH_ONE), *args], capture_output=True,
                              text=True, env=env, timeout=120)

    def test_a_raw_fetch_that_hangs_exits_4_and_writes_nothing(self):
        proc = self.run_fetch("--id", "rec1", mode="hang", timeout_s="1")
        self.assertEqual(proc.returncode, 4, proc.stderr)
        self.assertIn("timed out", proc.stderr)
        self.assertEqual(self.cached_count(), "0 recordings")

    def test_a_polish_fetch_that_hangs_on_a_refresh_does_not_leave_a_stale_polish(self):
        self.assertEqual(self.run_fetch("--id", "rec1").returncode, 0)
        self.assertIn(POLISHED_MARK, self.srt().stdout)
        proc = self.run_fetch("--id", "rec1", "--force", mode="polish_hang", timeout_s="1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("older polished", proc.stderr)
        out = self.srt()
        self.assertIn(RAW_MARK, out.stdout)
        self.assertNotIn(POLISHED_MARK, out.stdout)

    def test_an_id_that_could_pass_as_an_option_is_rejected_with_exit_2(self):
        for bad in ("-o", "--help", "-rf"):
            proc = self.run_fetch(f"--id={bad}")
            self.assertEqual(proc.returncode, 2, f"{bad!r}: {proc.stderr}")
        self.assertEqual(self.cached_count(), "0 recordings")

    def test_an_id_with_shell_metacharacters_is_rejected_with_exit_2(self):
        proc = self.run_fetch("--id", 'x"; echo pwned; "')
        self.assertEqual(proc.returncode, 2, proc.stderr)

    def test_a_manifest_that_says_complete_but_whose_file_is_gone_is_fetched_again(self):
        self.assertEqual(self.run_fetch("--id", "rec1").returncode, 0)
        (self.cache_dir / "rec1.md").unlink()
        proc = self.run_fetch("--id", "rec1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("already cached", proc.stdout)
        self.assertTrue((self.cache_dir / "rec1.md").is_file())


class TestFetchOneR2(FetchOneTestCase):
    """What verify R2 found. Each of these was reproduced or read out of the code
    before it was classified."""

    def run_id(self, *args: str, mode: str = "ok", extra_env: dict | None = None) -> subprocess.CompletedProcess:
        env = self._env(mode)
        env.update(extra_env or {})
        return subprocess.run([sys.executable, str(FETCH_ONE), "--id", "rec1", *args],
                              capture_output=True, text=True, env=env, timeout=60)

    def shown(self) -> str:
        return self.cache_py("show", "rec1").stdout

    def test_a_name_that_looks_like_an_option_can_still_be_cached(self):
        """`--name -notes` is two argv items, and argparse reads the second as an
        option. The transcript was already fetched, so the failure used to be
        reported as exit 4 'nothing usable came back' — wrong on both counts."""
        for name in ("-notes", "--x", "-"):
            shutil.rmtree(self.cache_dir, ignore_errors=True)   # each name starts from an empty cache
            proc = self.run_id(extra_env={"FAKE_PLAUD_NAME": name})
            self.assertEqual(proc.returncode, 0, f"{name!r}: {proc.stderr}")
            self.assertIn(f'name: "{name}"', self.shown(), name)

    def test_a_recording_named_auth_failed_is_not_mistaken_for_a_logged_out_cli(self):
        """The check searched all of stdout, and `plaud file` prints the name."""
        proc = self.run_id(extra_env={"FAKE_PLAUD_NAME": "Investigating AUTH_FAILED errors"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Investigating AUTH_FAILED errors", self.shown())

    def test_a_refresh_interrupted_before_the_polish_is_written_leaves_no_stale_polish(self):
        """Raw is replaced first, polish second; a crash between them used to leave
        the NEW raw beside the OLD polish, and `to_srt` prefers a polish file whenever
        one exists. The old polish is now removed before anything is rewritten, so
        every stopping point is internally consistent."""
        self.assertEqual(self.run_id().returncode, 0)
        self.assertIn(POLISHED_MARK, self.srt().stdout)
        crash = (
            "import os, sys\n"
            f"sys.path.insert(0, {str(SCRIPTS)!r})\n"
            "import fetch_one\n"
            "real = fetch_one._put\n"
            "def patched(rec_id, body, *a):\n"
            "    if '--kind' in a and 'polish' in a:\n"
            "        os._exit(137)  # the process is killed right here\n"
            "    return real(rec_id, body, *a)\n"
            "fetch_one._put = patched\n"
            "sys.argv = ['fetch_one.py', '--id', 'rec1', '--force']\n"
            "sys.exit(fetch_one.main())\n")
        env = self._env()
        env["FAKE_PLAUD_TAIL"] = "[00:10 - 00:12] Speaker 1: refreshed content\n"
        proc = subprocess.run([sys.executable, "-c", crash], capture_output=True, text=True,
                              env=env, timeout=60)
        self.assertEqual(proc.returncode, 137, proc.stderr)
        out = self.srt()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("refreshed content", out.stdout)       # the new raw is what is read
        self.assertNotIn(POLISHED_MARK, out.stdout)           # the old polish is gone

    def test_a_malformed_timeout_setting_falls_back_with_a_warning(self):
        proc = self.run_id(extra_env={"PLAUD_FETCH_TIMEOUT": "abc"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("PLAUD_FETCH_TIMEOUT", proc.stderr)

    def test_the_duration_the_real_cli_prints_is_converted_to_milliseconds(self):
        """Measured against the real CLI: `duration:     10m42s` for a 642000 ms
        recording, the same value `list_files` reports."""
        proc = self.run_id(extra_env={"FAKE_PLAUD_DURATION": "10m42s"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("duration_ms: 642000", self.shown())


class TestFetchOneAndTheSyncCutoff(FetchOneTestCase):
    def test_fetching_one_recording_does_not_move_the_incremental_cutoff(self):
        """Found by verify R1. A cache that had a full sweep reports where an
        incremental `plaud-download` may stop paging. Fetching ONE newer recording used
        to move that point to the new recording, so the next sync skipped every
        recording between the last real sync and it — silently."""
        self.assertEqual(self.cache_py(
            "put", "--id", "old1", "--created-at", "2026-03-01T09:00:00",
            "--complete", "true", "--last-cursor", "",
            stdin="[00:00:01] Speaker 1: hi\n").returncode, 0)
        self.assertEqual(self.cache_py("mark-full-sweep").returncode, 0)
        before = self.cache_py("status", "--list-cutoff").stdout.strip()
        self.assertEqual(before, "2026-02-28T09:00:00")

        proc = self.fetch()  # created-at 2026-10-01 — far newer than the March record
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.cache_py("status", "--list-cutoff").stdout.strip(), before)


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
