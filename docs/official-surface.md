# Plaud's official surface, as measured

What the official CLI and MCP actually do — not what `--help` says they do.

**Measured 2026-08-07 against `@plaud-ai/cli` 0.3.7 (commit `bacaae9`) and
`@plaud-ai/mcp` 0.3.7, on an authenticated personal account.**

Everything here was run. Where something was inferred rather than executed, it
says so. These are observations of one release, not a contract Plaud has
published.

**To re-measure, run `plaud-repo-audit`.** It exists because "re-measure after a
version bump" as a sentence in a file has never once caused anyone to re-measure —
and because the *checklist* matters more than the reminder: that skill encodes the
specific ways this repo has been wrong about someone else's software (reading a
tool list instead of opening the tarball; treating a present field as a cheap
one). A version number that has not moved is not evidence that nothing changed.

> Contains no transcript content. The recordings used are the author's own, and
> what other people said in them stays out of this repository.

---

## Why this file exists

Four separate design decisions in this repo turned on "what can the official
surface actually do", and each one re-derived the answer from scratch:

| Issue | What was needed | How it was obtained then |
|---|---|---|
| #2 | Does the CLI paginate transcripts? | **Inferred from the absence of a flag** — not verified until #6 |
| #6 | `get_transcript` cursor shape and caps | Blocked for rounds; resolved only after OAuth |
| #8 | Where `next_cursor` lives, how exhaustion reads | Three unknowns held up a whole round |
| #20 | What `audio` returns | Filed with the answer marked "unverified" |

#8 is the instructive one: a cursor heuristic was designed to work around a
`total` field **that was there the whole time**. Unauthenticated design is
necessarily over-defensive — you cannot know what you were given, so you assume
you were given nothing.

---

## Two logins, not one

The CLI and the MCP hold **separate tokens** — `~/.plaud/tokens.json` and
`~/.plaud/tokens-mcp.json`. Authenticating one does nothing for the other.

"The MCP works but the CLI says `[AUTH_FAILED]`" is this, not a bug. Run
`plaud login` as well.

---

## CLI — 13 commands

`plaud <command>`. All read-only except `login` / `logout`.

| Command | What it does | Measured notes |
|---|---|---|
| `login` | Browser OAuth | Opens a browser; prints `Logged in successfully!` |
| `logout` | Revoke authorization | **Not exercised** — it would destroy the session under test |
| `me` | Current user | `workspace_id`, `member_id` |
| `files` | List recordings | `-p/--page`, `-s/--page-size` (default 20). Prints "Files on this page: N" — **no total is available** |
| `file <id>` | One recording's details | **See below — this one matters** |
| `audio <id>` | Audio download URL | **Presigned S3 URL, expires in 24 hours** |
| `transcript <id>` | Transcript | `-o/--output`, `--block`, `--polished`. **No pagination flag, and none is needed** — see below |
| `summary <id>` | AI summary | `-o/--output`. Returns title / datetime / location / key points |
| `search <keyword>` | Name search | `--from`, `--to`, `--max` (default 50). Reports "Matched N of M scanned" |
| `recent` | Last N days | `-d/--days` (default 7) |
| `today` | Today's recordings | Prints "No recordings created today." when empty |
| `update` | Check npm for a newer CLI | **Not exercised** — it touches a global install |
| `version` | Version + commit + build date | `plaud 0.3.7 / commit bacaae9 / built 2026-08-05` |

### `plaud file <id>` reports what a recording *has*

```
id:            <id>
name:          <name>
created_at:    2026-08-03T03:31:20
start_at:      2026-08-03T02:29:07.842000
duration:      1h01m
serial_number: <serial>
audio:         available
transcript:    available
summary:       available
```

Those last three lines are the useful part: **you can tell whether a recording
has a transcript without fetching it**. The MCP's `list_files` gives no such
signal, so anything driven by the MCP alone has to fetch and find out.

### `plaud transcript` does not truncate

This was the single unknown that could still produce a wrong answer, because
`plaud-index`'s CLI fast path marks what it writes as `complete` and has no
cursor to check that claim against.

Measured: a recording the MCP reports as `total: 94` produced a file with
**exactly 94** speaker-tagged segments. One call returns the whole transcript.

Re-check after a CLI upgrade. A future version could start paginating, and the
`--help` output is the first place it would show.

### The three `--block` values are genuinely different

Same recording, all three blocks:

| `--block` | Size | Segments | What it is |
|---|---|---|---|
| `transaction` (default) | 53,060 B | 94 | Raw transcript — speech as spoken |
| `transaction_polish` | 50,159 B | 94 | Same segments and timings, filler removed |
| `outline` | 2,502 B | 59 | Structural outline with timestamps |

**`transaction_polish` is a filler pass, not a correction pass.** It is 95% the
size of raw, keeps every segment and timestamp, and thins the filler:

| Filler | raw | polished |
|---|---:|---:|
| 呃 | 51 | 15 |
| 就是 | 204 | 153 |
| 那個 | 25 | 23 |
| 嗯 | 5 | 2 |

It also normalises script — one instance of a name written in simplified
characters came back traditional.

**What it does not do is fix mishearings.** In the same recording a speaker's
surname appears one way four times and another way once; the polished version
keeps both spellings. Script normalisation is not disambiguation.

That settles a question this repo had open: `plaud-proofread` is **not** made
redundant by `--polished`. They fix different things — polish removes what
nobody needs to read, proofreading fixes what the ASR got wrong. Running both is
coherent.

---

## MCP — 7 tools

Launched via `npx -y @plaud-ai/mcp@latest`. All read-only except `login` /
`logout`.

`login`, `logout`, `get_current_user`, `list_files`, `get_file`, `get_note`,
`get_transcript`.

**There is no tool *named* `audio` — but the audio is reachable.** `get_file`
returns a `presigned_url`, and Plaud's own `plaud-read` skill uses exactly that.
An earlier version of this file said the recording was "reachable only through
the CLI". That was wrong: absence of a name was read as absence of a capability.

What is true is a cost difference — see `get_file` below.

### `get_file` — carries a lot more than metadata

Measured response for one recording: **140,970 characters**.

| Field | Size |
|---|---|
| `source_list` | 135,268 |
| `note_list` | 3,502 |
| `presigned_url` | 1,726 |
| `id` / `name` / `created_at` / `serial_number` / `start_at` / `duration` | ~140 |

It embeds the transcript source. Two consequences worth stating plainly:

- **It is the expensive door to the audio link.** The CLI's `plaud audio` returns
  the same URL in one line.
- **It is unusable as an availability pre-check.** Pulling 141KB to learn whether
  a 53KB transcript exists costs more than just fetching the transcript. The CLI's
  `plaud file` carries the same availability flags as pure metadata.

### `list_files`

```jsonc
{ "type": "list",
  "data": [ { "id", "name", "created_at", "serial_number", "start_at", "duration" } ],
  "page": 1, "page_size": 10 }
```

Three things it does **not** give you:

- **No total.** You cannot know how many recordings exist without paging until a
  page comes back short.
- **No `has_more`.** Same consequence.
- **No availability flags.** Unlike `plaud file`, nothing says whether a
  recording has a transcript.

And one undocumented constraint: **`page_size` has a minimum of 10.** Passing 3
returns `page_size: Input should be greater than or equal to 10`. The tool schema
documents a default of 20 and no minimum.

Client-side filtering (`query` / `date_from` / `date_to`) paginates **up to 5
pages × 100 = 500 recordings**, which the tool description states outright. The
CLI says the same thing in its own words: `plaud search` "scans up to 500 most
recent recordings".

### `get_transcript`

```jsonc
{ "file_id": "...", "block": "transaction",
  "total": 94,          // total segments — present on every page
  "offset": 92, "limit": 200, "returned": 2,
  "next_cursor": null,  // base64 `{"o":N}` while more remain
  "segments": [ { "start_time", "end_time", "content", "speaker", "original_speaker" } ] }
```

- `next_cursor` is a **top-level key**. Exhaustion is a JSON **`null`** — not a
  missing key, not an empty string.
- The cursor is base64 of `{"o":<offset>}`. Verified by decoding one. **Treat it
  as opaque anyway** — an encoding you reverse-engineered is a dependency you did
  not negotiate.
- `limit` is **not silently downgraded**: ask for 200 and the response echoes
  200. The schema caps it at 500; the default is 50.
- **`total` exists**, which makes completeness arithmetic (`offset + returned >=
  total`) rather than a judgement about whether a cursor looks empty. Prefer the
  arithmetic.

**A recording with no transcript returns a bare `[]`, not an object.** The two
shapes are not interchangeable: reaching for `.next_cursor` on the empty case is
reading a property of an array. Treat `[]` as "not transcribed yet, skip".

---

## How the official plugin is distributed — and why one documented command cannot work

`@plaud-ai/mcp` is not only an MCP server. The published tarball also contains a
Claude Code **plugin**:

```
package/plugin.json   { "name": "plaud", "version": "0.3.7", … }
package/.mcp.json     { "mcpServers": { "plaud": { … } } }
```

It reaches Claude Code through the **skills-directory** mechanism, not a
marketplace. From the package's own bundled source:

```js
await installSkillsToClaudeCode();
console.log("Plaud Skills have been installed to ~/.claude/skills/ for Claude Code.");
```

A folder under a skills directory carrying a `plugin.json` is loaded as
`<name>@skills-dir` on the next session — no marketplace, no install step. That
is why `npx @plaud-ai/mcp` offers `clean-plugin` (which *clears* the Claude Code
plugin cache) but nothing that publishes or registers a marketplace: grepping the
bundled output for marketplace registration finds nothing at all.

### The consequence

Plaud's upgrade instructions end with:

> Then reinstall inside Claude Code:
> `/plugin install plaud`

**That command cannot succeed on any machine.** `/plugin install <name>` resolves
names against *registered marketplaces*, and no marketplace publishes `plaud` —
Plaud does not operate one. Running it returns `Plugin "plaud" not found in any
marketplace`. The working path is `npx @plaud-ai/mcp install`, after which the
plugin loads on the next session with nothing further to type.

The two mental models got crossed in the docs: a skills-dir plugin is installed
by *placing a directory*, and a marketplace plugin is installed by *name*. The
instructions distribute one and then tell you to install the other.

### What this repo does instead, and what that costs

This plugin ships through a marketplace (`/plugin marketplace add …` then
`/plugin install …@…`). That is one more step at install time. What it buys:
a declared version, an update path, and visibility in the `/plugin` UI — none of
which a skills-dir drop has. The trade runs the other way too: zero-step install
is genuinely nicer, right up until someone needs to know which version they are
running.

**Not measured:** `plaud-mcp install` was never executed here — it rewrites the
client's own configuration, which is not something to do to a machine in order to
document it. Everything above is read from the published tarball, the `--help`
output, and the absence of any `plaud` entry in this machine's registered
marketplaces.

### If you reach the MCP through `npx`, none of this applies

`.mcp.json` in this repo runs `npx -y @plaud-ai/mcp@latest`, which fetches the
package at launch and never touches `~/.claude/skills/`. Plaud's upgrade
procedure — `npm install -g`, `clean-plugin`, reinstall — is for people who used
its installer. On the `npx` path there is nothing to upgrade: `@latest` already
resolves fresh each launch.

## The official plugin ships seven skills, not just seven tools

Enumerated from the tarball's `skills/` directory:

| Skill | What it does |
|---|---|
| `plaud-shared` | Auth, error handling, output conventions — the prerequisite read for the others |
| `plaud-browse` | List and paginate recordings |
| `plaud-find` | Find a recording by name keyword, date range, or topic |
| `plaud-read` | Read transcript, summary, notes; get the audio link via `get_file` |
| `plaud-followup` | Generate a follow-up email, thank-you note, action-item list, **SOAP note**, or meeting brief |
| `plaud-digest` | Roll several recordings into a weekly or monthly digest |
| `plaud-export` | Push content to Notion, Slack, HubSpot, Linear, Gmail, or a webhook |

This file previously documented the seven MCP **tools** and stopped there, which
made the official offering look narrower than it is. Reading a tool list is not
reading a package.

**They are only present if `plaud-mcp install` was run** — that is the command
that copies them into `~/.claude/skills/`. Running the MCP server through `npx`
(what this plugin's `.mcp.json` does) starts the server and installs nothing. So
the two distributions are **alternatives, not layers**: a user who installed this
plugin does not get those skills by default.

## Packaging

| | `@plaud-ai/cli` | `@plaud-ai/mcp` |
|---|---|---|
| Latest measured | 0.3.7 (2026-08-05) | 0.3.7 (2026-07-30) |
| First published | 0.1.0 (2026-04-10) | 0.1.0 (2026-04-02) |
| `license` field | **absent** | **absent** |
| `repository` field | absent | absent |
| Binary | `plaud` | — |

Both packages omit `license`, which by default means all rights reserved. This
repo does not redistribute either — `.mcp.json` runs `npx -y @plaud-ai/mcp@latest`,
so the package is fetched from npm by the user's own machine. Nothing of Plaud's
is vendored here.

`@latest` also means the dependency is **unpinned**: Plaud shipped 46 versions in
four months, and any of them reaches users without a release on this side.

---

## What is still unmeasured

- **Rate limits.** Normal use did not trigger throttling. No limit was probed for,
  so "there is no rate limit" is *not* what this says.
- **Token refresh stability.** A `auth:token_refresh` telemetry event exists, so
  refresh is implemented. Whether it holds over weeks needs weeks.
- **`logout` and `update`.** Deliberately not run — one destroys the session under
  test, the other mutates a global install.
- **Whether `plaud audio`'s URL needs auth headers.** It downloaded without any,
  but only the unexpired case was tried.

---

## Telemetry

`@plaud-ai/mcp` sends PostHog analytics by default. `DO_NOT_TRACK=1` disables it.
Transcript content is not sent — the events are usage counters — but the fact of
the default being on is worth knowing.
