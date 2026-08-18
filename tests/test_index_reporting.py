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
# Checked inside the `### 4. Report` section, not across the file. A file-wide
# search for 產生 passes on this file no matter what the Report section says —
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

REPORT_SECTION = re.compile(r"(?ms)^### 4\. Report\b(.*?)(?=^### |\Z)")


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
