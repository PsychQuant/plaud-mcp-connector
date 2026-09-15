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

---

## `plaud-transcriber/` — parked 2026-09-15

**This entry is different in kind from the one above.** `plaud-upload` was archived
to *stay* archived. `plaud-transcriber/` is archived **pending re-entry**: the
maintainer decided (#60, 2026-09-14) to bring Safari-driven write paths — upload,
rename / move / delete, and triggering transcription — back into this repo *later*
(#61). Until then the whole sibling plugin sits here intact so the restore is a
`git mv`, not a reconstruction. The top-of-file sentence "Restoring is a deliberate
act, not an accident" still holds; for this entry the act is already scheduled.
The file's first line — "code that used to ship and no longer does" — does not:
none of this ever shipped from *this* repo. That is the second way this entry
differs, and the top-of-file layout rule (mirror the repo root so relative paths
keep resolving) bends with it: this tree mirrors the *sibling's* root instead, and
its relative paths resolve inside that subtree.

**What it is.** The maintainer's sibling plugin `plaud-transcriber` (from the
private `che-local-plugins` marketplace), snapshotted **whole**, mirroring its own
root: `.claude-plugin/`, `.codex-plugin/`, `CLAUDE.md`, `README.md`, `skills/`. Five
Safari/AppleScript-driven skills — `plaud-upload`, `plaud-download`, `plaud-search`,
`plaud-status`, `plaud-manage` — plus their scripts (six `batch_*.sh`,
`dom_transcript_to_srt.py`, `json_to_srt.py`, `collect_files.js`,
`inject_upload.sh`). 19 files. Excluded from the snapshot: `.DS_Store` and a
`.impeccable/hook.cache.json` tool cache (same class as #64).

**Source, exactly.** che-claude-config `che-local-plugins/plugins/plaud-transcriber/`
at repo HEAD `fbcead9`; the plugin's last own commit is `7e8076a` (2026-07-20);
`.claude-plugin/plugin.json` says **1.12.1** and `.codex-plugin/plugin.json` says
**1.10.0** — that drift is the source's, kept as-is; working tree clean under the plugin at
snapshot
time, so snapshot == commit. It is kept in a **subdirectory of its own**, not merged
into `archive/skills/`, because `archive/skills/plaud-upload/` above is a *descendant*
of this plugin's `plaud-upload` (#4 ported it, #13 and #36 then changed it); merging
would clobber that lineage, and #61's restore wants the plugin as one unit.

**Why.** #4 (2026-08-06) opened the door — port the sibling's use cases one by one:
upload was ported, SRT became `plaud-srt`, search yielded to `plaud-grep`. Then #48
archived the ported upload and #47 ruled this repo does not trigger transcription. But
#4 never closed the door: the sibling kept shipping all five skills on the same
machine, so the write paths #47/#48 declined were still available one skill-load
away, and the two plugins overlapped and each carried its own web.plaud.ai
compatibility patches (the sibling's last: two white-screen root causes, 2026-07-20).
On 2026-09-14 the maintainer loaded the wrong one. #60 closes the door: the sibling
leaves its marketplace (che-claude-config#16) and lands here.

**What this repo lost.** Nothing — none of this ever shipped from here.

**What replaces it.**

| sibling skill | here | status |
|---|---|---|
| `plaud-download` (SRT / DOCX / notes via Safari) | official `get_transcript`, `get_note`, `get_file` (audio via `plaud-audio`); SRT from the local cache via `plaud-srt` | covered — except the ASR-hallucination flagging in `json_to_srt.py`, which `plaud-srt` does not have yet (#62) |
| `plaud-search` (name / date / folder) | official `list_files` (name substring + date) and full-text `plaud-grep` | covered; folder / tag filtering is an open question (#63) |
| `plaud-status` (list, transcription state) | `list_files` + `get_file` (`source_list` empty ⇒ never transcribed; #51) | covered |
| `plaud-upload` | upload: `safari-browser:safari-plaud-upload` in the maintainer's marketplace (no Generate step — see the entry above); triggering transcription: **nothing** (#47/#48) | parked → #61 |
| `plaud-manage` (rename / move / delete / Generate) | **nothing** | parked → #61 |

**What it did have — and what #36 actually said.** The copy above lost its
transcription claim in #36. *This* copy still carries the claim — the Chinese
sentence 「上傳音訊/影片到 Plaud 並啟動轉錄。」 — **and** the code behind it: a
Step 4 in `plaud-upload/SKILL.md` that clicks 「產生」 then 「立即產生」 (`Generate now`) and checks for
`Generating`. #36's evidence used this sibling as the control that *had* a trigger
step; the "claimed and did not" verdict was about the port above, not this
snapshot. Whether the sibling's step ever worked end-to-end is unverified here;
the snapshot shows it was implemented, not that it succeeded. The claim is inert
only because `tests/test_skill_claims.py` scans `skills/*/SKILL.md` and never
looks under `archive/`; its capability regex *is* bilingual (it matches 啟動／觸發
＋轉錄) and would fire on this sentence and on `plaud-manage`'s description the
moment either file is `git mv`'d back — see Restoring step 2b below. #61's scope should
read this paragraph before listing "trigger transcription" as a capability to build
from scratch.

**Scrub delta — the only difference from `7e8076a`.** This repo is public. The
first archive commit (`03c26e8`) replaced the maintainer's account e-mail and plan
name in nine files (the gate also looked for absolute home paths; the source had
none) and gated on `grep -rniE '@gmail|@icloud|/Users/'`. The verify pass on #60 showed that gate
was blind to three shapes that were sitting in the tree it approved, and a
second commit (`a5b393d`) widened the scrub to twelve archived files and a third
removed the last verbatim third-party sentence, dates and a speaker alias. Every replacement is a *literal
value*; no paragraph was removed:

- `- Email：` / `- 方案：` lines (5 SKILL.md files, `CLAUDE.md`) → placeholders
- the Keychain lookup's `-a "<e-mail>"` argument → `-a "<plaud-account-email>"`
- `PLAUD_EMAIL="<e-mail>"` in the three batch scripts → `${PLAUD_EMAIL:?…}`, **and**
  the login step's key-press sequence (`<c>+<c>+…+@+…`, the same address spelled
  one key at a time — invisible to a substring grep) → derived from `$PLAUD_EMAIL`
- first names of tutoring students and a research collaborator, used as worked
  examples in `plaud-download/SKILL.md`, `json_to_srt.py`, `plaud-search/SKILL.md`,
  `plaud-manage/SKILL.md` → `StudentA…D`, `CollaboratorX`
- four real Plaud recording ids (32-hex) and two recording titles in
  `plaud-status/SKILL.md`, `plaud-manage/SKILL.md`, one folder name in `README.md`
  → placeholder ids and generic titles
- the quota figure in `CLAUDE.md` and the plan name in `plaud-manage/SKILL.md`,
  which together reconstructed the removed `- 方案：` line → generic wording

Everything else — the web.plaud.ai white-screen root causes, the CookieYes dialog
handling, the i18n label table, the token-relocation history, the vague-query
disambiguation protocol — is preserved verbatim. It is the ops knowledge #61 will
need. The `03c26e8` tree is still in history; whether to rewrite it is a
maintainer decision recorded on #60.

**Related.** #4 (the port plan that never closed), #47 / #48 (the rulings this
parking eventually reverses — the reversal and its reasons are #61's to write, not
this entry's), #60 (this archiving), #61 (restore write paths), #62 (port the
hallucination flagging into `plaud-srt`), #63 (folder / tag filtering),
che-claude-config#16 (the marketplace-side retirement).

**Restoring (this is #61's plan, not a contingency).**

1. `/archive-first:archived-unlock` — see the note at the top of this file.
2. `git mv archive/plaud-transcriber/skills/<skill> skills/<skill>` for the skills
   #61 chooses to revive; leave the rest here.
   2b. Expect `make check` to go red at once: `tests/test_skill_claims.py` scans
   `skills/*/SKILL.md` with a bilingual capability regex and the revived
   descriptions say 啟動轉錄／觸發轉錄. Either implement and prove the capability
   (then extend `ALLOWED_SENTENCES`) or rewrite the description — do not weaken
   the guard.
3. **`/archive-first:archived-lock` — immediately.**
4. Put the account values back the way this repo does it (Keychain, generic).
5. Write the reversal of #47/#48 into this file and into #61 *before* the first
   commit — otherwise #48's rationale is silently voided.
6. Then the advertising surfaces — enumerate them from the tree at restore time
   (`grep -rl plaud-upload README.md site/ .claude-plugin/ skills/`), not from the
   list under `plaud-upload` above, which that entry itself shows was incomplete —
   plus a version bump (#52): restoring a capability is a surface change; parking
   was not.
