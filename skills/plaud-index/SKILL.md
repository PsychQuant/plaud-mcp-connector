---
name: plaud-index
description: |
  Build or refresh the local Plaud transcript cache so recordings can be searched
  by their CONTENT, not just their filename. Use when the user says "index my
  Plaud recordings", "sync Plaud transcripts", "重建 Plaud 索引", "更新逐字稿快取",
  or when a plaud-grep search reports the cache is empty or stale. Also use before
  any question of the form "which recording mentioned X" — that question cannot be
  answered until the transcripts are on disk.
  Also triggers in the languages Plaud localises for (its own hreflang list):
  "Plaud-Aufnahmen indexieren", "indexar mis grabaciones de Plaud", "indexer mes enregistrements Plaud", "Plaudの録音をインデックス化", "indicizza le registrazioni Plaud", "Plaud-opnames indexeren", "indexar minhas gravações Plaud", "lập chỉ mục bản ghi Plaud", "จัดทำดัชนีการบันทึก Plaud", "indeks rakaman Plaud", "فهرسة تسجيلات بلود".
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

#### Ask what a recording HAS before fetching it — on a first index

`list_files` does not say whether a recording has a transcript (measured: it
returns `id`, `name`, `created_at`, `start_at`, `duration`, and nothing else). So
the naive loop fetches everything and finds out the expensive way — a recording
that was never transcribed answers with a bare `[]`.

There is a better signal, on a call this skill never used to make:

```bash
plaud file "<id>"       # → audio: available / transcript: available / summary: available
```

**Through the MCP, do not do this.** `get_file` carries the same availability
information, but its response is **140,970 characters** measured — it embeds the
transcript source (`source_list` alone is 135,268). Pre-checking with it costs
more than the ~53KB fetch it would avoid: you would pull three transcripts' worth
of payload to learn you can skip one.

So the pre-check is **CLI-only**. On the MCP path, fetch and treat the bare `[]`
as the answer — that is the cheaper of the two wrong-shaped options.

*(An earlier version of this section said "do both paths". That was written from
the field list without measuring the payload — having the field you need does not
mean it is cheap to obtain.)*

**When to pre-check, and when not to.** This trades N cheap `get_file` calls for
M avoided `get_transcript` calls, where M is however many recordings have no
transcript. When M is near zero it is a net loss:

| Situation | Do |
|---|---|
| First index of a library, or `--rebuild` | **Pre-check.** M is unknown and possibly large |
| Incremental run over a handful of new ids | **Skip the pre-check.** Just fetch — N ≈ M ≈ small, and the extra round trip buys nothing |

Say which one you did, and report skipped-for-no-transcript **separately** from
skipped-for-already-cached. They are different facts about the library and
merging them hides one of them.

Why this matters beyond speed: the real rate limit is **unmeasured** (see #6 —
normal use does not trigger throttling, but no ceiling was probed for). Not making
a request you know will be useless is the one optimisation that is correct without
knowing where the limit is.

#### Prefer the CLI when it is installed — it keeps transcripts out of the context

`get_transcript` returns the text **through the model**. Every page of every
recording is read into context on the way to disk, which is what makes a large
first index slow and expensive — and the whole point of this plugin is searching a
large library.

The official CLI writes straight to a file, so the text never enters context:

```bash
# Once per session, before using this path: does the CLI paginate too?
plaud transcript --help
```

The docs give `plaud transcript <id>` exactly two forms — bare, and `-o <file>` —
with **no cursor, page, or limit flag**, while `plaud files` and `plaud search` do
document theirs (`-p/--page`, `--max`). Paging is spelled out where it exists, so
its absence here reads as "one call returns the whole transcript". **That is an
inference from silence, not a guarantee** — hence checking `--help` first. If it
does list a paging flag, this fast path is unsafe: use the MCP loop below instead.

**Measured, not inferred (2026-08-07, CLI 0.3.7, authenticated account).** A
94-segment recording was fetched both ways: the MCP reported `total: 94`, and
`plaud transcript <id> -o file` produced a file with exactly 94 speaker-tagged
segments. **The CLI does not truncate** — one call returns the whole transcript.

That closes the risk this section was written to flag: if the CLI *had* truncated,
this fast path would have written truncated transcripts to disk marked
`--complete true`, and the completeness check could not have caught it (there is no
cursor on this path to check against). The premise is now verified for 0.3.7 rather
than inferred from the absence of a flag — re-run `plaud transcript --help` after a
CLI upgrade, because a later version could still start paginating.

```bash
tmp=$(mktemp)
plaud transcript "<id>" -o "$tmp"          # never touches the model context
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cache.py" put \
  --id "<id>" --name "<name>" --created-at "<created_at>" --duration "<duration>" \
  --complete true --pages 1 --last-cursor "" < "$tmp"
rm -f "$tmp"
```

`--complete true` here rests on the CLI fetching everything. If a recording indexed
this way later looks truncated, that assumption is where to look first.

**The CLI and the MCP hold separate logins.** Tokens live in `~/.plaud/tokens.json`
and `~/.plaud/tokens-mcp.json` respectively, so `plaud login` and the MCP's `login`
tool are two different acts. "The MCP works but the CLI says unauthorised" is this,
not a bug — run `plaud login`.

Not installed? Say so once and use the MCP loop:

```
plaud CLI not found — indexing through the MCP instead, which pulls every
transcript through the model context. `npm install -g @plaud-ai/cli` makes
large libraries much cheaper to index.
```

#### Cache the polished transcript too — subtitles want it, search must not

Plaud returns the same speech twice: raw, and a filler-thinned **polish** with
**identical segments and identical timings** (measured 2026-08-07 — 94 segments
either way, filler roughly halved). Subtitles want the tidy one; nobody reads
"呃" on screen.

```bash
tmp=$(mktemp)
plaud transcript "<id>" --polished -o "$tmp" 2>/dev/null && \
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cache.py" put --id "<id>" --kind polish < "$tmp"
rm -f "$tmp"
```

Through the MCP: `get_transcript` with `block="transaction_polish"`, same paging
loop as the raw transcript, piped into the same `--kind polish` call.

**`polish/` is deliberately excluded from search.** It is the same sentence said
more tidily, so including it would return every line twice — raw and cleaned —
which is not extra reach, it is halved signal. Contrast `summaries/`, which IS
searched: a summary is *new* content, so searching it finds things findable
nowhere else. That difference is the whole rule.

Two things polish does **not** do, both measured — say them if the user expects
otherwise:

- **It does not fix mishearings.** In one recording a speaker's surname appears
  one way four times and another way once; the polished copy keeps both. For that,
  `plaud-proofread`.
- **It normalises script** — simplified characters came back traditional. Good for
  most readers here, a surprise for anyone expecting the bytes as transcribed.

Polish is optional. A recording without one falls back to the raw transcript for
subtitles, which is the pre-existing behaviour.

#### Cache the summary too — it is often what the person remembers

What someone recalls is usually closer to the **summary** (the point that was
made) than to the transcript (the same point buried in filler). Both are worth
searching, so index both.

```bash
# CLI path — stays out of the model context, same as the transcript
tmp=$(mktemp)
plaud summary "<id>" -o "$tmp" 2>/dev/null && \
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cache.py" put --id "<id>" --kind summary < "$tmp"
rm -f "$tmp"
```

Through the MCP instead, `get_note` returns the same content; pipe it into the
same `--kind summary` call.

`--kind summary` writes to `summaries/` and **does not touch** the transcript,
its `chars`, or its `complete` flag — those describe the transcript and would
stop meaning anything if a summary could move them. A summary for a recording
with no cached transcript is refused rather than written as an orphan.

Summaries are optional. A recording with no summary is not incomplete — skip it
quietly and carry on.

#### `get_transcript` is paginated — one call is not the whole transcript

**Response shape, measured 2026-08-07 (authenticated):**

```jsonc
{ "file_id": "...", "block": "transaction",
  "total": 94,          // ← total segment count, present on every page
  "offset": 92, "limit": 200, "returned": 2,
  "next_cursor": null,  // ← JSON null when exhausted (base64 `{"o":N}` otherwise)
  "segments": [ { "start_time": ..., "content": "...", "speaker": "..." } ] }
```

Three things this settles:

- `next_cursor` is a **top-level key**, and exhaustion is a JSON **`null`** — not a
  missing key, not an empty string.
- `limit` is **not silently downgraded**: a request for 200 comes back echoing 200
  (the schema caps it at 500).
- **`total` exists.** That is a stronger completeness test than any cursor
  heuristic: `offset + returned >= total` is arithmetic, while "does this cursor
  look empty" is a judgement. Prefer the arithmetic.

**A recording with no transcript returns a bare `[]`, not an object.** The two
shapes are not interchangeable — code that reaches for `.next_cursor` on the empty
case is reading a property of an array. Treat `[]` as "not transcribed yet, skip".

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
