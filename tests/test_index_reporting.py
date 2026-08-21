#!/usr/bin/env python3
"""`plaud-index` must not report an absent transcript as a pending one (#37).

Plaud's API cannot distinguish three states, and `plaud-index` reports all
three with one phrase:

    never requested   → the user must press 產生 / Generate, or it never comes
    still processing  → wait, run again later
    failed            → retry in Plaud

Only the first needs an action, and it is the common one: measured across the
account's ten most recent recordings during #36, nine had no transcript. The
official CLI's own test is `sourceList.some(s => s.data_type === "transaction")`
— an existence check. There is no status field to read; `list_files` returns
six fields and none of them is about transcription.

So the wording is the whole fix. "Still processing" and "no transcript yet"
both assert the reading that requires no action, which turns the common case
into a wait that never ends and produces no error to notice it by.

This file pins the wording rather than checking a general property, for the
reason #39 settled: a named pin fails on the exact edit that would undo the
fix, and a general rule about prose honesty is not mechanically checkable.
"""
from __future__ import annotations

import pathlib
import re
import unittest

SKILL = (pathlib.Path(__file__).resolve().parent.parent
         / "skills" / "plaud-index" / "SKILL.md")

# Phrasings that assert "it is coming" when the API cannot know that.
PRESUMES_PENDING = (
    (re.compile(r"no transcript yet", re.I),
     "'yet' promises the transcript is on its way; for the common case it "
     "never is"),
    (re.compile(r"\(\s*still processing\s*\)", re.I),
     "names one of three states as though it were the only one"),
)

# What an honest report has to carry: the ambiguity, and the one action.
#
# What an honest report has to carry: the ambiguity, and the two presses.
#
# Checked against `report_template.txt`, which is the report template as plain
# text. NOT extracted from SKILL.md by pattern — see the class below for why
# that approach was abandoned after failing three verify rounds.
MUST_MENTION = (
    (re.compile(r"never requested"),
     "the state that needs the user to act is not named"),
    (re.compile(r"cannot say|can't say|無法分辨|no way to tell"),
     "the report does not admit the API cannot distinguish the three"),
)

# `產生 / Generate` is a SUBSTRING of `立即產生 / Generate now`, so a naive
# pattern for the first press can never fail while the second is present.
# Both guards are load-bearing; `test_the_first_press_pattern_...` below
# fails the day either is dropped.
FIRST_PRESS = re.compile(r"(?<!立即)產生\s*/\s*Generate(?!\s*now)")
SECOND_PRESS = re.compile(r"立即產生\s*/\s*Generate now")

# "Do not press 產生 / Generate" satisfies every pattern above while telling
# the reader the opposite of the instruction. Checked in the 120 characters
# before the first press, which covers the sentence it sits in.
NEGATED = re.compile(r"(?i)\b(do not|don't|never|rather than|instead of|without)\b")

TEMPLATE_FILE = SKILL.parent / "report_template.txt"


class TestTheSkillFileIsReadable(unittest.TestCase):
    """Absent-file and empty-file both look like 'no bad phrasings found'."""

    def test_the_skill_is_there_and_substantial(self):
        self.assertTrue(SKILL.is_file(), f"{SKILL} is gone — repoint this pin")
        self.assertGreater(len(SKILL.read_text(encoding="utf-8")), 5000,
                           "SKILL.md is suspiciously short")


class TestTheShippedReportIsTheOneUnderTest(unittest.TestCase):
    """One substring check, replacing three rounds of Markdown parsing.

    #47's requirement is about the report template a user receives. Pinning it
    meant locating that template inside SKILL.md, and every way of locating it
    was defeated:

      #36  a file-wide search — satisfied from anywhere in the file
      B1   scoped to `### 4. Report` — the terminator never stopped at `## `,
           so the section ran to EOF and swallowed two H2 sections
      C1   the first fenced block in the section — a ```bash block's CLOSING
           line is a bare ```, so the locator returned the prose BETWEEN two
           code blocks
      R3   the block whose body matched `skipped N — no transcript` — the
           anchor lived on the artifact being guarded, so an edit that moved
           the anchor moved the guard with it; plus a 3-space-indented ATX
           heading (legal Markdown) leaked past the terminator, and a
           4-backtick fence flipped fence parity and re-opened C1 verbatim

    Four fixes, each a better regex, each beaten by a Markdown construct the
    regex did not model. Markdown has more constructs than this repo has
    verify rounds, so the fifth regex was not going to be the one.

    The template now lives in `report_template.txt` and SKILL.md carries a
    verbatim copy. This test asserts the copy is there, exactly once, by plain
    string containment — no headings, no fences, no offsets, nothing to parse
    and so nothing to fool. Every check below runs against the file.

    The cost is honest and stated: two copies that must agree. That is what
    this test is for, and it is a class of failure a string comparison cannot
    get wrong.
    """

    def test_the_template_file_exists_and_is_substantial(self):
        self.assertTrue(TEMPLATE_FILE.is_file(),
                        f"{TEMPLATE_FILE} is gone — every check below would "
                        f"otherwise assert about an empty string")
        self.assertGreater(
            len(TEMPLATE_FILE.read_text(encoding="utf-8")), 200,
            "report_template.txt is suspiciously short")

    def test_the_template_sits_inside_section_4(self):
        """Containment says the bytes are present; this says they are where the model reads.

        Round 4: `count(template) == 1` forbids nothing about position. A
        one-press shape at `Say it in this shape:` plus the verbatim template
        parked in an appendix — or inside an HTML comment no reader sees —
        left the whole suite green. The relocation attacks from round 3 did
        not become RED; they became unnecessary.

        Three `str.index` calls, no parsing. A renamed heading raises
        ValueError, which is loud and correct: this check is meaningless if it
        cannot find the landmarks.
        """
        skill = SKILL.read_text(encoding="utf-8")
        template = TEMPLATE_FILE.read_text(encoding="utf-8")
        heading = skill.index("### 4. Report")
        try:
            end_of_section = skill.index("\n## ", heading)
        except ValueError:
            end_of_section = len(skill)
        at = skill.index(template)
        self.assertTrue(
            heading < at < end_of_section,
            f"the template is at offset {at}, outside `### 4. Report` "
            f"({heading}..{end_of_section}). The bytes being somewhere in the file "
            f"is not the requirement — the requirement is that the report the model "
            f"is told to emit contains them.")

    def test_the_skill_ships_the_template_verbatim_exactly_once(self):
        template = TEMPLATE_FILE.read_text(encoding="utf-8")
        occurrences = SKILL.read_text(encoding="utf-8").count(template)
        self.assertEqual(
            1, occurrences,
            f"report_template.txt appears in SKILL.md {occurrences} times, expected 1. "
            f"Zero means the two have drifted and the checks below are pinning a file "
            f"the skill no longer ships — which is the whole failure this file exists "
            f"to prevent. More than one means the report is stated twice and a reader "
            f"could follow the wrong copy.")


class TestAbsentTranscriptsAreNotReportedAsPending(unittest.TestCase):
    """Whole-file, deliberately: no phrasing anywhere may promise it is coming."""

    def test_no_phrasing_presumes_the_transcript_is_coming(self):
        text = SKILL.read_text(encoding="utf-8")
        for pattern, why in PRESUMES_PENDING:
            with self.subTest(pattern=pattern.pattern):
                hits = [m.group(0) for m in pattern.finditer(text)]
                self.assertEqual(
                    [], hits,
                    f"plaud-index still says {hits!r} — {why}. Nine of the ten "
                    f"most recent recordings on this account had no transcript "
                    f"because nobody asked for one (#37).",
                )


class TestTheReportSaysWhatItMustSay(unittest.TestCase):
    """Against the template file. Plain text — there is no Markdown here."""

    def _template(self) -> str:
        return TEMPLATE_FILE.read_text(encoding="utf-8")

    def test_the_report_names_the_ambiguity(self):
        body = self._template()
        for pattern, why in MUST_MENTION:
            with self.subTest(pattern=pattern.pattern):
                self.assertRegex(body, pattern, why)

    def test_the_first_press_pattern_cannot_be_satisfied_by_the_second(self):
        """If this ever passes trivially, the first-press check is dead weight."""
        self.assertIsNone(
            FIRST_PRESS.search("press 立即產生 / Generate now."),
            "`產生 / Generate` matched inside `立即產生 / Generate now` — the "
            "first-press assertion is vacuous and the template can drop the "
            "chooser step with the suite green")

    def test_the_template_names_both_presses_in_order(self):
        body = self._template()
        first, second = FIRST_PRESS.search(body), SECOND_PRESS.search(body)
        self.assertIsNotNone(
            first, "the report does not name 產生 / Generate — the press that opens "
                   "the chooser. Naming only the second sends the reader hunting a "
                   "button that is not on screen yet")
        self.assertIsNotNone(
            second, "the report names only the FIRST press. The first opens the "
                    "chooser; the second is what starts transcription, and a reader "
                    "who stops after one is back in the wait that never ends")
        self.assertLess(
            first.start(), second.start(),
            "the two presses are named out of order. 產生 opens the chooser and "
            "立即產生 is inside it; reversed, the instruction cannot be followed")

    def test_the_second_press_is_not_talked_out_of(self):
        """Round 4: `You do not need 立即產生 / Generate now` passed every guard.

        The first press got a negation window in round 3; the second — the one
        that actually starts transcription, and the entire subject of #47 —
        did not. Checked on both sides, because "you do not need X" negates
        forward and "X is unnecessary" negates backward.
        """
        body = self._template()
        second = SECOND_PRESS.search(body)
        self.assertIsNotNone(second, "no second press to check")
        window = body[max(0, second.start() - 120):second.end() + 120]
        hit = NEGATED.search(window)
        if hit is not None:
            self.fail(f"the report says {hit.group(0)!r} near 立即產生 / Generate now. "
                      f"Both presses are still named and still in order, so every "
                      f"other check passes — while the reader is being told to skip "
                      f"the press that starts transcription")

    def test_the_first_press_is_not_negated(self):
        """`Do not press 產生 / Generate` satisfies every pattern above."""
        body = self._template()
        first = FIRST_PRESS.search(body)
        self.assertIsNotNone(first, "no first press to check")
        window = body[max(0, first.start() - 120):first.start()]
        hit = NEGATED.search(window)
        if hit is not None:                       # build the message only when there IS one
            self.fail(f"the report says {hit.group(0)!r} shortly before naming "
                      f"產生 / Generate. Every press pattern still matches, but the "
                      f"instruction now tells the reader the opposite")
