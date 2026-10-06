---
name: plaud-search
description: |
  Search the FULL TEXT of your Plaud transcripts — the actual words spoken, not
  just recording names. Use whenever the user asks which recording mentioned a
  topic, person, decision or number: "哪次會議談到 X", "which meeting did we
  discuss the budget", "找出提到 Kubernetes 的錄音", "search my transcripts for
  X", "Plaud 全文搜尋". Runs on the transcripts `plaud-download` has already put in
  the local cache, so it matches what was said rather than what a file is
  called, and it needs no network. The official Plaud MCP cannot answer these —
  its query matches recording NAMES only, over the newest 500 recordings.
  Also triggers in the languages Plaud localises for (its own hreflang list):
  "welche Besprechung ging es ums Budget", "¿en qué reunión hablamos del presupuesto?", "quelle réunion parlait du budget", "どの録音で予算の話をしたか", "quale riunione parlava del budget", "welke opname ging over het budget", "qual gravação falou sobre o orçamento", "cuộc họp nào nói về ngân sách", "การประชุมไหนพูดถึงงบประมาณ", "mesyuarat mana yang bincang bajet", "أي اجتماع تحدث عن الميزانية".
argument-hint: "<search terms>"
---

# Plaud Search — search what was actually said

Searches the local transcript cache built by `plaud-download`. Everything runs on
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
id, date and the matching lines. Every run — hit or miss — also prints, before
anything else, what it searched:

```
searched 412 cached recordings, covering 2025-03-02 → 2026-10-04, 3 incomplete
(the cache holds only what has been downloaded — not necessarily everything in Plaud)
```

**Quote that line in your answer, every time.** The cache holds only what the user
chose to download, so a search result describes that part and nothing else.

### 2. Read the surrounding context

A grep hit is a pointer, not an answer. To see what was actually being discussed,
print the cached transcript and read around the hit:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cache.py" show "<id>"
```

For the AI summary and action items of that recording, call the Plaud MCP's
`get_note` tool with the same id (see `plaud-download` for tool-name resolution).

### 3. Answer with citations

Quote the transcript lines you relied on and name the recording and date. If the
transcript is ambiguous, say so rather than smoothing it over — ASR output
contains mishearings, and a confident paraphrase of a misheard line is worse than
a hedged quote.

## When a hit is marked `⚠ partially indexed`

A recording can be cached without being cached *completely* — the fetch loop hit
its page cap or was interrupted. Those hits carry:

```
⚠ partially indexed — more transcript may exist; download this recording again
```

Treat the result as a floor, not a total. Say the recording is only partly
indexed and suggest downloading it again with `plaud-download` before drawing
conclusions from it.
This is not the same as "(unnamed)", which means the manifest lost the entry —
different cause, different fix.

## When there are no matches

Empty results are ambiguous. **Never answer with a bare "no match"** — always say
what was searched, using the `searched …` line, and what that does not cover.
Then distinguish:

- **Cache is empty** → `cache.py` says so; ask the user which range to download
  with `plaud-download`.
- **Cache does not cover the period** → compare the `covering A → B` dates to what
  the user is asking about. A recording outside that span, or made after the last
  download, is not searchable. Say this explicitly instead of reporting "not found",
  and offer to download that period.
- **Genuinely absent** → the term really was not spoken (or ASR heard it
  differently — suggest a looser pattern, e.g. a distinctive substring or an
  alternation with likely mishearings). **Check `cache.py status` for an
  `incomplete:` count first** — you cannot conclude "never said" while any
  recording in the relevant period is only partly indexed.

Never report "no such recording" when the real cause is that it was never
downloaded.

## Scope limit — be honest about it

This searches **cached** transcripts only. Coverage equals whatever the user has
downloaded with `plaud-download`, which is not kept up to date. Before answering a question that depends on completeness ("did we
*ever* discuss X?"), check `cache.py status` and state the covered date range
alongside the answer.

## Some hits are not what was said

Results carry a source label when the matched line did not come from the
transcript:

| Tag | Where it came from | How to use it |
|---|---|---|
| _(none)_ | The transcript — what was actually said | Quote it |
| `[corrected]` | A proofread copy (`plaud-proofread`) | Quote as corrected text, not as verbatim speech |
| `[summary]` | An AI-written summary | **Nobody said these words.** Open the transcript for the actual wording |

The labels exist because the reader cannot tell by looking. A summary reads like
clean prose precisely because it was written rather than spoken, which makes it
the easiest thing on the page to misquote as a direct quotation.

When a search only matches summaries and never the transcripts, say so — it
usually means the phrasing the user remembers is the summariser's, not the
speaker's, and the transcript wording is different.
