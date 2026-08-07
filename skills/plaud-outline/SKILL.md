---
name: plaud-outline
description: |
  Get the shape of a recording — what it covered and roughly when — without
  pulling the whole transcript. Use when the user wants an overview rather than
  a search or a full read: "what was that meeting about", "give me the structure
  of the lecture", "which part covers X, roughly", "這場在講什麼", "那個會議的大綱",
  "跳到討論預算的那段". Also triggers in the languages Plaud localises for (its own
  hreflang list): "Worum ging es in der Besprechung", "¿de qué trató la reunión?",
  "de quoi parlait la réunion", "その会議は何の話だったか", "di cosa parlava la riunione",
  "waar ging die opname over", "sobre o que foi a reunião", "cuộc họp đó nói về gì",
  "การประชุมนั้นเกี่ยวกับอะไร", "mesyuarat itu tentang apa", "عمّ كان الاجتماع".
  Not for finding an exact sentence — that is plaud-grep.
---

# Plaud Outline — the shape of a recording, cheaply

Plaud produces three views of the same recording. Two of them were already in
use here; this is the third.

| Block | Size, one measured recording | What it is |
|---|---|---|
| `transaction` | 53,060 B, 94 segments | Everything said |
| `transaction_polish` | 50,159 B, 94 segments | Same, filler thinned |
| **`outline`** | **2,502 B, 59 timestamped items** | **Structure** |

Roughly a twentieth the size (21× by bytes, 13× by characters — CJK costs three
bytes each, so the two ratios differ), and still timestamped. That combination is
what makes it useful: a summary tells you what a meeting was about but gives you
nowhere to jump to; a transcript gives you everywhere to jump to at twenty times
the cost of reading it.

## What this fills

| The question | The tool |
|---|---|
| Who said what, at exactly which second | `plaud-grep` |
| What was decided | the cached summary (`plaud-grep` finds those too, marked `[summary]`) |
| **How this recording is laid out, and where to jump** | **here** |

## Prerequisites

```bash
command -v plaud >/dev/null || {
  echo "The official CLI gets the outline in one call, off the model context."
  echo "  npm install -g @plaud-ai/cli && plaud login"
}
```

Without the CLI, the MCP path works: `get_transcript` with `block="outline"`,
same cursor loop as any other block. It costs model context, but an outline is
small enough that this is tolerable — unlike a full transcript.

## Steps

### 1. Find the recording

If the user gave a name rather than an id, resolve it against the local cache —
that matches on what was *said*, not just on the filename:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cache.py" search "<distinctive words>"
```

Falling back to `plaud search` matches names only, across the newest 500.

### 2. Fetch

```bash
plaud transcript "<id>" --block outline
```

It is small. Reading it into the conversation is fine — that is the point of
this skill. A full transcript is not, and `plaud-index` exists so you never have
to.

### 3. Report

Present it as structure: the sections and their timestamps, in order. When the
user asked about a specific topic, name the timestamp they should jump to and say
plainly if the outline does not mention it.

## What an outline is not

**It is AI-written structure, not speech.** Nobody said these headings. Do not
quote a line from an outline as something a person said — for that, take the
timestamp and read the transcript there.

**It is not complete.** The measured recording had 59 outline items against 94
transcript segments: the outline *skips things*. So:

- "the outline does not mention X" is **not** evidence that X was not discussed.
  Say "the outline does not list it — want me to search the transcript?" and let
  `plaud-grep` answer properly.
- Never present an outline as coverage of a whole recording.

**It is not cached.** This skill fetches each time, by design. Putting outlines
in the cache would put them in `plaud-grep`'s results, and then the question
"how should AI-written structure be labelled in search hits" has to be answered —
the same question `[summary]` and `[corrected]` answer for their content. That is
worth doing properly rather than as a side effect here.

## Failure modes

| Symptom | Cause |
|---|---|
| Empty output | The recording has no outline yet. `plaud file <id>` shows what it does have |
| `[AUTH_FAILED]` | The CLI holds its own token, separate from the MCP's. `plaud login` |
| `plaud: command not found` | Use the MCP path above — `get_transcript` with `block="outline"` |
