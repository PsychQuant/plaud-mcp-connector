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
# Checked inside the `### 4. Report` section, not across the file — and that
# claim is now itself pinned, by TestTheSectionBoundaryIsWhereTheCommentSaysItIs.
# It was prose-only until #47's own verify caught the boundary running to EOF.
# A file-wide search for 產生 passes on this file no matter what the Report says —
# the word appears three times, and deleting the instruction from the report
# left every assertion green. #36 hit exactly this: an assertion satisfied
# from somewhere else stops guarding the place it was written for.
MUST_MENTION = (
    (re.compile(r"never requested", re.I),
     "the state that needs the user to act is not named"),
    (re.compile(r"press\s+產生\s*/\s*Generate"),
     "the report never says what to press"),
    (re.compile(r"立即產生\s*/\s*Generate now"),
     "the report names only the FIRST press. The first opens the chooser; the "
     "second is what starts transcription, and a reader who stops after one is "
     "back in the wait that never ends (#36, re-homed here by #47 — it was "
     "briefly pinned file-wide in test_skill_claims.py, which this file's own "
     "header warns is not enough)"),
    (re.compile(r"cannot say|can't say|無法分辨|no way to tell", re.I),
     "the report does not admit the API cannot distinguish the three"),
)

# Terminates on ANY heading of level 1-3, not just another `###`.
# `#{1,3}[ \t]` cannot match `#### ` (no space after the third #), so H4
# subsections stay inside the report where they belong. The earlier
# `(?=^### |\Z)` never stopped at `^## `, and since `### 4. Report` is the
# last H3 in the file it captured everything to EOF — see
# TestTheSectionBoundaryIsWhereTheCommentSaysItIs for what that cost.
REPORT_SECTION = re.compile(r"(?ms)^### 4\. Report\b(.*?)(?=^#{1,3}[ \t]|\Z)")


def report_section(text: str) -> str:
    m = REPORT_SECTION.search(text)
    return m.group(1) if m else ""


class TestTheSkillFileIsReadable(unittest.TestCase):
    """Absent-file and empty-file both look like 'no bad phrasings found'."""

    def test_the_skill_is_there_and_substantial(self):
        self.assertTrue(SKILL.is_file(), f"{SKILL} is gone — repoint this pin")
        self.assertGreater(len(SKILL.read_text(encoding="utf-8")), 5000,
                           "SKILL.md is suspiciously short")


class TestAbsentTranscriptsAreNotReportedAsPending(unittest.TestCase):
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

    def test_the_report_section_is_found(self):
        """An empty section makes every check below vacuous."""
        section = report_section(SKILL.read_text(encoding="utf-8"))
        self.assertGreater(
            len(section), 400,
            "'### 4. Report' did not match — the heading was renamed and the "
            "checks below are now asserting things about an empty string")

    def test_the_report_names_the_ambiguity_and_the_action(self):
        section = report_section(SKILL.read_text(encoding="utf-8"))
        for pattern, why in MUST_MENTION:
            with self.subTest(pattern=pattern.pattern):
                self.assertRegex(section, pattern, why)


class TestTheSectionBoundaryIsWhereTheCommentSaysItIs(unittest.TestCase):
    """The scope claim is itself pinned, because last time it was only prose.

    `#47` re-homed the second-press requirement here from a file-wide pin in
    test_skill_claims.py, and the commit message said it was now "scoped to
    `### 4. Report`". It was not: the lookahead stopped at `^### ` or EOF and
    never at `^## `, and `### 4. Report` is the last H3 in the file — so the
    capture ran to the end, swallowing `## Cost warning` and `## Where the
    cache lives`. Every check above was really asking about the last tenth of
    the file. Mutation-proved twice: the phrase could be deleted from the
    report template and re-added under `## Cost warning` with the suite green,
    and the liveness check above passed on a Report section gutted to `TBD.`,
    because the swallowed tail supplied 834 chars against its 400 threshold.

    That is the fourth time this repo has shipped an assertion wider than the
    thing it guards (#36, #37, `eb7b610`, then the fix for `eb7b610`). The
    lesson that keeps not sticking is that a comment saying "scoped to X" is
    not a scope; only a test is. So these use synthetic fixtures rather than
    the live file — they pin the FUNCTION's boundary behaviour, and stay
    meaningful when SKILL.md is next rearranged.
    """

    FIXTURE = (
        "## Earlier\n\nbefore\n\n"
        "### 4. Report\n\nINSIDE the report\n\n"
        "## Cost warning\n\nOUTSIDE, one heading level up\n\n"
        "### 9. Later H3\n\nOUTSIDE, same level\n"
    )

    def test_the_section_stops_at_the_next_h2(self):
        section = report_section(self.FIXTURE)
        self.assertIn("INSIDE the report", section)
        self.assertNotIn(
            "OUTSIDE, one heading level up", section,
            "report_section() ran past `## Cost warning`. Anything the checks "
            "above require can then be satisfied from a section the user never "
            "reads, which is exactly how the phrase survived deletion from the "
            "report template")

    def test_the_section_stops_at_the_next_h3(self):
        self.assertNotIn("OUTSIDE, same level", report_section(self.FIXTURE))

    def test_a_gutted_report_cannot_borrow_length_from_what_follows(self):
        """The liveness check must fail on an empty report, not pass on a long tail."""
        gutted = ("### 4. Report\n\nTBD.\n\n"
                  "## Where the cache lives\n\n" + ("filler. " * 200) + "\n")
        self.assertLess(
            len(report_section(gutted)), 400,
            "a Report section gutted to `TBD.` still measured over the liveness "
            "threshold, because the capture reached content that is not the report")


class TestTheRequirementIsPinnedToWhatTheUserReads(unittest.TestCase):
    """The requirement is about the emitted report, so pin the emitted report.

    `#47` requires that the REPORT names both presses. Satisfying that from
    prose elsewhere in the section is the same class of miss as satisfying it
    from elsewhere in the file, one radius smaller.
    """

    FENCE = re.compile(r"(?ms)^```\n(.*?)^```")

    def _report_template(self) -> str:
        section = report_section(SKILL.read_text(encoding="utf-8"))
        m = self.FENCE.search(section)
        self.assertIsNotNone(
            m, "no fenced block inside `### 4. Report` — the report template is "
               "what this pins, and it is gone or no longer fenced")
        return m.group(1)

    def test_the_template_names_the_second_press(self):
        self.assertRegex(
            self._report_template(), r"立即產生\s*/\s*Generate now",
            "the report template names only the FIRST press. The first opens the "
            "chooser; the second is what starts transcription, and a reader who "
            "stops after one is back in the wait that never ends")

    def test_the_template_names_the_first_press_too(self):
        self.assertRegex(self._report_template(), r"產生\s*/\s*Generate")
