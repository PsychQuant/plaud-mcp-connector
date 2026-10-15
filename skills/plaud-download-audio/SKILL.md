---
name: plaud-download-audio
description: |
  Get the original audio file back out of Plaud — the recording itself, not its
  transcript. Use when the user wants the audio to archive, to edit, to feed to
  another tool, or to transcribe again with a different engine: "download the
  audio from that meeting", "get me the original recording", "把那次錄音的音檔抓下來",
  "我要原始音檔", "export the audio so I can re-transcribe it". This is the
  recording as captured, not any of the things Plaud derived from it — not the
  subtitles, not a document, not a summary. Reaches the audio through the
  official CLI, which returns the link directly, and downloads the file rather
  than handing over a link that expires.
  Also triggers in the languages Plaud localises for (its own hreflang list):
  "Originalaufnahme herunterladen", "descargar el audio original", "télécharger l'audio original", "元の音声をダウンロード", "scarica l'audio originale", "originele audio downloaden", "baixar o áudio original", "tải âm thanh gốc", "ดาวน์โหลดเสียงต้นฉบับ", "muat turun audio asal", "تنزيل الصوت الأصلي".
---

# Plaud Download Audio — get the recording itself back

**Correction (2026-08-07).** An earlier version of this skill said the official
MCP "has no tool for this at all". That was wrong, and it was wrong in an
instructive way: there is no tool *named* `audio`, and that got treated as
there being no *capability*. `get_file` returns a `presigned_url`, and Plaud's
own `plaud-read` skill uses exactly that.

What is actually true is a cost difference:

| Path | To get the audio link | Response size (measured) |
|---|---|---|
| CLI `plaud audio <id>` | one call, one line back | a URL |
| MCP `get_file <id>` | the URL is one field of the payload | **140,970 chars** — `source_list` alone is 135,268 |

`get_file` embeds the transcript source, so reaching the audio through it means
pulling a whole transcript into the model context to read one field. That is why
this skill uses the CLI — not because the MCP cannot, but because it is the
cheap door to the same room.

This skill also **downloads the file**, where the official path hands you a link.
The link expires (below), so which of those you want depends on whether you
intend to keep the audio or just click once.

## The one thing that will bite you

`plaud audio` does not return a file. It returns a **presigned S3 URL**. Plaud's own
tools say it expires in 24 hours (the CLI prints "This URL expires in 24 hours."), but
the URL carries its own lifetime, `X-Amz-Expires=3600`: one hour. Measured 2026-10-15,
CLI 0.3.14, on the ten newest recordings: all ten say 3600, and a link kept for 4 h 37 min
was refused with HTTP 403 (when it stopped working between those two was not measured).
Treat the link as good for about an hour.

```
Audio Download URL:
https://<bucket>.s3-accelerate.amazonaws.com/<path>.<ogg or mp3>?X-Amz-…&X-Amz-Expires=3600&…
Note: This URL expires in 24 hours.
```

**Never store that URL.** Not in the cache, not in a note, not in a manifest.
A stored URL works when you test it and fails silently an hour or so later, which is the
worst failure shape there is — the link looks fine and the download 403s.

Download immediately, or fetch a fresh URL each time. Those are the only two
correct patterns.

## Prerequisites

```bash
command -v plaud >/dev/null || {
  echo "This skill uses the official CLI — one call for the link."
  echo "  npm install -g @plaud-ai/cli && plaud login"
  echo "  (The MCP's get_file also carries a presigned_url, but returns ~141KB to do it.)"
  exit 1
}
plaud me >/dev/null 2>&1 || echo "Run 'plaud login' — the CLI holds its own token, separate from the MCP's."
```

The CLI and the MCP have **separate logins** (`~/.plaud/tokens.json` vs
`~/.plaud/tokens-mcp.json`). "The MCP works but the CLI says unauthorised" is
that, not a bug.

## Finding the recording

If the user names a recording rather than an id, find it the way `plaud-search`
does — search the local cache, which matches on what was *said*, not just the
name. Falling back to `plaud search` only matches names, and only across the
newest 500.

## Fetching

Check the id before it goes into a command: an id is `of_` followed by hexadecimal
characters. Anything else, do not put it in a command; tell the user which recording was
skipped and why.

```bash
URL=$(plaud audio "<file_id>" 2>/dev/null | grep -oE 'https://[^ ]+')
[ -n "$URL" ] || { echo "no audio URL returned"; exit 1; }

# The audio is Ogg for most recordings and MP3 for some (8 and 2 of the ten newest, 2026-10-15):
# take the extension from the URL instead of assuming one.
EXT=$(printf '%s' "${URL%%\?*}" | sed -E 's/.*\.([A-Za-z0-9]{1,5})$/\1/')
EXT=$(printf '%s' "$EXT" | tr 'A-Z' 'a-z')
case "$EXT" in ogg|mp3|m4a|wav|opus|aac|flac) ;; *) EXT=audio ;; esac

DEST="${PLAUD_CACHE_DIR:-$HOME/.plaud-connector/cache}/audio"
mkdir -p "$DEST"
curl -fsSL "$URL" -o "$DEST/<file_id>.$EXT"   # download NOW — the link is good for about an hour
```

Report the path and the size. Do **not** echo the URL back to the user as
something to keep — it is a fetch token with a deadline, and handing it over
invites exactly the stale-link failure above.

## Where it lands, and why not the repo

Audio goes in the cache directory, never in the repository. Original recordings
are third-party content — other people's voices — and this repo's rule is that
raw recordings stay local. `.gitignore` lists the audio extensions this skill can
write (and `.audio`, the fallback above); do not `git add -f` around it.

If the user asks for the file somewhere specific (Desktop, a project folder),
put it there instead. That is their filesystem and their call.

## Re-transcribing with a different engine

Having the audio makes it possible to run a different ASR over it — `bestasr`
is installed for exactly this kind of work.

**State the result honestly.** Without a ground-truth transcript there is no
accuracy number, so the claim you can make is *"here is what a different engine
heard"*, never *"this one is more accurate"*. If a name or a term comes out
right that Plaud got wrong, say that — one observed difference is a fact. A
general claim of higher accuracy is not, and this plugin does not make it.

For correcting Plaud's own transcript rather than replacing it, use
`plaud-proofread` — corrections land beside the original and searches mark them
`[corrected]`.

## Failure modes

| Symptom | Cause |
|---|---|
| `plaud: command not found` | CLI not installed. The MCP's `get_file` can substitute — its `presigned_url` field is the same link — but the response is ~141KB, so install the CLI if you will do this more than once |
| `[AUTH_FAILED] Token invalid or expired` | CLI's own login expired. `plaud login` |
| Download 403s | The URL expired. Get a fresh one; never reuse a stored URL |
| No URL in output | The recording may still be processing, or has no audio (imported text) |
