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

Subtract the cached id set from the listed ids. For each remaining id call
`get_transcript`.

Then write it to the cache. Pass the transcript body on **stdin** — never as an
argument (transcripts are long and contain quotes, newlines and CJK):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cache.py" put \
  --id "<id>" \
  --name "<name>" \
  --created-at "<created_at>" \
  --duration "<duration>" <<'TRANSCRIPT'
<the transcript text, one segment per line>
TRANSCRIPT
```

Normalise whatever shape `get_transcript` returns into **one segment per line**,
keeping the timestamp and speaker label inline, e.g.
`[00:12:03] Speaker 1: ...`. One segment per line is what makes `plaud-grep`'s
line-level hits map back to a point in the audio.

If a recording has no transcript yet (still processing), skip it and say so —
`cache.py put` refuses an empty body rather than caching a blank entry that would
look indexed but match nothing.

### 4. Report

State how many were already cached, how many were newly fetched, how many were
skipped for having no transcript yet. Then show `cache.py status`.

## Cost warning

Each uncached recording costs one `get_transcript` call. A first run over hundreds
of recordings is slow and pulls a lot of text through the model context. For a
large library, **tell the user the count first and let them scope it** with
`--days` / `--since` rather than silently pulling everything.

## Where the cache lives

`~/.plaud-connector/cache/` (override with `PLAUD_CACHE_DIR`), one `<id>.md` per
recording plus `manifest.json`.

**This is third-party speech.** It stays on the machine — it is not committed to
git and not uploaded anywhere. Do not add it to a repository.
