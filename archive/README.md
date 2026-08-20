# archive/

Code that used to ship and no longer does.

**Archived means: kept, but off the plugin surface.** The files stay readable and
their tests stay runnable; nothing here is registered as a skill, discovered by
`python3 -m unittest discover -s tests`, or described in `README.md`, the plugin
manifest, or the site. Restoring something is a deliberate act, not an accident of
it still being on disk.

The directory layout mirrors the repo root (`archive/skills/…`, `archive/tests/…`)
so relative paths inside archived files keep resolving — an archived test can still
find the script it tests without being rewritten. That holds for paths a program
resolves; prose inside archived files still says `skills/plaud-upload/…` in places,
which reads correctly only from `archive/`.

**This directory is write-protected on maintainer machines.** The `archive-first`
plugin's PreToolUse hook denies `Write` and `Edit` on any path matching `/archived?/`,
so editing anything here — including the restore steps below — needs
`/archive-first:archived-unlock` first, and `/archive-first:archived-lock` after.
The hook's Bash rule only covers `rm`/`rmdir`/`unlink`, so a `git mv` is not blocked;
unlock anyway, because the policy is about intent, not about which tool slips past.

---

## `skills/plaud-upload` — archived 2026-08-18

Drove Safari via AppleScript to import a local audio file into a Plaud library.
macOS-only. Archived with its two tests, now at
`archive/tests/upload_verify_logic.test.mjs` and its Python wrapper.

**Why.** The official surface is read-only in both forms — the MCP exposes 7 tools
(`login`, `logout`, `get_current_user`, `list_files`, `get_file`, `get_transcript`,
`get_note`) and the CLI adds only `audio`, `search`, `recent`, `today` on top of the
same reads. Neither can upload. Driving the web app was the only way to close that
gap, and browser automation against someone else's import dialog is a standing
liability: it breaks on their redesign, not on our commit.

**What this repo lost.** The ability to put a file into a Plaud library from here.
Nothing replaces it; use the web app directly.

**What it never had.** It did not start transcription, despite four commits of its
description claiming it did — see #36, which fixed the claim rather than adding the
step. A recording it uploaded sat untranscribed until a person opened it and pressed
產生 / Generate, then 立即產生 / Generate now. That gap is unchanged by archiving.

**Guards that follow it here.** `tests/test_skill_claims.py` pins four of the five
sentences #36 removed, repointed at the archived copy — if this is ever restored,
the false sentences must not ride back in with it. The fifth, a README pin requiring
the second press to be named, was retired here rather than repointed. **That was a
mistake, corrected in #47**: the requirement was never "the README documents upload",
it was "a reader learns there are two presses", and that outlived the skill. Retiring
the pin let the last shipping mention leave with the archive and nothing went red.
It now lives in `tests/test_index_reporting.py`, scoped to `### 4. Report` and to the
fenced template inside it, with the boundary itself under test.

**Related.** #36 (the claim that was never true), #48 (this archiving), #47 (whether
this repo should be able to trigger transcription at all — answered "no, document the
two presses instead"; its earlier write-path-parity framing was superseded on
2026-08-18 by this very archiving).

**Restoring.** Run `/archive-first:archived-unlock` first — see the note at the top.
Then `git mv archive/skills/plaud-upload skills/plaud-upload`, move the two tests
back, repoint the pins in `tests/test_skill_claims.py`, and put the capability back
into **five** places:

1. `README.md`
2. `.claude-plugin/plugin.json`
3. `.claude-plugin/marketplace.json` — **both** descriptions (top-level and the
   plugin entry)
4. `site/index.html` — inline **and** all three i18n objects (`en` / `ja` / `zh-Hant`),
   or `scripts/site_check.py` will block the deploy on key parity
5. **The GitHub repo description** (`gh repo edit --description`) — it is not in the
   repo, so nothing in `tests/` or `make check` can see it. #48 swept the four above
   and left this one advertising `audio upload` on the public repo page for three
   days; #47's verify found it. Anything that changes the pitch has to change this
   too, by hand.

Finish with `/archive-first:archived-lock`.
