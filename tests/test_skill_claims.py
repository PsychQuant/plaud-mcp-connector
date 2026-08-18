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

`TestTheExactRegressionOf36` is unchanged and remains the load-bearing guard
for #36 itself: it names five files and five sentence shapes, so the exact
edit that would undo the fix fails.

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
    # Repointed at `plaud-index` instead: that is where a user now meets the
    # gap (its report template is what they read when a recording has no
    # transcript), so that is where the requirement lives.

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
        ("skills/plaud-index/SKILL.md", r"立即產生 / Generate now",
         "the shipping surface must name the SECOND press; the first only opens "
         "the chooser, and a reader who stops after one is back in the silent "
         "wait (#36 finding, re-homed here by #47 after #48 archived the README "
         "paragraph that used to carry it)"),
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
