---
name: plaud-upload
description: |
  Upload an audio or video file into your own Plaud library and start
  transcription. Use when the user says "upload to Plaud", "上傳到 Plaud",
  "transcribe this recording", "把這個音檔丟去 Plaud 轉錄", or hands over a local
  audio/video file to be transcribed. Both halves of the official Plaud surface
  — the MCP and the CLI — are read-only, so this skill is what covers writing
  into a library. It uploads and hands off: the transcript reaches your machine
  later, when `plaud-index` fetches it into the local cache that `plaud-grep`
  and `plaud-srt` read. macOS only (drives Safari).
  Also triggers in the languages Plaud localises for (its own hreflang list):
  "zu Plaud hochladen", "subir a Plaud", "téléverser vers Plaud", "Plaudにアップロード", "carica su Plaud", "uploaden naar Plaud", "enviar para o Plaud", "tải lên Plaud", "อัปโหลดไปยัง Plaud", "muat naik ke Plaud", "رفع إلى بلود".
argument-hint: "<file_path_or_glob>"
---

# Plaud Upload

Uploads a local audio/video file to `web.plaud.ai` and starts transcription.

## Why this skill exists

The official Plaud MCP exposes seven tools — `login`, `logout`,
`get_current_user`, `list_files`, `get_file`, `get_note`, `get_transcript` — and
the official CLI mirrors them. **All read.** Plaud's REST API does have upload
endpoints, but they live on the Plaud Embedded / partner surface, authenticated
with partner tokens, and are meant for audio your own application collects — not
for pushing a file into an existing consumer account's library.

So getting a file *into* your own library still means driving the web app.

## Requirements

- **macOS only.** This drives Safari through AppleScript; there is no Linux or
  Windows path.
- `safari-browser` CLI installed and Safari's *Allow JavaScript from Apple
  Events* enabled.
- `ffmpeg` on PATH for format conversion.
- You are already signed in to `web.plaud.ai` in Safari.

If credentials are needed, read them from the macOS Keychain — **never** hardcode
an address or password in a file:

```bash
security find-generic-password -s "plaud" -a "<your-account>" -w
```

## Known root cause — read before debugging a blank page

If `web.plaud.ai` shows a white screen or resources fail to load, check
**Safari → Settings → Privacy → Prevent cross-site tracking** first. That setting
is the confirmed cause (2026-07-18). Do **not** reflexively clear the Cache API or
unregister the service worker — that is not the root cause, and both are
persistent state changes that need the user's consent first.

## File limits

- Formats: MP3, MP4, WAV, AAC, OGG, M4A and other common containers
- Max 500 MB and 5 hours per file

Convert video to audio before uploading — Plaud only needs the audio track, and
an mp3 is 10–20× smaller:

```bash
ffmpeg -y -i "input.mp4" -vn -acodec libmp3lame -q:a 2 "output.mp3"
```

Check duration first, and trim anything over 5 hours:

```bash
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "file.mp3"
ffmpeg -y -i "input.mp3" -t 17940 -c copy "input_trimmed.mp3"
```

> **Git LFS trap**: if an `.mp4` is ~130 bytes, it is an LFS pointer, not a video.
> `head -1 file.mp4` showing `version https://git-lfs.github.com/spec/v1` confirms
> it. Fetch the real file before converting.

## Steps

### Step 0 — open Plaud and clear the cookie banner

The consent banner intercepts every later click, so clear it first.

```bash
if ! safari-browser documents 2>/dev/null | grep -q "web.plaud.ai"; then
  safari-browser open "https://web.plaud.ai"
  sleep 3
fi
safari-browser get url --url plaud
# URL contains /login → ask the user to sign in manually, then continue.

safari-browser js "var b=document.querySelector('.cky-btn-accept'); if(b)b.click(); else document.querySelectorAll('[class*=cky]').forEach(e=>e.remove()); 'done'" --url plaud
```

Prefer clicking the banner's own accept button; only force-remove if that fails.

Every command carries `--url plaud` so it targets the Plaud document regardless of
which window or tab has focus.

### Step 1 — open the import dialog

```bash
safari-browser js "document.querySelector('.recording-button').click(); 'clicked'" --url plaud
sleep 2
safari-browser js "
  var items = document.querySelectorAll('.menu-item');
  for (var i = 0; i < items.length; i++) {
    var t = items[i].textContent.trim();
    if (t === '匯入音訊' || t === 'Import Audio') { items[i].click(); break; }
  }
  'clicked';
" --url plaud
sleep 1
```

### Step 2 — upload

```bash
safari-browser upload --native "input[type='file']" "<absolute file path>" --url plaud
sleep 30   # a 130 MB file takes roughly 30–60s
```

**Do not switch apps or type during the upload.** The native path briefly raises
the window and drives the file dialog with simulated keystrokes.

**Always `--native`, never `--js`, for real files.** The `--js` path base64-chunks
the file and injects it via `DataTransfer`; string concatenation is O(n²) in V8, so
a 131 MB file balloons to ~500 MB of transient memory and crashes Safari (observed
failing at chunk 500/913, surfacing as AppleScript error -609). `safari-browser`
enforces a 10 MB hard cap on `--js` for this reason.

**Multiple Plaud tabs** make `--url plaud` ambiguous and it fails closed, listing
the matches. Narrow the substring, e.g. `--url "plaud.ai/file/abc"`.

### Step 2 fallback — in-page injection (small files only)

If both `--native` and `--js` fail — on some macOS builds the native file dialog
opens and receives keystrokes but the file never lands — use the bundled
fallback, which constructs a `File` inside the page and feeds the dropzone:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/skills/plaud-upload/scripts/inject_upload.sh" "<file>"
```

Limited to roughly 3 MB: the base64 payload is embedded in the injected
JavaScript. Voice memos yes, lecture recordings no.

### Step 3 — verify

Do not trust `input.files.length` afterwards — Plaud's dropzone reads the files
out of the event and resets the input, so it reads `0` even on success. **The
real check is the modal showing the filename with a success marker.** Confirm
visually, then confirm the recording appears in the library.

After it appears, `plaud-index` can pull its transcript once processing finishes.

## Honesty about this path

This is browser automation against a UI that its vendor can change without
notice. It works today; a redesign of the import dialog will break the selectors
above. When it breaks, the failure is usually visible (a click does nothing)
rather than silent — but verify the upload landed rather than assuming it did.
