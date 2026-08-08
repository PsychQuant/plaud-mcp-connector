# plaud-mcp-connector

A Claude Code plugin that runs the **official Plaud MCP** — fetched from npm at
launch, not vendored here — and adds the two things it cannot do: **full-text transcript search** and **uploading audio into
your own library**.

📄 **[plaud-mcp-connector.vercel.app](https://plaud-mcp-connector.vercel.app)** — what it does, and when not to install it.

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

**Then turn on auto-update.** Claude Code ships third-party marketplaces with
auto-update *disabled*, so an install left alone stays on the version it was
installed at — including versions with search bugs since fixed. Ask Claude to
enable it, or: `/plugin` → Marketplaces → plaud-mcp-connector → Enable auto-update.
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

It also stops **listing** early. Walking every page to find three new recordings
costs more each time the library grows, so a re-run pages until it is past
everything it already holds and then stops. The saving is bounded below by two
pages, not one: the cutoff sits a day behind the newest cached recording, and
that recording is still on page one — so page one always says keep going. A
first index, or `--all`, still walks the lot.

The official CLI has a `plaud recent` that looks like the tool for this. It is
not: it is the same `list_files` walk with a local filter, capped at 300
recordings **without saying so**, and it compares the API's timezone-less
timestamps against your local clock — eight hours of drift in UTC+8. Both sides
of the comparison here come from the API, so that question never arises.

On a first index it asks `plaud file` whether a recording has a transcript at all
and skips the ones that do not — a recording can sit there with audio and no
transcript for as long as it likes, and fetching it is a guaranteed wasted call.
Incremental runs skip the check: paying one call to maybe save one is a losing
trade when almost nothing is new.

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

### `plaud-srt` — subtitles from a recording

Converts a cached transcript to `.srt`. Neither the official MCP nor the official
CLI produces timed subtitles — they return transcript text only. Runs on the local
cache: no API call, no auth.

Subtitles use Plaud's cleaned-up transcript by default, so the fillers stay off
the screen — but that is a preference, not a verdict. Qualitative work measures
disfluency: hesitation and restarts are the data, and polish deletes them. The
first time a recording has both versions you get asked once, shown one line of
your own recording rendered both ways, and the answer is remembered in
`~/.plaud-connector/config.json`.

Search does not follow that preference and cannot be made to. `plaud-grep` keeps
matching the verbatim text, because what you remember is what someone *said*,
not what an AI tidied it into. Both versions sit in the cache; only one of them
is searched, so a hit is never counted twice.

```
把上週的產品週會轉成字幕
make subtitles from the Kubernetes migration meeting
```

### `plaud-proofread` — fix what the ASR misheard

Runs `bestasr`'s proofreading pipeline over a cached transcript and stores the
result **beside** the original, never over it. This is the ceiling on search: if
Plaud heard "Iverson" as "艾佛森", no amount of fixing the search finds it — the
fault is in the text. Hits from the corrected copy are tagged `[corrected]` so a
correction is never quoted as verbatim speech. Requires the optional `bestasr`
plugin.

```
這段逐字稿的人名都聽錯了，幫我校對
proofread the research meeting transcript against these slides
```

### `plaud-upload` — put a file into your library

Converts video to audio, checks the 500 MB / 5 h limits, and drives Safari to
import the file. **macOS only.**

It stops at the upload. Plaud transcribes nothing on its own — the recording
waits until you open it and press 產生 / Generate, then 立即產生 / Generate now
to confirm, and `plaud-index` has nothing to fetch until you do. The skill says
this when it finishes, because an untranscribed recording is indistinguishable
from one still processing.

```
上傳這個音檔到 Plaud 轉錄
transcribe ~/Recordings/interview.m4a
```


### `plaud-audio` — get the original recording back

Downloads the audio file itself, not its transcript. Useful for archiving, for
editing, or for running a different ASR over it.

The official CLI returns the link in one call; the MCP's `get_file` carries the
same `presigned_url` but returns ~141 KB to do it, so this uses the CLI. The link
**expires in 24 hours**, so this downloads rather than handing you a URL to keep.

```
把那次會議的音檔抓下來
download the audio from the Kubernetes migration meeting
```

### `plaud-outline` — the shape of a recording, cheaply

Plaud's `outline` block is about a twentieth the size of the full transcript and
still timestamped. Answers "what was this about, and where do I jump to" without
pulling 53 KB through the model.

It is AI-written structure, not speech — and it **skips things** (59 outline items
against 94 transcript segments in one measured recording), so "the outline does
not mention it" is not evidence it was not discussed.

```
這場在講什麼
what was that meeting about
```

### `plaud-repo-audit` — re-measure this repo against the official surface

`docs/official-surface.md` is a snapshot of what Plaud's CLI and MCP actually do.
This re-measures it and reports what changed, and what that means here.

Its checklist is not generic — each item is a specific way this repo has been
wrong about someone else's software: reading a tool list instead of opening the
tarball, treating a present field as a cheap one, inferring behaviour from a
missing flag. Run it before trusting the doc, not after being surprised by it.

```
官方有沒有改
audit the official surface
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
python3 scripts/cache.py show --kind outline <recording-id>   # or summary / polish
```

Three more commands exist for the incremental listing. They are called by
`plaud-index`, not by hand, but they are the answer to "why did it stop
paging there" when a run looks wrong:

| Command | Answers |
|---|---|
| `status --list-cutoff` | how far back a listing may stop. Exit 3 means no cutoff is available — walk everything |
| `should-stop-paging --cutoff X` | does this page end the walk? Page on stdin, one `created_at` per line. Exit 0 stop, 3 continue, always with the reason |
| `mark-full-sweep` | records that a listing was walked to its end, unscoped. **The only thing that turns the cutoff on** |

`status` also reports how long since that last full sweep, and says so when it
is over 30 days — the early exit's blind spot grows with that number, and
nothing else makes it visible.

## Limits — stated plainly

- **Search covers what you indexed.** A recording made after your last
  `plaud-index` run is not searchable. `cache.py status` prints the covered date
  range; the skill is instructed to report it rather than answer "not found".
- **An incremental run is a fast path, not a completeness guarantee.** It stops
  listing once it is past everything it holds. A recording that reaches the
  cloud long after it was made carries an old timestamp, sits deep in the
  listing, and is stepped over — with no error and no count to notice it by.
  Nothing in the API offers a change feed to fix this; run `--all` periodically.
- **Upload is browser automation**, not an API. It works today and will break the
  day Plaud redesigns its import dialog. Failures are usually visible (a click
  does nothing) rather than silent, but verify the file landed.
- **Upload is macOS-only.** It drives Safari via AppleScript. There is no Linux
  or Windows path, and none is planned.
- **First index of a large library is slow.** Transcripts are paginated, so a long
  recording takes several fetches. Without the CLI installed, every page also
  passes through the model context. Install `@plaud-ai/cli` (above) and scope with
  `--days` / `--since`.
- **Completeness is tracked, not assumed.** A recording whose fetch was cut short
  is marked incomplete, re-fetched on the next run, and flagged in search results
  as `⚠ partially indexed`. `cache.py status` shows the count. This exists because
  v0.1.0 silently kept only each transcript's first page and reported "no match"
  for words that were spoken.

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
