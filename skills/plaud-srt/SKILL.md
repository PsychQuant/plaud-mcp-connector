---
name: plaud-srt
description: |
  Turn a Plaud recording into SubRip (.srt) subtitles for video editing,
  lecture captions, or class recordings. Use when the user asks for subtitles,
  captions, an SRT file, 字幕, 逐字稿轉字幕, "make subtitles from this
  recording", or wants to caption a video whose audio is in Plaud. Generates the
  cues from the locally cached transcript — it does not download a subtitle file
  Plaud already produced, and needs no network. Neither the official Plaud MCP
  nor the official CLI can produce timed subtitles — they return transcript text
  only.
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

### 2. Pick the source — ask once, then remember

Plaud gives two versions of the same speech. Both are cached, with the same
segments and the same timings, so switching never shifts the timeline:

| | |
|---|---|
| **polished** (default) | filler-thinned. Nobody wants to read "呃 那個 就是" on screen |
| **verbatim** | exactly as transcribed. Qualitative and conversation-analytic work **measures** disfluency — hesitation and restarts are the data, and polish deletes them |

Neither is the right answer in general, which is why it is a preference rather
than something this skill decides. Search is a different matter and is **not**
configurable: it stays on the verbatim text, because polish is the same speech
reworded and a search that returns sentences nobody said is a different problem
(see `#28`).

**Do not ask every time, and do not ask in the abstract.** Ask once, when the
choice is real, showing the user one line of their own recording rendered both
ways — then remember the answer:

```bash
# Has a preference already been chosen? exit 3 = never asked.
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/config.py" get subtitle_source

# Is there anything to choose between? exit 3 = no, do not ask.
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/to_srt.py" <id> --preview-sources
```

| Situation | What to do |
|---|---|
| `get` exits 3 **and** `--preview-sources` exits 0 | **Ask**, quoting the two lines it printed. Then `config.py set subtitle_source <answer>` |
| `get` exits 0 | A choice is on record — use it, say nothing |
| `--preview-sources` exits 3 | **Do not ask, and read stderr — it always says why.** The reason is never inferred from the exit code: every refusal prints `⚠ no source comparison to show: <cause>`. Relay that sentence if it points at something the user should fix (a dropped line, a diverging timeline); stay quiet if it does not (no polish, identical versions). This row used to enumerate the causes and was wrong every time the code grew one |
| Nobody is there to answer | Use the default and **say which version you used** in the report |

Asking "polished or verbatim?" with nothing attached is unanswerable — the user
has not seen either. Asking it beside their own line answers itself:

```
polished: 講者一: 我們要把預算拆成兩期
verbatim: 講者一: 呃 那個 就是 我們要把預算拆成兩期
```

For one recording only, skip the preference entirely: `--source verbatim`.

Polish does **not** fix misheard names (that is `plaud-proofread`) and it
normalises simplified characters to traditional. Say so if it matters to them.

### 3. Convert

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/to_srt.py" <id> -o "<name>.srt"
```

Useful flags:

| Flag | Effect |
|---|---|
| `--no-speaker` | drop the `Speaker 1: ` prefix from every cue |
| `--tail-seconds N` | duration for the final cue (default 4) |
| `--file <path>` | convert a transcript file directly, bypassing the cache |
| `--source polished\|verbatim` | override the stored preference, this once |
| `--preview-sources` | print one line both ways; exit 3 when there is no choice |

Without `-o` it writes to stdout, which is handy for piping but **do not paste a
long transcript into the conversation** — write the file and report the path.

### 4. Report honestly

Say where the file went and how many cues it has.

**Surface every `⚠` line the tool puts on stderr.** Not a list to check against —
relay what is there. None of these is visible in the resulting `.srt`, and this
section has twice been a closed count that went stale the moment the code grew
another one. What each means:

- **`⚠ N of M content lines … did not parse` on stderr** — the file was
  converted, but part of the transcript is missing from it. **This is the one
  that contradicts the success line**: the run exits 0 and stdout still reports
  a cue count, because the `.srt` was written and is usable. The count is real
  and it is also short. Report both, and lead with the loss — a 7.4-hour
  recording once produced 57 perfectly-formed cues out of 281 segments and was
  reported as a success (#50). When this fires, stdout says
  `wrote N cues (H header, K content line(s) dropped — see stderr)`; pass that
  whole sentence on rather than just the number. The three numbers are a ledger:
  cues plus dropped plus header accounts for every non-blank line in the file,
  so a header far larger than a handful of `key: value` lines is worth a second
  look even when nothing else is reported. Without `-o` there is no success
  line — stdout is the subtitle file itself — so the same sentence arrives on
  stderr as `wrote N cues to stdout (K content line(s) dropped — see stderr)`.
- **`⚠ marked incomplete` on stderr** — the cache holds only part of this
  recording, so the subtitles simply stop partway with nothing to explain why.
  Tell the user to re-run `plaud-index` before using the file.
- **`⚠ N line(s) … were taken as the file's header and not read`** — the block
  from the first `---` to the next one was treated as the header. **The sentence
  continues past the count and the rest is the part that matters** — it says what
  those lines look like, whether any of them would have parsed as cues, and
  whether the block is the shape `cache.py` actually writes. Relay it whole; do
  not summarise it down to N, and do not read a large N on its own as harmless.
  Usually this means a file with no header whose first line happens to be `---`,
  or a header whose closing delimiter is later than intended.
- **`⚠ a declared end time was discarded`** — the producer wrote an end for that
  cue and it could not be read, so the cue's duration is inferred instead. The
  words are all there; one timing is a guess that looks like a measurement.
- **`error: no lines in … looked like segments`** — nothing in the file became a
  cue, so subtitles are impossible from it. This exits non-zero rather than
  writing an empty `.srt`, which would look like success and produce a silent
  video. The message states how many content lines and how many header lines
  were present, and only guesses "most likely a recording without timestamps"
  when there is no header that could be to blame — read those numbers before
  repeating the guess.
- **`⚠ … trimmed` / `⚠ … clamped`** — a declared end ran past the next cue's
  start, or a cue would have had no length. Corrections, not losses — mention
  them only if the user is checking timing closely.

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
- **No re-wrapping to your player's taste.** Long cues *are* wrapped — 42
  characters for Latin script, 20 for CJK, since each CJK glyph is full-width.
  Both are configurable (`srt_line_limits`, below). Thai is deliberately left
  unwrapped: it has no word spaces, and guessing a break point is worse than a
  long line.
- **No translation.** Subtitles come out in whatever language was spoken.

## Preferences

Stored in `~/.plaud-connector/config.json` — beside the cache, not inside it, so
clearing the cache to fix an indexing problem does not also erase your settings.

```json
{
  "subtitle_source": "polished",
  "srt_line_limits": { "latin": 42, "cjk": 20 }
}
```

`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/config.py" show` prints the current
values and marks which are still defaults.

Precedence, highest first: `--source` flag → `PLAUD_SUBTITLE_SOURCE` →
config file → default. An unrecognised key is reported on stderr and skipped —
a typo costs you the preference, never the subtitles.

`srt_line_limits` has no ask-once flow, unlike `subtitle_source`. There is no
moment in captioning a recording where "how many characters per line does your
player like?" is a natural question, so it is edited by hand. That asymmetry is
deliberate but it does mean the two settings differ in how discoverable they
are: one introduces itself, the other only exists in this document.
