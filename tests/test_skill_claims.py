#!/usr/bin/env python3
"""A skill description must not claim to be the only way to do something (#35).

A `description:` block is the only thing a model reads when deciding whether a
skill applies. A false sentence in there is a false premise inside the routing
decision — worse than a false sentence in prose, because nobody re-reads it and
its effect is a tool firing when it should not have.

`plaud-upload` said:

    The official Plaud MCP and CLI are read-only — this is the only path that
    writes into a personal library.

True when written: the comparison was the official surface, and both halves of
it are read-only. Written as an **absolute** though, so it stopped being true
the moment anything else on the machine could upload. Measured 2026-08-08: a
second Plaud plugin was installed alongside this one and also uploads. The
sentence had already gone false and nothing noticed.

The rule: **an exclusivity claim must name what it is exclusive against.** "The
official MCP cannot answer these" is fine and stays fine — a claim about a
specific, measured surface.

## Scope, stated rather than implied

This catches a **literal rhetorical form** in this repo's descriptions. It does
not, and cannot:

- know what other plugins are installed, or that one collides by name;
- catch an exclusive *implication* carried without the vocabulary — "this skill
  is what covers writing into a library" says the same thing and matches
  nothing here;
- verify a qualifier actually modifies the claim rather than sitting nearby;
- decide whether a description discriminates well against a sibling tool.

Cross-model review (#35 verify) surfaced every one of those. They are real and
they are out of reach of a regex. What is in reach is the exact sentence shape
that already went false once, so that is what this guards — and the name of
each test says only that much.

## What is deliberately NOT checked

Whether a description makes clear where its data lives (local cache vs Plaud's
servers) — the other half of #35. The only mechanical version is "does it
contain the string `local cache`", which is a proxy, not the property:
rewording trips it, and badly-written prose containing the magic words passes.
A proxy that looks like a guard is worse than an acknowledged gap.
"""
from __future__ import annotations

import pathlib
import re
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"

# Claims of the form "nothing else does this" — a promise about the whole world,
# and the world is bigger than the surface that was measured.
#
# Widened after cross-model review found the first draft caught six English
# phrasings and one Chinese word while missing `sole`, `unique`, `exclusively`,
# `alone`, `nowhere else`, leading `Only this…`, and every Chinese form except
# 唯一 — in descriptions that deliberately carry eleven languages.
EXCLUSIVITY = re.compile(
    r"\b(the only|only path|only way|nothing else|no other|nowhere else|"
    r"sole|solely|unique|uniquely|exclusively|the one and only)\b"
    r"|\bonly (this|these|the) \w+ (can|is|are|does|do)\b"
    r"|\b\w+ alone (can|is|does)\b"
    r"|唯一|只有|僅此|別無",
    re.I,
)

# Negations. "This is NOT the only way" is the opposite of an exclusivity claim
# and must not be flagged — a guard that punishes the very disclaimer it wants
# teaches people to delete disclaimers.
# Window is five words, not three: "We do not claim that this is the only path"
# puts four words between the negation and the claim. Bounded rather than
# unbounded so a negation early in a long clause cannot excuse an absolute at
# the end of it — the limit is real and stated, not pretended away.
NEGATED = re.compile(r"\b(not|never|no longer|isn't|is not|aren't|are not|"
                     r"do not|don't|does not|doesn't)\b\s+(\w+\s+){0,5}$", re.I)

# What an exclusivity claim may be exclusive *against*. Every entry names a
# specific, measured surface — that is what makes the claim checkable rather
# than a promise about everything that exists.
#
# `this skill` / `this plugin` were in the first draft and are NOT here: they
# made `This skill is the only way to upload` pass, which is a global absolute
# wearing a local-sounding subject. Verified — that sentence passed the first
# version of this file.
QUALIFIERS = ("official", "plaud mcp", "plaud cli", "official surface",
              "official plaud")

# Word-boundary matching, because substring matching let `unofficial` satisfy
# `official` — verified: "This is the only way any tool can upload, despite
# being unofficial." passed the first version.
QUALIFIER_RE = re.compile(r"\b(" + "|".join(re.escape(q) for q in QUALIFIERS) + r")\b", re.I)


def _description(skill_md: pathlib.Path) -> str:
    """The frontmatter `description:` block, or "" when there is none."""
    text = skill_md.read_text(encoding="utf-8")
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    if not m:
        return ""
    block, out, collecting = m.group(1), [], False
    for line in block.splitlines():
        if re.match(r"^description:", line):
            collecting = True
            out.append(re.sub(r"^description:\s*[|>]?[-+]?\s*", "", line))
            continue
        if collecting:
            if line and not line[0].isspace():   # next top-level key
                break
            out.append(line.strip())
    return "\n".join(out).strip()


def clauses(text: str) -> list[str]:
    """Split into clauses, after undoing YAML's soft wrapping.

    Splitting on newlines tears a qualifier off the claim it qualifies — a
    `description:` block is YAML-wrapped, so its line breaks fall mid-sentence.
    Soft wraps are joined first.

    Splitting on sentences alone is not enough either. `plaud-upload` read
    "The official Plaud MCP and CLI are read-only — this is the only path that
    writes into a personal library": one sentence containing "official", so a
    sentence-level check passes it. But that qualifier describes *the official
    surface being read-only*; it never bounded "the only path". Splitting at the
    dash separates the claim from a word that was never qualifying it.

    Dash and CJK handling both come from cross-model review: the first draft
    required a spaced em dash and a space after `。`, so `-`, `–`, an unspaced
    `—`, and ordinary Chinese punctuation all failed to split.

    Commas split too, which makes the rule STRICTER: "…, and it opens the
    official sign-in page" can no longer bless "this is the only way any tool
    can upload" sitting in front of it. The cost is that a qualifier placed
    before a comma ("Compared with the official CLI, this is the only path")
    now fails — and that is the phrasing the rule wants moved into the claim
    anyway. Erring strict is the safe direction here: a false positive is loud
    and one edit away, a false negative is the original bug coming back.
    """
    joined = re.sub(r"\s*\n\s*", " ", text)
    return [c.strip() for c in re.split(
        r"(?<=[.!?])\s+|(?<=[。！？；，、])\s*|\s*[—–]\s*|\s+-\s+|[;,]\s+", joined) if c.strip()]


def offending_claims(description: str) -> list[str]:
    """Clauses making an exclusivity claim without naming what it excludes.

    Extracted so the rule can be tested against synthetic input. The first
    version of this file only exercised the rule through `plaud-grep`'s live
    wording, via an `or "cannot" in sentence` branch that production never
    used — so the test named "a qualified claim is accepted" was checking a
    path the guard does not have. That is the #22 shape, inside the file
    written to prevent that shape.
    """
    bad = []
    for clause in clauses(description):
        m = EXCLUSIVITY.search(clause)
        if not m:
            continue
        if NEGATED.search(clause[: m.start()]):
            continue
        if not QUALIFIER_RE.search(clause):
            bad.append(clause)
    return bad


def _skills() -> list[pathlib.Path]:
    return sorted(SKILLS_DIR.glob("*/SKILL.md"))


class TestTheRuleItself(unittest.TestCase):
    """Synthetic cases — no coupling to any live description's wording."""

    REJECT = [
        "this is the only path that writes into a personal library.",
        "This skill is the only way to upload to Plaud.",
        "This plugin is the only uploader in existence.",
        "This is the only way any tool can upload, despite being unofficial.",
        "Only this skill can upload to Plaud.",
        "This is the sole upload route.",
        "Uploading is supported exclusively by this skill.",
        "Nowhere else can a file be uploaded.",
        "只有這個技能可以上傳。",
        "別無其他上傳途徑。",
    ]

    ACCEPT = [
        "The official Plaud MCP cannot answer these.",
        "Neither the official Plaud MCP nor the official CLI can produce timed subtitles.",
        "Within the official Plaud CLI this is the only supported upload path.",
        "This is not the only way to upload.",
        "We do not claim that this is the only path.",
        "Converts a cached transcript into .srt.",
    ]

    def test_absolutes_are_rejected(self):
        for text in self.REJECT:
            with self.subTest(text=text):
                self.assertTrue(offending_claims(text),
                                f"should have been flagged as an unqualified absolute: {text}")

    def test_qualified_and_non_claims_are_accepted(self):
        for text in self.ACCEPT:
            with self.subTest(text=text):
                self.assertEqual([], offending_claims(text),
                                 f"should NOT have been flagged: {text}")

    def test_a_qualifier_elsewhere_in_the_clause_does_not_bless_a_global_claim(self):
        """The precise hole the first draft had, kept as a permanent case."""
        self.assertTrue(offending_claims(
            "This is the only way any tool can upload to Plaud, "
            "and it opens the official Plaud sign-in page."),
            "an incidental 'official' must not qualify a claim about every tool")


class TestLiveDescriptions(unittest.TestCase):
    def test_the_scan_actually_reads_descriptions(self):
        """A scanner that finds nothing reports everything is fine.

        #31 and #32 were both this shape: a check whose own failure looked
        exactly like a pass. So the scan proves it found real content before
        any verdict below means anything.
        """
        found = _skills()
        self.assertGreaterEqual(len(found), 5, "no SKILL.md files found — has skills/ moved?")
        empty = [p.parent.name for p in found if len(_description(p)) < 40]
        self.assertEqual(
            [], empty,
            f"description block came back empty or near-empty for {empty} — the "
            f"frontmatter parser has drifted from how these files are written, "
            f"and an empty scan passes every other test in this file",
        )

    def test_no_unqualified_exclusivity_claim(self):
        for skill_md in _skills():
            for clause in offending_claims(_description(skill_md)):
                with self.subTest(skill=skill_md.parent.name):
                    self.fail(
                        f"\n{skill_md.parent.name} makes an exclusivity claim without "
                        f"naming what it is exclusive against:\n\n    {clause}\n\n"
                        f"An unqualified claim is a promise about the whole world, and it "
                        f"goes false the moment another tool can do the same thing — "
                        f"measured, not hypothetical (#35).\n"
                        f"Either name the surface, or drop the claim.\n"
                        f"Accepted qualifiers: {list(QUALIFIERS)}"
                    )
