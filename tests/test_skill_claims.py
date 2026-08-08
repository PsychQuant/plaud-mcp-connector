#!/usr/bin/env python3
"""A skill description must not claim to be the only way to do something (#35).

A `description:` block is the only thing a model reads when deciding whether a
skill applies. A false sentence in there is a false premise inside the routing
decision, which is worse than a false sentence in prose — nobody re-reads it,
and its effect is a tool firing when it should not have.

`plaud-upload` said:

    The official Plaud MCP and CLI are read-only — this is the only path that
    writes into a personal library.

True when written: the comparison was the official surface, and both halves of
it are read-only. Written as an **absolute** though, so it stops being true the
moment anything else on the machine can upload. Measured 2026-08-08: a second
Plaud plugin was installed alongside this one and also uploads. The sentence had
already gone false and nothing noticed.

The rule this file enforces is narrow on purpose: **an exclusivity claim must
name what it is exclusive against.** "The official MCP cannot answer these" is
fine and stays fine — it is a claim about a specific, measured surface.

## What this does NOT check

Whether a description makes clear where its data lives — local cache versus
Plaud's servers — which is the other half of #35 and the one that actually
disambiguates this plugin from a cloud-operating sibling.

That was considered and left out. The only mechanical version is "does the
description contain the string `local cache`", and that is a proxy, not the
property: rewording the same idea trips it, and a badly-written description
containing the magic words passes. A proxy that looks like a guard is worse
than an acknowledged gap, so this is the acknowledged gap. Those four
descriptions are reviewed by a human, not by this file.
"""
from __future__ import annotations

import pathlib
import re
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"

# Claims of the form "nothing else does this". Each is a promise about the whole
# world, and the world is bigger than the surface that was measured.
EXCLUSIVITY = re.compile(
    r"(the only\b|nothing else\b|no other\b|only path\b|only way\b|唯一)", re.I
)

# What an exclusivity claim is allowed to be exclusive *against*. Everything
# here names a specific, measured surface — which is what makes the claim
# checkable instead of a promise about everything that exists.
#
# A whitelist rather than a judgement call: a whitelist can be audited, and a
# failure explains itself. "Does this sentence feel adequately qualified" in a
# test is a gate nobody can reproduce.
QUALIFIERS = ("official", "plaud mcp", "plaud cli", "official surface",
              "this plugin", "this skill")


def _description(skill_md: pathlib.Path) -> str:
    """The frontmatter `description:` block, or "" when there is none.

    Frontmatter runs between the first two `---` lines. Inside it, `description:`
    opens a YAML block that continues while lines stay indented.
    """
    text = skill_md.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return ""
    block, out, collecting = m.group(1), [], False
    for line in block.splitlines():
        if re.match(r"^description:", line):
            collecting = True
            out.append(re.sub(r"^description:\s*\|?\s*", "", line))
            continue
        if collecting:
            if line and not line[0].isspace():   # next top-level key
                break
            out.append(line.strip())
    return "\n".join(out).strip()


def _clauses(text: str) -> list[str]:
    """Split into clauses, after undoing YAML's soft wrapping.

    Two mistakes this avoids, both found while writing it:

    **Splitting on newlines.** A `description:` block is YAML-wrapped, so its
    line breaks fall mid-sentence. Splitting there tears a qualifier off the
    claim it qualifies and reports a false positive. Soft wraps are joined
    first.

    **Splitting on sentences only.** `plaud-upload` read:

        The official Plaud MCP and CLI are read-only — this is the only path
        that writes into a personal library.

    One sentence, containing "official" — so a sentence-level check passes it.
    But the qualifier describes *the official surface being read-only*; it does
    not bound "the only path", which is still a claim about everything that
    exists. Splitting at the em dash separates the claim from a word that was
    never qualifying it.

    The cost is honest: a qualifier deliberately placed in a preceding clause
    ("Compared to the official CLI — this is the only path…") now fails. That
    phrasing is ambiguous to a reader too, and the fix is to move the qualifier
    into the claim, which is what the failure message asks for.
    """
    joined = re.sub(r"\s*\n\s*", " ", text)
    return [c.strip() for c in re.split(r"(?<=[.。!?])\s+|\s+—\s+|;\s+", joined) if c.strip()]


def _skills() -> list[pathlib.Path]:
    return sorted(SKILLS_DIR.glob("*/SKILL.md"))


class TestExclusivityClaims(unittest.TestCase):
    def test_the_scan_actually_reads_descriptions(self):
        """A scanner that finds nothing reports everything is fine.

        Both #31 and #32 were this shape: a check whose own failure looked
        exactly like a pass. So the scan asserts it found real content before
        any of its verdicts mean anything.
        """
        found = _skills()
        self.assertGreaterEqual(len(found), 5, "no SKILL.md files found — has skills/ moved?")
        descriptions = {p.parent.name: _description(p) for p in found}
        empty = [n for n, d in descriptions.items() if len(d) < 40]
        self.assertEqual(
            [], empty,
            f"description block came back empty or near-empty for {empty} — the "
            f"frontmatter parser has drifted from how these files are written, "
            f"and an empty scan passes every other test in this file",
        )

    def test_no_unqualified_exclusivity_claim(self):
        for skill_md in _skills():
            desc = _description(skill_md)
            for sentence in _clauses(desc):
                hit = EXCLUSIVITY.search(sentence)
                if not hit:
                    continue
                with self.subTest(skill=skill_md.parent.name, claim=hit.group(0)):
                    low = sentence.lower()
                    self.assertTrue(
                        any(q in low for q in QUALIFIERS),
                        f"\n{skill_md.parent.name} claims {hit.group(0)!r} without naming "
                        f"what it is exclusive against:\n\n    {sentence}\n\n"
                        f"An unqualified claim is a promise about the whole world, and it "
                        f"goes false the moment another tool can do the same thing — "
                        f"measured, not hypothetical (#35).\n"
                        f"Either name the surface, or drop the claim.\n"
                        f"Currently accepted qualifiers: {list(QUALIFIERS)}",
                    )

    def test_a_qualified_claim_is_accepted(self):
        """The rule must not punish the claims that are still true.

        `plaud-grep` says the official Plaud MCP cannot answer full-text
        questions — a claim about a measured surface, and correct. If the rule
        cannot tell that apart from an absolute, it is not a rule, it is a ban
        on a word.
        """
        desc = _description(SKILLS_DIR / "plaud-grep" / "SKILL.md")
        self.assertIn("official", desc.lower(),
                      "plaud-grep's claim about the official MCP is the worked example "
                      "of a qualified claim; if it changed, this test needs a new one")
        for sentence in _clauses(desc):
            if EXCLUSIVITY.search(sentence) or "cannot" in sentence.lower():
                low = sentence.lower()
                if any(q in low for q in QUALIFIERS):
                    return
        self.fail("no qualified claim left in plaud-grep to serve as the positive case")
