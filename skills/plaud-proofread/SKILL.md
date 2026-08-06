---
name: plaud-proofread
description: |
  Correct ASR mishearings in cached Plaud transcripts using bestasr, so that
  searching for a term finds it even when Plaud heard it wrong. Use when the user
  says a name or technical term is transcribed incorrectly, asks to proofread or
  clean up a transcript, mentions 校對逐字稿 / 專有名詞聽錯 / 人名錯字, or when a
  plaud-grep search for a term the user is certain was said returns nothing.
  Also triggers in the languages Plaud localises for (its own hreflang list):
  "Namen falsch transkribiert", "nombres mal transcritos", "noms mal transcrits", "固有名詞が誤って文字起こしされている", "nomi trascritti male", "namen verkeerd getranscribeerd", "nomes transcritos incorretamente", "tên bị ghi sai", "ชื่อถอดความผิด", "nama tersalah transkrip", "أسماء مكتوبة خطأ".
argument-hint: "<recording id or name> [--context <doc path>]"
---

# Plaud Proofread

Runs `bestasr`'s proofreading pipeline over a cached transcript and stores the
result **alongside** the original, never on top of it.

## Why this is the ceiling on search

`plaud-grep` can only find words that made it into the transcript. If Plaud's ASR
heard "Iverson" as "艾佛森", searching `Iverson` returns nothing — and the answer
"that was never discussed" is wrong. No amount of fixing the search fixes this;
the fault is upstream in the text.

## Requirements — checked, not assumed

```bash
ls "$HOME/.claude/plugins/cache/bestasr" >/dev/null 2>&1
```

`bestasr` is **optional**. If it is missing, say so and stop — do not attempt a
hand-rolled substitute:

```
plaud-proofread needs the bestasr plugin, which supplies the proofreading
pipeline (context-ingest → srt-proofread). Install it, or skip proofreading —
plaud-grep still works on the raw transcripts, it just cannot find words the
ASR misheard.
```

## Steps

### 1. Build a term list from domain documents

Mishearings cluster on names and jargon, which is exactly what a domain document
contains. Point `bestasr:context-ingest` at whatever the recording is about —
slides, a paper, a roster, meeting notes:

```
/bestasr:context-ingest <path to the domain document>
```

It produces a `context.json` of terms, names and phrases.

**Without a context document there is nothing to correct against**, and the
proofreader is reduced to guessing. If the user has no such document, say that
proofreading will be weak and let them decide whether to continue.

### 2. Proofread against that term list

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cache.py" show "<id>" > /tmp/plaud-<id>.txt
```

Then run `/bestasr:srt-proofread` on it with the `context.json` from step 1.
Its discipline matters here and should not be relaxed: **timestamps are never
altered**, and a word is only changed when the context file supports it.

### 3. Store beside the original — never over it

```bash
mkdir -p "${PLAUD_CACHE_DIR:-$HOME/.plaud-connector/cache}/proofread"
cp <proofread output> "${PLAUD_CACHE_DIR:-$HOME/.plaud-connector/cache}/proofread/<id>.md"
```

The raw transcript is what was actually captured; the proofread copy is a derived
artefact built from a term list that may itself be wrong. Overwriting the original
throws away the only record of what the ASR really produced, and there is no way
back. Both are searched — `plaud-grep` recurses into `proofread/`.

### 4. Confirm the correction landed

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cache.py" search "<the term that was missing>"
```

Hits from the proofread copy print with a `[corrected]` tag. That tag is the point:
a corrected line is **not** a verbatim quote, and anything citing it should say so.

## Honest limits

- **Proofreading can introduce errors.** `srt-proofread` only changes what the
  context file supports, so a wrong term list produces wrong corrections —
  systematically, and in the direction of looking more authoritative.
- **Quality is not measurable here.** With no ground-truth transcript there is no
  accuracy figure. The user will notice "I can find it now"; nobody can state a
  number. Do not imply one.
- **Cost scales with library size.** This is a full extra pass over each
  transcript. Proofread the recordings that matter, not everything.
- **Corrected text is not testimony.** When quoting for anything that matters —
  minutes, a citation, a record of who said what — quote the original and note
  the correction separately.
