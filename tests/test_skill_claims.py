#!/usr/bin/env python3
"""A skill description must not say things that are not true.

Two rules live here, guarding two measured failures of the same kind. Both
are about `description:` blocks, because that is the only thing a model reads
when deciding whether a skill applies — a false sentence in there is a false
premise inside a routing decision.

1. **Exclusivity** (#35) — a claim to be the only way to do something must
   name what it is exclusive against. Written below.
2. **Capability** (#36, rewritten by #43) — a description may not mention a
   listed capability unless that exact sentence is allow-listed. See
   `CAPABILITY_PHRASES` further down.

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

The same sentence governed rule 2, and #43 acted on it.

## Rule 2 was rewritten, and what it gave up (#43)

Rule 2 used to be general: *a description claiming an action must have a step
performing it*. Making that mechanical required two things a regex cannot do
— parse Markdown well enough to find the steps, and tell a claim from a
mention. Two rounds of cross-model review found ten and then fourteen defects
in it, six of them silent, which is the direction where an unbacked claim
simply passes. The count went up, not down.

So the rule was replaced rather than patched a third time. It now asks only
whether a description **mentions** a listed capability, and a mention is
allowed only when that exact sentence is on `ALLOWED_SENTENCES` with a reason.
No body parsing, no negation heuristics, nothing to walk around.

**What that gives up**: the rule no longer notices that a claim is backed by a
real step. Every mention stops a human, including the honest ones. This repo
had exactly one, `plaud-upload`'s denial, allow-listed by its full text so that
editing it takes the exemption away. `plaud-upload` was archived on 2026-08-18,
so no shipping skill exercises the exemption today; the entry is kept against a
restore rather than deleted.

**What it buys**: the escape hatch is gone, so the phrase pattern can be as
wide as it needs to be. `start the transcription` — a bypass #39 had to keep
as a stated limit, because widening the old regex only moved it — is caught
now. So are `triggers transcription` and `啟動轉錄`.

Both halves of that trade are asserted, not described:
`test_the_old_one_word_bypass_is_closed` and
`test_a_step_in_the_body_no_longer_excuses_anything`.

`TestTheExactRegressionOf36` remains the load-bearing guard for #36 itself:
five pins across two files (`archive/skills/plaud-upload/SKILL.md` and
`README.md`), each naming a sentence shape, so the exact edit that would undo
the fix fails. It was described here as "unchanged … five files" until #47
noticed both halves had stopped being true: #48 repointed the pins at the
archived copy and retired one, leaving two distinct files, not five.

Rule 2 still stops at the `description:` block and does not scan body prose.
Body prose has legitimate non-claiming uses of the same words ("To start
transcription, press 產生" is correct documentation), and the description is
what gets read when deciding whether to fire a skill.

A related structural idea was measured and rejected: treating each body's
opening paragraph as a second description. Across these eight skills that
paragraph is sometimes a summary, sometimes a correction notice, sometimes a
scope note. Applying a claim rule to a location whose role varies is how
false positives get manufactured.
"""
from __future__ import annotations

import pathlib
import ast
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


# --- rule 2: capability (#36, rewritten for #43) ----------------------------
#
# This was a general rule: "a description claiming an action must have a step
# performing it". Making that mechanical meant parsing the body for steps
# (a Markdown parser, by regex) and telling a claim from a mention (semantics,
# by regex). Two rounds of cross-model review found 10 then 14 defects in it,
# six of them silent — the direction where an unbacked claim simply passes.
#
# The trend was not converging, so the rule was replaced rather than patched
# again. #43 has the full argument; the short version is the repo's own
# doctrine, in `common-spec-prose-enumeration.md`:
#
#     能列舉的就列舉，不要寫總括判準
#
# and this file's own sentence, one section up: a proxy that looks like a
# guard is worse than an acknowledged gap.
#
# What replaces it: a description may not MENTION a listed capability at all,
# unless that exact sentence is allow-listed with a reason. No body parsing,
# no negation heuristics, nothing to walk around. The phrase pattern can be
# deliberately wide because a false positive costs one reviewed line here,
# not a widened escape hatch.
#
# The trade is stated plainly: this no longer tries to notice that a claim is
# backed by a step. It notices the sentence and makes a human look. For eight
# skills whose descriptions change a few times a year, that is the cheaper
# side of the trade — and it is the side that cannot fail silently.

# Capabilities this repo has measured itself getting wrong. A CLOSED LIST:
# a second entry may be added only after that claim has been measured against
# the steps and found unbacked. "It also looks worth checking" is not a
# reason, and a registry that grows by resemblance stops being a record of
# measured facts.
CAPABILITY_PHRASES = (
    ("start-transcription",
     # Wide on purpose. The old pattern required adjacent words, so `start
     # the transcription` walked past it (#39 kept that as a known limit).
     # Here a false positive is answered with an allow-list line, so width
     # costs almost nothing and closes the bypass instead of moving it.
     # `\W*(?:\w+\W+){0,2}` is up to two intervening words: `start the
     # transcription`, `start a new transcription`. Bounded rather than
     # unbounded so a `start` at one end of a long sentence cannot reach a
     # `transcription` at the other.
     re.compile(r"\b(start|starts|starting|trigger|triggers|kick off|"
                r"begin|begins|request|requests)\b\W*(?:\w+\W+){0,2}"
                r"(transcription|transcript|transcribing)"
                r"|(啟動|觸發|開始)\W{0,6}(轉錄|轉譯)"
                r"|文字起こしを(開始|実行)",
                re.I),
     "#36 — plaud-upload said it started transcription from v0.1.0 and no "
     "version ever contained the step. Plaud shows 準備生成 with a 產生 "
     "button waiting; nothing starts on its own."),
)

# Sentences that mention a listed capability and are not claiming it. A CLOSED
# list keyed on the WHOLE SENTENCE, so rewording a denial into a claim stops
# matching and goes red. Each entry says why it is here; adding one means
# reading the sentence, which is the entire mechanism.
ALLOWED_SENTENCES = {
    # plaud-upload's #36 fix. It denies the capability in order to hand the
    # job back to the user, and naming the thing is how it does that.
    #
    # plaud-upload was archived 2026-08-18, so nothing live matches this today.
    # Kept, not deleted: a restore from archive/ must not trip the guard on the
    # very sentence that fixed #36. Delete it only if the skill is deleted.
    "It does not start transcription: no step here asks Plaud to, and the "
    "uploaded file waits at 準備生成 / Ready to generate until somebody "
    "presses 產生 / Generate and then 立即產生 / Generate now in the web app "
    "— two presses, the second one being the commit.",
}

_SOFT_WRAP = re.compile(r"\s*\n\s*")
_SENTENCE_END = re.compile(r"(?<=[.。！？!?])\s+|(?<=[。！？])")


def sentences(text: str) -> list[str]:
    """Whole sentences, after undoing YAML's soft wrapping.

    Sentences, not the finer `clauses()` used by rule 1: the allow-list is
    keyed on a whole sentence so that rewording any part of a disclaimer stops
    matching it. A clause-level key would let "It does not start transcription"
    keep its exemption while the rest of the sentence turned into a promise.
    """
    joined = _SOFT_WRAP.sub(" ", text).strip()
    return [s.strip() for s in _SENTENCE_END.split(joined) if s.strip()]


def capability_mentions(description: str) -> list[tuple[str, str]]:
    """(capability name, sentence) for each mention that is not allow-listed."""
    found = []
    for name, phrase, _history in CAPABILITY_PHRASES:
        for sentence in sentences(description):
            if phrase.search(sentence) and sentence not in ALLOWED_SENTENCES:
                found.append((name, sentence))
    return found


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

    # --- limits kept as facts, not fixed (#39 R2) --------------------------
    #
    # Cross-model review reached four more shapes. All four fail in the LOUD
    # direction — a real step goes unseen, or a genuine disclaimer is flagged,
    # and either way somebody reads a red test and rewrites one sentence.
    # None of them lets an unbacked claim through, which is the difference
    # that decided whether to fix or to record.
    #
    # Recorded as tests rather than prose because the two drift apart. If a
    # later change makes one of these pass, the test goes red and whoever did
    # it has to say so here.

    def test_a_qualifier_elsewhere_in_the_clause_does_not_bless_a_global_claim(self):
        """The precise hole the first draft had, kept as a permanent case."""
        self.assertTrue(offending_claims(
            "This is the only way any tool can upload to Plaud, "
            "and it opens the official Plaud sign-in page."),
            "an incidental 'official' must not qualify a claim about every tool")


class TestTheCapabilityMentionRule(unittest.TestCase):
    """Rule 2 after #43: mention it and a human looks, or allow-list it."""

    NO_STEPS = "no steps here — the rule no longer looks at the body"

    def test_a_plain_claim_is_flagged(self):
        self.assertTrue(
            capability_mentions("Upload a file and start transcription."),
            "the sentence the whole rule exists for went unnoticed")

    def test_the_old_one_word_bypass_is_closed(self):
        """The reason for the rewrite, in one assertion.

        `start the transcription` walked past the old pattern, and #39 kept
        that as a stated limit because widening the regex only moved the
        bypass — the fence machinery behind it was the thing that could not
        be made right. With the escape hatch gone the pattern can be as wide
        as it needs to be.
        """
        for text in ("It will start the transcription for you.",
                     "This triggers transcription once the upload lands.",
                     "上傳後會自動啟動轉錄。"):
            with self.subTest(text=text):
                self.assertTrue(capability_mentions(text),
                                "a phrasing walked past the pattern")

    def test_the_allow_listed_sentence_passes(self):
        only = next(iter(ALLOWED_SENTENCES))
        self.assertEqual([], capability_mentions(only),
                         "the allow-list entry does not match its own text")

    def test_rewording_an_allow_listed_sentence_stops_exempting_it(self):
        """Whole-sentence keys are the mechanism, not an implementation note.

        A clause-level or skill-level key would let the denial keep its
        exemption while the rest of the sentence turned into a promise. Here
        any edit to the sentence takes the exemption with it.
        """
        reworded = next(iter(ALLOWED_SENTENCES)).replace("does not", "will")
        self.assertTrue(capability_mentions(reworded),
                        "an edited sentence kept the old exemption")

    def test_an_edit_to_the_tail_also_loses_the_exemption(self):
        """The case a prefix-keyed allow-list would miss.

        Keying on an opening fragment looks equivalent and is not: a denial
        can keep its first twenty characters and turn into a promise by the
        end. Acid found the earlier test only exercised an edit near the
        start, so a substring key passed it.
        """
        allowed = next(iter(ALLOWED_SENTENCES))
        turned = allowed.replace("the second one being the commit.",
                                 "and this skill presses both for you.")
        self.assertNotEqual(allowed, turned, "the tail edit did not apply")
        self.assertTrue(capability_mentions(turned),
                        "a sentence that became a promise kept its exemption")

    def test_a_step_in_the_body_no_longer_excuses_anything(self):
        """The trade #43 made, asserted rather than described.

        The old rule accepted a claim when the body contained a step. That
        inference needed a Markdown parser and a claim/mention distinction,
        and both were wrong in ways cross-model review kept finding. Nothing
        about the body is consulted now — deliberately, and this is where it
        would be noticed if someone reintroduced the idea.
        """
        self.assertTrue(
            capability_mentions("Upload a file and start transcription."),
            "a body argument crept back in")


class TestSentenceSplitting(unittest.TestCase):
    """The allow-list is keyed on whole sentences, so the split is load-bearing."""

    def test_yaml_soft_wraps_are_joined(self):
        wrapped = "Upload a file and start\n  transcription for it."
        self.assertEqual(["Upload a file and start transcription for it."],
                         sentences(wrapped))

    def test_cjk_full_stops_split(self):
        self.assertEqual(["上傳檔案。", "不會啟動轉錄。"],
                         sentences("上傳檔案。不會啟動轉錄。"))

    def test_a_description_splits_into_more_than_one_sentence(self):
        """Guards the direction that fails silently.

        A splitter returning the whole block as one sentence would make every
        allow-list key unmatchable — every skill would light up, which is
        loud. The dangerous direction is the reverse only if a key were a
        substring; it is not. This keeps the splitter honest either way.
        """
        p = SKILLS_DIR / "plaud-grep" / "SKILL.md"
        self.assertGreater(len(sentences(_description(p))), 3,
                           "the description came back as one blob")


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

    # Repointed when `plaud-upload` was archived. The skill left the shipping
    # surface, so these pins now guard `archive/skills/plaud-upload/SKILL.md`:
    # the archived copy is the artifact anyone would restore from, and the five
    # sentences #36 removed must not ride back in with it.
    #
    # `REMOVED` keeps its README entry — the README must never claim upload
    # starts transcription, and that stays true whether or not it documents
    # upload at all.
    #
    # `KEPT`'s README entry was first RETIRED here, on the reasoning that it
    # "required the README to name the second press, which only made sense while
    # the README had an upload handoff paragraph to protect." **That reasoning
    # was wrong about what the pin protected** (#47). The requirement was never
    # "the README documents upload" — it was "a user learns there are TWO
    # presses, because the first only opens the chooser." That outlived the
    # upload skill. Retiring the guard let the last shipping mention of the
    # second press leave with #48, and nothing went red.
    #
    # Re-homed to `tests/test_index_reporting.py`'s MUST_MENTION, which is
    # scoped to `### 4. Report` and has a liveness test against vacuity.
    #
    # It was first added HERE as a file-wide KEPT pin, and that was wrong in the
    # way this very file warns about four lines below: the phrase also appears
    # in an explanatory paragraph outside the report section, so reverting the
    # user-facing template alone kept the suite green. Verified by mutation.
    # That is instance four of the shape #37 called a pattern — file-wide
    # assertions stop guarding the place they were written for.

    # (path, forbidden pattern, what it was)
    REMOVED = (
        ("archive/skills/plaud-upload/SKILL.md", r"and\s+starts?\s+transcription",
         "the description and body headline both claimed the upload starts it"),
        ("README.md", r"and\s+starts?\s+transcription",
         "the README's plaud-upload paragraph claimed the same"),
        ("archive/skills/plaud-upload/SKILL.md", r"transcript\s+reaches\s+your\s+machine",
         "promised the transcript would arrive on its own"),
        ("archive/skills/plaud-upload/SKILL.md", r"once\s+processing\s+finishes",
         "presupposed processing had started; it never does"),
    )

    # (path, required pattern, why)
    KEPT = (
        ("archive/skills/plaud-upload/SKILL.md", r"does not\s*\n?\s*start transcription",
         "the description must deny it outright — that denial is the fix"),
    )

    # File-wide "contains 立即產生" is not enough for the handoff: adding the
    # second press to the *description* (a later fix) satisfied it on its own,
    # and the acid run caught the whole handoff section becoming deletable
    # again. Scope the assertion to the section it is about.
    HANDOFF_HEADING = "### The skill stops here, and the recording has no transcript"

    def _handoff_section(self) -> str:
        text = (REPO_ROOT / "archive/skills/plaud-upload/SKILL.md").read_text(encoding="utf-8")
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

    def test_no_skill_description_mentions_a_capability_unbidden(self):
        """Rule 2 against the live descriptions (#36, rewritten by #43).

        The synthetic cases above prove the rule works; this is the one that
        would go red if a skill actually started claiming transcription
        again. It is not vacuous the way the old live check became: the old
        one asked whether a body contained a step, and once #36 was fixed no
        description mentioned the capability at all, so it passed no matter
        what the rule did. This asks about the mention itself, which
        is what plaud-upload's fixed description did — it names the capability
        in order to deny it, and is on the allow-list by its whole text. That
        skill was archived on 2026-08-18, so the live scan no longer reaches it
        and nothing in the current set matches. Read the green here as "the rule
        is not firing", not as "the rule still catches what it was built for".
        """
        for skill_md in _skills():
            for name, sentence in capability_mentions(_description(skill_md)):
                with self.subTest(skill=skill_md.parent.name):
                    self.fail(
                        f"\n{skill_md.parent.name} mentions '{name}' in its "
                        f"description:\n\n    {sentence}\n\n"
                        f"Either the skill does not do this and the sentence "
                        f"should go, or it is a deliberate mention (a denial, "
                        f"a trigger phrase) — in which case add the sentence "
                        f"verbatim to ALLOWED_SENTENCES with the reason. "
                        f"Reading it is the mechanism (#43).")


class TestTheSkillSurfacesEveryWarningTheToolCanEmit(unittest.TestCase):
    """A closed list of signals in the operator's instructions must stay closed.

    `skills/plaud-srt/SKILL.md` is the only caller of `scripts/to_srt.py` in
    this repo, and its step 4 tells the operator — a model — which stderr
    signals to pass on. It enumerated exactly two, with the justification
    "because neither is visible in the resulting `.srt`".

    #50 then added a third with the same justification and did not grow the
    list, so the fix's entire user-visible output terminated at a line the
    operator's own checklist said it did not need to mention. Meanwhile the
    plausible-looking cue count kept going to stdout with exit 0 — which is
    precisely the shape of the "6 succeeded / 0 failed" report #50 opens with.

    This class pins the correspondence rather than the wording: every warning
    the CLI can print must be findable in the skill that reads it.

    That sentence was here for six rounds with **no test doing it**. The six
    tests were one hardcoded warning, one walk in the REVERSE direction (every
    string SKILL.md quotes must exist in the tool), one retired-wording check
    and three enumeration-shape checks — none of them tool → skill. It was
    already stale inside the round that wrote it: the tool had grown
    `⚠ N character(s) were removed from the cue text` and the glossary did not
    mention it, exactly the history the paragraph above recounts, with the
    guard green. `test_every_warning_the_tool_emits_has_a_glossary_entry`
    below is the sentence, as a test.
    """

    SKILL = pathlib.Path(__file__).resolve().parent.parent / "skills" / "plaud-srt" / "SKILL.md"

    def _skill_text(self) -> str:
        return self.SKILL.read_text(encoding="utf-8")

    def test_the_partial_drop_warning_is_in_the_list(self):
        self.assertIn(
            "did not parse", self._skill_text(),
            "to_srt.py warns that timestamped lines were dropped, and the only "
            "skill that runs it never mentions the warning. The operator is a "
            "model following this checklist; a signal absent from it is a signal "
            "that does not reach the user (#50)")

    def test_every_quoted_string_is_one_the_tool_actually_prints(self):
        """Walk what SKILL.md QUOTES. The earlier version walked the reverse.

        Its name promised that every string SKILL.md quotes is one the tool
        prints. Its body iterated a hardcoded three-element tuple and asserted
        each appeared in both files — so SKILL.md could quote a string that does
        not exist, and did: `no timestamped segments` was retired in #40
        precisely because that phrasing sent people to debug the wrong thing,
        and it sat in the glossary for two rounds with the suite green.

        A hand-maintained list standing in for a property is the construct the
        two sibling tests here exist to forbid. It was removed from the prose in
        the same commit and reintroduced inside the guard written to protect the
        prose.

        Placeholders are the reason this needs care rather than a plain
        substring check: the glossary writes `N`, `M`, `K`, `H` where the tool
        writes numbers, so the comparison is on the FIXED fragments between them.
        """
        # The oracle is the tool's STRING LITERALS, not its source text.
        #
        # Searching the whole file accepts wording the tool never prints,
        # because this file's documented habit is to keep retired wording in
        # comments explaining why it was retired. Verified: SKILL.md quoting
        # `0 content line(s) were present` passed, because that phrase survives
        # at to_srt.py:761 inside the round-10 comment saying the old message
        # was wrong. A guard whose oracle includes the graveyard cannot tell the
        # living from the dead.
        #
        # `ast` gives the literals and nothing else — no comments, no
        # docstrings that are not also messages — and adjacent f-string
        # fragments arrive already joined, which the previous regex had to
        # reconstruct by hand.
        import ast
        tree = ast.parse((pathlib.Path(__file__).resolve().parent.parent
                          / "scripts" / "to_srt.py").read_text(encoding="utf-8"))
        pieces = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                pieces.append(node.value)
            elif isinstance(node, ast.JoinedStr):
                pieces.append("".join(
                    v.value for v in node.values
                    if isinstance(v, ast.Constant) and isinstance(v.value, str)))
        # Docstrings are Constants too, so drop the ones that are statements:
        # a message is never a bare expression at the top of a block.
        for node in ast.walk(tree):
            doc = ast.get_docstring(node) if isinstance(
                node, (ast.Module, ast.FunctionDef, ast.ClassDef)) else None
            if doc and doc in pieces:
                pieces.remove(doc)
        src = re.sub(r"\s+", " ", " ".join(pieces))
        text = self._skill_text()
        step4 = text[text.index("### 4. Report honestly"):
                     text.index("## How the timing works")]

        # `[^`\n]+` could not see a quote spanning a line break, which is the
        # third layer of this guard: round 12 narrowed the oracle and widened
        # the input selection, and the INPUT REGEX was still too small. The
        # sibling guard learned the same lesson in the same commit.
        for quoted in re.findall(r"`([^`]+)`", step4, re.S):
            quoted = " ".join(quoted.split())
            # Which quotes count as claims about output was itself a closed
            # list: `⚠` or `wrote `. `0 content line(s) were present` starts
            # with neither, so the retired wording round 11 found was skipped
            # entirely — the oracle was narrowed and the INPUT SELECTION was
            # still an enumeration, one line away.
            #
            # Anything sentence-shaped is checked now: long enough to be a
            # message, with a space in it. `key: value` and `plaud-index` fall
            # under that on length and shape, and erring toward checking more is
            # the right direction for a guard whose failure mode has twice been
            # "did not look".
            if len(quoted) < 20 or " " not in quoted:
                continue
            if quoted.startswith("-") or quoted.startswith("python3"):
                continue          # flag rows and command lines, not messages
            # Split on placeholders and elisions; each remaining run of >= 12
            # characters is a fixed fragment the tool must contain verbatim.
            for fragment in re.split(r"[…]|\b[NMKH]\b", quoted):
                # Template punctuation belongs to the glossary, not the tool.
                fragment = " ".join(fragment.strip(" ⚠()").split())
                if len(fragment) < 12:
                    continue
                with self.subTest(fragment=fragment[:40]):
                    self.assertIn(
                        fragment, src,
                        f"SKILL.md quotes {fragment!r} and scripts/to_srt.py "
                        f"never prints it. The operator is a model told to relay "
                        f"these strings; one that does not exist is one it will "
                        f"never match, and one that has DRIFTED gets relayed "
                        f"wrong")

    def test_the_glossary_does_not_quote_the_retired_wording(self):
        """#40 retired it for sending people to debug the wrong thing."""
        self.assertNotIn("no timestamped segments", self._skill_text(),
                         "SKILL.md still quotes the phrasing #40 removed from "
                         "the tool; nothing on stderr will ever match it")

    def test_the_exit_three_row_enumerates_nothing(self):
        """Round 7: the list is gone, because a list drifts.

        The row named three causes when the code had six, and three of those
        exited 3 in silence — so the operator applied the table and told the
        user one of the listed causes, which was not what happened. Growing the
        list is what round 6 did, and it was wrong again in the same commit.

        The tool now prints a reason on every refusal, so the row points at
        stderr instead of enumerating. This test fails if an enumeration comes
        back: a row that counts its causes has to be kept in step with the code,
        and nothing keeps it.
        """
        row = next(l for l in self._skill_text().splitlines()
                   if "`--preview-sources` exits 3" in l)
        for counting in ("two causes", "three causes", "four causes",
                         "Two causes", "Three causes", "Four causes"):
            self.assertNotIn(counting, row,
                             f"the exit-3 row enumerates again ({counting!r}). "
                             f"Every count it has ever stated went stale within "
                             f"one commit")
        self.assertIn("no source comparison to show", row,
                      "the row does not quote the sentence the tool actually "
                      "prints, so the operator has nothing to relay")

    def test_the_refusal_sentence_is_what_the_tool_prints(self):
        src = (pathlib.Path(__file__).resolve().parent.parent
               / "scripts" / "to_srt.py").read_text(encoding="utf-8")
        self.assertIn("no source comparison to show", src,
                      "this test's reference is stale — the tool no longer "
                      "prints the sentence SKILL.md quotes")

    def test_step_four_counts_nothing(self):
        """Round 7: stop counting. Every count has gone stale within one commit.

        "Two things to surface" outlived the second thing. "Three things" was
        wrong before the commit that wrote it had finished — the same commit
        added a fourth warning. A section that states how many signals exist has
        to be kept in step with the code, and nothing keeps it, so it is now an
        instruction to relay whatever is there plus a glossary of meanings.
        Adding a warning does not invalidate a glossary.
        """
        text = self._skill_text()
        for counting in ("Two things to surface", "Three things to surface",
                         "Four things to surface", "Five things to surface"):
            self.assertNotIn(counting, text,
                             f"step 4 enumerates again ({counting!r}). Every count "
                             f"this section has ever stated was wrong within a "
                             f"commit or two of being written")
        self.assertIn("Surface every", text,
                      "step 4 no longer tells the operator to relay everything on "
                      "stderr, so a warning the glossary does not mention reaches "
                      "nobody")


class TestStepFourQuotesToolOutputInBackticks(unittest.TestCase):
    """Backticks are the only quoting style the sibling guard can read.

    `test_every_quoted_string_is_one_the_tool_actually_prints` scans backtick
    fragments. A retired zero-cue message sat in step 4 inside ASCII double
    quotes for two rounds with that guard green — it was written for exactly
    that defect and could not see the punctuation the sentence happened to use.

    Widening it to more quote characters is the wrong repair: step 4 is prose
    and prose legitimately uses quotes, so every widening trades one blind spot
    for a crop of false positives. Requiring one style instead makes the
    sibling guard's coverage of this section total, which no amount of
    widening can do.
    """

    SKILL = SKILLS_DIR / "plaud-srt" / "SKILL.md"

    def test_step_four_uses_no_ascii_double_quotes(self):
        text = self.SKILL.read_text(encoding="utf-8")
        step4 = text[text.index("### 4. Report honestly"):
                     text.index("## How the timing works")]
        stray = re.findall(r'"([^"]+)"', step4, re.S)
        self.assertEqual(
            [], stray,
            f"step 4 quotes {len(stray)} fragment(s) with ASCII double quotes: "
            f"{stray}. Use backticks — anything quoted here is a claim about "
            f"what the tool prints, and the guard that checks those claims "
            f"reads backticks only. This is how "
            f"'most likely a recording without timestamps' survived in this "
            f"section after the tool stopped printing it.")


class TestEveryWarningReachesTheGlossary(unittest.TestCase):
    """Tool → skill. The direction the sibling class named and never walked.

    Its docstring said "every warning the CLI can print must be findable in
    the skill that reads it" while every test in it walked skill → tool. The
    difference is not academic: the reverse direction catches a glossary that
    quotes something retired, and only this direction catches a glossary that
    is missing something new. #50's own history is the second kind twice over
    — a warning added, the list not grown, and the fix's entire user-visible
    output terminating at a line the operator's checklist said to skip.
    """

    SKILL = SKILLS_DIR / "plaud-srt" / "SKILL.md"
    SCRIPT = REPO_ROOT / "scripts" / "to_srt.py"

    def _warnings(self) -> list[str]:
        """Every `⚠ …` the tool can print, from its syntax tree.

        f-strings arrive in pieces; the interpolations are dropped and the
        literal fragments joined, which is the same shape the sibling guard
        compares against and for the same reason — the numbers differ per run
        and the words do not.
        """
        tree = ast.parse(self.SCRIPT.read_text(encoding="utf-8"))
        # `ast.walk` reaches a JoinedStr AND the Constants inside it, so the
        # literal chunk before an interpolation — `⚠ and ` — arrived as a
        # warning of its own and was too short to check. The named-exclusion
        # assertion below is what surfaced it; a silent length filter would
        # have swallowed a defect in the extractor as "nothing to see".
        inside = {id(v) for node in ast.walk(tree)
                  if isinstance(node, ast.JoinedStr) for v in node.values}
        out = []
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                parts = [v.value for v in node.values
                         if isinstance(v, ast.Constant) and isinstance(v.value, str)]
            elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
                  and id(node) not in inside):
                parts = [node.value]
            else:
                continue
            joined = " ".join("".join(parts).split())
            if joined.startswith("⚠"):
                out.append(joined)
        return sorted(set(out))

    #: A glossary fragment writes `N`, `M`, `K`, `H` and `…` where the tool
    #: writes numbers and filenames, so the comparison is on the FIXED pieces
    #: between them — the same treatment the sibling guard gives the reverse
    #: direction, for the same reason.
    PLACEHOLDER = re.compile(r"[NMKH]\b|…|\.\.\.|\d+|'[^']*'|<[^>]+>")

    def _fixed_pieces(self, fragment: str) -> list[str]:
        return [" ".join(p.split())
                for p in self.PLACEHOLDER.split(" ".join(fragment.split()))]

    def test_every_warning_the_tool_emits_has_a_glossary_entry(self):
        # The WHOLE skill, not step 4. `⚠ no source comparison to show:` is
        # documented in step 2's table, where it belongs — the property is
        # that a warning is findable by the operator, not that every warning
        # lives in one section.
        #
        # Fenced blocks come out FIRST. With ```` ``` ```` still in the text,
        # `[^`]+` pairs a fence with the next stray backtick and swallows the
        # single-backtick fragments between them into one blob — widening the
        # search from one section to the file made every warning MISS, which
        # is a guard reporting the opposite of the truth because of its own
        # lexer. Caught only because three entries that had matched stopped
        # matching; a check that had been failing all along would have looked
        # like it was simply still failing.
        whole = re.sub(r"```.*?```", " ", self.SKILL.read_text(encoding="utf-8"),
                       flags=re.S)
        pieces = [p for q in re.findall(r"`([^`]+)`", whole, re.S)
                  for p in self._fixed_pieces(q) if len(p) >= 12]
        missing, too_short = [], []
        for warning in self._warnings():
            # A wrapper like `print(f"⚠ {note}")` carries no words of its own;
            # its sentences are built elsewhere and are checked there. Named
            # rather than dropped, because a filter that quietly removes what
            # it cannot judge is how these lists went stale before.
            if len(warning) < 8:
                too_short.append(warning)
                continue
            if not any(p in warning for p in pieces):
                missing.append(warning)
        self.assertEqual(
            [], missing,
            f"{len(missing)} warning(s) the tool can print have no entry in "
            f"SKILL.md step 4: {missing}\n\nThe operator is a model reading "
            f"that section. A signal absent from it is a signal that does not "
            f"reach the user, which is the shape of #50 itself — a plausible "
            f"cue count on stdout, exit 0, and the loss on a line nobody was "
            f"told to look at.")
        self.assertEqual(
            ["⚠"], too_short,
            f"the set of warnings this check cannot judge changed: {too_short}. "
            f"Each one carries no words of its own, so it has to be checked "
            f"wherever its sentences are actually built — decide that for the "
            f"new one rather than letting it sit here unexamined.")

    def _step4(self) -> str:
        text = self.SKILL.read_text(encoding="utf-8")
        return text[text.index("### 4. Report honestly"):
                    text.index("## How the timing works")]

    def test_the_guard_can_actually_fail(self):
        """A guard whose oracle is empty passes everything."""
        self.assertGreaterEqual(
            len(self._warnings()), 5,
            "the extractor found almost no warnings, so the check above is "
            "vacuous — it would pass an empty glossary")


class TestEveryWarningInTheSkillIsQuotedWhereTheGuardsCanSeeIt(unittest.TestCase):
    """`⚠` in prose is a claim about tool output. It has to be in backticks.

    The skill → tool guard reads backtick fragments; the tool → skill guard
    matches against them. A `⚠` sentence written as plain prose, or in ASCII
    or curly quotes, is invisible to both — so a retired warning can sit in
    the file, and a new one can be missing from it, with everything green.

    Round 16 closed one character of this (ASCII double quotes in step 4) and
    round 17 pointed out that single quotes, curly quotes and unquoted prose
    reopen the same hole. Enumerating quote characters is the losing move.
    Requiring the one form the guards can read is not.
    """

    SKILL = SKILLS_DIR / "plaud-srt" / "SKILL.md"

    def test_no_warning_text_sits_outside_backticks(self):
        text = re.sub(r"```.*?```", " ", self.SKILL.read_text(encoding="utf-8"),
                      flags=re.S)
        # Blank out every backticked span, then look for survivors.
        outside = re.sub(r"`[^`]*`", " ", text)
        stray = [line.strip() for line in outside.splitlines() if "⚠" in line]
        self.assertEqual(
            [], stray,
            f"{len(stray)} line(s) mention ⚠ outside backticks: {stray}\n\n"
            f"Both guards over this file read backtick fragments. Text that "
            f"claims to be tool output and is not in backticks is checked by "
            f"neither, which is how a retired message survived here for two "
            f"rounds.")
