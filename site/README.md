# site/

The public page for plaud-mcp-connector. One static HTML file, no build step.

## Why not Next.js

The plan for this called for Next.js with App Router, then in the same breath said
"static, no backend, no database, no forms — this is a docs page, not an app."
Those two things pull in opposite directions, and for one page the second one wins:
Next.js would add a build step, a `node_modules` tree, and a dependency surface to
a plugin repo, in exchange for nothing this page uses. Vercel serves static files
natively.

If the page ever grows something that genuinely needs a framework — dynamic
content, auth, a real form — revisit it then, not now.

## Deploy

Go through the Makefile at the repo root, not `vercel` directly:

```bash
make site-check                      # just the rules below, no deploy
make site-preview                    # preview URL
make site-prod CONFIRM=1             # production
make site-check DOMAIN=example.com   # also check a custom domain
```

Both deploy targets run the test suite and `scripts/site_check.py` first, and
`vercel` is the last line of the recipe — so a page that breaks the rules below
cannot reach the internet by accident. Production additionally requires
`CONFIRM=1`, because publishing is outward-facing and awkward to take back.

Both name `--target` outright. **An unspecified target is not a preview**: for a
project with no connected Git repository Vercel resolves it to production and
aliases the public hostname. The first run of `site-preview` published the site
while reporting itself as a preview, and the test guarding it passed throughout —
it asserted that `--prod` was absent, which says nothing about where a deploy
lands. Absence of a flag is not a behaviour.

`vercel deploy` by hand still works and skips every one of those checks. That is
the hole the Makefile exists to close; use the targets.

Nothing to install and nothing to build; `vercel.json` only sets response headers.

## Positioning rules — do not quietly drop these

This page describes a third-party tool that works with someone else's product.
Four constraints follow from that, and they are requirements, not style notes.
Everything mechanical about them is enforced by `scripts/site_check.py`, so the
list below is documentation of a gate rather than a promise someone has to
remember:

1. **No Plaud logo, brand colours, or typeface.** Plaud's own material uses the
   Jokker typeface and `#8F53ED` purple; this page deliberately uses neither. A
   page about someone else's product that borrows their look reads as theirs.
2. **The independence notice stays in the header and the footer.** Not one or the
   other.
3. **The domain must not resemble `plaud.ai`.** `plaud-mcp.com` and friends would
   read as official.

   **Reviewed and settled (2026-08-06):** `plaud-mcp-connector.vercel.app` is
   accepted. It contains "plaud", so `site_check.py` warns on the shape by
   default — a string comparison cannot judge whether a name reads as official.
   A human judged it, and two independent review lenses agreed: a `.vercel.app`
   subdomain carrying "connector", on a page that says "not affiliated" in both
   the header and the footer, is not mistakable for Plaud's own. The decision is
   recorded as `ACCEPT_DOMAIN` in the Makefile rather than left as prose, so the
   gate stops re-raising it. **The record downgrades a warning on that exact
   host only, and can never clear a blocked name** — accepting `plaud.ai` is not
   something the mechanism can express. Any other new domain gets asked about
   again, because it is a different question.
4. **Every mention of the official integration links to `docs.plaud.ai`.** Readers
   looking for Plaud's own thing should be sent there, not kept here.

Wording is kept identical to the README and `plugin.json`, so a reader meets the
same sentence wherever they arrive.

## What the gate can and cannot decide

`make site-check` fails the deploy on: Plaud's brand purple or typeface appearing
in effective CSS (comments naming them are exempt — this page documents the rule
it follows), the independence notice missing from the header or the footer, no
link to `docs.plaud.ai`, install commands that differ from `README.md`, a
subresource the CSP would silently block, and a `vercel.json` that has lost its
CSP. It blocks domains whose shape is unambiguously Plaud's own.

It **warns rather than blocks** on a domain that merely contains "plaud". Whether
such a name reads as official is a judgement, and a string comparison has no
business making it — so it asks instead of pretending.

## Adding a language

The page ships in English, Traditional Chinese, and Japanese — 22 of the 37
markets Plaud declares an `hreflang` for. Adding one of the remaining ten
(`es` `fr` `de` `it` `nl` `pt` `vi` `th` `ms` `ar`) is a **data change**, not a
code change:

1. Add the language to the `I18N` object in `index.html` with **every** key the
   English table has. `scripts/site_check.py` refuses a partial set — a page
   half-translated into a language is worse than one that does not offer it.
2. Add an `<option value="xx" lang="xx">Name</option>` to the switcher. The
   `lang` attribute is not optional: a language name written in its own script
   needs it, or a screen reader pronounces it with English rules.
3. Add the language's own way of saying "not affiliated with Plaud" to
   `NOT_AFFILIATED` in `scripts/site_check.py`. The gate checks each language
   against its own phrasing — matching the English phrase everywhere would be a
   check only English can pass.
4. Extend the `navigator.languages` branch in the page's script.

**Do not machine-translate to fill the table.** The page's only real asset is
that its claims are exact; ten translations nobody can vouch for would spend
that to gain a language count. `ar` additionally needs `dir="rtl"` and layout
work — it is not a data-only addition.

## Editing

Claims on this page must match what actually ships. The install commands were run
end to end against a clean install of v0.2.0, and `site_check.py` now fails the
deploy if the page and `README.md` ever disagree about them — that divergence is
issue #7 verbatim, and it shipped once already.
