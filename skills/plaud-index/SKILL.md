---
name: plaud-index
description: |
  Build or refresh the local Plaud transcript cache so recordings can be searched
  by their CONTENT, not just their filename. Use when the user says "index my
  Plaud recordings", "sync Plaud transcripts", "重建 Plaud 索引", "更新逐字稿快取",
  or when a plaud-grep search reports the cache is empty or stale. Also use before
  any question of the form "which recording mentioned X" — that question cannot be
  answered until the transcripts are on disk.
argument-hint: "[--days N | --all | --since YYYY-MM-DD]"
---

# Plaud Index — land transcripts on disk

The official Plaud MCP matches `query` against **recording names only**, across the
**newest 500 recordings**. There is no server-side full-text search. This skill
fetches transcript bodies once and caches them so `plaud-grep` can search them
locally, forever, offline.

Incremental by design: a re-run only fetches recordings that are not already cached.

## Prerequisites

The Plaud MCP must be connected and authorised. If tool calls fail with an auth
error, tell the user to run the `login` tool (it opens a browser for OAuth) — do
not try to work around it.

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

### 1. Read what is already cached

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cache.py" status
```

Keep the id set (`--ids-only`) for the diff in step 3.

### 2. List candidate recordings

Call `list_files`. Honour the user's scope argument:

| Argument | `list_files` params |
|---|---|
| *(none)* | `page_size: 100`, walk pages until a page returns fewer than `page_size` |
| `--days N` | `date_from` = today − N days |
| `--since YYYY-MM-DD` | `date_from` = that date |
| `--all` | walk every page |

`list_files` returns `id`, `name`, `created_at`, `start_at`, `duration`,
`serial_number`.

> **Pagination trap**: the docs state `page` / `page_size` are **ignored when
> filters are set**. So when you pass `date_from` / `date_to`, do NOT assume you
> can page through the filtered result — take what comes back and, if the count
> looks suspiciously like a cap, narrow the date window instead of paging.

### 3. Diff, then fetch only what is new

Subtract the cached id set from the listed ids. `--ids-only` lists **only
recordings fetched to the end**, so anything left half-fetched comes back here
automatically — there is no rebuild flag to remember.

**Say so before you start.** Recordings cached before paging existed carry no
completeness marker and count as incomplete, so a first run after upgrading can
re-fetch a lot. Print the count and let the user stop you:

```
Re-fetching N recordings that were cached without a completeness marker
(≈N get_transcript calls). Ctrl-C now if you would rather not.
```

#### `get_transcript` is paginated — one call is not the whole transcript

It returns **one page of utterances** with a `next_cursor` for the rest. Calling
it once and caching the result was the v0.1.0 bug: every recording was truncated
to its first page, and `plaud-grep` then reported "no match" for words that were
spoken — a wrong answer that looks like a correct one.

Loop per recording, accumulating pages:

```
cursor    = resume cursor from the manifest if this id was left incomplete, else none
seen      = {}                      # cursors already followed
segments  = []
pages     = 0

repeat up to 50 times:
    resp = get_transcript(file_id=<id>, block="transaction", limit=200, cursor=cursor)
    pages += 1
    append resp segments to segments
    nxt = resp next_cursor

    if nxt is absent / null / "" / whitespace        → complete, stop
    if nxt already in seen, or this page had 0 segments → stuck, stop INCOMPLETE
    seen.add(nxt); cursor = nxt
else (hit the 50-page cap)                            → stop INCOMPLETE
```

Each guard earns its place:

- **`block="transaction"` explicitly.** It is the API default, but writing it
  down keeps the next person from swapping in `transaction_polish`, whose
  AI-cleaned wording would break `plaud-grep`'s promise that the cache holds what
  was actually said.
- **`limit=200`, not the 500 maximum.** 500 is legal but untested here, and the
  API's own default of 50 suggests pages are sized to bound response size. 200
  cuts round trips without betting the whole recording on an unverified value.
- **Stop on a repeated cursor or an empty page.** A cursor that stops advancing
  otherwise burns all 50 pages before anyone notices. These catch it on page 2.
- **The 50-page cap is a backstop, not the plan.** Hitting it means incomplete,
  never "close enough".

#### Write the cache **once**, after the loop

`cache.py put` **overwrites**. Calling it per page leaves only the last page on
disk — a cache that looks populated and is 1/N complete. Concatenate every page
first, normalise, then write one entry:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cache.py" put \
  --id "<id>" \
  --name "<name>" \
  --created-at "<created_at>" \
  --duration "<duration>" \
  --complete true|false \
  --pages <N> \
  --last-cursor "<the last next_cursor you saw, verbatim>" <<'TRANSCRIPT'
<the transcript text, one segment per line>
TRANSCRIPT
```

**Pass `--last-cursor` verbatim even when it looked empty.** `cache.py` re-checks
it against its own rule and downgrades a `--complete true` claim that does not
hold up. That check is the only thing standing between "the loop ended early" and
a truncated cache that reports itself as whole — this file's instructions are
prose, and prose cannot be unit-tested.

If the loop breaks part-way (network error, page cap), still write what you have
with `--complete false`. Partial beats nothing: it is searchable now, carries a
warning, and resumes next run.

Normalise whatever `get_transcript` returns into **one segment per line**, keeping
the timestamp and speaker label inline, e.g. `[00:12:03] Speaker 1: ...`. One
segment per line is what makes `plaud-grep`'s line-level hits map back to a point
in the audio.

If a recording has no transcript yet (still processing), skip it and say so —
`cache.py put` refuses an empty body rather than caching a blank entry that would
look indexed but match nothing.

### 4. Report

State how many were already cached, how many were newly fetched, how many were
skipped for having no transcript yet, and **how many finished incomplete** (page
cap, stuck cursor, or an interrupted loop). Incomplete ones resume on the next
run — say that, so nobody goes hunting for a rebuild flag. Then show
`cache.py status`, which prints the same count.

## Cost warning

Each uncached recording costs **at least one** `get_transcript` call — long
recordings paginate and may need several. A first run over hundreds of recordings
is slow and pulls a lot of text through the model context. For a large library,
**tell the user the count first and let them scope it** with `--days` / `--since`
rather than silently pulling everything.

The same applies to the first run after upgrading from a pre-paging cache: every
old entry is re-fetched. Step 3 tells you to announce that count before starting.

## Where the cache lives

`~/.plaud-connector/cache/` (override with `PLAUD_CACHE_DIR`), one `<id>.md` per
recording plus `manifest.json`.

**This is third-party speech.** It stays on the machine — it is not committed to
git and not uploaded anywhere. Do not add it to a repository.
