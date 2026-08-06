---
name: plaud-grep
description: |
  Search the FULL TEXT of your Plaud transcripts — the actual words spoken, not
  just recording names. Use whenever the user asks which recording mentioned a
  topic, person, decision or number: "哪次會議談到 X", "which meeting did we
  discuss the budget", "找出提到 Kubernetes 的錄音", "search my transcripts for
  X", "Plaud 全文搜尋". The official Plaud MCP cannot answer these — its query
  matches recording NAMES only, over the newest 500 recordings.
argument-hint: "<search terms>"
---

# Plaud Grep — search what was actually said

Searches the local transcript cache built by `plaud-index`. Everything runs on
this machine: no API calls, no quota, no network.

## Steps

### 1. Search

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cache.py" search "<pattern>"
```

`<pattern>` is a regular expression, case-insensitive by default. Useful forms:

```bash
# any of several terms
... search "budget|預算|經費"

# a phrase, tolerant of spacing
... search "action *item"

# case-sensitive (acronyms, product names)
... search --case-sensitive "MCP"

# more context lines per recording (default 5)
... search "onboarding" --max-lines 15
```

Results are grouped per recording, newest first, each with the recording name,
id, date and the matching lines.

### 2. Read the surrounding context

A grep hit is a pointer, not an answer. To see what was actually being discussed,
print the cached transcript and read around the hit:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cache.py" show "<id>"
```

For the AI summary and action items of that recording, call the Plaud MCP's
`get_note` tool with the same id (see `plaud-index` for tool-name resolution).

### 3. Answer with citations

Quote the transcript lines you relied on and name the recording and date. If the
transcript is ambiguous, say so rather than smoothing it over — ASR output
contains mishearings, and a confident paraphrase of a misheard line is worse than
a hedged quote.

## When a hit is marked `⚠ partially indexed`

A recording can be cached without being cached *completely* — the fetch loop hit
its page cap or was interrupted. Those hits carry:

```
⚠ partially indexed — more transcript may exist; re-run plaud-index
```

Treat the result as a floor, not a total. Say the recording is only partly
indexed and suggest re-running `plaud-index` before drawing conclusions from it.
This is not the same as "(unnamed)", which means the manifest lost the entry —
different cause, different fix.

## When there are no matches

Empty results are ambiguous. Distinguish:

- **Cache is empty** → `cache.py` says so; run `plaud-index` first.
- **Cache is stale** → run `cache.py status` and compare the date range to what
  the user expects. A recording made after the last index run is not searchable.
  Say this explicitly instead of reporting "not found".
- **Genuinely absent** → the term really was not spoken (or ASR heard it
  differently — suggest a looser pattern, e.g. a distinctive substring or an
  alternation with likely mishearings). **Check `cache.py status` for an
  `incomplete:` count first** — you cannot conclude "never said" while any
  recording in the relevant period is only partly indexed.

Never report "no such recording" when the real cause is an unindexed cache.

## Scope limit — be honest about it

This searches **cached** transcripts only. Coverage equals whatever `plaud-index`
last pulled. Before answering a question that depends on completeness ("did we
*ever* discuss X?"), check `cache.py status` and state the covered date range
alongside the answer.
