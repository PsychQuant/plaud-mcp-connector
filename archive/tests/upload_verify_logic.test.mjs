// Regression test for the upload-result verification logic in
// skills/plaud-upload/scripts/inject_upload.sh (issue #13).
//
// It extracts the JS *from the shell script itself* — not a copy — so the test
// cannot silently drift from what actually ships. If someone edits the heredoc,
// this test runs the edited version.
//
// Run directly:  node tests/upload_verify_logic.test.mjs
// Or via the Python suite: tests/test_upload_verify_logic.py wraps this.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { runInNewContext } from 'node:vm';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..');
const SCRIPT = join(REPO, 'skills/plaud-upload/scripts/inject_upload.sh');

// Pull the body of the `cat > "$VJS" <<EOF ... EOF` heredoc.
function extractVerifyTemplate() {
  const lines = readFileSync(SCRIPT, 'utf8').split('\n');
  const start = lines.findIndex((l) => l === 'cat > "$VJS" <<EOF');
  if (start === -1) throw new Error('verification heredoc not found in ' + SCRIPT);
  const rest = lines.slice(start + 1);
  const end = rest.findIndex((l) => l === 'EOF');
  if (end === -1) throw new Error('unterminated verification heredoc');
  return rest.slice(0, end).join('\n');
}

const TEMPLATE = extractVerifyTemplate();

// Mimic what bash does to the heredoc: expand ${STEM_JS}, which js_str() produced
// with python json.dumps — i.e. a fully escaped JS string literal.
//
// Evaluated with node:vm in a fresh context whose only global is a stubbed `document`.
// (Not `new Function` — that pattern invites interpolating untrusted input into a
// function body. Here both inputs are trusted, but vm states the intent plainly and
// keeps the snippet from touching this process's globals.)
function run(stem, bodyText) {
  const src = TEMPLATE.replace('${STEM_JS}', JSON.stringify(stem));
  return runInNewContext(src, { document: { body: { innerText: bodyText } } });
}

// ★ marks the scenarios an adversarial review flagged as the ones that actually matter.
const CASES = [
  ['zh success', 'meeting-01', 'meeting-01 上傳成功', 'OK'],
  // ★ The blocking regression this test exists for: Plaud's duplicate dialog may be
  // generic template text with no filename. Gating DUPLICATE behind "filename appeared"
  // downgrades a real duplicate to UNKNOWN — the user loses the "pick cancel or continue"
  // warning and is more likely to import a duplicate by mistake.
  ['★ zh duplicate, dialog omits filename', 'meeting-01', '重複檔案\n此檔案已存在，是否繼續匯入？', 'DUPLICATE'],
  ['zh duplicate, dialog includes filename', 'meeting-01', 'meeting-01 重複，檔案已存在', 'DUPLICATE'],
  // ★ A failure toast also contains the filename. Without a FAILED branch it falls
  // through to SHOWN — reporting "injected, awaiting success" for an upload that failed.
  ['★ zh failure toast', 'meeting-01', 'meeting-01 上傳失敗：格式不支援', 'FAILED'],
  ['en success', 'meeting-01', 'meeting-01 uploaded successfully', 'OK'],
  ['★ en duplicate, dialog omits filename', 'meeting-01', 'This file already exists. Continue?', 'DUPLICATE'],
  ['en failure', 'meeting-01', 'meeting-01 upload failed', 'FAILED'],
  ['filename shown, no verdict yet', 'meeting-01', 'meeting-01', 'SHOWN'],
  ['filename absent', 'meeting-01', 'Plaud 首頁 我的錄音', 'UNKNOWN'],
  // ★ Apostrophes are common in real filenames and used to break the injected JS.
  ["★ apostrophe in filename", "Mom's mtg", "Mom's mtg 上傳成功", 'OK'],
  ['double quote in filename', 'say "hi"', 'say "hi" 上傳成功', 'OK'],
  ['backslash in filename', 'a\\b', 'a\\b 上傳成功', 'OK'],
  ['empty stem never matches', '', 'anything at all', 'UNKNOWN'],
];

let failed = 0;
for (const [desc, stem, body, want] of CASES) {
  let got;
  try {
    got = run(stem, body);
  } catch (err) {
    got = `THREW: ${err.message}`;
  }
  const ok = got === want;
  if (!ok) failed++;
  console.log(`${ok ? 'ok  ' : 'FAIL'}  ${desc} → ${got}${ok ? '' : ` (expected ${want})`}`);
}

console.log(`\n${CASES.length - failed}/${CASES.length} passed`);
process.exit(failed ? 1 : 0);
