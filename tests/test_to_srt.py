"""Tests for scripts/to_srt.py (issue #4).

Subtitle timing is the kind of thing that looks right and is off by a second, so
these pin the arithmetic and the edge cases rather than smoke-testing that it
produces *some* output.
"""

import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "to_srt.py"


def cli_env(cache: pathlib.Path, **extra: str) -> dict:
    """Environment for running to_srt.py under test.

    Always pins PLAUD_CONFIG, not just PLAUD_CACHE_DIR. Once the CLI started
    reading preferences (#29), every subprocess test silently inherited whatever
    the developer had configured — and one #22 test, which asserts the polish is
    used, fails outright for anyone who has chosen verbatim. It passed here only
    because this machine had no config file.

    Isolation lives in this one helper rather than in each test's env dict so
    that forgetting it is not possible: the next person to add a CLI test gets
    it by using the same door everyone else uses.
    """
    return {
        **os.environ,
        "PLAUD_CACHE_DIR": str(cache),
        # Inside the per-test cache dir, which is a fresh tempdir — so it is
        # both absent by default and impossible to share between tests.
        "PLAUD_CONFIG": str(cache / "test-config.json"),
        **extra,
    }


_spec = importlib.util.spec_from_file_location("to_srt", SCRIPT)
to_srt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(to_srt)


class TestTimestampParsing(unittest.TestCase):
    def test_accepts_the_forms_the_cache_actually_contains(self) -> None:
        cases = {
            "00:00:00": 0.0,
            "00:01:30": 90.0,
            "1:02:03": 3723.0,
            "02:03": 123.0,            # MM:SS short form
            "00:00:01.250": 1.25,
            "00:00:01,250": 1.25,      # comma decimal, as some tools emit
        }
        for raw, want in cases.items():
            with self.subTest(raw=raw):
                self.assertAlmostEqual(to_srt.parse_timestamp(raw), want, places=3)

    def test_rejects_garbage(self) -> None:
        for raw in ("", "abc", "1", "1:2:3:4"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    to_srt.parse_timestamp(raw)

    def test_rejects_what_its_docstring_says_it_rejects(self) -> None:
        """"Raises ValueError on anything else" was not true, and #53 is that gap.

        The function split on `:` and converted, so a malformed stamp became a
        plausible-looking NUMBER rather than an error: `00:412` came back as
        412.0 seconds. Round 1 and round 2 both closed this at the CALL SITES
        by checking the shape before converting, which fixed every reachable
        path and left the function itself still contradicting its own
        docstring — so #53's title claim stayed literally true while its
        symptom was gone. Validation living in two places, neither of which is
        the function that promises it, is how the two halves drifted apart in
        the first place.
        """
        for raw in ("00:412", "99:99", "12:99:99", "10000:00", "1:2", "00:1"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    to_srt.parse_timestamp(raw)


class TestTimestampFormatting(unittest.TestCase):
    def test_renders_srt_shape(self) -> None:
        self.assertEqual(to_srt.format_timestamp(0), "00:00:00,000")
        self.assertEqual(to_srt.format_timestamp(90.5), "00:01:30,500")
        self.assertEqual(to_srt.format_timestamp(3723.25), "01:02:03,250")

    def test_negative_clamps_instead_of_wrapping(self) -> None:
        # Wrapping would silently produce 23:59:59 and put the cue at the end of
        # a 24-hour timeline, which players accept and humans never find.
        self.assertEqual(to_srt.format_timestamp(-5), "00:00:00,000")

    def test_rounds_rather_than_truncates(self) -> None:
        self.assertEqual(to_srt.format_timestamp(1.0006), "00:00:01,001")


class TestSegmentParsing(unittest.TestCase):
    def test_extracts_timestamp_speaker_and_text(self) -> None:
        segs = to_srt.parse_segments("[00:00:05] Speaker 1: hello there\n")
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0]["start"], 5.0)
        self.assertEqual(segs[0]["speaker"], "Speaker 1")
        self.assertEqual(segs[0]["text"], "hello there")

    def test_speaker_is_optional(self) -> None:
        segs = to_srt.parse_segments("[00:00:05] no speaker label here\n")
        self.assertEqual(segs[0]["speaker"], "")
        self.assertEqual(segs[0]["text"], "no speaker label here")

    def test_text_containing_colons_survives(self) -> None:
        segs = to_srt.parse_segments("[00:00:05] Ann: the ratio is 3:1 and rising\n")
        self.assertEqual(segs[0]["speaker"], "Ann")
        self.assertEqual(segs[0]["text"], "the ratio is 3:1 and rising")

    def test_non_segment_lines_are_dropped(self) -> None:
        body = "Subject: a meeting\n\n[00:00:05] A: real line\nrandom prose\n"
        self.assertEqual(len(to_srt.parse_segments(body)), 1)

    def test_frontmatter_is_stripped_before_parsing(self) -> None:
        raw = "---\nid: rec1\ncomplete: true\n---\n\n[00:00:05] A: hi\n"
        self.assertEqual(len(to_srt.parse_segments(to_srt.strip_frontmatter(raw))), 1)

    def test_cjk_text_survives(self) -> None:
        segs = to_srt.parse_segments("[00:01:02] 講者一: 我們把預算拆成兩期\n")
        self.assertEqual(segs[0]["speaker"], "講者一")
        self.assertEqual(segs[0]["text"], "我們把預算拆成兩期")

    # --- ranged form (#40) ---------------------------------------------
    #
    # `plaud-index`'s CLI fast path writes `[start - end]`, and this parser
    # accepted only `[start]`, so every recording indexed the cheap way — the
    # way the README recommends — produced no subtitles at all. Every fixture
    # in this file used the other producer's shape, so the suite stayed green
    # through it.
    #
    # Fixtures here are synthetic: real shape, invented words. The recordings
    # that exposed this are other people's speech.

    def test_ranged_form_parses_and_keeps_the_end(self) -> None:
        segs = to_srt.parse_segments("[01:01 - 01:55] Speaker 1: hello there\n")
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0]["start"], 61.0)
        self.assertEqual(segs[0]["end"], 115.0)
        self.assertEqual(segs[0]["speaker"], "Speaker 1")
        self.assertEqual(segs[0]["text"], "hello there")

    def test_ranged_form_in_full_hms(self) -> None:
        segs = to_srt.parse_segments("[00:01:01 - 00:01:55] A: x\n")
        self.assertEqual((segs[0]["start"], segs[0]["end"]), (61.0, 115.0))

    def test_point_form_reports_no_end(self) -> None:
        """The other producer's shape must keep behaving exactly as before."""
        segs = to_srt.parse_segments("[00:00:05] Speaker 1: hello there\n")
        self.assertIsNone(segs[0]["end"])

    def test_an_unparseable_end_loses_the_end_not_the_line(self) -> None:
        """Dropping the segment would lose speech over a timing detail."""
        segs = to_srt.parse_segments("[01:01 - notatime] A: the words still matter\n")
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0]["start"], 61.0)
        self.assertIsNone(segs[0]["end"])
        self.assertEqual(segs[0]["text"], "the words still matter")


class TestEveryBracketLineBecomesACue(unittest.TestCase):
    """The comparison test: parsed cue count MUST equal `[`-leading line count.

    This is deliberately not a test for one regex. #40 was "the CLI's bracket
    shape does not parse"; #50 was "the same shape parses until the minute
    field passes 99". Both are instances of one thing — the cache grew a line
    shape the parser did not follow — and both produced a syntactically valid
    SRT that was silently short. A test written against either specific shape
    would not have caught the other.

    An independent oracle matters here. Counting lines that start with `[` does
    not reuse `to_srt`'s parser, so this does not have the circularity #42
    raises about the cache contract test judging itself.
    """

    def _fixture(self, *lines: str) -> str:
        return "Subject: t\n\n---\n\n" + "\n".join(lines) + "\n"

    def test_no_bracket_line_is_silently_dropped(self) -> None:
        body = self._fixture(
            "[00:00 - 00:12] Speaker 1: first minute",
            "[99:58 - 99:59] Speaker 1: the last line under the 100-minute mark",
            "[100:05 - 100:31] Speaker 1: the first line past 99 minutes",
            "[446:12 - 446:40] Speaker 1: seven hours in",
        )
        stripped = to_srt.strip_frontmatter(body)
        bracket_lines = sum(1 for line in stripped.splitlines() if line.startswith("["))
        segments = to_srt.parse_segments(stripped)
        self.assertEqual(
            bracket_lines, len(segments),
            f"{bracket_lines - len(segments)} line(s) starting with '[' produced no cue. "
            f"A file can lose most of its transcript this way and still emit a "
            f"syntactically valid SRT — #50 lost four fifths of a 7.4-hour recording "
            f"(281 segments in, 57 cues out) with "
            f"no error, no warning, and continuous timecodes.")

    def test_the_point_form_counts_too(self) -> None:
        """The MCP producer writes `[start]` with no end (#40). Same rule."""
        body = self._fixture(
            "[00:30] Speaker 1: point form, two-digit",
            "[132:07] Speaker 1: point form, three-digit",
        )
        stripped = to_srt.strip_frontmatter(body)
        bracket_lines = sum(1 for line in stripped.splitlines() if line.startswith("["))
        self.assertEqual(bracket_lines, len(to_srt.parse_segments(stripped)))


class TestTheBoundAppliesToBothEndsAndBothShapes(unittest.TestCase):
    r"""Verify round 1 found the bound was enforced on a quarter of what it claimed.

    `_STAMP` governs the `ts` group only. The `end` group is `[^\]]*?` and goes
    straight to `parse_timestamp`, which has no width limit — so `10000:00` as
    an END sailed through while the same string as a START was rejected. And
    `_STAMP` is ONE pattern serving TWO shapes: `MM:SS` (total minutes, which
    is what #50 is about) and `HH:MM:SS` (literal hours). Widening it to four
    digits for the first silently widened the hours field of the second to
    9999 hours — 416 days — where two digits had rejected it before.

    The comment justifying the bound said an unbounded class "would trade a
    silent-drop bug for a silent-accept one at the same site". It was right,
    and it only checked one of the two shapes it was describing.
    """

    def test_a_five_digit_end_does_not_become_a_timestamp(self):
        segs = to_srt.parse_segments("[100:05 - 10000:00] Speaker 1: five-digit end")
        self.assertEqual(1, len(segs), "the line itself must survive — a malformed end "
                                       "costs the timing, not the words")
        self.assertIsNone(segs[0]["end"],
                          "a five-digit end was accepted as a real time. The contract "
                          "says five digits is malformed and stays rejected; it was "
                          "only ever true of starts")

    def test_a_well_formed_end_still_parses(self):
        segs = to_srt.parse_segments("[100:05 - 100:31] Speaker 1: fine")
        self.assertAlmostEqual(6031.0, segs[0]["end"])

    def test_a_malformed_end_keeps_the_words(self):
        """The existing trade, restated as a test rather than a comment."""
        for bad in ("banana", "00:412", "10000:00", ""):
            with self.subTest(end=bad):
                segs = to_srt.parse_segments(f"[00:10 - {bad}] Speaker 1: the words")
                self.assertEqual(1, len(segs), f"end={bad!r} cost the whole line")
                self.assertEqual("the words", segs[0]["text"])

    def test_seconds_must_be_exactly_two_digits(self):
        """`00:412` became 412.0 — a plausible-looking wrong number (#53's substance)."""
        segs = to_srt.parse_segments("[00:10 - 00:412] Speaker 1: three-digit seconds")
        self.assertIsNone(segs[0]["end"],
                          "`00:412` was converted to a number instead of refused. "
                          "Silently wrong beats silently absent for danger")

    def test_the_hours_field_is_not_four_digits(self):
        """`\\d{1,4}` was meant for TOTAL MINUTES. On the HH:MM:SS shape it is hours."""
        self.assertEqual(
            [], to_srt.parse_segments("[1234:05:06] Speaker 1: 51 days in"),
            "a four-digit HOURS field parsed as 4442706s (51.4 days). The bound was "
            "justified as `9999:59 is about seven days`, which is the minutes reading; "
            "the same four digits on hours is 416 days")

    def test_the_ordinary_hour_forms_still_parse(self):
        for line, want in (("[00:01:01] S: a", 61.0),
                           ("[12:30:00] S: b", 45000.0),
                           ("[1:02:03.250] S: c", 3723.25)):
            with self.subTest(line=line):
                segs = to_srt.parse_segments(line)
                self.assertEqual(1, len(segs), f"{line} stopped parsing")
                self.assertAlmostEqual(want, segs[0]["start"])

    def test_total_minutes_still_reach_four_digits(self):
        segs = to_srt.parse_segments("[1440:00] Speaker 1: a day of total minutes")
        self.assertEqual(1, len(segs))
        self.assertAlmostEqual(86400.0, segs[0]["start"])


class TestTheGuardDoesNotShareTheParsersAssumption(unittest.TestCase):
    r"""The denominator must be LOOSER than the parser, or it cannot see the gap.

    The guard counted `line.startswith("[")` — the same column-0 anchor
    `SEGMENT`'s `^\[` requires. Any drop whose cause also breaks that anchor
    left both the numerator and the denominator, so `unparsed` stayed 0 and
    nothing was said. One leading space was enough to reproduce #50's exact
    signature: an SRT that looks complete, no error, no warning.
    """

    def _run(self, body: str) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "abc123.md").write_text(body, encoding="utf-8")
            proc = subprocess.run([sys.executable, str(SCRIPT), "abc123"],
                                  capture_output=True, text=True, env=cli_env(cache))
            return proc.returncode, proc.stderr

    def test_an_indented_bracket_line_is_counted_and_warned_about(self):
        body = ("Subject: t\n\n---\n\n"
                "[00:00 - 00:12] S: parses\n"
                " [00:13 - 00:20] S: one leading space, silently dropped\n"
                "[00:21 - 00:30] S: parses\n")
        _, err = self._run(body)
        self.assertIn("did not parse", err,
                      "an indented bracket line vanished without a word. The guard "
                      "shares the parser's line-start assumption, so the drop leaves "
                      "the denominator too and `unparsed` stays 0")

    def test_a_clean_file_is_still_quiet(self):
        body = ("Subject: t\nDuration: 00:01\n\n---\n\n"
                "[00:00 - 00:12] S: parses\n\n[00:13 - 00:20] S: also parses\n")
        _, err = self._run(body)
        self.assertNotIn("did not parse", err, f"warned about a clean file: {err!r}")


class TestTheDenominatorSharesNoAssumptionWithTheParser(unittest.TestCase):
    """Round 2: `lstrip()` NARROWED the shared assumption instead of removing it.

    Round 1 caught the denominator counting `startswith("[")` — the parser's own
    column-0 anchor — so a drop caused by breaking that anchor left numerator and
    denominator together. The repair moved to `lstrip().startswith("[")`, which
    sees the indent class and still requires a leading `[`, exactly as `SEGMENT`
    does. Anything cue-shaped without that bracket escapes both again, and #50's
    whole signature came back: three cue-shaped lines in, two cues out, exit 0,
    stderr empty.

    A denominator is only honest if it shares NO gate with the parser. This one
    asks a single question the parser never asks: does the line carry a
    timestamp at all?
    """

    def _run(self, body: str, name: str = "abc123.md") -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / name).write_text(body, encoding="utf-8")
            proc = subprocess.run([sys.executable, str(SCRIPT), name[:-3]],
                                  capture_output=True, text=True, env=cli_env(cache))
            return proc.returncode, proc.stdout, proc.stderr

    def test_a_cue_shaped_line_without_a_bracket_is_counted(self):
        """#50's signature, reproduced through a prefix the bracket test cannot see."""
        body = ("Subject: t\n\n---\n\n"
                "[00:00 - 00:12] S: parses\n"
                "(00:13 - 00:20) S: paren form, silently dropped\n"
                "[00:21 - 00:30] S: parses\n")
        _, _, err = self._run(body)
        self.assertIn("did not parse", err,
                      "a cue-shaped line vanished without a word because the "
                      "denominator still required a leading '[' — the same gate "
                      "SEGMENT applies, one character narrower than round 1")

    def test_a_byte_order_mark_does_not_hide_a_drop(self):
        """`str.lstrip()` does not strip a BOM, so it hid the line from BOTH sides.

        The assertion is on the OUTCOME — no line lost — and not on the warning,
        because the warning is only the second-best answer here. Reading the file
        as `utf-8-sig` consumes the mark and the line simply parses, which beats
        parsing one of two and saying so. An earlier draft of this test demanded
        the warning, and would have failed the better fix.
        """
        body = ("\ufeff[00:00 - 00:12] S: a BOM defeats SEGMENT's column-zero anchor\n"
                "[00:13 - 00:20] S: parses\n")
        _, out, err = self._run(body)
        self.assertNotIn("did not parse", err, f"a BOM cost a line: {err!r}")
        # Without `-o` the CLI writes the SRT itself to stdout, so count cues there.
        self.assertEqual(2, out.count(" --> "),
                         f"the BOM line vanished — round 1's B3 reached through a "
                         f"different invisible prefix: {out!r}")

    def test_an_annotation_line_is_not_counted_as_a_lost_cue(self):
        """`[laughter]` was never meant to be a cue; claiming it is cries wolf."""
        body = ("Subject: t\n\n---\n\n"
                "[00:00] S: hello\n"
                "[laughter]\n"
                "[00:10] S: bye\n")
        _, _, err = self._run(body)
        self.assertNotIn("did not parse", err,
                         f"warned about a bracketed annotation: {err!r}. A warning "
                         f"that fires on every clean file is one nobody reads")

    def test_the_denominator_is_not_bounded_where_the_parser_is(self):
        """A drift PAST the parser's bound must be visible, not agreed-upon."""
        body = ("Subject: t\n\n---\n\n"
                "[00:00] S: parses\n"
                "[10000:00] S: five digits — past the contract's bound\n")
        _, _, err = self._run(body)
        self.assertIn("did not parse", err,
                      "a five-digit minute field was invisible to the denominator "
                      "AND the parser, so they agreed silently — which is the one "
                      "thing this count exists to prevent")


class TestTheWarningDoesNotPublishSomebodysWords(unittest.TestCase):
    """`tests/test_cache_line_format_live.py` already forbids this for CI logs.

    Its reason — "Recordings are other people's speech; a CI log is a
    publication" — applies at least as strongly to a terminal, and `shape_of`
    already existed to describe a line without quoting it. The warning added for
    #50 quoted 90 raw characters instead, which both publishes third-party
    speech and lets embedded control codes rewrite the terminal.
    """

    def _run(self, body: str) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "abc123.md").write_text(body, encoding="utf-8")
            proc = subprocess.run([sys.executable, str(SCRIPT), "abc123"],
                                  capture_output=True, text=True, env=cli_env(cache))
            return proc.returncode, proc.stderr

    def test_the_warning_never_quotes_the_transcript(self):
        secret = "something somebody actually said"
        body = ("Subject: t\n\n---\n\n"
                "[00:00] S: fine\n"
                f"[99999:00] S: {secret}\n")
        _, err = self._run(body)
        self.assertIn("did not parse", err, "precondition: the warning must fire")
        self.assertNotIn(secret, err,
                         "the warning printed a transcript line verbatim. This repo "
                         "already has shape_of() and a test forbidding exactly this")

    def test_control_codes_never_reach_the_terminal(self):
        body = ("Subject: t\n\n---\n\n"
                "[00:00] S: fine\n"
                "[99999:00] S: benign\x1b[2K\x1b[1A\x1b[2K forged\n")
        _, err = self._run(body)
        self.assertNotIn("\x1b", err,
                         "an ANSI escape from untrusted transcript text reached "
                         "stderr, where \\x1b[1A\\x1b[2K erases the very line "
                         "reporting the problem")


class TestTheBoundIsOnMagnitudeNotJustDigitCount(unittest.TestCase):
    """Round 2: the gate counted digits and never looked at what they meant.

    Round 1's B1 named `600000.0` as "the exact value the contract text added by
    this branch promised could not exist". The repair bounded field WIDTH, so
    `[9999:99]` produced 600039.0 — past the very number that was the point. A
    seconds field of 99 is not a seconds field.
    """

    def test_seconds_above_fifty_nine_are_not_a_timestamp(self):
        for line in ("[99:99] S: x", "[12:99:99] S: x", "[9999:99] S: x"):
            with self.subTest(line=line):
                self.assertEqual(
                    [], to_srt.parse_segments(line),
                    f"{line!r} parsed. Sixty-plus seconds is malformed, and "
                    f"accepting it is the silent-accept this bound exists to stop")

    def test_the_middle_field_of_the_three_part_form_is_also_bounded(self):
        self.assertEqual([], to_srt.parse_segments("[12:60:00] S: x"),
                         "sixty minutes in the HH:MM:SS form parsed")

    def test_a_bad_seconds_field_at_the_end_loses_the_timing_not_the_words(self):
        segs = to_srt.parse_segments("[00:10 - 00:99] Speaker 1: the words")
        self.assertEqual(1, len(segs), "the line must survive")
        self.assertIsNone(segs[0]["end"], "an end of 99 seconds was accepted as real")

    def test_the_shapes_that_must_still_parse(self):
        for line, want in (("[99:59] S: x", 5999.0),
                           ("[12:59:59] S: x", 46799.0),
                           ("[9999:59] S: x", 599999.0),
                           ("[1:02:03] S: x", 3723.0),
                           ("[00:10.500] S: x", 10.5)):
            with self.subTest(line=line):
                segs = to_srt.parse_segments(line)
                self.assertEqual(1, len(segs), f"{line!r} stopped parsing")
                self.assertAlmostEqual(want, segs[0]["start"])


class TestEveryCorrectionReachesSomebody(unittest.TestCase):
    """`build_cues` appends trim warnings "when a list is passed" — and the CLI
    never passed one, so the branch was unreachable in the only shipped path.
    A PR whose whole subject is "partial loss must not be silent" left a second
    silent correction in the function it feeds."""

    def test_a_trimmed_cue_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "abc123.md").write_text(
                "Subject: t\n\n---\n\n"
                "[00:00 - 09:59] S: a long declared end\n"
                "[00:30 - 00:40] S: the next cue starts long before it\n",
                encoding="utf-8")
            proc = subprocess.run([sys.executable, str(SCRIPT), "abc123"],
                                  capture_output=True, text=True, env=cli_env(cache))
        self.assertIn("trim", proc.stderr.lower(),
                      "a declared end was pulled back by nine minutes and nothing "
                      "said so. build_cues' own docstring: 'a correction nobody "
                      "can see is one nobody can judge'")


class TestTheCueCountCarriesItsOwnCaveat(unittest.TestCase):
    """#50's harm was a plausible number reported as success.

    The story in the issue is a script reporting "6 succeeded / 0 failed" over
    files that were 80% empty. The branch put a contradicting signal on stderr
    and left the plausible-looking count alone on stdout, without saying which
    one wins. Anything that reads only the success line still gets #50.
    """

    def test_stdout_says_lines_were_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "abc123.md").write_text(
                "Subject: t\n\n---\n\n"
                "[00:00] S: parses\n"
                "[99999:00] S: dropped\n",
                encoding="utf-8")
            out = pathlib.Path(d) / "o.srt"
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "abc123", "-o", str(out)],
                capture_output=True, text=True, env=cli_env(cache))
        self.assertIn("dropped", proc.stdout.lower(),
                      f"stdout said {proc.stdout.strip()!r} with no hint that a "
                      f"line was lost — the same shape as the '6 succeeded / 0 "
                      f"failed' report #50 opens with")


class TestPartialDropIsLoud(unittest.TestCase):
    """A file that parses PARTLY is the case both guards were blind to.

    `parse_segments` drops non-matching lines silently, and that is right for
    the `Subject:` header and blank lines — warning about those would bury the
    real problem. The caller then guards on `if not segments`, which fires only
    at ZERO. #50 parsed 20% of one file: the drop was silent by design and the
    guard was quiet because the list was not empty. "All or nothing" was an
    assumption nobody wrote down, and partial parsing fell straight through it.

    So the signal belongs on the line count, not on the parser: a line that
    starts with `[` was meant to be a cue, and if it did not become one, say so.
    """

    def _run(self, body: str) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "abc123.md").write_text(body, encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "abc123"],
                capture_output=True, text=True, env=cli_env(cache))
            return proc.returncode, proc.stderr

    def test_a_partially_parsed_file_warns(self) -> None:
        # These carry a TIMESTAMP and still fail to parse, which is the class the
        # count is about. The first version of this fixture used `[bogus shape]`
        # and `[also bogus]` — bracketed lines with no digits in them. Those are
        # annotations, not lost cues, and counting them was the false positive
        # that made this warning fire on any transcript containing `[laughter]`
        # (verify round 2).
        body = ("Subject: t\n\n---\n\n"
                "[00:00 - 00:12] Speaker 1: parses\n"
                "[99999:00] Speaker 1: five-digit minutes, out of contract\n"
                "[00:99] Speaker 1: ninety-nine seconds, out of contract\n")
        _, err = self._run(body)
        # NOT `assertIn("2", err)` — the fixture filename `abc123.md` is printed in
        # the warning and contains a "2", so that assertion passed on the filename
        # and never tested the count at all (verify round 1).
        self.assertRegex(err, r"\b2 of 3 timestamped lines\b",
                         f"the warning does not state how many of how many were "
                         f"dropped: {err!r}")
        # Named by SHAPE, not quoted. Round 1 asked for the offending line to be
        # named so a false positive would be diagnosable; round 2 found the
        # naming was done by printing ninety raw characters of somebody's
        # speech. Both are satisfiable at once — the shape is what you need to
        # find the line and fix the producer, and it is not what was said.
        self.assertRegex(err, r"opens with '\[9",
                         f"the warning does not identify the first offending line's "
                         f"shape, so a false positive would be undiagnosable: {err!r}")

    def test_a_fully_parsed_file_stays_quiet(self) -> None:
        """The header and blank lines must not trip it — that silence is deliberate."""
        body = ("Subject: t\nDuration: 00:01\n\n---\n\n"
                "[00:00 - 00:12] Speaker 1: parses\n"
                "\n"
                "[00:13 - 00:20] Speaker 1: also parses\n")
        _, err = self._run(body)
        self.assertNotIn("did not parse", err,
                         f"warned about a clean file — header/blank lines must stay silent: {err!r}")


class TestMinutesPastNinetyNine(unittest.TestCase):
    """Plaud's CLI writes TOTAL minutes, so 100 minutes in the field is `100:05`.

    `parse_timestamp` already computed `int(minutes) * 60` with no width
    assumption — the arithmetic was never wrong. `_STAMP` was the only gate.
    """

    def test_three_digit_minutes_parse(self) -> None:
        segs = to_srt.parse_segments("[100:05 - 100:31] Speaker 1: past the hour and a half")
        self.assertEqual(1, len(segs), "a three-digit minute field was dropped")
        self.assertAlmostEqual(6005.0, segs[0]["start"])
        self.assertAlmostEqual(6031.0, segs[0]["end"])

    def test_seven_hours_in(self) -> None:
        segs = to_srt.parse_segments("[446:12] Speaker 1: 7.4 hours")
        self.assertEqual(1, len(segs))
        self.assertAlmostEqual(446 * 60 + 12, segs[0]["start"])

    def test_two_digit_minutes_still_parse(self) -> None:
        """The widening must not cost the common case."""
        segs = to_srt.parse_segments("[07:25 - 07:31] Speaker 1: normal")
        self.assertEqual(1, len(segs))
        self.assertAlmostEqual(445.0, segs[0]["start"])

    def test_the_widening_is_bounded(self) -> None:
        """`\\d{1,4}` not `\\d+` — an unbounded class trades one silent bug for another.

        9999:59 is about seven days, past any real recording. Five digits is not
        a long meeting, it is a malformed line, and it should stay rejected.
        """
        self.assertEqual([], to_srt.parse_segments("[99999:00] Speaker 1: not a recording"))


class TestCueBuilding(unittest.TestCase):
    def _segs(self, *starts: float) -> list[dict]:
        return [{"start": s, "speaker": "A", "text": f"line {i}"}
                for i, s in enumerate(starts)]

    def test_a_cue_ends_where_the_next_begins(self) -> None:
        cues = to_srt.build_cues(self._segs(0.0, 5.0, 12.0))
        self.assertEqual(cues[0]["end"], 5.0)
        self.assertEqual(cues[1]["end"], 12.0)

    def test_final_cue_uses_the_tail_estimate(self) -> None:
        cues = to_srt.build_cues(self._segs(0.0, 10.0), tail_seconds=3.0)
        self.assertEqual(cues[-1]["end"], 13.0)

    def test_out_of_order_timestamps_get_a_positive_duration(self) -> None:
        # A zero- or negative-length cue is rejected by players, so the line would
        # disappear entirely rather than merely being mistimed.
        cues = to_srt.build_cues(self._segs(10.0, 4.0), min_duration=0.5)
        self.assertGreater(cues[0]["end"], cues[0]["start"])
        self.assertEqual(cues[0]["end"], 10.5)

    def test_duplicate_timestamps_also_get_a_positive_duration(self) -> None:
        cues = to_srt.build_cues(self._segs(7.0, 7.0), min_duration=0.5)
        self.assertEqual(cues[0]["end"], 7.5)

    def test_speaker_prefix_can_be_omitted(self) -> None:
        with_speaker = to_srt.build_cues(self._segs(0.0))
        without = to_srt.build_cues(self._segs(0.0), show_speaker=False)
        self.assertTrue(with_speaker[0]["text"].startswith("A: "))
        self.assertFalse(without[0]["text"].startswith("A: "))

    # --- real end times (#40) ------------------------------------------
    #
    # Until a producer supplied end times there was nothing to use, and the
    # docstring above says as much: the next segment's start is "the only
    # honest signal the transcript carries", and the last cue gets a guess.
    # The ranged form carries the real thing, so the guess can stop.

    def _ranged(self, *pairs: tuple) -> list[dict]:
        return [{"start": s, "end": e, "speaker": "A", "text": f"line {i}"}
                for i, (s, e) in enumerate(pairs)]

    def test_a_real_end_is_used_instead_of_the_next_start(self) -> None:
        cues = to_srt.build_cues(self._ranged((0.0, 3.0), (5.0, 9.0)))
        self.assertEqual(cues[0]["end"], 3.0)   # not 5.0

    def test_the_last_cue_stops_being_a_guess(self) -> None:
        """The tail estimate exists because nothing better was available."""
        cues = to_srt.build_cues(self._ranged((0.0, 3.0), (5.0, 9.0)),
                                 tail_seconds=4.0)
        self.assertEqual(cues[-1]["end"], 9.0)  # not 5.0 + 4.0

    def test_segments_without_an_end_still_use_the_old_rules(self) -> None:
        mixed = [{"start": 0.0, "end": None, "speaker": "A", "text": "x"},
                 {"start": 5.0, "end": 9.0, "speaker": "A", "text": "y"}]
        cues = to_srt.build_cues(mixed)
        self.assertEqual(cues[0]["end"], 5.0)   # inferred from the next start
        self.assertEqual(cues[1]["end"], 9.0)   # real

    def test_an_end_past_the_next_start_is_clamped(self) -> None:
        """Overlapping cues are legal SRT and players disagree about them.

        Accepting ranges should be a pure gain, not a change in how the
        output behaves, so an overlap is pulled back to where the next cue
        begins — exactly where the inferred value would have put it.
        """
        cues = to_srt.build_cues(self._ranged((0.0, 7.0), (5.0, 9.0)))
        self.assertEqual(cues[0]["end"], 5.0)

    def test_clamping_is_reported_rather_than_silent(self) -> None:
        notes: list[str] = []
        to_srt.build_cues(self._ranged((0.0, 7.0), (5.0, 9.0)), warnings=notes)
        self.assertTrue(notes, "an overlap was corrected and nothing said so")

    def test_nothing_is_reported_when_no_clamp_happens(self) -> None:
        notes: list[str] = []
        to_srt.build_cues(self._ranged((0.0, 3.0), (5.0, 9.0)), warnings=notes)
        self.assertEqual([], notes)

    def test_a_backwards_range_still_gets_a_positive_duration(self) -> None:
        cues = to_srt.build_cues(self._ranged((10.0, 4.0)), min_duration=0.5)
        self.assertEqual(cues[0]["end"], 10.5)


class TestRender(unittest.TestCase):
    def test_produces_wellformed_srt(self) -> None:
        cues = [{"start": 0.0, "end": 2.5, "text": "first"},
                {"start": 2.5, "end": 4.0, "text": "second"}]
        out = to_srt.render_srt(cues)
        self.assertEqual(
            out,
            "1\n00:00:00,000 --> 00:00:02,500\nfirst\n\n"
            "2\n00:00:02,500 --> 00:00:04,000\nsecond\n",
        )

    def test_indices_start_at_one_and_increment(self) -> None:
        cues = [{"start": float(i), "end": i + 1.0, "text": "x"} for i in range(3)]
        self.assertEqual(
            [ln for ln in to_srt.render_srt(cues).splitlines() if ln.isdigit()],
            ["1", "2", "3"],
        )


class TestSuiteIsolation(unittest.TestCase):
    """Guards the guard.

    Before #29 the CLI read no preferences, so pinning only PLAUD_CACHE_DIR was
    complete isolation. The moment it started reading a config file, every
    subprocess test quietly began depending on the developer's own settings —
    and #22's `test_the_cli_actually_uses_the_preference`, which asserts the
    polish is used, fails for anyone who has chosen verbatim.

    It passed review because the machine it ran on had no config file. That is
    the shape worth guarding: a test that only passes because of something
    absent from this particular machine.
    """

    def test_cli_env_pins_the_config_path(self) -> None:
        env = cli_env(pathlib.Path("/tmp/whatever"))
        self.assertIn("PLAUD_CONFIG", env)
        self.assertTrue(env["PLAUD_CONFIG"].startswith("/tmp/whatever"),
                        "config must land inside the per-test cache dir")

    def test_cli_env_overrides_an_inherited_config(self) -> None:
        with mock.patch.dict(os.environ, {"PLAUD_CONFIG": "/home/dev/real.json"}):
            env = cli_env(pathlib.Path("/tmp/whatever"))
        self.assertNotEqual("/home/dev/real.json", env["PLAUD_CONFIG"])


class TestCli(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="srt-test-")
        self.addCleanup(self._tmp.cleanup)
        self.cache = pathlib.Path(self._tmp.name)

    def _run(self, *args: str, cache=None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              capture_output=True, text=True,
                              env=cli_env(cache or self.cache))

    def _write(self, rec_id: str, body: str, front: str = "") -> None:
        (self.cache / f"{rec_id}.md").write_text(front + body)

    def test_converts_a_cached_recording(self) -> None:
        self._write("rec1", "[00:00:01] A: hello\n[00:00:04] B: world\n")
        p = self._run("rec1")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("00:00:01,000 --> 00:00:04,000", p.stdout)
        self.assertIn("A: hello", p.stdout)

    def test_missing_recording_fails_with_a_useful_message(self) -> None:
        p = self._run("nope")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("not found", p.stderr)

    def test_refuses_a_traversing_id(self) -> None:
        p = self._run("../../etc/passwd")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("unsafe recording id", p.stderr)

    def test_transcript_without_timestamps_fails_loudly(self) -> None:
        # Emitting an empty .srt would look like success and produce a video with
        # no subtitles and no explanation.
        #
        # Asserts the properties the message must have, not its wording. The
        # earlier version pinned the literal "no timestamped segments", so
        # #40 — which found that exact phrasing sends people to debug the
        # wrong thing — could not correct it without a test failing for a
        # reason unrelated to behaviour.
        self._write("plain", "just prose, no timestamps at all\n")
        p = self._run("plain")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("error", p.stderr.lower())
        self.assertIn("plain", p.stderr)          # names the file it read
        self.assertIn("[00:12:03]", p.stderr)     # shows an accepted shape

    def test_the_failure_names_both_accepted_shapes(self) -> None:
        """A reader who cached the ranged form must see it listed (#40).

        Naming only the point form is what made this failure read as "your
        recording has no timestamps" when the real cause was a second, equally
        valid shape the parser did not yet accept.
        """
        self._write("plain2", "just prose, no timestamps at all\n")
        p = self._run("plain2")
        self.assertIn(" - ", p.stderr, "the ranged form is not shown as accepted")

    def test_incomplete_cache_warns_on_stderr_but_still_converts(self) -> None:
        self._write("part", "[00:00:01] A: half a transcript\n",
                    front="---\nid: part\ncomplete: false\n---\n\n")
        p = self._run("part")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("marked incomplete", p.stderr)
        self.assertIn("00:00:01,000", p.stdout)

    def test_output_flag_writes_a_file(self) -> None:
        self._write("rec1", "[00:00:01] A: hello\n")
        out = self.cache / "out.srt"
        p = self._run("rec1", "-o", str(out))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("1 cues", p.stdout)
        self.assertIn("A: hello", out.read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)


# --------------------------------------------------------------------------
# Language-dependent line length (#14)
#
# Subtitle readability conventions are not universal. Latin scripts run ~42
# characters per line; CJK is roughly half that in character count because each
# glyph is full-width. Emitting one unwrapped line regardless of script is the
# same mistake in both directions — unreadably long for Latin, and wrong by a
# factor of two for CJK.
# --------------------------------------------------------------------------
class TestScriptDetection(unittest.TestCase):
    def test_latin_text(self):
        self.assertEqual("latin", to_srt.detect_script("we should split the budget"))

    def test_chinese_text(self):
        self.assertEqual("cjk", to_srt.detect_script("我們應該把預算拆成兩期"))

    def test_japanese_kana(self):
        self.assertEqual("cjk", to_srt.detect_script("よろしくお願いします"))

    def test_thai_text(self):
        self.assertEqual("thai", to_srt.detect_script("การประชุมเรื่องงบประมาณ"))

    def test_mixed_text_follows_the_majority(self):
        """A Chinese sentence with an English term in it is still a Chinese line."""
        self.assertEqual("cjk", to_srt.detect_script("那就先把 budget 拆成兩期比較好"))

    def test_empty_text_defaults_to_latin(self):
        self.assertEqual("latin", to_srt.detect_script(""))


class TestWrapCueText(unittest.TestCase):
    def test_short_latin_line_is_untouched(self):
        self.assertEqual("hello there", to_srt.wrap_cue_text("hello there"))

    def test_long_latin_wraps_on_spaces(self):
        text = "we agreed to split the budget across two quarters and revisit it in March"
        out = to_srt.wrap_cue_text(text)
        self.assertIn("\n", out)
        for line in out.split("\n"):
            self.assertLessEqual(len(line), 42, line)
        self.assertEqual(text.split(), out.replace("\n", " ").split())

    def test_latin_never_splits_a_word(self):
        out = to_srt.wrap_cue_text("supercalifragilistic " * 4)
        for line in out.split("\n"):
            for word in line.split():
                self.assertIn(word, "supercalifragilistic")

    def test_cjk_uses_a_shorter_limit(self):
        """Full-width glyphs take about twice the space per character."""
        text = "我們應該把預算拆成兩期然後在三月的時候重新檢視這件事情比較妥當"
        out = to_srt.wrap_cue_text(text)
        self.assertIn("\n", out)
        for line in out.split("\n"):
            self.assertLessEqual(len(line), 20, line)

    def test_cjk_wraps_without_spaces(self):
        """CJK has no word spaces — wrapping on spaces would never fire."""
        text = "預算" * 30
        out = to_srt.wrap_cue_text(text)
        self.assertIn("\n", out)
        self.assertEqual(text, out.replace("\n", ""))

    def test_thai_is_left_unwrapped_and_that_is_deliberate(self):
        """Thai has no word spaces and needs a segmenter to break correctly.
        Breaking mid-word is worse than a long line, so it is left alone rather
        than broken wrongly."""
        text = "การประชุม" * 12
        self.assertEqual(text, to_srt.wrap_cue_text(text))

    def test_no_content_is_lost_for_any_script(self):
        """Checked per script, because "same content" means different things.
        Latin wraps AT a space, so the newline replaces one — compare tokens.
        CJK wraps BETWEEN characters with no space involved, so replacing the
        newline with a space would invent one — compare the raw string."""
        for text in ["a b c " * 30, "hello"]:
            self.assertEqual(text.split(), to_srt.wrap_cue_text(text).replace("\n", " ").split(), text)
        for text in ["預算" * 40, "よろしく" * 20]:
            self.assertEqual(text, to_srt.wrap_cue_text(text).replace("\n", ""), text)

    def test_wrapping_is_applied_when_rendering(self):
        cues = [{"start": 0.0, "end": 4.0,
                 "text": "we agreed to split the budget across two quarters and revisit in March"}]
        out = to_srt.render_srt(cues)
        body = [l for l in out.splitlines() if l and "-->" not in l and not l.strip().isdigit()]
        self.assertGreater(len(body), 1, out)
        for line in body:
            self.assertLessEqual(len(line), 42, line)


# --------------------------------------------------------------------------
# Subtitle source preference (#22)
#
# Plaud returns two versions of the same speech with identical segments and
# timings: raw, and a filler-thinned polish. Subtitles want the tidy one —
# nobody reads "呃" on screen — while search keeps the raw one, because search
# answers "what was said".
# --------------------------------------------------------------------------
class TestSubtitleSource(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache = pathlib.Path(self._tmp.name)
        patch = mock.patch.object(to_srt, "CACHE_DIR", self.cache)
        patch.start()
        self.addCleanup(patch.stop)

    def _write(self, rel: str, text: str) -> pathlib.Path:
        p = self.cache / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def test_polish_is_preferred_when_present(self):
        self._write("rec_a.md", "[00:00:01] Speaker 1: raw words\n")
        self._write("polish/rec_a.md", "[00:00:01] Speaker 1: tidy words\n")
        self.assertEqual(self.cache / "polish" / "rec_a.md", to_srt.subtitle_source("rec_a"))

    def test_transcript_is_used_when_no_polish(self):
        self._write("rec_b.md", "[00:00:01] Speaker 1: raw words\n")
        self.assertEqual(self.cache / "rec_b.md", to_srt.subtitle_source("rec_b"))

    def test_missing_recording_returns_the_transcript_path(self):
        """So the caller's own 'not cached' error still fires, with the path the
        user expects to see named in it."""
        self.assertEqual(self.cache / "rec_c.md", to_srt.subtitle_source("rec_c"))

    def test_empty_polish_file_is_not_preferred(self):
        """A zero-byte polish would silently produce an empty subtitle file —
        the failure that looks like success."""
        self._write("rec_d.md", "[00:00:01] Speaker 1: raw words\n")
        self._write("polish/rec_d.md", "")
        self.assertEqual(self.cache / "rec_d.md", to_srt.subtitle_source("rec_d"))

    def test_the_cli_actually_uses_the_preference(self):
        """Testing `subtitle_source` alone proves the part works, not that it is
        wired in — deleting the call from main() left every other test green.
        Exercise the command, not the helper."""
        self._write("rec_e.md", "[00:00:01] Speaker 1: 呃 那個 raw wording\n")
        self._write("polish/rec_e.md", "[00:00:01] Speaker 1: tidy wording\n")
        out = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "to_srt.py"), "rec_e"],
            capture_output=True, text=True, env=cli_env(self.cache),
        )
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("tidy wording", out.stdout)
        self.assertNotIn("raw wording", out.stdout)

    def test_the_cli_falls_back_when_no_polish(self):
        self._write("rec_f.md", "[00:00:01] Speaker 1: only the raw exists\n")
        out = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "to_srt.py"), "rec_f"],
            capture_output=True, text=True, env=cli_env(self.cache),
        )
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("only the raw exists", out.stdout)


# --------------------------------------------------------------------------
# Subtitle source as a preference, not a decision (#29)
#
# #22 established the split — subtitles from the polish, search from the raw —
# and hardcoded it. Which one your *subtitles* use is a preference, though:
# clean reads better on screen, verbatim keeps the disfluency that qualitative
# work measures. Both answers are right; which one depends on the work.
#
# Search is NOT configurable and deliberately so: polish is the same speech
# reworded, so searching it returns sentences nobody said. That is #28's
# unanswered labelling question, not a preference.
# --------------------------------------------------------------------------
class TestSourcePreference(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache = pathlib.Path(self._tmp.name)
        patch = mock.patch.object(to_srt, "CACHE_DIR", self.cache)
        patch.start()
        self.addCleanup(patch.stop)

    def _write(self, rel: str, text: str) -> pathlib.Path:
        p = self.cache / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def _both(self, rec_id: str) -> None:
        self._write(f"{rec_id}.md", "[00:00:01] Speaker 1: 呃 那個 就是 raw wording\n")
        self._write(f"polish/{rec_id}.md", "[00:00:01] Speaker 1: tidy wording\n")

    def test_polished_is_still_the_default(self) -> None:
        """The default must not move — an existing user with no config file has
        to see exactly what they saw yesterday."""
        self._both("r1")
        self.assertEqual(self.cache / "polish" / "r1.md", to_srt.subtitle_source("r1"))

    def test_verbatim_takes_the_raw_transcript_even_though_polish_exists(self) -> None:
        self._both("r2")
        self.assertEqual(self.cache / "r2.md",
                         to_srt.subtitle_source("r2", prefer="verbatim"))

    def test_verbatim_with_no_polish_is_still_the_raw_transcript(self) -> None:
        self._write("r3.md", "[00:00:01] Speaker 1: only raw\n")
        self.assertEqual(self.cache / "r3.md",
                         to_srt.subtitle_source("r3", prefer="verbatim"))


class TestDifferingSample(unittest.TestCase):
    """The pair of lines that makes the question answerable.

    Asking "polished or verbatim?" in the abstract is not answerable — the user
    has not seen either. Asking it beside the same line rendered both ways is.
    That is the whole reason this function exists.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache = pathlib.Path(self._tmp.name)
        patch = mock.patch.object(to_srt, "CACHE_DIR", self.cache)
        patch.start()
        self.addCleanup(patch.stop)

    def _write(self, rel: str, text: str) -> None:
        p = self.cache / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def test_returns_the_first_segment_that_actually_differs(self) -> None:
        """Not the first segment — the first *differing* one. A recording whose
        opening line has no fillers would otherwise preview two identical lines
        and demonstrate nothing."""
        self._write("s1.md",
                    "[00:00:01] A: same opening\n[00:00:05] A: 呃 那個 the budget\n")
        self._write("polish/s1.md",
                    "[00:00:01] A: same opening\n[00:00:05] A: the budget\n")
        sample = to_srt.differing_sample("s1")
        self.assertIsNotNone(sample)
        self.assertIn("呃 那個 the budget", sample["verbatim"])
        self.assertEqual("A: the budget", sample["polished"])

    def test_returns_none_when_there_is_no_polish(self) -> None:
        self._write("s2.md", "[00:00:01] A: only raw\n")
        self.assertIsNone(to_srt.differing_sample("s2"))

    def test_returns_none_when_the_two_versions_are_identical(self) -> None:
        """The fifth state the issue's table did not list. A recording with no
        fillers to thin has nothing to choose between — asking anyway would be
        a question with no information in it."""
        text = "[00:00:01] A: nothing to thin here\n"
        self._write("s3.md", text)
        self._write("polish/s3.md", text)
        self.assertIsNone(to_srt.differing_sample("s3"))

    def test_returns_none_when_polish_is_empty(self) -> None:
        self._write("s4.md", "[00:00:01] A: raw\n")
        self._write("polish/s4.md", "")
        self.assertIsNone(to_srt.differing_sample("s4"))


class TestConfigurableLineLimits(unittest.TestCase):
    def test_default_limits_still_apply_when_none_passed(self) -> None:
        """The existing single-argument call sites must keep working unchanged."""
        text = "一二三四五六七八九十一二三四五六七八九十一二三"
        self.assertEqual(to_srt.wrap_cue_text(text),
                         to_srt.wrap_cue_text(text, limits=to_srt.LINE_LIMITS))

    def test_a_narrower_cjk_limit_wraps_sooner(self) -> None:
        text = "一二三四五六七八九十一二"      # 12 chars: under the default 20
        self.assertNotIn("\n", to_srt.wrap_cue_text(text))
        wrapped = to_srt.wrap_cue_text(text, limits={"latin": 42, "cjk": 5})
        self.assertIn("\n", wrapped)
        self.assertTrue(all(len(line) <= 5 for line in wrapped.split("\n")))

    def test_a_narrower_latin_limit_wraps_sooner(self) -> None:
        text = "the quick brown fox jumps over the lazy dog"
        wrapped = to_srt.wrap_cue_text(text, limits={"latin": 12, "cjk": 20})
        self.assertTrue(all(len(line) <= 12 for line in wrapped.split("\n")
                            if " " in line or len(line) <= 12))

    def test_render_srt_threads_the_limits_through(self) -> None:
        """Testing wrap_cue_text alone proves the part works, not that render_srt
        hands it anything — the #22 lesson, applied one layer up."""
        cues = [{"start": 0.0, "end": 2.0, "text": "一二三四五六七八九十"}]
        self.assertIn("\n", to_srt.render_srt(cues, limits={"latin": 42, "cjk": 3}))


class TestSourcePreferenceCLI(unittest.TestCase):
    """The wiring, not the parts.

    #22 shipped a passing test for `subtitle_source()` while `main()` could have
    stopped calling it entirely without a single test going red. Everything here
    runs the actual command.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache = pathlib.Path(self._tmp.name)
        self.config = self.cache / "config.json"

    def _write(self, rel: str, text: str) -> None:
        p = self.cache / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def _both(self, rec_id: str) -> None:
        self._write(f"{rec_id}.md", "[00:00:01] Speaker 1: 呃 raw wording\n")
        self._write(f"polish/{rec_id}.md", "[00:00:01] Speaker 1: tidy wording\n")

    def _run(self, *args: str, **env_extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
            env=cli_env(self.cache, PLAUD_CONFIG=str(self.config), **env_extra))

    def _set_config(self, **keys) -> None:
        self.config.write_text(json.dumps(keys), encoding="utf-8")

    def test_config_file_preference_reaches_the_output(self) -> None:
        self._both("c1")
        self._set_config(subtitle_source="verbatim")
        out = self._run("c1")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("raw wording", out.stdout)
        self.assertNotIn("tidy wording", out.stdout)

    def test_flag_beats_config_file(self) -> None:
        self._both("c2")
        self._set_config(subtitle_source="verbatim")
        out = self._run("c2", "--source", "polished")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("tidy wording", out.stdout)

    def test_env_beats_config_file(self) -> None:
        self._both("c3")
        self._set_config(subtitle_source="polished")
        out = self._run("c3", PLAUD_SUBTITLE_SOURCE="verbatim")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("raw wording", out.stdout)

    def test_flag_beats_env(self) -> None:
        self._both("c4")
        out = self._run("c4", "--source", "polished", PLAUD_SUBTITLE_SOURCE="verbatim")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("tidy wording", out.stdout)

    def test_no_config_still_means_polished(self) -> None:
        self._both("c5")
        out = self._run("c5")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("tidy wording", out.stdout)

    def test_line_limits_from_config_reach_the_output(self) -> None:
        self._write("c6.md", "[00:00:01] A: 一二三四五六七八九十\n")
        self._set_config(srt_line_limits={"cjk": 3})
        out = self._run("c6", "--no-speaker")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("一二三\n", out.stdout)

    def test_preview_sources_prints_both_and_exits_zero(self) -> None:
        self._both("c7")
        out = self._run("c7", "--preview-sources")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("raw wording", out.stdout)
        self.assertIn("tidy wording", out.stdout)

    def test_preview_sources_exits_3_when_there_is_no_choice(self) -> None:
        """Exit 3, not exit 0 with empty output.

        An empty stdout that exits 0 is indistinguishable from success, and a
        caller branching on it would ask the user to choose between two things
        it never found.
        """
        self._write("c8.md", "[00:00:01] A: only raw\n")
        out = self._run("c8", "--preview-sources")
        self.assertEqual(3, out.returncode)

    def test_preview_sources_exits_3_when_versions_are_identical(self) -> None:
        text = "[00:00:01] A: nothing to thin\n"
        self._write("c9.md", text)
        self._write("polish/c9.md", text)
        out = self._run("c9", "--preview-sources")
        self.assertEqual(3, out.returncode)

    def test_unknown_config_key_warns_but_subtitles_still_come_out(self) -> None:
        """A typo costs you the preference, never the work."""
        self._both("c10")
        self._set_config(subtitle_soruce="verbatim")
        out = self._run("c10")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("subtitle_soruce", out.stderr)
        self.assertIn("tidy wording", out.stdout, "the typo must not silently take effect")
