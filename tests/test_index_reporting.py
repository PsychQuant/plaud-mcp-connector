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


# A fenced block, tag or no tag. Used only to hide its contents from the
# terminator above — SKILL.md uses ```bash ten times and `# comment` lines
# inside them are ordinary, so a line-prefix terminator would stop there.
_FENCED = re.compile(r"(?ms)^```.*?^```[ \t]*$")


def _mask_fences(text: str) -> str:
    """Blank line-leading `#` inside fenced blocks, preserving every offset.

    One character in, one character out, so a span found in the masked copy
    slices the ORIGINAL text correctly. Masking rather than deleting is the
    whole point: deleting would shift every offset after the first fence.
    """
    return _FENCED.sub(lambda m: re.sub(r"(?m)^#", " ", m.group(0)), text)


def report_section(text: str) -> str:
    m = REPORT_SECTION.search(_mask_fences(text))
    return text[m.start(1):m.end(1)] if m else ""


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
    """The scope claim is itself pinned, because prose kept not being enough.

    `#47` re-homed the second-press requirement here from a file-wide pin, and
    `3cb09f5`'s message said it was now "scoped to `### 4. Report`". It was
    not: the lookahead stopped at `^### ` or EOF and never at `^## `, and
    `### 4. Report` is the last H3 in the file — so the capture ran to EOF,
    swallowing two H2 sections. Mutation-proved twice: the phrase could be
    deleted from the template and re-added under `## Cost warning` with the
    suite green, and the liveness check passed on a Report gutted to `TBD.`
    because the swallowed tail supplied 834 chars against its 400 threshold.

    Synthetic fixtures, not the live file: these pin the FUNCTION's boundary
    behaviour, so they stay meaningful when SKILL.md is next rearranged. The
    live file supplies only a lower bound (it must contain the requirement);
    the upper bound — that the capture stops where it should — is only
    checkable against documents built to probe it.
    """

    FIXTURE = (
        "## Earlier\n\nbefore\n\n"
        "### 4. Report\n\nINSIDE the report\n\n"
        "## Cost warning\n\nOUTSIDE, one heading level up\n\n"
    )

    # An H3 terminator needs its own fixture. The combined one above puts an H2
    # first, so it truncates there and an H2-only terminator would pass an
    # "does it stop at H3" assertion without ever exercising H3 — which is
    # exactly what the first version of this class did.
    FIXTURE_H3_FIRST = (
        "### 4. Report\n\nINSIDE the report\n\n"
        "### 9. Later H3\n\nOUTSIDE, same level\n"
    )

    FIXTURE_H4 = (
        "### 4. Report\n\nintro\n\n"
        "#### 4a. A subsection\n\nstill the report\n\n"
        "## Next\n\nout\n"
    )

    def test_the_section_stops_at_the_next_h2(self):
        section = report_section(self.FIXTURE)
        self.assertIn("INSIDE the report", section)
        self.assertNotIn(
            "OUTSIDE, one heading level up", section,
            "report_section() ran past `## Cost warning`. Anything the checks "
            "above require can then be satisfied from a section the user never "
            "reads, which is how the phrase survived deletion from the template")

    def test_the_section_stops_at_the_next_h3(self):
        section = report_section(self.FIXTURE_H3_FIRST)
        self.assertIn("INSIDE the report", section)
        self.assertNotIn("OUTSIDE, same level", section)

    def test_an_h4_subsection_stays_inside_the_report(self):
        """`#### ` has no space after the third #, so the terminator skips it."""
        self.assertIn("still the report", report_section(self.FIXTURE_H4))

    def test_a_hash_inside_a_fenced_block_does_not_end_the_section(self):
        """A `# comment` in a fence is an idiom this file uses ten times over.

        The terminator is a line-prefix match, so without fence awareness a
        shell comment inside the report's own example truncates the section.
        That direction is fail-LOUD — the liveness check below fires — so it
        is a misleading false red rather than a hole a regression can walk
        through. Closed anyway: a guard whose failure message misdiagnoses the
        cause costs the next reader the same hour it cost this one.
        """
        doc = ("### 4. Report\n\n```bash\n# a shell comment\ncache.py status\n```\n\n"
               "trailing prose that must survive\n\n## Next\n\nout\n")
        section = report_section(doc)
        self.assertIn("trailing prose that must survive", section)
        self.assertNotIn("out", section)

    def test_a_gutted_report_cannot_borrow_length_from_what_follows(self):
        """The liveness check must fail on an empty report, not pass on a long tail."""
        gutted = ("### 4. Report\n\nTBD.\n\n"
                  "## Where the cache lives\n\n" + ("filler. " * 200) + "\n")
        self.assertLess(
            len(report_section(gutted)), 400,
            "a Report gutted to `TBD.` still measured over the liveness threshold, "
            "because the capture reached content that is not the report")


class TestTheRequirementIsPinnedToWhatTheUserReads(unittest.TestCase):
    """Pin the emitted report, and pin how the emitted report is located.

    `009e0f7` moved the requirement off section prose and onto "the fenced
    block", which was the right direction and the wrong implementation: it
    took the FIRST block matching `^```\n`, and the CLOSING line of a ```bash
    block is exactly that. So in a section containing any tagged fence, the
    locator returned the prose BETWEEN two code blocks and called it the
    template. Three ordinary edits — add a ```bash example (the section's own
    prose invites one), add a sentence naming both presses, revert the real
    template — left 475 tests green while the report a user reads lost the
    second press entirely.

    That was instance five of one shape: file-wide, then section-wide, then
    template-scoped-with-an-unpinned-template. The lesson that finally has to
    stick is that "the first fence is the template" is an assumption, and an
    assumption is not a scope. So the template is now located by its CONTENT,
    that content anchor is asserted to be unique, and the locator itself is
    tested against a document built to hijack it.
    """

    # Opening fence may carry a language tag; closing fence is bare.
    FENCE_BLOCK = re.compile(r"(?ms)^```[^\n]*\n(.*?)^```[ \t]*$")

    # The report template is identified by what it IS, not by where it sits.
    REPORT_SHAPE = re.compile(r"^\s*skipped \d+ — no transcript", re.M)

    # `產生 / Generate` is a SUBSTRING of `立即產生 / Generate now`, so a naive
    # pattern for the first press can never fail while the second press is
    # present. Both guards are load-bearing.
    FIRST_PRESS = re.compile(r"(?<!立即)產生\s*/\s*Generate(?!\s*now)")
    SECOND_PRESS = re.compile(r"立即產生\s*/\s*Generate now")

    @classmethod
    def _locate(cls, section: str) -> list[str]:
        return [m.group(1) for m in cls.FENCE_BLOCK.finditer(section)
                if cls.REPORT_SHAPE.search(m.group(1))]

    def _report_template(self) -> str:
        blocks = self._locate(report_section(SKILL.read_text(encoding="utf-8")))
        self.assertEqual(
            1, len(blocks),
            f"expected exactly one fenced block inside `### 4. Report` shaped like "
            f"the report (`skipped N — no transcript`), found {len(blocks)}. Zero "
            f"means the template moved or changed shape and these checks would be "
            f"asserting about nothing; more than one means the anchor no longer "
            f"identifies it uniquely and this class would be pinning an arbitrary one")
        return blocks[0]

    def test_the_locator_is_not_fooled_by_a_tagged_fence(self):
        """The exact hijack that made 009e0f7's version vacuous."""
        section = (
            "\n\nThen show `cache.py status`:\n\n"
            "```bash\ncache.py status\n```\n\n"
            "In the web app press 產生 / Generate and then 立即產生 / Generate now.\n\n"
            "Say it in this shape:\n\n"
            "```\nskipped 9 — no transcript\n  open it in Plaud and press 產生 / Generate.\n```\n\n")
        blocks = self._locate(section)
        self.assertEqual(1, len(blocks), "the content anchor did not isolate the template")
        self.assertIn("skipped 9", blocks[0])
        self.assertNotIn("In the web app", blocks[0],
                         "the locator returned prose between two code blocks — the "
                         "closing ``` of the tagged block was read as an opening")

    def test_the_first_press_pattern_cannot_be_satisfied_by_the_second(self):
        """If this ever passes trivially, the first-press check is dead weight."""
        self.assertIsNone(
            self.FIRST_PRESS.search("press 立即產生 / Generate now."),
            "`產生 / Generate` matched inside `立即產生 / Generate now` — the "
            "first-press assertion is vacuous and the template can drop the "
            "chooser step with the suite green")

    def test_the_template_names_both_presses_in_order(self):
        body = self._report_template()
        first = self.FIRST_PRESS.search(body)
        second = self.SECOND_PRESS.search(body)
        self.assertIsNotNone(
            first, "the report template does not name 產生 / Generate — the press "
                   "that opens the chooser. Naming only the second sends the reader "
                   "hunting a button that is not on screen yet")
        self.assertIsNotNone(
            second, "the report template names only the FIRST press. The first opens "
                    "the chooser; the second is what starts transcription, and a "
                    "reader who stops after one is back in the wait that never ends")
        self.assertLess(
            first.start(), second.start(),
            "the two presses are named out of order. 產生 opens the chooser and "
            "立即產生 is inside it; reversed, the instruction cannot be followed")
