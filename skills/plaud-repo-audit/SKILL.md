---
name: plaud-repo-audit
description: |
  Audit THIS repo against Plaud's official CLI and MCP — re-measure the official
  surface, compare it to what `docs/official-surface.md` records, and report what
  changed and what it means for this plugin. Not an audit of your recordings. Use when
  the official packages have been updated, before relying on the surface doc for
  a design decision, when something that used to work stops working, or on a
  periodic check: "audit the official surface", "did Plaud change anything",
  "re-measure the CLI", "官方有沒有改", "重測 Plaud 介面", "surface 文件還準嗎".
  Run it before trusting the doc, not after being surprised by it.
---

# Plaud Repo Audit — re-measure, don't re-assume

**Scope: this repository, measured against Plaud's official packages.** The
subject is our documentation and our skills; Plaud's CLI and MCP are the ruler.
Nothing here reads your recordings.

`docs/official-surface.md` is a snapshot. It says so, and it names the versions it
was taken against. What it cannot do is notice when it stops being true.

This skill is the thing that notices.

## Why the checklist looks like this

Every item below is here because something was missed. This is not a generic
"check for updates" — it is a list of the specific ways this repo has been wrong
about someone else's software.

| What was missed | How | Item that now catches it |
|---|---|---|
| Seven official **skills** shipped in the same npm package | Read the tool list, never opened the tarball | **§2 Package contents** |
| `get_file` returns `presigned_url` → "the MCP cannot reach audio" was false | No tool was *named* `audio`; absence of a name read as absence of a capability | **§4 Response shapes** |
| Recommended `get_file` as a cheap pre-check | Saw the field it needed; never measured the payload (141 KB) | **§4 Response shapes** — sizes, not just fields |
| CLI transcript truncation assumed impossible | Inferred from a missing flag | **§3 Command surface** — flags AND behaviour |
| §4's own measurement could report a stale file's size as this recording's | `plaud transcript` exits 0 when the block is absent, so `&&` guarded nothing | **§4** — `mktemp -d` per run + explicit `[ -s ]` (found 2026-08-07, first real run of this skill) |

**A version number that has not moved is not evidence of nothing changing.**
`.mcp.json` runs `npx -y @plaud-ai/mcp@latest`, so users can be on a build the doc
never saw. Check content, not just the number.

## What this cannot do

It detects changes that **already happened**, in **places we know to look**. The
lesson of the misses above is that the list itself was incomplete — so when you
find a new blind spot, **come back and add it here**. A checklist that never grows
after a surprise is a checklist that learned nothing.

## Steps

### 1. Versions and publish history

```bash
for pkg in @plaud-ai/cli @plaud-ai/mcp; do
  curl -sS "https://registry.npmjs.org/$pkg" | python3 -c "
import json,sys
d=json.load(sys.stdin); lat=d['dist-tags']['latest']; v=d['versions'][lat]
print(f'  {sys.argv[1]:16s} latest {lat}  ({d[\"time\"][lat][:10]})  versions={len(d[\"versions\"])}')
print(f'    license={v.get(\"license\") or \"(absent)\"}  bin={v.get(\"bin\")}')
" "$pkg"
done
```

Compare against the versions named at the top of `docs/official-surface.md`.

### 2. Package contents — open the tarball

**Do not skip this because the version is unchanged.** This is the check that
would have caught the seven skills.

```bash
D=$(mktemp -d) && cd "$D"
curl -sS "$(curl -sS https://registry.npmjs.org/@plaud-ai/mcp \
  | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d["versions"][d["dist-tags"]["latest"]]["dist"]["tarball"])')" -o p.tgz
tar xzf p.tgz
find package -type f ! -path '*/dist/*' | sed 's|package/||' | sort
```

Then read every `SKILL.md` description found, and any `plugin.json` / `.mcp.json`.
A new skill, a renamed one, or a changed description is a change in what the
official plugin *offers* — invisible from the tool list.

### 3. Command and tool surface

```bash
plaud --help                      # the 13 commands, or however many there are now
for c in files file transcript summary search recent today; do
  echo "--- $c ---"; plaud "$c" --help 2>&1 | sed -n '/Options/,$p'
done
```

For the MCP: load the tool schemas and compare names, required params, defaults,
and documented limits against the doc.

Look for three kinds of change, not one:

- **New** command or flag → a capability we may not be using (that is how #20 and
  #23 started)
- **Removed** → something our skills call is about to break
- **Same name, changed default or limit** → the quietest and most dangerous kind

### 4. Response shapes and sizes — needs authorisation

This section cannot run without an authenticated account. **If you skip it, say so
in the report.** A report that silently omits its expensive half reads as a clean
bill of health.

```bash
plaud me >/dev/null 2>&1 || echo "CLI not authenticated — §4 will be partial (plaud login)"
```

For each of `get_transcript`, `get_file`, `list_files`, record:

- **The field layout** — has `next_cursor` moved? is `total` still there? does the
  empty case still return a bare `[]` rather than an object?
- **The size.** Measure it. A field being present says nothing about what it costs
  to obtain — `get_file` carries what you want inside 141 KB, and that fact is the
  difference between a good pre-check and a bad one.

```bash
# Size, without reading the payload into the conversation.
# Fresh directory per run and an explicit non-empty check, because
# `plaud transcript` EXITS 0 when the block does not exist — it prints
# `No "transaction" transcript for this recording. Available: (none).`
# and writes no file. With a shared path and `&&` alone, a leftover file
# from the previous run gets measured and reported as this recording's
# size. An audit that mixes up which recording it measured is worse than
# one that admits it measured nothing.
D=$(mktemp -d)
if plaud transcript "<id>" --block transaction -o "$D/t.txt" && [ -s "$D/t.txt" ]; then
  wc -c < "$D/t.txt"
else
  echo "no transaction block for this recording — check 'plaud file <id>'"
fi
rm -rf "$D"
```

**Pick the recording before measuring.** `plaud file <id>` reports
`transcript: available | unavailable` per recording; a fresh recording commonly
has `audio: available` with `transcript: unavailable`. Measured 2026-08-07: the
30 most recent recordings on this account all had transcripts unavailable, so
"the fetch returned nothing" was an accurate statement about the recording, not
a fault in the CLI. Check availability first and you skip that whole detour.

For MCP tools, call them and note the response size the harness reports.

**Never paste transcript content into the report.** These are other people's
words; this repo's rule is that raw recordings stay local. Record shapes, field
names, and sizes only.

### 5. Report — differences, then consequences

A list of differences is not the deliverable. **What it means for us** is.

```
## Plaud surface audit — <date>

### Versions
  @plaud-ai/cli   0.3.7 → 0.4.1   (doc recorded 0.3.7)
  @plaud-ai/mcp   0.3.7 → 0.3.7   (unchanged — contents still checked, see below)

### Changed
  - `plaud files` gained `--since <date>`
  - skills/: new `plaud-remind`

### Unchanged but re-confirmed
  - `get_transcript` still returns `total`; exhaustion still JSON null
  - empty transcript still a bare `[]`

### Not measured
  - §4 sizes: CLI not authenticated this run

### What it means for us
  - `--since` would let plaud-index do incremental listing without paging the
    whole library (currently an open gap)
  - `plaud-remind` overlaps nothing we ship — no action
```

The "what it means" section is the point. `plaud files` gaining a flag is a fact;
"this closes the incremental-indexing gap" is a decision someone can act on.

### 6. Do not update the doc automatically

Write the report. **Let a human read it before anything touches
`docs/official-surface.md`.**

Auto-updating would silently absorb Plaud's changes into "that's how it has always
been" — and the whole value of that file is being able to see *when* a fact was
established and *what* it was before. A diff nobody read is a diff that taught
nobody anything.

If the differences warrant code changes, file issues (`/idd-issue`) rather than
fixing them inside an audit. Auditing and changing are different jobs; doing both
at once means the audit's own findings never get reviewed.

## Failure modes

| Symptom | Cause |
|---|---|
| `plaud: command not found` | §1, §2 still work (they only need npm). §3, §4 need the CLI |
| `[AUTH_FAILED]` | §4 needs `plaud login` — the CLI holds its own token, separate from the MCP's |
| Tarball fetch fails | npm registry or network; §1 and §2 both depend on it — report as not-measured, not as unchanged |
| Everything matches the doc | Say so explicitly, with the date. "Re-measured, no change" is a useful result and the doc's next reader needs to know it was checked |
