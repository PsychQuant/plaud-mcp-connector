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

```bash
cd site
vercel deploy          # preview URL
vercel deploy --prod   # production
```

Nothing to install and nothing to build; `vercel.json` only sets response headers.

## Positioning rules — do not quietly drop these

This page describes a third-party tool that works with someone else's product.
Four constraints follow from that, and they are requirements, not style notes:

1. **No Plaud logo, brand colours, or typeface.** Plaud's own material uses the
   Jokker typeface and `#8F53ED` purple; this page deliberately uses neither. A
   page about someone else's product that borrows their look reads as theirs.
2. **The independence notice stays in the header and the footer.** Not one or the
   other.
3. **The domain must not resemble `plaud.ai`.** `plaud-mcp.com` and friends would
   read as official.
4. **Every mention of the official integration links to `docs.plaud.ai`.** Readers
   looking for Plaud's own thing should be sent there, not kept here.

Wording is kept identical to the README and `plugin.json`, so a reader meets the
same sentence wherever they arrive.

## Editing

Claims on this page must match what actually ships. The install commands were run
end to end against a clean install of v0.2.0. If the commands change, run them
again before changing the page — every previous "surely this works" in this repo
turned out to be wrong.
