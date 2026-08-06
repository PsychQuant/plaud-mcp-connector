---
name: plaud-audio
description: |
  Get the original audio file back out of Plaud — the recording itself, not its
  transcript. Use when the user wants the audio to archive, to edit, to feed to
  another tool, or to transcribe again with a different engine: "download the
  audio from that meeting", "get me the original recording", "把那次錄音的音檔抓下來",
  "我要原始音檔", "export the audio so I can re-transcribe it". The official Plaud
  MCP has no tool for this at all — only the CLI can reach the audio, which is why
  this skill requires it.
---

# Plaud Audio — get the recording itself back

The official MCP ships seven tools and none of them return audio. The official
CLI has `plaud audio <file_id>`. That asymmetry is the whole reason this skill
exists: with the CLI installed, the original recording is reachable; without it,
it is not reachable at all.

## The one thing that will bite you

`plaud audio` does not return a file. It returns a **presigned S3 URL that
expires 24 hours after it is issued** (measured 2026-08-07, CLI 0.3.7):

```
Audio Download URL:
https://apse1-prod-plaud-bucket.s3-accelerate.amazonaws.com/audiofiles/<id>…
Note: This URL expires in 24 hours.
```

**Never store that URL.** Not in the cache, not in a note, not in a manifest.
A stored URL works when you test it and fails silently a day later, which is the
worst failure shape there is — the link looks fine and the download 403s.

Download immediately, or fetch a fresh URL each time. Those are the only two
correct patterns.

## Prerequisites

```bash
command -v plaud >/dev/null || {
  echo "The official CLI is required — the MCP cannot return audio at all."
  echo "  npm install -g @plaud-ai/cli && plaud login"
  exit 1
}
plaud me >/dev/null 2>&1 || echo "Run 'plaud login' — the CLI holds its own token, separate from the MCP's."
```

The CLI and the MCP have **separate logins** (`~/.plaud/tokens.json` vs
`~/.plaud/tokens-mcp.json`). "The MCP works but the CLI says unauthorised" is
that, not a bug.

## Finding the recording

If the user names a recording rather than an id, find it the way `plaud-grep`
does — search the local cache, which matches on what was *said*, not just the
name. Falling back to `plaud search` only matches names, and only across the
newest 500.

## Fetching

```bash
URL=$(plaud audio "<file_id>" 2>/dev/null | grep -oE 'https://[^ ]+')
[ -n "$URL" ] || { echo "no audio URL returned for <file_id>"; exit 1; }

DEST="${PLAUD_CACHE_DIR:-$HOME/.plaud-connector/cache}/audio"
mkdir -p "$DEST"
curl -fsSL "$URL" -o "$DEST/<file_id>.mp3"   # download NOW — the URL dies in 24h
```

Report the path and the size. Do **not** echo the URL back to the user as
something to keep — it is a fetch token with a deadline, and handing it over
invites exactly the stale-link failure above.

## Where it lands, and why not the repo

Audio goes in the cache directory, never in the repository. Original recordings
are third-party content — other people's voices — and this repo's rule is that
raw recordings stay local. `.gitignore` already blocks the audio extensions; do
not `git add -f` around it.

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
| `plaud: command not found` | CLI not installed. The MCP cannot substitute — it has no audio tool |
| `[AUTH_FAILED] Token invalid or expired` | CLI's own login expired. `plaud login` |
| Download 403s | The URL expired. Get a fresh one; never reuse a stored URL |
| No URL in output | The recording may still be processing, or has no audio (imported text) |
