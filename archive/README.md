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

**This directory is write-protected on maintainer machines — but the protection is
narrower than it looks.** The `archive-first` PreToolUse hook denies the `Write` and
`Edit` tools on any path matching `/archived?/`. Its Bash rule is much weaker: it
splits the command on `;`, `&&` and `||` and denies a segment only when that same
segment contains both a bare `rm`/`rmdir`/`unlink` **and** an `archive/` path. So
everything below goes through untouched:

- any non-`rm` shell write — `sed -i`, a `>` redirect, `python3 -c "open(…, 'w')"`
- `git mv`, `install`, `cp` over an existing file
- even a deletion, if the path is not in the same segment: `cd archive && rm x`

Treat the hook as a reminder, not a wall. Editing anything here means
`/archive-first:archived-unlock` first and `/archive-first:archived-lock` after,
because the policy is about intent, and reaching for the shell to get past a guard
is the thing the policy exists to stop.

**`archived-unlock` is a machine-wide switch with no expiry.** It creates
`~/.cache/archive-first/disabled`, which disables the hook for **every repo and
every session** until something deletes it. Nothing times it out and nothing warns
you it is still set. Re-lock as soon as the edit lands, not at the end of a session.

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
Use the web app directly.

Not "nothing replaces it", which is what this said until #47's verify checked:
`safari-browser:safari-plaud-upload`, in the maintainer's own marketplace, still
uploads to Plaud by driving Safari. It does **not** close the gap this archiving
opened, though — it contains no Generate action at all, and its description claims
`trigger Plaud transcription`, the exact sentence #36 spent four commits removing
from the skill archived here. Tracked separately in `psychquant-claude-plugins`.

**What it never had.** It did not start transcription, despite four commits of its
description claiming it did — see #36, which fixed the claim rather than adding the
step. A recording it uploaded sat untranscribed until a person opened it and pressed
產生 / Generate, then 立即產生 / Generate now. That gap is unchanged by archiving.

**Guards that follow it here.** `tests/test_skill_claims.py` holds **five pins across
two files** — counted from the file, not from memory, because the previous two
versions of this paragraph both got it wrong:

- `REMOVED`, 4 pins, all four of the sentences #36 deleted. Three were repointed to
  `archive/skills/plaud-upload/SKILL.md`; the fourth still targets `README.md`,
  deliberately — the README must never claim upload starts transcription, whether or
  not it documents upload at all.
- `KEPT`, 1 pin, on the archived copy: the `does not start transcription` denial.
  That sentence is the #36 fix, so a restore must not trip a guard on it.

A sixth pin used to exist: a `KEPT` entry requiring `README.md` to name the second
press. #48 retired it, reasoning that it only mattered while the README had an upload
handoff to protect. **That was wrong, and #47 caught it.** The requirement was never
"the README documents upload"; it was "a reader learns there are two presses", which
outlived the skill. Retiring it let the last shipping mention leave with the archive
and nothing went red. Note it was a `KEPT` pin — a sentence #36 *preserved* — not one
of the four #36 removed; earlier drafts of this paragraph miscounted it as a fifth
removal and thereby lost track of the `KEPT` denial above.
It now lives in `tests/test_index_reporting.py`, scoped to `### 4. Report` and to the
fenced template inside it, with the boundary itself under test.

**Related.** #36 (the claim that was never true), #48 (this archiving), #47 (whether
this repo should be able to trigger transcription at all — answered "no, document the
two presses instead"; its earlier write-path-parity framing was superseded on
2026-08-18 by this very archiving).

**Restoring.**

1. `/archive-first:archived-unlock` — see the note at the top of this file.
2. `git mv archive/skills/plaud-upload skills/plaud-upload` and move the two tests back.
3. **`/archive-first:archived-lock` — immediately, here, not at the end.** Everything
   after this step is outside `archive/`. The unlock flag is machine-wide and never
   expires, so leaving it set through a long restore leaves every other repo on this
   machine unprotected for as long as the work takes.
4. Repoint the pins in `tests/test_skill_claims.py`.
5. Put the capability back where the pitch is stated. `make check` can see only the
   first three:

   | | Where | Seen by `make check`? |
   |---|---|---|
   | a | `README.md` | partly — `test_skill_names.py` catches a backticked skill name, not a prose claim |
   | b | `.claude-plugin/plugin.json` | **no** |
   | c | `.claude-plugin/marketplace.json` — **both** descriptions | **no** |
   | d | `site/index.html` — inline **and** all three i18n objects | yes, `scripts/site_check.py` blocks on key parity |
   | e | GitHub repo description — `gh repo edit --description` | **no** |
   | f | **The deployed site** — `make site-prod CONFIRM=1` | **no** |

**The list is the wrong artifact to trust; the criterion is.** Ask *"where does this
software say what it does, that `make check` cannot read?"* and sweep all of those.
The enumeration above was four entries when #48 shipped, and it was wrong twice in
one week: #47's verify round 1 found (e) still advertising `audio upload` on the
public repo page, and round 2 found (f) — a deployment that had not moved since
before 2026-08-10, still serving both the upload pitch and an exclusivity sentence
#38 had removed eleven days earlier. Vercel has no connected Git repo, so nothing
deploys on merge; production only moves when a person runs the command.
