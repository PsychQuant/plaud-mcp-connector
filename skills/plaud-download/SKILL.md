---
name: plaud-download
description: |
  Download the transcripts of a range of Plaud recordings that YOU name — "the last
  two weeks", "September", "these three meetings" — into the local cache, so they
  can be searched by what was SAID (plaud-search) and turned into subtitles
  (plaud-to-srt). Use when the user says "download my Plaud transcripts", "download
  September's recordings", "把 9 月的錄音抓下來", "把這幾場抓下來", "下載逐字稿",
  "抓逐字稿到本機", or when a plaud-search lookup reports that the cache is empty or
  does not cover the period they are asking about. It always asks for a range first
  and shows the count before fetching; it never pulls the whole library by itself,
  and it keeps nothing up to date afterwards. To find WHICH recording to open, use
  the official plaud-find instead.
  Also triggers in the languages Plaud localises for (its own hreflang list):
  "Plaud-Transkripte herunterladen", "descargar mis transcripciones de Plaud", "télécharger mes transcriptions Plaud", "Plaudの文字起こしをダウンロード", "scarica le trascrizioni di Plaud", "Plaud-transcripties downloaden", "baixar minhas transcrições do Plaud", "tải xuống bản ghi chép Plaud", "ดาวน์โหลดการถอดความ Plaud", "muat turun transkrip Plaud", "تنزيل نصوص تسجيلات بلود".
argument-hint: "[--days N | --since YYYY-MM-DD [--until YYYY-MM-DD] | <recording name or id> …]"
---

# Plaud Download — land transcripts on disk

The official Plaud MCP matches `query` against **recording names only**, across the
**newest 500 recordings**. There is no server-side full-text search. This skill
downloads the transcripts of a range you name, once, into a local cache so
`plaud-search` can search them offline and `plaud-to-srt` can turn them into
subtitles.

It does **not** keep anything in sync. Nothing is fetched unless a range was named,
nothing is refreshed afterwards, and a recording added to Plaud later is not here
until a download names it. That is why this is called *download*: a cache that
quietly stays current would be a promise this skill cannot keep.

## Prerequisites

The Plaud MCP must be connected and authorised — listing goes through it. If tool
calls fail with an auth error, tell the user to run the `login` tool (it opens a
browser for OAuth) — do not try to work around it.

The official CLI (`plaud`) is optional and makes downloading much cheaper: it writes
transcripts straight to disk without passing them through the model. It keeps its
own login (`plaud login`), separate from the MCP's — "the MCP works but the CLI says
unauthorised" is that, not a bug.

## Tool naming

The exact MCP tool names depend on how the server is registered:

| Registration | Tool prefix |
|---|---|
| This plugin (bundled `.mcp.json`) | `mcp__plugin_plaud-mcp-connector_plaud__` |
| `claude mcp add` / official installer | `mcp__plaud__` |

Resolve the actual prefix once (any Plaud tool you can see), then reuse it. If no
Plaud tool is available at all, stop and tell the user to install/authorise the
MCP — do not silently fall back to scraping.

## Steps

### 1. Get a range — never default to everything

Ask for one if none was given. Do not pick a range for the user, and do not treat
"no argument" as "all of it".

| The user says | The range |
|---|---|
| "the last two weeks" / `--days N` | `date_from` = today − N days |
| "September" / `--since 2026-09-01` (and `--until 2026-09-30`) | `date_from` / `date_to` |
| specific recordings, by id or by name | each one individually (below) |

A name is resolved before an id is assumed. First check what is already cached:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cache.py" find "<part of the name>"
```

It exits 3 when nothing matches. Then ask Plaud: `list_files` with `query` set to
the same words. More than one match is the user's to choose between — list name,
date and length, do not guess.

If the user wants everything, say what that means: ask for a wide date range and
show the count in step 2. There is no whole-library mode.

### 2. List what is in the range, then confirm

Call `list_files` with `date_from` / `date_to` (or `query`). It returns `id`,
`name`, `created_at`, `start_at`, `duration`, `serial_number`.

> **Pagination trap**: the docs state `page` / `page_size` are **ignored when
> filters are set**, and the cap on a filtered result is not documented. If the
> number that comes back is round (50, 100, 200, 500) or equals the page size you
> asked for, assume it was cut off: split the window in half, list each half, and
> repeat until no piece looks like a cap. If you cannot get there, say so in the
> report — **never claim a range is complete when you could not show it.**

Then subtract what is already cached **and whole**:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cache.py" status --ids-only
```

`--ids-only` lists only recordings fetched to the end, so one that was left
half-fetched comes back into the plan on its own.

Show the plan and **confirm before fetching anything**:

```
In range 2026-09-01 → 2026-09-30: 18 recordings
  already cached and whole: 11
  to download now:           7
Continue?
```

Each recording costs a few seconds through the CLI. Through the MCP it costs at
least one `get_transcript` call per recording — long ones paginate — and pulls the
whole text through the model context, so give the user the count first.

### 3. Download one recording at a time

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/fetch_one.py" --id "<id>"
```

**Pass only `--id`.** The name, date and length are read from `plaud file <id>`: a
recording's name is text from Plaud and can contain quotes, `$(...)` and backticks,
so it must not be pasted into a shell command. It caches the raw transcript, the
polished one and the summary, and it never leaves a half-written entry.

| Exit | Meaning | Do |
|---|---|---|
| `0` | cached, or already cached and whole | next recording |
| `3` | `plaud` is not on PATH | use the MCP path below for this and the rest |
| `4` | the CLI answered with nothing usable | nothing was cached; count it as *no transcript* (below) |
| `5` | the CLI is not logged in | stop, tell the user `plaud login`; resume after |
| `2` / `1` | bad arguments / a bug | stop and show the message; do not retry blindly |

`--force` fetches again a recording that is already whole. The polish is optional
and the summary is optional: a recording without either is not incomplete, and the
script warns instead of failing.

What the extra two are for. The **polished** transcript is the same speech with
fillers thinned, identical segments and timings — subtitles want it, so
`plaud-to-srt` prefers it, and `plaud-search` deliberately does **not** search it
(every line would match twice). The **summary** is searched, and is often closer to
what someone remembers than the transcript is.

#### The MCP path, when the CLI is not installed

Say so once: "plaud CLI not found — downloading through the MCP, which pulls every
transcript through the model context. `npm install -g @plaud-ai/cli` makes large
ranges much cheaper." Then, per recording:

```
cursor = none; seen = {}; segments = []; pages = 0
repeat up to 50 times:
    resp = get_transcript(file_id=<id>, block="transaction", limit=200, cursor=cursor)
    pages += 1; append resp's segments
    complete when  offset + returned >= total
    stop INCOMPLETE if next_cursor is already in seen, or the page had 0 segments
    cursor = next_cursor
```

- `block="transaction"` explicitly — the polished block's reworded text would break
  `plaud-search`'s promise that the cache holds what was said.
- A recording with no transcript answers with a bare `[]`, not an object. Skip it.
- Write the cache **once**, after the loop. `cache.py put` overwrites, so a call per
  page leaves only the last page on disk:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cache.py" put \
  --id "<id>" --name "<name>" --created-at "<created_at>" --duration "<duration>" \
  --complete true|false --pages <N> --last-cursor "<last next_cursor, verbatim>" <<'TRANSCRIPT'
<one segment per line, e.g. [00:12:03] Speaker 1: ...>
TRANSCRIPT
```

Pass `--last-cursor` verbatim even when it looked empty: `cache.py` re-checks the
`--complete` claim against it and downgrades one that does not hold. If the loop
breaks part-way, still write what you have with `--complete false` — partial and
labelled beats nothing, and a later download that names it fetches it again.

The summary is `get_note`, piped into `cache.py put --id "<id>" --kind summary`.

### 4. Report

State the range you listed and **whether it may have been cut off** (step 2); how
many were already cached, how many were downloaded now, how many were skipped for
having no transcript, how many failed and with which exit code, and how many ended
incomplete. Then show `cache.py status`.

The no-transcript count needs its ambiguity said out loud, because the reader will
otherwise supply the harmless reading. Say it in this shape:

```
skipped 9 — no transcript
  Plaud's API cannot say which of these it is: never requested, still being
  made, or failed. If a recording has sat here across several runs it is
  almost certainly the first — open it in Plaud, press 產生 / Generate, then
  立即產生 / Generate now. The second press is the one that starts it; stopping
  after the first only opens the chooser.
```

**Do not try to guess which one it is.** There is no field to read, and any
heuristic (age, say) will tell a user to press Generate on a recording that was
merely slow. Naming the ambiguity and the one action that can help is all there is.

Finish by saying what the cache now is: **it holds only what has been downloaded.**
A search over it covers that, not everything in Plaud.

## Cost warning

Through the MCP, each uncached recording costs at least one `get_transcript` call
and pulls its text through the model context. For a wide range, **tell the user the
count first** (step 2) and let them narrow it.

## Where the cache lives

`~/.plaud-connector/cache/` (override with `PLAUD_CACHE_DIR`), one `<id>.md` per
recording plus `manifest.json`.

**This is third-party speech.** It stays on the machine — it is not committed to
git and not uploaded anywhere. Do not add it to a repository.
