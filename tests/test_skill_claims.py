#!/usr/bin/env python3
"""A skill description must not say things that are not true.

Two rules live here, guarding two measured failures of the same kind. Both
are about `description:` blocks, because that is the only thing a model reads
when deciding whether a skill applies — a false sentence in there is a false
premise inside a routing decision.

1. **Exclusivity** (#35) — a claim to be the only way to do something must
   name what it is exclusive against. Written below.
2. **Capability** (#36) — a claim to perform an action must correspond to a
   step that performs it. See `CAPABILITY_CLAIMS` further down.

They failed differently, and the difference is worth keeping in mind. #35's
sentence was **true when written** and went false when the world changed.
#36's was **never true**: `plaud-upload` said it started transcription from
its first commit, and no version of it ever contained the step that would.
Nothing marks the moment a never-true sentence goes wrong, so nothing
prompts anyone to re-read it.

--- rule 1: exclusivity (#35) ---------------------------------------------

A skill description must not claim to be the only way to do something.

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

The same sentence governs rule 2. "Every capability a description claims has
a step performing it" is not checkable — it needs to read the prose and know
what counts as performing. So rule 2 is a **closed list of one**, and its
tests are named for the single claim they pin.

Rule 2 also stops at the `description:` block, and does **not** scan body
prose. #36's false sentence was in both, so this is a real gap, chosen with
the reason stated: body prose has legitimate non-claiming uses of the same
words ("To start transcription, press 產生" is correct documentation), and no
regex separates those from a claim. Extending the rule there would fail
correct writing and teach the next author to weaken the pattern. The stakes
also differ — the description is what gets read when deciding whether to fire
the skill and whether the job is done.

A related structural idea was measured and rejected: treating each body's
opening paragraph as a second description. Across these eight skills that
paragraph is sometimes a summary, sometimes a correction notice, sometimes a
scope note. Applying a claim rule to a location whose role varies is how
false positives get manufactured.
"""
from __future__ import annotations

import pathlib
import re
import unittest
from typing import NamedTuple

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


# --- rule 2: capability (#36) ----------------------------------------------


class CapabilityClaim(NamedTuple):
    """One measured claim, and the step that would make it true."""

    name: str
    claim: re.Pattern           # matched against description clauses
    action: re.Pattern          # the thing being pressed/invoked
    driver: re.Pattern | None   # must appear in the SAME fence as `action`
    history: str


# A CLOSED LIST. There is exactly one entry, and **a second one may not be
# added by analogy** — the bar for adding is "this claim has been measured
# against the steps and found unbacked", not "this claim also looks worth
# checking". A registry that grows by resemblance stops being a list of
# measured facts and becomes a guess about which sentences feel risky.
#
# Each entry names its own boundary. `action` is what a step *doing the thing*
# looks like, not what mentioning it looks like: `plaud-upload`'s body has
# always contained the word transcription, which is exactly why prose-level
# matching would have called this file green through four releases.
CAPABILITY_CLAIMS = (
    CapabilityClaim(
        name="start-transcription",
        claim=re.compile(r"start(?:s|ing)?\s+transcription|啟動轉錄", re.I),
        # Plaud's generate flow is two presses; the second one is the commit.
        # Matching only the first would accept a skill that opens the dialog
        # and walks away.
        action=re.compile(r"立即產生|Generate now"),
        # The skill drives the browser through `safari-browser` — that string
        # is what separates a command this skill runs from a command it tells
        # the reader to run. Without it, a fenced block saying "Open Plaud and
        # click 立即產生" satisfies the rule, which is the prose hole one level
        # down (verified reachable, kept as a REJECT case below).
        driver=re.compile(r"safari-browser"),
        history=(
            "#36 — plaud-upload said it started transcription from v0.1.0 and "
            "no version ever contained the step. Measured 2026-08-08: Plaud's "
            "own file page shows 準備生成 with a 產生 button waiting, and asks "
            "the user to choose how to generate. Nothing starts on its own."
        ),
    ),
)


def _body(skill_md: pathlib.Path) -> str:
    """Everything after the frontmatter — where the steps live."""
    text = skill_md.read_text(encoding="utf-8")
    m = re.match(r"^---\r?\n.*?\r?\n---\r?\n", text, re.S)
    return text[m.end():] if m else text


# Backtick and tilde fences both. Tilde is unused in this repo today (all
# eight skills use backticks, all paired — measured), so this arm is latent;
# it costs four characters and removes a false-positive class before anyone
# trips it.
FENCE = re.compile(r"(?ms)^[ \t]*(?:```|~~~)[^\n]*\n(.*?)^[ \t]*(?:```|~~~)")


def code_fences(body: str) -> list[str]:
    """Each fenced code block, kept separate.

    Separate, not joined, because a rule may need two things to appear *in the
    same block* — see `driver` on CapabilityClaim. Joining first would let a
    driver in one block vouch for an action in another.

    Telling the *user* to press a button is not the skill pressing it, and
    across a whole body those two read identically. That distinction is not
    hypothetical tidiness: the first version of this guard matched the whole
    body, and the acid run for #36 caught it going **green on #36 itself** —
    the remediation prose ("open it and press 產生, then 立即產生") contains
    the words a real step contains, so restoring the false claim tripped
    nothing. A guard that its own fix disarms is worse than no guard, because
    the green is now evidence for the wrong thing.

    Known blind spots, all in the false-positive direction (a skill that DOES
    have the step gets flagged — loud, and one edit away): an unclosed fence
    swallows everything after it, four-space indented blocks are not fences at
    all, and a quad-backtick wrapper hides the inner block. Measured against
    this repo: eight skills, all backtick, all paired, no indented code
    blocks. Latent, not live.
    """
    return FENCE.findall(body)


def _performs(rule: CapabilityClaim, body: str) -> bool:
    """Does some fenced block actually carry out `rule`?

    Both the action and its driver must land in the SAME fence. A driver
    somewhere else in the file says the skill drives a browser at some point,
    not that it drives it *for this*.
    """
    for fence in code_fences(body):
        if not rule.action.search(fence):
            continue
        if rule.driver is None or rule.driver.search(fence):
            return True
    return False


def unbacked_claims(description: str, body: str) -> list[str]:
    """Clauses claiming a capability no step in `body` performs.

    Negation is checked the same way rule 1 checks it, and for a sharper
    reason: the *fix* for #36 puts the words "start transcription" into the
    description — inside "It does not start transcription". A guard that
    flagged its own remedy would be uninstalled within the day.
    """
    bad = []
    for rule in CAPABILITY_CLAIMS:
        for clause in clauses(description):
            m = rule.claim.search(clause)
            if not m:
                continue
            if NEGATED.search(clause[: m.start()]):
                continue
            if not _performs(rule, body):
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
        # No comma, so clause-splitting cannot rescue this one — it is here to
        # protect the word-boundary match specifically. Under the substring
        # matching the first draft used, `unofficial` satisfied `official` and
        # this passed. (Found when the acid run for the word-boundary fix came
        # back green: the comma case above was already being caught by the
        # splitter, so the guard it was meant to protect had no test of its own.)
        "This is the only unofficial upload route.",
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


class TestTheCapabilityRuleItself(unittest.TestCase):
    """Synthetic cases, because the live scan below passes *vacuously*.

    Once #36 is fixed no description claims transcription, so the live test
    would stay green with the rule deleted. These three cases are the only
    thing standing between "the guard works" and "the guard is absent" — the
    #31/#32 shape, one more time.
    """

    CLAIMS = "Upload a file into your own Plaud library and start transcription."
    DENIES = "Upload a file into your own Plaud library. It does not start transcription."
    BODY_WITHOUT_STEP = (
        "### Step 2 — upload\n"
        "safari-browser upload --native \"input[type='file']\" \"<path>\"\n\n"
        "### Step 3 — verify\n"
        "Confirm the recording appears in the library.\n"
        # The word is present, and means nothing. This line is the reason the
        # rule matches a keypress and not a mention.
        "After it appears, plaud-index can pull its transcript.\n"
    )
    # Prose telling the *user* to press it. Reads almost identically to a real
    # step, and is exactly what the fix for #36 had to say — which is how the
    # first version of this guard ended up green on #36. Kept as a permanent
    # case so that cannot happen twice.
    BODY_INSTRUCTING_THE_USER = BODY_WITHOUT_STEP + (
        "\n### The skill stops here\n"
        "Open it in Plaud and press 產生, then 立即產生. Nothing starts by itself.\n"
    )
    # A fenced block, but it is what the *reader* is told to do — no driver,
    # so the skill is not the one acting. One level below the prose hole and
    # verified reachable before this case existed.
    BODY_FENCED_USER_INSTRUCTION = BODY_WITHOUT_STEP + (
        "\n### Tell the user to do this\n"
        "```\n"
        "Open web.plaud.ai and click 立即產生\n"
        "```\n"
    )
    BODY_WITH_STEP = BODY_WITHOUT_STEP + (
        "\n### Step 4 — start transcription\n"
        "```bash\n"
        "safari-browser js \"...t==='立即產生'...click()\" --url plaud\n"
        "```\n"
    )
    # Same step, tilde-fenced. Unused in this repo today, which is exactly why
    # it needs a case — an untested arm of the parser is a guess, and the arm
    # fails in the direction that flags correct documentation.
    BODY_WITH_TILDE_STEP = BODY_WITHOUT_STEP + (
        "\n### Step 4 — start transcription\n"
        "~~~bash\n"
        "safari-browser js \"...t==='立即產生'...click()\" --url plaud\n"
        "~~~\n"
    )
    # Driver present, action present, different blocks. Driving a browser
    # somewhere in the file is not driving it for this.
    BODY_DRIVER_IN_ANOTHER_FENCE = BODY_WITHOUT_STEP + (
        "\n### Step 2 — upload\n"
        "```bash\n"
        "safari-browser upload --native \"input[type='file']\" \"<path>\"\n"
        "```\n"
        "\n### What happens next\n"
        "```\n"
        "Someone clicks 立即產生\n"
        "```\n"
    )

    def test_a_claim_with_no_step_performing_it_is_flagged(self):
        """Exactly the state plaud-upload shipped in for four releases."""
        self.assertTrue(
            unbacked_claims(self.CLAIMS, self.BODY_WITHOUT_STEP),
            "a description claiming transcription over a body that never "
            "triggers it must be flagged — that is #36",
        )

    def test_denying_the_capability_is_not_claiming_it(self):
        self.assertEqual(
            [], unbacked_claims(self.DENIES, self.BODY_WITHOUT_STEP),
            "'It does not start transcription' is the fix, not the defect",
        )

    def test_telling_the_user_to_press_it_is_not_a_step(self):
        """The case that caught this guard being green on its own defect."""
        self.assertTrue(
            unbacked_claims(self.CLAIMS, self.BODY_INSTRUCTING_THE_USER),
            "instructions to the user are not the skill acting — if this "
            "passes, the guard is satisfied by the very prose written to "
            "remediate the claim, and goes green forever",
        )

    def test_a_fenced_instruction_to_the_user_is_not_a_step(self):
        """One level below the prose hole; verified reachable before this fix."""
        self.assertTrue(
            unbacked_claims(self.CLAIMS, self.BODY_FENCED_USER_INSTRUCTION),
            "a code fence showing the reader what to click is not the skill "
            "clicking it — without the driver requirement this passed",
        )

    def test_a_driver_in_a_different_fence_does_not_vouch(self):
        self.assertTrue(
            unbacked_claims(self.CLAIMS, self.BODY_DRIVER_IN_ANOTHER_FENCE),
            "driving the browser elsewhere in the file is not driving it for "
            "this action — the two must share a block",
        )

    def test_a_claim_backed_by_a_step_is_accepted(self):
        self.assertEqual(
            [], unbacked_claims(self.CLAIMS, self.BODY_WITH_STEP),
            "a skill that really presses the button may say that it does",
        )

    def test_a_tilde_fenced_step_counts_too(self):
        self.assertEqual(
            [], unbacked_claims(self.CLAIMS, self.BODY_WITH_TILDE_STEP),
            "~~~ is a markdown fence; a parser that only knows ``` would flag "
            "a skill whose step is right there",
        )


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

    def test_the_scan_actually_reads_bodies(self):
        """Same reasoning as above, for the half rule 2 depends on.

        `unbacked_claims` only fires when the body lacks the action. An empty
        body therefore makes every claim look unbacked — loud, so harmless.
        The dangerous direction is the parser returning the *whole file*
        including frontmatter, which would let a description satisfy its own
        claim. Both are caught by checking the body is real and starts after
        the frontmatter.
        """
        for skill_md in _skills():
            with self.subTest(skill=skill_md.parent.name):
                body = _body(skill_md)
                self.assertGreater(len(body), 200, "body came back empty or near-empty")
                self.assertNotIn(
                    "description:", body.split("\n# ")[0],
                    "frontmatter leaked into the body — a description could then "
                    "satisfy its own capability claim",
                )

    def test_no_skill_claims_a_capability_no_step_performs(self):
        for skill_md in _skills():
            for clause in unbacked_claims(_description(skill_md), _body(skill_md)):
                with self.subTest(skill=skill_md.parent.name):
                    self.fail(
                        f"\n{skill_md.parent.name} claims a capability its steps "
                        f"never perform:\n\n    {clause}\n\n"
                        f"Either add the step, or stop claiming it. A skill that "
                        f"hands back silently after promising to start something "
                        f"leaves the user waiting for an event that will not "
                        f"happen, with nothing to notice it by (#36).\n"
                        f"Registry entries: {[r.name for r in CAPABILITY_CLAIMS]}"
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
