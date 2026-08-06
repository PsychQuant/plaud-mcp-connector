---
name: plaud-srt
description: |
  Turn a Plaud recording into SubRip (.srt) subtitles for video editing,
  lecture captions, or class recordings. Use when the user asks for subtitles,
  captions, an SRT file, 字幕, 逐字稿轉字幕, "make subtitles from this
  recording", or wants to caption a video whose audio is in Plaud. Neither the
  official Plaud MCP nor the official CLI can produce timed subtitles — they
  return transcript text only.
  Also triggers in the languages Plaud localises for (its own hreflang list):
  "Untertitel erstellen", "crear subtítulos", "créer des sous-titres", "字幕を作成", "creare sottotitoli", "ondertitels maken", "criar legendas", "tạo phụ đề", "สร้างคำบรรยาย", "buat sari kata", "إنشاء ترجمة".
argument-hint: "<recording name or id> [-o out.srt]"
---

# Plaud SRT

Converts a cached transcript into `.srt`. Runs entirely on the local cache — no
API call, no auth, no re-fetch.

## Why this exists

The official surface returns transcript **text**. `get_transcript` gives
timestamped utterances but no subtitle format, and the CLI's `plaud transcript`
writes plain text. Nothing in either produces the `HH:MM:SS,mmm --> HH:MM:SS,mmm`
cue structure a video editor or player needs. Anyone captioning a recorded lecture
has to build that timing themselves.

## Steps

### 1. Find the recording

The user will normally give a name, not an id. Resolve it:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cache.py" status
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cache.py" search "<distinctive words>"
```

`search` prints the id under each hit. If the recording is not cached, run
`plaud-index` first — this skill never fetches.

### 2. Convert

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/to_srt.py" <id> -o "<name>.srt"
```

Useful flags:

| Flag | Effect |
|---|---|
| `--no-speaker` | drop the `Speaker 1: ` prefix from every cue |
| `--tail-seconds N` | duration for the final cue (default 4) |
| `--file <path>` | convert a transcript file directly, bypassing the cache |

Without `-o` it writes to stdout, which is handy for piping but **do not paste a
long transcript into the conversation** — write the file and report the path.

### 3. Report honestly

Say where the file went and how many cues it has. Two things to surface if they
happen, because neither is visible in the resulting `.srt`:

- **`⚠ marked incomplete` on stderr** — the cache holds only part of this
  recording, so the subtitles simply stop partway with nothing to explain why.
  Tell the user to re-run `plaud-index` before using the file.
- **`no timestamped segments`** — the cached transcript has no timestamps, so
  subtitles are impossible from it. This exits non-zero rather than writing an
  empty `.srt`, which would look like success and produce a silent video.

## How the timing works, and where it is a guess

Each cue ends when the next one starts. That is the only timing the transcript
actually carries, so it is what gets used.

**The last cue is a guess** — nothing follows it, so it gets `--tail-seconds`
(4 by default). If the recording ends on a long sentence, raise it.

Out-of-order or duplicate timestamps get a half-second minimum instead of a
zero- or negative-length cue. Players reject those outright, so the line would
vanish rather than merely sit at the wrong moment.

## What this does not do

- **No re-timing against the audio.** Cue boundaries come from the transcript's
  own timestamps. If Plaud's ASR placed an utterance a second late, the subtitle
  inherits that.
- **No line wrapping.** A long utterance becomes one long cue. Players wrap it,
  but a subtitle editor may want it split.
- **No translation.** Subtitles come out in whatever language was spoken.
