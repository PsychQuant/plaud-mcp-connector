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
| Search | `query` matches **recording names only**, across the **newest 500** recordings | Full-text search over transcript **bodies**, across the recordings you have downloaded |

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
With the CLI present, `plaud-download` writes transcripts straight to disk instead of
reading every one through the model context — the difference between a few minutes
and a very expensive afternoon on a range of hundreds. Note the CLI keeps its own
login (`plaud login`), separate from the MCP's.

> Already ran Plaud's own installer? You do not need both. This plugin declares
> the same `@plaud-ai/mcp` package itself, so a second registration just means a
> second server process sharing the same token file.

## Skills

As of v0.12.0 the skill names say what you would type to get the thing done. Old
names are written here without backticks on purpose: a name in backticks is a claim
that the skill exists. plaud-srt is now `plaud-to-srt`; plaud-index, which became
plaud-sync, is now `plaud-download`; plaud-grep is now `plaud-search`; plaud-audio is
now `plaud-download-audio`. The old names no longer exist, and nothing in your cache
needs migrating. `plaud-repo-audit` moved out of the skills you install; see its section
below.

### `plaud-download` — land transcripts on disk

Downloads the transcripts of **a range you name** — the last two weeks, September,
these three meetings — and writes one markdown file per recording to
`~/.plaud-connector/cache/`. It lists the range, shows how many are already cached
and how many it would fetch, **asks you to confirm**, then downloads them one at a
time. Through the official CLI each recording also gets its polished transcript (for
subtitles) and its summary (searched by `plaud-search`); without the CLI, the MCP path
caches the raw transcript and the summary, and subtitles come from the raw one.

It does not keep anything in sync, and that is why it is called *download*. There
is no whole-library mode, nothing is fetched unless you named a range, and a
recording added to Plaud later is not in the cache until a download names it. The
cache is whatever you chose to download — and `plaud-search` says so every time it
answers.

The official CLI has a `plaud recent` that looks like the tool for listing. It is
not: it is the same `list_files` walk with a local filter, capped at 300
recordings **without saying so**, and it compares the API's timezone-less
timestamps against your local clock — eight hours of drift in UTC+8. Listing here
goes through `list_files` with explicit dates. The timezone question moves rather
than disappearing: `scanned_back_to` is UTC and your `date_from` is a local date, so
the skill asks for a day of margin instead of comparing them to the hour.

A filtered `list_files` ignores `page` / `page_size` and scans only the 500 most
recent recordings, and says how far back it got (`scanned_back_to`). The skill reads
that and says in its report when a range reaches further back than the scan did,
instead of reporting a short list as if it were the whole range.

```
把 9 月的錄音抓下來
download my Plaud transcripts from the last two weeks
```

### `plaud-search` — search what was actually said

Regex search across the cached transcripts. Runs entirely locally: no API calls,
no quota, no network. Results group per recording, newest first, with the matching
lines and their timestamps.

```
哪次會議談到預算拆兩期？
which recording mentions Kubernetes migration
search my transcripts for "action item"
```

### `plaud-to-srt` — subtitles from a recording

Turns one recording into `.srt`. Neither the official MCP nor the official CLI
produces timed subtitles — they return transcript text only. Give it a recording's
name. If the transcript is already cached the conversion is local; if not, it
fetches that one recording first — through the CLI when that is installed and
logged in, otherwise through the MCP — so there is nothing to sync beforehand.

Finding a recording by name scans the 500 most recent recordings. One older than
that is not found, and the skill says how far back it looked instead of reporting
the recording missing.

How long each subtitle stays on screen depends on what the cache holds. Both
the official CLI and the MCP return a start **and an end** for every segment, but
not at the same resolution: the MCP in milliseconds, the CLI as whole seconds
rounded down. On one recording, all twenty segments compared were the MCP's
millisecond value truncated, and four of its 95 segments (`First.`, `Thank you.`)
landed on a single second, which `to_srt` lengthens to half a second and reports.
So a subtitle from the CLI path can appear up to a second early.

Where the cache keeps the ends they are used as given. The CLI path does, at that
one-second resolution. `plaud-to-srt`'s MCP path does, in milliseconds.
`plaud-download`'s MCP path writes start-only lines, so for a recording downloaded that
way a cue runs until the next one begins and the final cue gets a four-second guess.
The CLI is still the cheaper way to download, because the text never passes through
the conversation.

**Recordings longer than 99 minutes work as of v0.10.1.** Before that they were
silently truncated at the 100-minute mark and the `.srt` gave no sign of it —
valid syntax, continuous timecodes, and four-fifths of a 7.4-hour transcript
missing — 281 segments in the cache, 57 cues in the file.
The CLI writes *total* minutes, so the field passes two digits at 100 (`100:05`,
then `446:12` at seven hours) and the parser had been built for two. If you
produced subtitles from a long recording before v0.10.1, redo them: the old file
looks complete and is not. See #50.

**A whitespace tail no longer costs quadratic time (#57).** A transcript line
whose text was only whitespace after its timestamp — `[00:10]` and 12 KB of
spaces — took five seconds to reject, and four times longer for every doubling
of the tail; the same shape with a speaker prefix (`[00:10] S:` and tabs) grew
at the same rate from a lower start, giving up on the speaker and matching the
rest as text. `--file` accepts any markdown, so that was a reachable local
denial of service. Matching now strips the tail first, in the one place the
pattern is applied, and no captured group changes (checked over every code
point against the pattern's own flags). The test suite times a family of such
lines — 323 shapes: the pattern's bracket forms crossed with the four named
whitespace classes and, on a spanning subset of the forms, with every class
the parser leaves inside a line; lines that survive the strip, in Latin, CJK
and tab-mixed text; lines with no closing bracket, or that grow inside one;
and blocks of many short lines that are dropped, kept as cues, or kept with a
lost end, every line of a block carrying its own timestamp and its own text —
on every path a line can reach the pattern, and on every list the tool walks
once per line or per cue that the family reaches (the class docstring names
the ones it does not), checking not only that the lines arrive but what they
become: how many cues came back out, how long the longest one still is, and
how many bytes reached the `.srt`. The children are killed when they stop
making progress, and each shape is measured against a same-length control
line, because ten rounds of review each found the previous guard covering one
region of the defect. The guard is the suite's heaviest item and roughly
three fifths of its wall time: on one 18-core machine, idle, `make test` went
from 10.5 s (637 tests, before the fix) to 25.5 s (650 tests), of which the
guard class is 15.2 s, and its eleven child processes need on the order of
two gigabytes between them. The absolute numbers move with the machine; the
ratio is the part worth remembering.

**Five other things changed in the same release**, each out of a review round on
that fix, and none of them announced by the paragraph above until a reviewer
pointed out that a user upgrading was not being told their subtitles now go
through a filter:

- **A malformed timestamp is now rejected rather than guessed.** `00:412` used
  to come back as 412 seconds and `10000:00` as 600000 — plausible numbers, in
  a function whose docstring said those raise. A malformed *start* now drops
  the line (and is counted); a malformed *end* is discarded with a warning and
  the duration inferred. See #53.
- **Cue text is filtered.** Control, format, surrogate, private-use and
  line/paragraph-separator characters are removed from every cue, so a
  transcript cannot address your terminal. Characters that carry meaning are
  kept — ZWJ, ZWNJ, LRM, RLM — and whitespace is normalised rather than
  deleted, because deleting a separator joins the words on either side.
  **Anything removed or normalised is counted**, on stderr and in the ledger,
  including on `--preview-sources`.
  *Unassigned* code points are deliberately **not** removed: unassigned is a
  property of the running Python's Unicode tables, not of the character, so
  that rule deleted letters assigned after the interpreter was built. And this
  is a category rule, not a guarantee — variation selectors pass through it.
  What makes it safe is the count, not the coverage.
- **Losses that used to reach only stderr now ride the success line**: discarded
  end times, corrected cue ends, removed or normalised characters, and cues
  that held nothing but invisible characters — alongside the dropped-line
  count. The success line reports the cues **in the file**, and those five
  numbers close over every non-blank line of the input.
- **`--preview-sources` declines more often, and says why.** It now refuses when
  either side dropped a line, when the two cue counts differ, when the timelines
  diverge, and when every line that differs sits at a timestamp the recording
  uses more than once — cases where the pair it used to show was two different
  moments — and when the transcript's header holds lines that are not
  `key: value`, since some of what the file says may then have been read as
  header. A repeated timestamp on its own does not stop it: if some other line
  differs at an unambiguous moment, that pair is shown.
- **The `⚠ marked incomplete` warning can fire on the default path.** It read
  the transcript's frontmatter flag from whichever file was being subtitled,
  and the default is the polish, which has no frontmatter — so on the shipped
  path it could never fire at all.

Before this was fixed, the CLI path produced **no subtitles at all** — the two
paths write different timestamp shapes and only one was understood, so the
recommended way to index was the one that could not be captioned (#40).

Subtitles use Plaud's cleaned-up transcript by default, so the fillers stay off
the screen — but that is a preference, not a verdict. Qualitative work measures
disfluency: hesitation and restarts are the data, and polish deletes them. The
first time a recording has both versions you get asked once, shown one line of
your own recording rendered both ways, and the answer is remembered in
`~/.plaud-connector/config.json`.

Search does not follow that preference and cannot be made to. `plaud-search` keeps
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

### `plaud-download-audio` — get the original recording back

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

For whoever maintains this repo, not for whoever installs the plugin. It lives in
`.claude/skills/`, which a plugin install is not expected to load (to be confirmed after the first release that ships this), so it appears only when
you work inside a clone of this repo.

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
python3 scripts/cache.py search -- '預算|budget'
python3 scripts/cache.py show <recording-id>
python3 scripts/cache.py show --kind outline <recording-id>   # or summary / polish
```

## Limits — stated plainly

- **Search covers what you downloaded.** A recording you never named in a download
  is not searchable, however recent. Every search prints how many recordings it
  searched and the date span they cover; the skill is instructed to quote that
  line, and never to answer a bare "no match".
- **A big range is slow.** Transcripts are paginated, so a long recording takes
  several fetches. Without the CLI installed, every page also passes through the
  model context. Install `@plaud-ai/cli` (above) and name a narrower range.
- **Completeness is tracked, not assumed.** A recording whose fetch was cut short
  is marked incomplete, fetched again by a download that covers it, and flagged in search results
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
