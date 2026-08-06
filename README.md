# plaud-mcp-connector

A Claude Code plugin that bundles the **official Plaud MCP** and adds the two
things it cannot do: **full-text transcript search** and **uploading audio into
your own library**.

> **Independent project.** Built and maintained by a Plaud user, not by Plaud.
> Not affiliated with, endorsed by, or supported by Plaud Inc. Problems with this
> plugin belong in [its issue tracker](https://github.com/PsychQuant/plaud-mcp-connector/issues),
> not with Plaud support. For Plaud's own integration see
> [docs.plaud.ai](https://docs.plaud.ai/plaud-mcp-cli/mcp).

> This is not a replacement for Plaud's official integration — it contains it.

## Why

Plaud shipped an official MCP server on 2026-08-06. It is good, and this plugin
runs it unmodified. But its surface is deliberately narrow:

| | Official Plaud MCP | This plugin adds |
|---|---|---|
| Tools | 7, all read-only: `login`, `logout`, `get_current_user`, `list_files`, `get_file`, `get_note`, `get_transcript` | — |
| Search | `query` matches **recording names only**, across the **newest 500** recordings | Full-text search over transcript **bodies**, across everything you have indexed |
| Upload | none — the REST upload endpoints belong to the partner/Embedded surface and do not write into a consumer library | Drives the web app to put a local file in your library |

So the official server can tell you a recording is called *"Weekly sync"*. It
cannot tell you **which** recording is the one where somebody actually said
"we're pushing the migration to Q3".

That is the gap this fills.

## Install

```
/plugin marketplace add PsychQuant/plaud-mcp-connector
/plugin install plaud-mcp-connector@plaud-mcp-connector
```

The `@plaud-mcp-connector` suffix names the marketplace. It reads oddly because
the marketplace and the plugin share a name, but leaving it off is ambiguous.
Both lines above were run end to end against a clean install of v0.2.0 — they are
tested, not assumed.

Then authorise once — ask Claude to *"log me into Plaud"*, or call the `login`
tool directly. It opens a browser for OAuth and stores the token in
`~/.plaud/tokens-mcp.json`.

Requirements: **Node.js ≥ 20**, a Plaud account with Cloud Sync (PCS) enabled,
and — for uploads only — macOS with Safari.

**Strongly recommended for large libraries**: `npm install -g @plaud-ai/cli`.
With the CLI present, `plaud-index` writes transcripts straight to disk instead of
reading every one through the model context — the difference between a few minutes
and a very expensive afternoon on a library of hundreds. Note the CLI keeps its own
login (`plaud login`), separate from the MCP's.

> Already ran Plaud's own installer? You do not need both. This plugin declares
> the same `@plaud-ai/mcp` package itself, so a second registration just means a
> second server process sharing the same token file.

## Skills

### `plaud-index` — land transcripts on disk

Walks `list_files`, fetches `get_transcript` for anything not already cached, and
writes one markdown file per recording to `~/.plaud-connector/cache/`. Incremental:
a re-run only fetches what is new.

```
索引最近 90 天的 Plaud 錄音
index my Plaud recordings from 2026-01-01
```

### `plaud-grep` — search what was actually said

Regex search across the cached transcripts. Runs entirely locally: no API calls,
no quota, no network. Results group per recording, newest first, with the matching
lines and their timestamps.

```
哪次會議談到預算拆兩期？
which recording mentions Kubernetes migration
search my transcripts for "action item"
```

### `plaud-upload` — put a file into your library

Converts video to audio, checks the 500 MB / 5 h limits, and drives Safari to
import the file and start transcription. **macOS only.**

```
上傳這個音檔到 Plaud 轉錄
transcribe ~/Recordings/interview.m4a
```

## How search works

No embedding service, no vector database, no extra dependency. Transcripts are
plain markdown on disk; search is `ripgrep` (or `grep` where ripgrep is absent).
One transcript segment per line, so every hit maps back to a timestamp in the
audio.

```bash
# the engine is usable directly, if you prefer a shell
python3 scripts/cache.py status
python3 scripts/cache.py search "預算|budget"
python3 scripts/cache.py show <recording-id>
```

## Limits — stated plainly

- **Search covers what you indexed.** A recording made after your last
  `plaud-index` run is not searchable. `cache.py status` prints the covered date
  range; the skill is instructed to report it rather than answer "not found".
- **Upload is browser automation**, not an API. It works today and will break the
  day Plaud redesigns its import dialog. Failures are usually visible (a click
  does nothing) rather than silent, but verify the file landed.
- **Upload is macOS-only.** It drives Safari via AppleScript. There is no Linux
  or Windows path, and none is planned.
- **First index of a large library is slow** and pulls a lot of text through the
  model context — one `get_transcript` call per recording. Scope it with
  `--days` / `--since`.

## Privacy

Cached transcripts are other people's speech. They live in
`~/.plaud-connector/cache/` on your machine, are never committed to this
repository, and are never sent anywhere except to the model answering your
question. Credentials come from OAuth (`~/.plaud/`) or the macOS Keychain — never
from a file in this repo.

## Related

- [Official Plaud MCP docs](https://docs.plaud.ai/plaud-mcp-cli/mcp) · [Plaud CLI](https://docs.plaud.ai/plaud-mcp-cli/cli)
- `@plaud-ai/mcp` on npm — the server this plugin runs

## License

MIT
