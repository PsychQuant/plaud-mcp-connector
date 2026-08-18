# archive/

Code that used to ship and no longer does.

**Archived means: kept, but off the plugin surface.** The files stay readable and
their tests stay runnable; nothing here is registered as a skill, discovered by
`python3 -m unittest discover -s tests`, or described in `README.md`, the plugin
manifest, or the site. Restoring something is a deliberate act, not an accident of
it still being on disk.

The directory layout mirrors the repo root (`archive/skills/…`, `archive/tests/…`)
so relative paths inside archived files keep resolving — an archived test can still
find the script it tests without being rewritten.

---

## `skills/plaud-upload` — archived 2026-08-18

Drove Safari via AppleScript to import a local audio file into a Plaud library.
macOS-only. Archived with its two tests (`tests/upload_verify_logic.test.mjs` and
its Python wrapper).

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

**Guards that follow it here.** `tests/test_skill_claims.py` still pins the five
sentences #36 removed, repointed at the archived copy — if this is ever restored,
the false sentences must not ride back in with it. One README pin was retired
instead of repointed; the reasoning is recorded at the pin.

**Related.** #36 (the claim that was never true), #47 (write-path parity — what
would have to exist before this repo could absorb the rest of `plaud-transcriber`).

**Restoring.** `git mv archive/skills/plaud-upload skills/plaud-upload`, move the two
tests back, repoint the pins in `tests/test_skill_claims.py`, and put the capability
back into `README.md`, `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`,
and `site/index.html` — the last one in all three languages.
