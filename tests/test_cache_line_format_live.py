#!/usr/bin/env python3
"""The one check on the cache line format that is not circular (#42).

`tests/test_cache_line_format.py` judges the contract with `to_srt`'s own
parser. That catches the parser drifting away from the contract on its own.
It cannot catch the case where a producer starts writing a third shape and
the parser is widened to match — both sides self-consistent, suite green.

That second case is #40 exactly: `plaud-index`'s CLI path wrote ranges, the
parser accepted points, every fixture in the suite used points, and the
recommended path produced no subtitles for as long as both existed. The only
thing that found it was running the real chain by hand.

So this file runs the real producer and asks whether its output is inside the
contract. It is the same act, moved from somebody's memory into the suite.

## Why opt-in rather than part of `make check`

Three options were on the table (#42):

    (a) call the API on every `make check`
    (b) opt-in via PLAUD_LIVE_TESTS=1                       ← chosen
    (c) commit a redacted sample of real output

(a) is not hermetic — it needs a login, spends somebody's API calls, and
fails for a contributor who has no Plaud account, which teaches people to
ignore a red suite.

(c) was tempting and is the trap the issue names: a committed sample records
what the producer emitted *once*. Detecting drift then means periodically
re-checking the sample against real output — the manual step returns, now
with a second thing to keep in sync. It also puts third-party speech one
redaction bug away from a public repository, and the redaction would itself
need to be correct in a way nothing checks.

(b) keeps the check honest about what it is: a thing a person runs, that the
suite knows how to run, with an unambiguous skip when it cannot.

## Discipline

Reads only. `plaud transcript` writes to a temporary directory that is
removed by `addCleanup`; nothing touches the cache, and no file survives the
run — the repo's standing rule after a run of these left 36 stray
directories in `$TMPDIR` (#39).

**No failure message may contain a transcript line.** Recordings are other
people's speech; a CI log is a publication. Assertions report the shape of an
offending line — its length and its leading punctuation — never its text.
"""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

from test_cache_line_format import to_srt  # noqa: E402  (same parser as the contract)

# A line that carries a timestamp at all. Deliberately looser than the
# contract: this file's job is to find lines the contract does NOT cover, so
# it must be able to see them.
#
# BOTH numeric fields are `\d+` — UNBOUNDED, where `_STAMP` is bounded at four
# digits of minutes and two of hours, and where the seconds field is `[0-5]\d`.
# That gap is the whole point, and it has now been got wrong twice in opposite
# directions:
#
#   round 1  `\d{1,2}`  — NARROWER than the parser. It stopped seeing
#                          `[446:12 - 446:40]`, so the live run would have gone
#                          red on exactly the data class #50 fixed.
#   round 2  `\d{1,4}`  — EQUAL to the parser. `[10000:00]` was invisible to
#                          both, the counts matched, and the assertion passed
#                          green over a line neither side could read.
#
# Both times the comment above it said "looser". A comment claiming a relation
# has nothing checking it, which is why the relation is now pinned by
# `TestTheOracleIsStrictlyLooserThanTheParser` below — every string the parser
# accepts must match here, and some strings that match here must be refused
# there. Widen the parser without widening this, or the reverse, and that test
# goes red before a live run is ever needed.
TIMESTAMPED = re.compile(r"^[\ufeff\s]*[\[(]?\s*\d+\s*:\s*\d+")

# The CLI colours its table. Ids arrive as `\x1b[36m<32 hex>\x1b[39m`, and the
# `m` of the escape is a word character — so `\b[0-9a-f]{32}\b` matched
# nothing at all. Stripped rather than worked around, because the same escapes
# would otherwise leak into any failure message.
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


# One implementation, not two. `to_srt` grew this function when round 2 found
# the CLI's own drop warning quoting ninety raw characters of somebody's speech
# — the very thing `test_a_failure_message_never_quotes_a_transcript_line` has
# forbidden here since this file was written. Pointing that test at the shipped
# function turns a rule this file kept for itself into one the tool obeys too.
shape_of = to_srt.shape_of


class TestRealProducerOutputIsInsideTheContract(unittest.TestCase):
    def setUp(self):
        if os.environ.get("PLAUD_LIVE_TESTS") != "1":
            self.skipTest("PLAUD_LIVE_TESTS=1 not set — the live contract "
                          "check calls the Plaud API and is opt-in (#42)")
        if shutil.which("plaud") is None:
            self.skipTest("plaud CLI not on PATH")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _run(self, *args: str) -> str:
        """Run the CLI, and refuse to skip on a mistake of my own.

        The first version skipped on any non-zero exit with the message
        "likely not logged in". It then skipped forever, because the flag it
        passed did not exist — a live test that never runs is indistinguishable
        from one that passes. Usage errors fail loudly; only the states this
        test genuinely cannot control (no login, no network) skip.
        """
        r = subprocess.run(["plaud", *args], capture_output=True, text=True,
                           timeout=120)
        if r.returncode == 0:
            return r.stdout
        err = (r.stderr + r.stdout).strip()
        if re.search(r"unknown option|unknown command|usage:|must be a",
                     err, re.I):
            self.fail(f"plaud {' '.join(args)} was invoked wrongly — fix this "
                      f"test, do not let it skip:\n  {err.splitlines()[:2]}")
        self.skipTest(f"plaud {args[0]} exited {r.returncode} — not logged in, "
                      f"or no network. This test needs a working CLI session.")

    def test_a_real_transcript_parses_under_the_contract(self):
        listing = ANSI.sub("", self._run("files", "-s", "20"))
        ids = re.findall(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])", listing)
        self.assertTrue(
            ids,
            "no recording ids found in `plaud files` output — the CLI prints a "
            "human table and this test scrapes 32-hex ids from it. If the table "
            "changed, fix the scrape; an empty scrape must not read as a pass.")

        checked = 0
        for fid in ids:
            out = pathlib.Path(self.tmp.name) / "t.txt"
            r = subprocess.run(["plaud", "transcript", str(fid), "-o", str(out)],
                               capture_output=True, text=True, timeout=180)
            if r.returncode != 0 or not out.exists() or not out.stat().st_size:
                continue          # no transcript on this one — see #37
            text = out.read_text(encoding="utf-8")
            timestamped = [l for l in text.splitlines() if TIMESTAMPED.match(l)]
            if not timestamped:
                continue
            parsed = to_srt.parse_segments(text)
            self.assertEqual(
                len(parsed), len(timestamped),
                f"the producer emitted {len(timestamped)} timestamped lines and "
                f"the contract's parser accepted {len(parsed)}. The shapes it "
                f"could not read: "
                + "; ".join(shape_of(l) for l in timestamped
                            if not to_srt.parse_segments(l + "\n"))
                + f"\n\nThis is #40's shape again: a producer writing a form "
                  f"the contract does not list. Measure the new form, then add "
                  f"it to tests/test_cache_line_format.py — do not widen the "
                  f"parser alone.")
            checked += 1
            # Not `break`. Stopping at the first recording that HAS a transcript
            # meant a live run could pass having looked at one short file, while
            # #50's shape only appears past 99 minutes — so the check most likely
            # to matter was the one least likely to run. Three is a compromise
            # against the CLI call each costs.
            if checked >= 3:
                break

        if not checked:
            self.skipTest("none of the 20 most recent recordings has a "
                          "transcript — nine of ten is normal on this "
                          "account (#37), so this is not a failure")


class TestTheOracleIsStrictlyLooserThanTheParser(unittest.TestCase):
    """The one property that makes this file's comparison mean anything.

    `TIMESTAMPED` is the independent side of `len(parsed) == len(timestamped)`.
    If it is NARROWER than the parser the live run goes red on good data; if it
    is EQUAL, the two agree silently about anything neither can read and the
    assertion passes over a real loss. Round 1 shipped the first mistake and
    round 2 shipped the second, each under a comment asserting the opposite.

    So the relation is checked here rather than described there. This runs in
    default CI — no `PLAUD_LIVE_TESTS`, no network — because the failure it
    guards against is invisible in an opt-in test by construction.
    """

    def test_everything_the_parser_accepts_the_oracle_can_see(self):
        for line in ("[00:00] S: x",
                     "[99:59] S: x",
                     "[446:12 - 446:40] S: x",      # #50's shape
                     "[9999:59] S: x",              # the parser's upper bound
                     "[1:02:03] S: x",
                     "[12:59:59] S: x",
                     "[00:10.500] S: x",
                     "[00:10,500] S: x"):
            with self.subTest(line=line):
                self.assertTrue(
                    to_srt.parse_segments(line), f"precondition: {line!r} must parse")
                self.assertTrue(
                    TIMESTAMPED.match(line),
                    f"the parser accepts {line!r} and the oracle cannot see it. The "
                    f"live comparison would go red on data that is actually fine — "
                    f"round 1's mistake, which cost a spurious failure on exactly "
                    f"the recordings #50 fixed")

    def test_the_oracle_sees_shapes_the_parser_refuses(self):
        """Where the two differ is where a real drift becomes visible."""
        for line in ("[10000:00] S: five-digit minutes",
                     "[00:99] S: ninety-nine seconds",
                     "[100:5] S: one-digit seconds",
                     " [00:13] S: indented",
                     "(00:13 - 00:20) S: paren form",
                     "\ufeff[00:00] S: byte-order mark"):
            with self.subTest(line=line):
                self.assertTrue(
                    TIMESTAMPED.match(line),
                    f"the oracle cannot see {line!r}, so a producer drifting to it "
                    f"would be dropped by the parser and agreed with here")
                self.assertFalse(
                    to_srt.parse_segments(line),
                    f"precondition: {line!r} is meant to be out of contract")


class TestTheLiveTestCannotSilentlyStopRunning(unittest.TestCase):
    """The parts of the live check that a live run cannot verify.

    A skipped test and a passing test are the same exit code. So mutating
    `_run` to skip on a usage error leaves the suite green — the failure mode
    this file is built around is invisible from inside its own live path.
    These run without the API and pin the two decisions that keep it honest.
    """

    def _probe(self):
        case = TestRealProducerOutputIsInsideTheContract(
            "test_a_real_transcript_parses_under_the_contract")
        return case

    def test_a_usage_error_fails_and_does_not_skip(self):
        if shutil.which("plaud") is None:
            self.skipTest("plaud CLI not on PATH")
        case = self._probe()
        # Not `assertRaises(failureException)`: SkipTest is not caught by it,
        # so a skipping `_run` would propagate out and mark *this* test
        # skipped — reported as OK. The trap climbed one level and had to be
        # caught explicitly. Third time this session that a skip wore a pass's
        # clothes.
        try:
            case._run("files", "--definitely-not-a-flag")
        except case.failureException:
            return
        except unittest.SkipTest as e:
            self.fail(f"a wrong invocation skipped instead of failing ({e}) — "
                      f"the live check would then skip forever, and a skip "
                      f"is indistinguishable from a pass")
        self.fail("a wrong invocation neither failed nor skipped")

    def test_ansi_escapes_never_reach_a_message(self):
        """Its job is the failure text, not the id scrape.

        Removing `ANSI.sub` leaves the scrape working — the id pattern uses
        lookarounds, so a colour code before the hex is harmless. What it
        still buys is that no escape sequence lands in an assertion message,
        which is checked here rather than assumed.
        """
        coloured = "\x1b[36m610649d8d10e4869e0bf84ca4c336051\x1b[39m  name"
        self.assertNotIn("\x1b", ANSI.sub("", coloured))
        self.assertIn("610649d8d10e4869e0bf84ca4c336051", ANSI.sub("", coloured))

    def test_a_failure_message_never_quotes_a_transcript_line(self):
        """Recordings are other people's speech; a CI log is a publication."""
        line = "[01:01 - 01:55] Speaker 1: something somebody actually said"
        described = shape_of(line)
        self.assertNotIn("somebody actually said", described)
        self.assertIn(str(len(line)), described)
