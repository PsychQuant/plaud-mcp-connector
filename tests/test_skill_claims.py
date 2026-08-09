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
what counts as performing. So rule 2 is a **closed list of one**.

## What actually protects #36, and what rule 2 adds (#39)

`TestTheExactRegressionOf36` further down is the load-bearing guard: it names
five files and five sentence shapes, so the exact edit that would undo #36
fails. Rule 2 is the weaker, more general layer sitting above it — a pattern
match, and a pattern match can be walked around.

That difference is worth being blunt about, because the two are easy to
confuse. The tests of rule 2 are named for the *rule* (`test_a_capability_
the_body_never_performs_is_flagged`), not for the claim, and the mechanism —
a registry keyed by name, with a `history` field — is shaped like something
meant to grow. It is not. A second entry may be added only after that claim
has been measured against the steps and found unbacked; the comment above
`CAPABILITY_CLAIMS` states the bar, and it is the bar, not a preference.

**Known limit, kept rather than papered over**: the claim pattern matches
adjacent words, so `start the transcription` walks past it. A wider regex
would move the bypass, not close it — the words admit unlimited variation
and the property being checked is semantic. `test_a_known_bypass_of_the_
capability_pattern` records it as a fact of the mechanism. What that costs is
bounded and stated: rule 2 catches the sentence that was actually written,
`TestTheExactRegressionOf36` catches the edit that would restore it, and
neither claims to catch a rephrasing.

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
        # Two ways a line can BE the commit press, rather than mention it:
        # naming the confirm control by its label, or clicking a control whose
        # selector says it confirms generation. Either counts; neither alone
        # is required.
        action=re.compile(
            r"立即產生|Generate now"
            r"|confirm[-_]?generation|generate[-_]?now",
            re.I,
        ),
        # `safari-browser` is how this repo's skills act on a page. Required,
        # but no longer sufficient — see `driver` usage in `_performs`, which
        # also demands a click and rejects echo/comment/negated lines. Three
        # inputs from cross-model review (#36 R3) broke the plain
        # co-occurrence version in both directions; all three are pinned as
        # cases above.
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


# A fence closes with the SAME delimiter, at least as long as the opener, on
# a line holding nothing else. All three conditions were missing (#39 gap 3):
# a ``` block could be closed by ~~~, a four-backtick wrapper by three, and
# ```not-a-close counted as a closing line. Each one lets prose outside any
# real fence be read as an executable step.
#
# `(?P=tick)` is the backreference that ties closer to opener; `{2,}` after it
# allows a longer closer (CommonMark permits that) while the group's own
# length sets the floor. `[ \t]*$` is what stops ```not-a-close.
FENCE = re.compile(
    r"(?ms)^[ \t]*(?P<tick>`{3,}|~{3,})[^\n`~]*\n"
    r"(?P<body>.*?)"
    r"^[ \t]*(?P=tick)(?:`|~)*[ \t]*$"
)


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
    return [m.group("body") for m in FENCE.finditer(body)]


# A line that only talks. `echo`/`print` emit text for the reader; `#` is a
# comment; a negation says the opposite of doing it. None of them press
# anything, and all three occurred in real review counterexamples.
_TALKS = re.compile(
    r"^\s*#|^\s*(?:echo|printf|print|cat)\b"
    r"|\b(?:never|do not|don't|must not|instead of|rather than|tell the user)\b",
    re.I,
)
# What acting on a control looks like here.
_CLICKS = re.compile(r"\.click\s*\(|\bclick\b", re.I)


def _performs(rule: CapabilityClaim, body: str) -> bool:
    """Does some fenced block actually carry out `rule`?

    A line performs the action when all three hold *on that line*: the driver
    is present, something is clicked, and the line is not merely talking about
    it. Same-line rather than same-fence, because a fence that uploads and
    then echoes advice satisfied same-fence while pressing nothing.

    None of this makes the rule sound — a determined author can still write a
    line that looks like a press and is not one. It closes the three holes
    that cross-model review actually reached (#36 R3): a sentence saying the
    skill must NOT click, a driver doing something else while the action sits
    in an `echo`, and — the other direction — a genuine selector click being
    flagged because it never names the button.
    """
    if rule.driver is None:
        return any(rule.action.search(f) for f in code_fences(body))
    for fence in code_fences(body):
        # Continuations first: a command wrapped with `\` is one logical line.
        for line in re.sub(r"\\\s*\n\s*", " ", fence).splitlines():
            if _TALKS.search(line):
                continue
            if (rule.driver.search(line)
                    and _CLICKS.search(line)
                    and rule.action.search(line)):
                return True
    return False


# --- naming a capability without claiming it (#39 gap 2) -------------------
#
# This rule keeps its OWN negation check rather than reusing `NEGATED` /
# `clauses()`. Those are tuned for rule 1's exclusivity claims and are shared
# with #35's tests; widening them to fit this rule risks regressing #35 for a
# problem that is not #35's. Two shapes, closed list — do NOT add a third by
# analogy:
#
#   1. denial — the negation may sit on EITHER side of the named capability.
#      `NEGATED` only looks leftward, so `"Start transcription" is not a
#      capability of this skill.` was flagged: the guard punished a sentence
#      whose whole point was to disclaim the thing.
#   2. quoted speech — the capability is named as something the USER says, not
#      something the skill does. Both the quotes and the speech verb are
#      required; quotes alone are emphasis, and every skill here has trigger
#      phrases in exactly this shape.
_CAP_DENIAL = (r"\b(not|never|isn't|is not|aren't|are not|do not|don't"
               r"|does not|doesn't)\b")
_CAP_DENIED_AFTER = re.compile(
    rf"^[\"'“”‘’」』]*[\s,]*(\w+[\s,]+){{0,3}}{_CAP_DENIAL}", re.I)
_CAP_SPEECH = re.compile(r"\b(say|says|saying|ask|asks|asking|tell|tells"
                         r"|request|requests)\b|說|問|要求", re.I)
_QUOTES = "\"'“”‘’「」『』"


def _named_but_not_claimed(clause: str, m: re.Match) -> bool:
    """True when the clause names the capability without asserting it."""
    if NEGATED.search(clause[: m.start()]):          # denial before
        return True
    if _CAP_DENIED_AFTER.search(clause[m.end():]):   # denial after
        return True
    before, after = clause[: m.start()], clause[m.end():]
    quoted = before.rstrip()[-1:] in _QUOTES and after.lstrip()[:1] in _QUOTES
    return quoted and bool(_CAP_SPEECH.search(before))


def unbacked_claims(description: str, body: str) -> list[str]:
    """Clauses claiming a capability no step in `body` performs.

    A named capability is not always a claimed one, and the sharpest case is
    this guard's own remedy: the #36 fix puts the words "start transcription"
    into the description — inside "It does not start transcription". A guard
    that flagged its own fix would be uninstalled within the day.
    """
    bad = []
    for rule in CAPABILITY_CLAIMS:
        for clause in clauses(description):
            m = rule.claim.search(clause)
            if not m:
                continue
            if _named_but_not_claimed(clause, m):
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

    def test_a_known_bypass_of_the_capability_pattern(self):
        """An article between the words walks past the claim pattern (#39).

        Asserted, not fixed. The pattern matches adjacent words; `start the
        transcription` does not match. A wider regex moves the bypass rather
        than closing it — the property is semantic and the phrasings are
        unlimited, so the honest thing is to say where the edge is.

        This is a fact about the mechanism, not a wish. If someone widens the
        pattern later this test goes red, and the docstring above has to be
        rewritten in the same change — which is the point.
        """
        self.assertEqual(
            [], unbacked_claims("It will start the transcription for you.",
                                "no steps here"),
            "the pattern now catches this — update the limits in the module "
            "docstring, do not just delete this test",
        )

    def test_denying_the_capability_after_naming_it_is_not_claiming_it(self):
        """A disclaimer must not be flagged (#39 gap 2).

        `NEGATED` only looks at text BEFORE the match, so a negation that
        follows it is invisible: `"Start transcription" is not a capability
        of this skill` was flagged as a claim. Punishing the exact sentence
        that disowns the capability teaches people to delete disclaimers.
        """
        self.assertEqual(
            [], unbacked_claims('"Start transcription" is not a capability '
                                'of this skill.', "no steps here"),
            "a sentence denying the capability was flagged as claiming it",
        )

    def test_a_capability_named_only_as_something_the_user_might_say(self):
        """Trigger phrases are not claims (#39 gap 1b).

        `description:` exists partly to hold what a user might type — this
        repo's descriptions carry eleven languages of them. Treating quoted
        user speech as a claim by the skill would flag correct routing
        documentation.
        """
        self.assertEqual(
            [], unbacked_claims('Use this when the user says '
                                '"start transcription".', "no steps here"),
            "a quoted trigger phrase was read as the skill claiming it",
        )

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

    # Cross-model review (#36 R3) broke the co-occurrence rule in both
    # directions with three concrete inputs. All three are pinned here.
    BODY_FENCE_SAYS_NOT_TO = (
        "```text\n"
        "Never use safari-browser to click Generate now; tell the user to do it.\n"
        "```\n"
    )
    BODY_DRIVER_UPLOADS_ACTION_ECHOED = (
        "```bash\n"
        'safari-browser upload --native "input[type=file]" "$file"\n'
        'echo "Tell the user to press Generate now"\n'
        "```\n"
    )
    BODY_SELECTOR_CLICK_NO_UI_STRING = (
        "```bash\n"
        "safari-browser js \\\n"
        "  \"document.querySelector('[data-testid=confirm-generation]').click()\"\n"
        "```\n"
    )

    def test_a_fence_saying_not_to_do_it_is_not_doing_it(self):
        self.assertTrue(
            unbacked_claims(self.CLAIMS, self.BODY_FENCE_SAYS_NOT_TO),
            "co-occurrence of the two strings inside a sentence that says the "
            "skill must NOT do this counted as doing it",
        )

    def test_the_driver_must_be_driving_the_action_not_something_else(self):
        self.assertTrue(
            unbacked_claims(self.CLAIMS, self.BODY_DRIVER_UPLOADS_ACTION_ECHOED),
            "the driver uploads and the action only appears inside an echo — "
            "same block, but nothing presses anything",
        )

    # Reads the button's label; presses nothing. No echo, no negation, so
    # only the click requirement stands between this and a false pass.
    BODY_READS_LABEL_WITHOUT_CLICKING = (
        "```bash\n"
        "safari-browser js \"var ready = "
        "document.querySelector('.btn').textContent === '立即產生'\"\n"
        "```\n"
    )
    # Driver on one line, action on another, neither of them talking. Only the
    # same-LINE requirement stands here — same-fence would let these vouch for
    # each other.
    BODY_SPLIT_ACROSS_LINES = (
        "```bash\n"
        'safari-browser js "document.querySelector(\'.upload\').click()"\n'
        "STATUS_LABEL=立即產生\n"
        "```\n"
    )

    def test_reading_the_label_is_not_pressing_the_button(self):
        """Isolates the click requirement.

        Without its own case this passed on the strength of the echo/negation
        filter instead — a guard whose acid is satisfied by a different guard
        has no test of its own. Same shape as the tilde arm earlier in #36.
        """
        self.assertTrue(
            unbacked_claims(self.CLAIMS, self.BODY_READS_LABEL_WITHOUT_CLICKING),
            "a command that only compares the label to a string is not the "
            "skill pressing it",
        )

    def test_driver_and_action_must_share_a_line(self):
        """Isolates the same-line requirement, for the same reason."""
        self.assertTrue(
            unbacked_claims(self.CLAIMS, self.BODY_SPLIT_ACROSS_LINES),
            "clicking one control and assigning the other's label to a shell "
            "variable is not pressing it, even in one block",
        )

    def test_a_selector_click_counts_even_without_the_ui_label(self):
        """The false-positive half. A real step may not name the button."""
        self.assertEqual(
            [], unbacked_claims(self.CLAIMS, self.BODY_SELECTOR_CLICK_NO_UI_STRING),
            "a skill that clicks the confirm control by selector is performing "
            "the action; requiring the UI label as well flags correct work",
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

    # --- fence pairing (#39 gap 3) -------------------------------------
    #
    # The fence regex matched any opener against any closer, so a block
    # could be "closed" by a different delimiter, or by a line that merely
    # starts with one. Both let prose be read as executable steps.

    def test_a_backtick_fence_is_not_closed_by_a_tilde(self):
        self.assertEqual(
            [], code_fences("```bash\nsafari-browser click 立即產生\n~~~\n"),
            "a ``` block was closed by ~~~, so unfenced prose after it "
            "would be read as a step",
        )

    def test_a_closing_line_must_be_only_the_delimiter(self):
        self.assertEqual(
            [], code_fences("```bash\nsafari-browser click 立即產生\n```not-a-close\n"),
            "```not-a-close was treated as a closing fence",
        )

    def test_a_longer_fence_is_not_closed_by_a_shorter_one(self):
        """Quad-backtick wrappers exist to contain triple-backtick examples."""
        body = "````md\n```bash\nsafari-browser click 立即產生\n```\n````\n"
        fences = code_fences(body)
        self.assertEqual(1, len(fences), f"expected one outer fence, got {fences}")
        self.assertIn("```bash", fences[0], "the inner example was lost")

    def test_a_tilde_fenced_step_counts_too(self):
        self.assertEqual(
            [], unbacked_claims(self.CLAIMS, self.BODY_WITH_TILDE_STEP),
            "~~~ is a markdown fence; a parser that only knows ``` would flag "
            "a skill whose step is right there",
        )


class TestTheRuleThroughTheFileFormat(unittest.TestCase):
    """The same rule, reached the way production reaches it (#39 gap 4).

    Every test above hands `unbacked_claims` two strings directly. That
    checks the rule and nothing else — `_description` and `_body` are never
    run, so a parser that silently dropped part of a description would leave
    all of them green while the live scan quietly stopped seeing anything.

    The live tests cover the empty case (`test_the_scan_actually_reads_*`),
    but "not empty" is a weaker claim than "the part that carries the claim
    survived". The hazard is positional: `_description` stops at the next
    top-level YAML key, so the *last* line of the block is the one an
    off-by-one drops. Both tests below put the capability there.
    """

    def _skill_file(self, body: str) -> pathlib.Path:
        import tempfile
        d = pathlib.Path(tempfile.mkdtemp())
        p = d / "SKILL.md"
        p.write_text(
            "---\n"
            "name: round-trip\n"
            'description: "Upload a file to Plaud through the web app, and\n'
            '  then start transcription for it."\n'
            "metadata:\n"
            "  requires:\n"
            "    bins: []\n"
            "---\n"
            "\n# A skill\n\n" + body,
            encoding="utf-8")
        return p

    BODY_WITHOUT_STEP = (
        "Tell the user to press 產生 / Generate themselves.\n\n"
        "```bash\nsafari-browser open https://app.plaud.ai\n```\n" + "x" * 300)

    BODY_WITH_STEP = (
        "```bash\nsafari-browser click '立即產生 / Generate now'\n```\n" + "x" * 300)

    def test_a_claim_on_the_last_description_line_is_still_read(self):
        p = self._skill_file(self.BODY_WITHOUT_STEP)
        self.assertIn("start transcription", _description(p),
                      "the last line of the description block was dropped — "
                      "every synthetic test above would still pass")
        self.assertTrue(
            unbacked_claims(_description(p), _body(p)),
            "an unbacked claim survived the round trip through SKILL.md",
        )

    def test_the_round_trip_still_clears_a_backed_claim(self):
        """Otherwise the test above would pass on a parser that flags anything."""
        p = self._skill_file(self.BODY_WITH_STEP)
        self.assertEqual(
            [], unbacked_claims(_description(p), _body(p)),
            "a skill whose step is right there was flagged through the file",
        )


class TestTheExactRegressionOf36(unittest.TestCase):
    """Pin the five sentences #36 removed, in the files they lived in.

    The general rule above guards `description:` only, and that was a real
    hole: of the five places #36 fixed, one was a description. Cross-model
    review demonstrated that the other four could be reverted verbatim with
    the whole suite still green — a guard written to stop #36 recurring that
    would not have noticed #36 recurring.

    The first instinct was to widen the general rule to body prose. That is
    the wrong shape and this file already says why: body prose has legitimate
    non-claiming uses of the same words, and no regex separates them. But the
    requirement was never a general rule — it was *this measured regression*.
    Naming the file and the phrasing sidesteps the false-positive problem
    entirely, because there is nothing to generalise.

    Brittleness is the point. A rewrite that touches these lines should have
    to look at this list and decide, rather than sail past.
    """

    # (path, forbidden pattern, what it was)
    REMOVED = (
        ("skills/plaud-upload/SKILL.md", r"and\s+starts?\s+transcription",
         "the description and body headline both claimed the upload starts it"),
        ("README.md", r"and\s+starts?\s+transcription",
         "the README's plaud-upload paragraph claimed the same"),
        ("skills/plaud-upload/SKILL.md", r"transcript\s+reaches\s+your\s+machine",
         "promised the transcript would arrive on its own"),
        ("skills/plaud-upload/SKILL.md", r"once\s+processing\s+finishes",
         "presupposed processing had started; it never does"),
    )

    # (path, required pattern, why)
    KEPT = (
        ("skills/plaud-upload/SKILL.md", r"does not\s*\n?\s*start transcription",
         "the description must deny it outright — that denial is the fix"),
        ("README.md", r"立即產生|Generate now",
         "the README must name the SECOND press; the first only opens the "
         "chooser, and a reader who stops after one is back in the silent wait"),
    )

    # File-wide "contains 立即產生" is not enough for the handoff: adding the
    # second press to the *description* (a later fix) satisfied it on its own,
    # and the acid run caught the whole handoff section becoming deletable
    # again. Scope the assertion to the section it is about.
    HANDOFF_HEADING = "### The skill stops here, and the recording has no transcript"

    def _handoff_section(self) -> str:
        text = (REPO_ROOT / "skills/plaud-upload/SKILL.md").read_text(encoding="utf-8")
        self.assertIn(self.HANDOFF_HEADING, text,
                      "the handoff section is gone — without it the skill ends "
                      "silently and the user waits for an event that never "
                      "comes (#36)")
        start = text.index(self.HANDOFF_HEADING)
        nxt = text.find("\n## ", start)
        return text[start:nxt if nxt != -1 else len(text)]

    def test_the_handoff_section_names_both_presses(self):
        section = self._handoff_section()
        for pattern, which in ((r"產生 / Generate\b", "first"),
                               (r"立即產生 / Generate now", "second (the commit)")):
            with self.subTest(press=which):
                self.assertRegex(
                    section, pattern,
                    f"the handoff no longer names the {which} press — a reader "
                    f"who stops early is back in the silent wait (#36)",
                )

    def test_no_removed_sentence_came_back(self):
        for rel, pattern, was in self.REMOVED:
            with self.subTest(file=rel, pattern=pattern):
                text = (REPO_ROOT / rel).read_text(encoding="utf-8")
                m = re.search(pattern, text, re.I)
                self.assertIsNone(
                    m,
                    f"\n{rel} matched /{pattern}/ again — {was}.\n"
                    f"Found: {m.group(0) if m else ''!r}\n"
                    f"#36: the skill uploads and stops. Plaud transcribes "
                    f"nothing until a person presses 產生 / Generate, then "
                    f"立即產生 / Generate now.",
                )

    def test_the_corrections_are_still_there(self):
        for rel, pattern, why in self.KEPT:
            with self.subTest(file=rel, pattern=pattern):
                text = (REPO_ROOT / rel).read_text(encoding="utf-8")
                self.assertRegex(
                    text, pattern,
                    f"\n{rel} no longer matches /{pattern}/ — {why} (#36).",
                )

    def test_the_files_this_pins_still_exist(self):
        """A pin against a moved file passes by reading nothing."""
        for rel in {r for r, _, _ in self.REMOVED} | {r for r, _, _ in self.KEPT}:
            with self.subTest(file=rel):
                p = REPO_ROOT / rel
                self.assertTrue(p.is_file(), f"{rel} is gone — repoint this pin")
                self.assertGreater(len(p.read_text(encoding="utf-8")), 500,
                                   f"{rel} is suspiciously short")


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
