#!/usr/bin/env python3
"""The public page is held to rule 1 as well (#38).

#35 measured that "this is the only path that writes into a personal library"
had already gone false — a second Plaud plugin on the same machine uploads —
and fixed it in `skills/*/SKILL.md`. The same sentence went on living in
`site/index.html`, in every language the page carries, because the guard's
scope was `skills/*/SKILL.md` and nobody had decided anything about the rest.

That is the part worth naming: the scope was never a decision, only an
absence. This file is the decision. `site/index.html` is what the public
reads, so rule 1 applies to it.

## What is scanned, and what is not

    rendered body text   ✅  what a visitor sees
    I18N string values   ✅  the same copy, once per language
    <script> code        ❌  engineering notes, not product claims
    <style>              ❌

The script exclusion is not a convenience. The page's own comment reads "a
single self-contained page is not a style choice here, it is the only shape
that works" — a true statement about `default-src 'none'`, and exactly the
kind of sentence that would be manufactured into a false positive by scanning
code comments as though they were copy.

## README.md was measured and left out

#38 asked whether `README.md` should be scanned too. Measured 2026-08-10:
two hits, and both are true statements about this repo's own commands —
"**The only thing that turns the cutoff on**" (about `mark-full-sweep`) and
"nothing else makes it visible" (about `status`). Neither is a claim against
the outside world; both are checkable by reading the code they describe.

So scanning README today would be two false positives out of two hits, and a
guard that is all noise is a guard somebody deletes. Left out **as a
decision**, not as an oversight — which is the distinction #38 was about.
Revisit if README ever starts making claims about what other tools cannot do.

## Rule 2 is deliberately not applied here

Capability claims (#36) are checked against the steps in a SKILL.md body.
`site/index.html` has no steps, so the check has nothing to compare against
and would flag every accurate description of what the plugin does. #43 is
already weighing whether that rule earns its keep where it does run.
"""
from __future__ import annotations

import html
import json
import pathlib
import re
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_skill_claims import offending_claims  # noqa: E402

SITE = pathlib.Path(__file__).resolve().parent.parent / "site" / "index.html"

SCRIPT_OR_STYLE = re.compile(r"(?is)<(script|style)\b.*?</\1>")
TAG = re.compile(r"<[^>]+>")
I18N_OBJECT = re.compile(r"(?s)const I18N = (\{.*?\n  \});")

# Sentences that match rule 1's vocabulary and are not exclusivity claims.
# A closed list. Each entry says why, and a new one may be added only after
# reading the sentence — "it looks like the others" is not a reason.
ALLOWED = {
    # 唯一 here modifies a Plaud *account*, not this plugin's abilities, and
    # the statement is true: nobody can create somebody else's account.
    "唯一沒人能幫你裝的，",
}


def visible_text(page: str) -> str:
    return html.unescape(TAG.sub(" ", SCRIPT_OR_STYLE.sub(" ", page)))


def i18n_strings(page: str) -> dict[str, str]:
    """Every translated string, keyed `lang.key`.

    Parsed as JSON rather than regex-scraped: the values contain escaped
    quotes and newlines, and a scrape that mangles them would quietly shorten
    the text being checked — the failure would look like a clean scan.
    """
    m = I18N_OBJECT.search(page)
    if not m:
        return {}
    table = json.loads(m.group(1))
    return {f"{lang}.{key}": value
            for lang, strings in table.items()
            for key, value in strings.items()}


class TestTheScanReachesRealContent(unittest.TestCase):
    """Every check below passes trivially on an empty scan."""

    def test_the_page_is_there(self):
        self.assertTrue(SITE.is_file(), f"{SITE} is gone — repoint this pin")

    def test_visible_text_is_real(self):
        text = visible_text(SITE.read_text(encoding="utf-8"))
        self.assertGreater(len(text), 2000, "visible text came back near-empty")
        self.assertNotIn("const I18N", text, "script survived the strip")

    def test_every_language_was_parsed(self):
        strings = i18n_strings(SITE.read_text(encoding="utf-8"))
        langs = {k.split(".", 1)[0] for k in strings}
        self.assertGreaterEqual(
            len(langs), 3,
            f"only parsed {langs} — the I18N object moved and this file is "
            f"now checking almost nothing")
        self.assertGreater(len(strings), 30, "too few strings to be the real table")


class TestThePublicPageMakesNoUnqualifiedExclusivityClaim(unittest.TestCase):
    def test_visible_text(self):
        for claim in offending_claims(visible_text(SITE.read_text(encoding="utf-8"))):
            if claim.strip() in ALLOWED:
                continue
            with self.subTest(claim=claim.strip()[:60]):
                self.fail(
                    f"\nsite/index.html tells visitors:\n\n    {claim.strip()}\n\n"
                    f"An exclusivity claim has to name what it is exclusive "
                    f"against, in the same clause. #35 measured this exact "
                    f"sentence going false while nobody noticed (#38).")

    def test_every_translation(self):
        strings = i18n_strings(SITE.read_text(encoding="utf-8"))
        for where, value in strings.items():
            for claim in offending_claims(html.unescape(TAG.sub(" ", value))):
                if claim.strip() in ALLOWED:
                    continue
                with self.subTest(where=where):
                    self.fail(
                        f"\n{where} tells visitors:\n\n    {claim.strip()}\n\n"
                        f"A claim left in one language is still published. "
                        f"The i18n table multiplies every unfixed sentence by "
                        f"the number of languages (#38).")
