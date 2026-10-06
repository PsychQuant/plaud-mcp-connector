# Naming skills

A skill name should be something a person could finish the sentence "I want to ___"
with, and the blank should be a verb plus the thing it acts on. Not a file format
(plaud-srt was one), not an internal mechanism (plaud-index was one), not a
tool name (plaud-grep was one). The reason is practical: the name is what shows
up when someone types `/`, and nobody types the name of a mechanism they have never
heard of.

This page is the one place that rule is written down for this repo. The rule itself
came from the plugin-authoring guidance; what is specific to Plaud is the vocabulary
below and the three things sharing the menu.

## One verb per intent

Each thing a person might want has one verb, and every skill that does it uses that
verb. When two skills do the same thing under two verbs, people cannot guess which
to type, which is the problem #76 set out to remove.

| What the person wants | Verb | Skill |
|---|---|---|
| Bring transcripts to disk, for a range they name | `download` | `plaud-download` |
| Bring the original recording back | `download` + `audio` | `plaud-download-audio` |
| Find where something was said | `search` | `plaud-search` |
| See what a recording covers, without reading it | `outline` | `plaud-outline` |
| Correct words the transcription heard wrong | `proofread` | `plaud-proofread` |
| Turn a recording into another format | `to-<format>` | `plaud-to-srt` |

Adding a skill: find its row first. If the intent already has a verb, either extend
that skill or say in the PR why a second one is warranted. If it is a new intent,
add a row here in the same change.

`plaud-search` and the official `plaud-find` sit next to each other on purpose, and
the difference is what they look at: `plaud-find` locates a recording by name, date
or topic through Plaud's own listing; `plaud-search` looks inside the transcripts
already on disk. `plaud-search`'s description does not name `plaud-find`, and nothing enforces that the difference stays visible; the official `plaud-find` is named in `plaud-download`'s description instead.

## Three things share one menu

| Source | Owned by | Checked how |
|---|---|---|
| Plaud's own skills (`OFFICIAL_SKILLS` in `tests/test_skill_names.py`) | Plaud | Test: our names must not reuse them. The list was measured at CLI/MCP 0.3.7 and has not been re-measured since (#77), so a pass proves only that there is no clash with that list |
| This plugin's `skills/` | this repo | Test: directory name equals frontmatter `name:`; docs may only name skills that exist |
| Other plugins installed on the same machine | whoever installed them | `python3 scripts/check_skill_collisions.py`, run before a release |

The third row cannot be a test, because the answer depends on the machine. The
script reads the install record (`installed_plugins.json`), compares each other
installed plugin that is not switched off in the user settings against `skills/`, and
exits 1 on a shared name. It exits 2 when it cannot read its input, so a clean result
always means it compared something, and its clean line says how many plugins. It does
not scan `commands/`, user-level or project-level skills (#80). Run it on the machine
that will install the release, not only on a clean one.

## Renames

Old names are written without backticks in docs on purpose: `tests/test_skill_names.py`
treats a backticked `plaud-<word>` as a claim that the skill exists. The pinned
history lives in `TestIntentNames.RENAMED`.

| When | Old | New | Why |
|---|---|---|---|
| #65 | plaud-srt | `plaud-to-srt` | named a file format |
| #65 | plaud-index | plaud-sync, then `plaud-download` | named an internal mechanism |
| #76 | plaud-sync | `plaud-download` | "sync" promises a standing local-remote match; the skill brings down the range you name, once, and keeps nothing up to date afterwards (#74) |
| #76 | plaud-audio | `plaud-download-audio` | noun-only; now the same verb as its sibling |
| #76 | plaud-grep | `plaud-search` | the name of a tool, not what a person says |
