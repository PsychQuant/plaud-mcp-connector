# plaud-mcp-connector

A Claude Code plugin that runs the **official Plaud MCP** — fetched from npm at
launch, not vendored here — and adds the one thing it cannot do: **full-text transcript search**.

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

Requirements: **Node.js ≥ 20** and a Plaud account with Cloud Sync (PCS) enabled.

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

How long each subtitle stays on screen depends on which way you indexed. The
official CLI reports a start **and an end** for every segment, and those end
times are used as given. The MCP reports only starts, so a cue has to run until
the next one begins and the final cue gets a four-second guess. Installing the
CLI (above) buys exact timing as well as a cheaper index.

**Recordings longer than 99 minutes work as of v0.10.1.** Before that they were
silently truncated at the 100-minute mark and the `.srt` gave no sign of it —
valid syntax, continuous timecodes, and four-fifths of a 7.4-hour transcript
missing — 281 segments in the cache, 57 cues in the file.
The CLI writes *total* minutes, so the field passes two digits at 100 (`100:05`,
then `446:12` at seven hours) and the parser had been built for two. If you
produced subtitles from a long recording before v0.10.1, redo them: the old file
looks complete and is not. See #50.

Before this was fixed, the CLI path produced **no subtitles at all** — the two
paths write different timestamp shapes and only one was understood, so the
recommended way to index was the one that could not be captioned (#40).

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
- **First index of a large library is slow.** Transcripts are paginated, so a long
  recording takes several fetches. Without the CLI installed, every page also
  passes through the model context. Install `@plaud-ai/cli` (above) and scope with
  `--days` / `--since`.
- **Completeness is tracked, not assumed.** A recording whose fetch was cut short
  is marked incomplete, re-fetched on the next run, and flagged in search results
  as `⚠ partially indexed`. `cache.py status` shows the count. This exists because
  v0.1.0 silently kept only each transcript's first page and reported "no match"
  for words that were spoken.
- **A transcript line the parser does not recognise is dropped, but no longer
  quietly.** Every non-blank line after the frontmatter that does not become a
  cue is counted; its shape — not its words, and not its digits — is named on
  stderr, and the count rides along with the cue count on stdout so a caller
  reading only the success line still sees it. The counter asks whether the line
  *became a cue*, never whether it *looks like* one: three attempts at the
  looks-like question each left a shape out (an indented line, a `(` bracket, a
  markdown bullet), because that question has to enumerate and producer drift
  does not. The cost of the inversion is the mirror image — prose written into
  the body counts as a drop — and that is the right way round, because prose in
  the body is itself a contract violation worth naming. This exists because two
  defences that were each individually reasonable left a gap between them:
  dropping unrecognised lines is deliberate (blank lines, frontmatter), and the
  guard against a broken file fires only when *nothing* parsed. Neither covered
  *partly* — one fifth parsed is not zero, so #50 lost most of a transcript in
  silence. Treat the warning as a
  contract gap rather than a bad file: the shape probably needs adding to
  `scripts/cache.py`.

  Where the count reports depends on how you invoke it. With `-o` it rides on
  the success line (`wrote N cues (K content line(s) dropped — see stderr)`);
  without `-o` stdout *is* the subtitle file, so the same sentence goes to
  stderr instead. Exit stays 0 either way — the file was written and is usable —
  so a caller checking only the exit status has to read the cue line.

- **Which lines count as the file's header is decided by the file's kind, not by
  what the lines look like.** `cache.py` writes a `---` block only for
  `--kind transcript`; polish, summary and outline are bare bodies. So in a
  polish file a first line of `---` is *content* and is counted, while in a
  transcript the block runs delimiter to delimiter whatever it contains and is
  never parsed for cues. Five earlier attempts asked instead what the lines
  *looked* like, and each one either ate content or turned metadata into
  subtitles (#50).

  **The three numbers are a ledger, and the tests check the numbers.** `wrote N
  cues (H header, K dropped)` accounts for every non-blank line in the file, so
  nothing the tool removed goes unmentioned. Every count the tool prints — the
  ledger, the drop warning, the header warning, the zero-cue diagnostic — is
  compared as an integer by the suite, and each one turns it red when corrupted.
  A test reads the tool's own syntax tree and fails if any value reaching a
  stream is not registered against the assertion that covers it, so a new one
  cannot be added silently. Three times this was claimed of numbers nothing was
  checking — the assertions looked for a word rather than a value, then covered
  one surface of three, then four of eight — each time because the evidence was
  drawn from the same list as the claim. When some of what the header swallowed would have parsed as
  a cue, stderr says how many — but the count does not depend on that judgement,
  which is the point: an earlier version reported the header *only* when its
  contents were cue-shaped, so every shape the parser could not read stayed
  invisible to the very warning meant to report the parser's blindness.

  This matters most for `--file`, where an arbitrary path gives nothing to
  consult and a leading `---` block is taken as a header regardless. Point
  `--file` at a *polish* file that starts with `---` and the block is still
  consumed — but now you are told what went into it. Prefer the recording id
  when the file came from the cache; `--file` is for transcripts from elsewhere.
- **`login` can fail with `port 8199 is in use`.** Five things bind that port —
  three in the MCP, one in the CLI — so a second login while one is open loses.
  `lsof -nP -iTCP:8199 -sTCP:LISTEN` says which: `*:8199` is a login in progress and
  clears within two minutes, `[::1]:8199` is an `http`-mode server that holds it
  until stopped. Which binder does what, and what `login` does before it binds
  anything: [`docs/official-surface.md`](docs/official-surface.md#the-oauth-callback-port-8199).

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
