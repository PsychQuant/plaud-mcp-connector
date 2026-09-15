#!/bin/bash
# Plaud batch download - SRT + transcript DOCX only
# With auto re-login on session expiry
# Usage: bash /tmp/plaud_batch_srt_docx.sh /tmp/plaud_files_private.txt "/path/to/output"

FILE_LIST="$1"
OUTPUT_DIR="$2"

TOTAL=$(wc -l < "$FILE_LIST" | tr -d ' ')
FAILED_SRT=""
FAILED_DOCX=""
PLAUD_EMAIL="${PLAUD_EMAIL:?set to the Plaud account email (archived copy: hardcoded value removed)}"

echo "=== Plaud Batch: SRT + DOCX ($TOTAL files) ==="
echo "Output: $OUTPUT_DIR"
echo ""

do_login() {
  echo "  [LOGIN] Session expired, re-logging in..."
  PLAUD_PW=$(security find-generic-password -s "plaud" -a "$PLAUD_EMAIL" -w)
  KEY_SEQ=$(echo "$PLAUD_PW" | sed 's/./&+/g' | sed 's/+$//')

  safari-browser open "https://web.plaud.ai" 2>/dev/null
  sleep 4

  # Accept cookies if present — click accept, then force-remove residue（2026-07-18 恢復）
  safari-browser js "
    var btn = document.querySelector('button[data-cky-tag=\"accept-button\"]') || document.querySelector('.cky-btn-accept');
    if (btn) btn.click();
    var ckys = document.querySelectorAll('.cky-overlay, .cky-consent-container, .cky-preference-center');
    for (var i = 0; i < ckys.length; i++) ckys[i].remove();
  " 2>/dev/null
  sleep 1

  # Clear and fill email
  safari-browser js "
    var el = document.querySelector('input[placeholder=\"Email address\"]');
    if (el) { el.focus(); el.value = ''; el.dispatchEvent(new Event('input', {bubbles: true})); }
  " 2>/dev/null
  sleep 0.5

  safari-browser snapshot 2>/dev/null
  safari-browser click @e2 2>/dev/null
  sleep 0.5
  safari-browser press "Meta+a" 2>/dev/null
  sleep 0.3
  safari-browser press "Backspace" 2>/dev/null
  sleep 0.5
  safari-browser press "$(printf %s "$PLAUD_EMAIL" | sed 's/./&+/g; s/+$//')" 2>/dev/null
  sleep 0.5

  # Clear and fill password
  safari-browser snapshot 2>/dev/null
  safari-browser click @e3 2>/dev/null
  sleep 0.5
  safari-browser press "Meta+a" 2>/dev/null
  sleep 0.3
  safari-browser press "Backspace" 2>/dev/null
  sleep 0.5
  safari-browser press "$KEY_SEQ" 2>/dev/null
  sleep 0.5

  safari-browser press "Enter" 2>/dev/null
  sleep 5

  # Dismiss "Maybe later" popup
  safari-browser js "
    var btns = document.querySelectorAll('button');
    for (var i = 0; i < btns.length; i++) {
      if (btns[i].textContent.trim() === 'Maybe later') { btns[i].click(); break; }
    }
  " 2>/dev/null
  sleep 2
  echo "  [LOGIN] Done"
}

check_login() {
  local url=$(safari-browser js "window.location.href" 2>&1 | tr -d '"')
  if echo "$url" | grep -q "login"; then
    do_login
    return 1
  fi
  return 0
}

navigate_to_file() {
  local hash="$1"
  safari-browser js "performance.clearResourceTimings()" 2>/dev/null
  sleep 0.5
  safari-browser open "https://web.plaud.ai/file/$hash" 2>/dev/null
  sleep 5

  # Check if redirected to login
  check_login
  if [ $? -eq 1 ]; then
    # Re-navigate after login
    safari-browser js "performance.clearResourceTimings()" 2>/dev/null
    sleep 0.5
    safari-browser open "https://web.plaud.ai/file/$hash" 2>/dev/null
    sleep 5
  fi

  # Dismiss cookie overlay — click accept, then force-remove residue（2026-07-18 恢復）
  safari-browser js "
    var btn = document.querySelector('button[data-cky-tag=\"accept-button\"]') || document.querySelector('.cky-btn-accept');
    if (btn) btn.click();
    var ckys = document.querySelectorAll('.cky-overlay, .cky-consent-container, .cky-preference-center');
    for (var i = 0; i < ckys.length; i++) ckys[i].remove();
  " 2>/dev/null
  sleep 1

  # Dismiss popup
  safari-browser js "
    var btns = document.querySelectorAll('button');
    for (var i = 0; i < btns.length; i++) {
      if (btns[i].textContent.trim() === 'Maybe later') { btns[i].click(); break; }
    }
  " 2>/dev/null
  sleep 1
}

MAX_CONSECUTIVE_FAIL=${3:-5}  # Stop after N consecutive both-fail (untranscribed)
CONSEC_FAIL=0

i=0
while IFS= read -r line; do
  [ -z "$line" ] && continue
  i=$((i + 1))
  HASH=$(echo "$line" | cut -d'|' -f1)
  NAME=$(echo "$line" | cut -d'|' -f4-)

  SRT_FILE="$OUTPUT_DIR/${NAME}.srt"
  DOCX_FILE="$OUTPUT_DIR/${NAME}.docx"

  NEED_SRT=false
  NEED_DOCX=false

  if [ ! -f "$SRT_FILE" ] || [ "$(stat -f%z "$SRT_FILE" 2>/dev/null || echo 0)" -lt 100 ]; then
    NEED_SRT=true
  fi
  if [ ! -f "$DOCX_FILE" ] || [ "$(stat -f%z "$DOCX_FILE" 2>/dev/null || echo 0)" -lt 1000 ]; then
    NEED_DOCX=true
  fi

  if [ "$NEED_SRT" = false ] && [ "$NEED_DOCX" = false ]; then
    echo "[$i/$TOTAL] SKIP $NAME"
    continue
  fi

  echo "[$i/$TOTAL] $NAME"

  # Navigate to file (with auto-login)
  navigate_to_file "$HASH"

  # Check if file is untranscribed
  FILE_STATE=$(safari-browser js "var t = document.body.innerText; t.indexOf('Ready to generate') !== -1 ? 'untranscribed' : t.indexOf('Transcription failed') !== -1 ? 'failed' : 'ok'" 2>&1 | grep -v '^\[' | tr -d '"')

  if [ "$FILE_STATE" = "untranscribed" ] || [ "$FILE_STATE" = "failed" ]; then
    echo "  SKIP ($FILE_STATE — not yet transcribed)"
    CONSEC_FAIL=$((CONSEC_FAIL + 1))
    FAILED_SRT="$FAILED_SRT\n  #$i $NAME ($FILE_STATE)"
    FAILED_DOCX="$FAILED_DOCX\n  #$i $NAME ($FILE_STATE)"
    if [ "$CONSEC_FAIL" -ge "$MAX_CONSECUTIVE_FAIL" ]; then
      echo ""
      echo "  >>> $CONSEC_FAIL consecutive untranscribed files — stopping early"
      echo "  >>> Remaining: $((TOTAL - i)) files. Run batch_generate.sh first, then re-run."
      break
    fi
    echo ""
    continue
  fi
  CONSEC_FAIL=0

  # === SRT ===
  if [ "$NEED_SRT" = true ]; then
    S3_URL=$(safari-browser js "
      var entries = performance.getEntriesByType('resource');
      var url = null;
      for (var i = 0; i < entries.length; i++) {
        if (entries[i].name.indexOf('trans_result') !== -1) url = entries[i].name;
      }
      url ? url : 'NOT_FOUND';
    " 2>&1 | tr -d '"')

    # Fallback: use file detail API when Performance API misses trans_result
    if echo "$S3_URL" | grep -q "NOT_FOUND"; then
      echo "  SRT: Performance API miss, trying file detail API..."
      S3_URL=$(safari-browser js "
        var token = JSON.parse(localStorage.getItem('pld_tokenstr'));
        var hash = '$HASH';
        var xhr = new XMLHttpRequest();
        xhr.open('GET', 'https://api-apse1.plaud.ai/file/detail/' + hash, false);
        xhr.setRequestHeader('Authorization', token);
        xhr.send();
        if (xhr.status === 200) {
          var match = xhr.responseText.match(/https[^\"]*trans_result[^\"]*/);
          match ? match[0] : 'NOT_FOUND';
        } else {
          'NOT_FOUND';
        }
      " 2>&1 | tr -d '"')
    fi

    if echo "$S3_URL" | grep -qv "NOT_FOUND"; then
      curl -sL "$S3_URL" | gunzip > /tmp/plaud_trans.json 2>/dev/null
      python3 -c "
import json, sys
with open('/tmp/plaud_trans.json') as f:
    data = json.load(f)
def ms_to_srt(ms):
    h = ms // 3600000
    m = (ms % 3600000) // 60000
    s = (ms % 60000) // 1000
    r = ms % 1000
    return f'{h:02d}:{m:02d}:{s:02d},{r:03d}'
srt = []
for i, item in enumerate(data, 1):
    start = ms_to_srt(item['start_time'])
    end = ms_to_srt(item['end_time'])
    speaker = item.get('speaker', '')
    content = item['content']
    srt.extend([str(i), f'{start} --> {end}', f'[{speaker}] {content}', ''])
with open(sys.argv[1], 'w', encoding='utf-8') as f:
    f.write('\n'.join(srt))
print(f'  SRT: OK ({len(data)} segs)')
" "$SRT_FILE" 2>/dev/null
      if [ $? -ne 0 ]; then
        echo "  SRT: FAILED (parse error)"
        FAILED_SRT="$FAILED_SRT\n  #$i $NAME"
        rm -f "$SRT_FILE"
      fi
    else
      echo "  SRT: FAILED (no URL)"
      FAILED_SRT="$FAILED_SRT\n  #$i $NAME"
    fi
  else
    echo "  SRT: SKIP"
  fi

  # === Transcript DOCX ===
  if [ "$NEED_DOCX" = true ]; then
    safari-browser js "performance.clearResourceTimings()" 2>/dev/null

    # Open export dropdown via share-button trigger
    safari-browser js "
      var trigger = document.querySelector('[data-testid=\"share-button\"]');
      if (trigger) trigger.click();
    " 2>/dev/null
    sleep 1

    # Click Export transcript
    safari-browser js "
      var items = document.querySelectorAll('[role=menuitem]');
      for (var i = 0; i < items.length; i++) {
        if (items[i].textContent.trim() === 'Export transcript') { items[i].click(); break; }
      }
    " 2>/dev/null
    sleep 2

    # Select DOCX format
    safari-browser js "
      var sel = document.querySelector('.el-select__wrapper');
      if (sel) sel.click();
    " 2>/dev/null
    sleep 1

    safari-browser js "
      var items = document.querySelectorAll('.el-select-dropdown__item');
      for (var i = 0; i < items.length; i++) {
        if (items[i].textContent.trim() === 'DOCX') { items[i].click(); break; }
      }
    " 2>/dev/null
    sleep 1

    # Click Export button
    safari-browser js "
      var btns = document.querySelectorAll('button');
      for (var i = 0; i < btns.length; i++) {
        if (btns[i].textContent.trim() === 'Export') { btns[i].click(); break; }
      }
    " 2>/dev/null
    sleep 5

    DOCX_URL=$(safari-browser js "
      var entries = performance.getEntriesByType('resource');
      var url = null;
      for (var i = 0; i < entries.length; i++) {
        var name = entries[i].name;
        if ((name.indexOf('trans-') !== -1 || name.indexOf('document-download') !== -1) && name.indexOf('.docx') !== -1) {
          url = name;
        }
      }
      url ? url : 'NOT_FOUND';
    " 2>&1 | tr -d '"')

    if echo "$DOCX_URL" | grep -qv "NOT_FOUND"; then
      curl -sL "$DOCX_URL" -o "$DOCX_FILE"
      SIZE=$(stat -f%z "$DOCX_FILE" 2>/dev/null)
      if [ "$SIZE" -gt 1000 ]; then
        echo "  DOCX: OK (${SIZE}B)"
      else
        echo "  DOCX: FAILED (too small: ${SIZE}B)"
        FAILED_DOCX="$FAILED_DOCX\n  #$i $NAME"
        rm -f "$DOCX_FILE"
      fi
    else
      echo "  DOCX: FAILED (no URL)"
      FAILED_DOCX="$FAILED_DOCX\n  #$i $NAME"
    fi
  else
    echo "  DOCX: SKIP"
  fi

  echo ""
done < "$FILE_LIST"

echo "=== SUMMARY ==="
SRT_COUNT=$(ls "$OUTPUT_DIR"/*.srt 2>/dev/null | wc -l | tr -d ' ')
DOCX_COUNT=$(ls "$OUTPUT_DIR"/*.docx 2>/dev/null | grep -v '_notes' | wc -l | tr -d ' ')
echo "SRT: $SRT_COUNT / $TOTAL"
echo "DOCX: $DOCX_COUNT / $TOTAL"
if [ -n "$FAILED_SRT" ]; then
  echo ""
  echo "Failed SRT:"
  echo -e "$FAILED_SRT"
fi
if [ -n "$FAILED_DOCX" ]; then
  echo ""
  echo "Failed DOCX:"
  echo -e "$FAILED_DOCX"
fi
if [ -z "$FAILED_SRT" ] && [ -z "$FAILED_DOCX" ]; then
  echo "ALL SUCCESSFUL!"
fi
