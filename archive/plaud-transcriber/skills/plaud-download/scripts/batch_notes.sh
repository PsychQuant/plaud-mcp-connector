#!/bin/bash
# Plaud batch download - Notes MD + DOCX
# With auto re-login, proper wait times, and span-click approach
# Usage: bash /tmp/plaud_batch_notes.sh /tmp/plaud_files_private.txt "/path/to/output"

FILE_LIST="$1"
OUTPUT_DIR="$2"

TOTAL=$(wc -l < "$FILE_LIST" | tr -d ' ')
FAILED_MD=""
FAILED_DOCX=""
PLAUD_EMAIL="${PLAUD_EMAIL:?set to the Plaud account email (archived copy: hardcoded value removed)}"

echo "=== Plaud Batch: Notes MD+DOCX ($TOTAL files) ==="
echo "Output: $OUTPUT_DIR"
echo ""

do_login() {
  echo "  [LOGIN] Re-logging in..."
  PLAUD_PW=$(security find-generic-password -s "plaud" -a "$PLAUD_EMAIL" -w)
  KEY_SEQ=$(echo "$PLAUD_PW" | sed 's/./&+/g' | sed 's/+$//')

  safari-browser open "https://web.plaud.ai" 2>/dev/null
  sleep 4
  # CookieYes overlay — click accept, then force-remove residue（2026-07-18 恢復）
  safari-browser js "
    var btn = document.querySelector('button[data-cky-tag=\"accept-button\"]') || document.querySelector('.cky-btn-accept');
    if (btn) btn.click();
    var ckys = document.querySelectorAll('.cky-overlay, .cky-consent-container, .cky-preference-center');
    for (var i = 0; i < ckys.length; i++) ckys[i].remove();
  " 2>/dev/null
  sleep 1
  safari-browser snapshot 2>/dev/null
  safari-browser click @e2 2>/dev/null; sleep 0.5
  safari-browser press "Meta+a" 2>/dev/null; sleep 0.3
  safari-browser press "Backspace" 2>/dev/null; sleep 0.5
  safari-browser press "$(printf %s "$PLAUD_EMAIL" | sed 's/./&+/g; s/+$//')" 2>/dev/null; sleep 0.5
  safari-browser snapshot 2>/dev/null
  safari-browser click @e3 2>/dev/null; sleep 0.5
  safari-browser press "Meta+a" 2>/dev/null; sleep 0.3
  safari-browser press "Backspace" 2>/dev/null; sleep 0.5
  safari-browser press "$KEY_SEQ" 2>/dev/null; sleep 0.5
  safari-browser press "Enter" 2>/dev/null
  sleep 6
  safari-browser js "var btns = document.querySelectorAll('button'); for (var i = 0; i < btns.length; i++) { if (btns[i].textContent.trim() === 'Maybe later') { btns[i].click(); break; } }" 2>/dev/null
  sleep 2
  echo "  [LOGIN] Done"
}

navigate_to_file() {
  local hash="$1"
  safari-browser js "performance.clearResourceTimings()" 2>/dev/null
  sleep 0.5
  safari-browser open "https://web.plaud.ai/file/$hash" 2>/dev/null
  sleep 6

  # Check login
  local url=$(safari-browser js "window.location.href" 2>&1 | tr -d '"')
  if echo "$url" | grep -q "login"; then
    do_login
    safari-browser js "performance.clearResourceTimings()" 2>/dev/null
    sleep 0.5
    safari-browser open "https://web.plaud.ai/file/$hash" 2>/dev/null
    sleep 6
  fi

  # Dismiss cookie overlay
  # CookieYes overlay — click accept, then force-remove residue（2026-07-18 恢復）
  safari-browser js "
    var btn = document.querySelector('button[data-cky-tag=\"accept-button\"]') || document.querySelector('.cky-btn-accept');
    if (btn) btn.click();
    var ckys = document.querySelectorAll('.cky-overlay, .cky-consent-container, .cky-preference-center');
    for (var i = 0; i < ckys.length; i++) ckys[i].remove();
  " 2>/dev/null
  sleep 1

  # Dismiss popup
  safari-browser js "var btns = document.querySelectorAll('button'); for (var i = 0; i < btns.length; i++) { if (btns[i].textContent.trim() === 'Maybe later') { btns[i].click(); break; } }" 2>/dev/null
  sleep 1
}

install_interceptor() {
  safari-browser js "
    window.__capturedExports = {};
    var origCreate = URL.createObjectURL;
    URL.createObjectURL = function(blob) {
      var url = origCreate.call(URL, blob);
      var reader = new FileReader();
      reader.onload = function() {
        window.__capturedExports[blob.type] = {content: reader.result, size: blob.size, type: blob.type};
      };
      reader.readAsText(blob);
      return url;
    };
    'ok';
  " 2>/dev/null
}

click_summary_tab() {
  # Use snapshot + click ref which is more reliable than JS querySelectorAll
  # i18n: 英文 "Summary" / 繁中「摘要」/ 简中「摘要」
  safari-browser js "
    var SUMMARY_LABELS = ['Summary', '摘要'];
    var btns = document.querySelectorAll('button, span.tab-text, [role=tab]');
    for (var i = 0; i < btns.length; i++) {
      var t = btns[i].textContent.trim();
      if (SUMMARY_LABELS.indexOf(t) !== -1) {
        btns[i].click();
        break;
      }
    }
  " 2>/dev/null
  sleep 2
}

open_export_notes() {
  # Step 1: Open export dropdown via share-button trigger
  safari-browser js "
    var trigger = document.querySelector('[data-testid=\"share-button\"]');
    if (trigger) trigger.click();
  " 2>/dev/null
  sleep 1

  # Step 2: Expand "Export notes" / 「匯出筆記」/「导出笔记」submenu
  # Also try .el-dropdown-menu__item (中文版 file actions menu uses this class instead of [role=menuitem])
  safari-browser js "
    var EXPORT_NOTES = ['Export notes', '匯出筆記', '导出笔记'];
    var items = document.querySelectorAll('[role=menuitem], .el-dropdown-menu__item');
    for (var i = 0; i < items.length; i++) {
      var t = items[i].textContent.trim();
      if (EXPORT_NOTES.indexOf(t) !== -1 && items[i].offsetParent !== null) {
        var btn = items[i].querySelector('button');
        var target = btn || items[i];
        target.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
        break;
      }
    }
  " 2>/dev/null
  sleep 1

  # Step 3: Click "Export Summary" sub-item (英文 sub-menu);中文版可能直接 fire export 不需 sub-step
  local result=$(safari-browser js "
    var EXPORT_SUMMARY = [
      'Export Summary', 'Export Teaching Note', 'Export Class Note',
      'Export Meeting Note', 'Export Voice Note',
      '匯出摘要', '匯出教學筆記', '匯出會議筆記',
      '导出摘要', '导出教学笔记', '导出会议笔记'
    ];
    var items = document.querySelectorAll('[role=menuitem], .el-dropdown-menu__item');
    var clicked = false;
    for (var i = 0; i < items.length; i++) {
      var text = items[i].textContent.trim();
      if (EXPORT_SUMMARY.indexOf(text) !== -1 && items[i].offsetParent !== null) {
        items[i].dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
        clicked = text;
        break;
      }
    }
    // 中文版可能沒 sub-menu — 若 dialog/format selector 直接出現,視為 already-clicked-in-step-2
    if (!clicked) {
      var hasFormatSelector = document.querySelector('.el-select__wrapper');
      if (hasFormatSelector && hasFormatSelector.offsetParent !== null) clicked = 'NO_SUBMENU_BUT_FORMAT_READY';
    }
    clicked ? clicked : 'NOT_FOUND';
  " 2>&1 | tr -d '"')

  echo "$result"
}

export_as_format() {
  local fmt="$1"  # "Markdown" or "DOCX"

  # Click format selector
  safari-browser js "var sel = document.querySelector('.el-select__wrapper'); if (sel) sel.click();" 2>/dev/null
  sleep 1

  # Select format
  safari-browser js "
    var items = document.querySelectorAll('.el-select-dropdown__item');
    for (var i = 0; i < items.length; i++) {
      if (items[i].textContent.trim() === '$fmt') { items[i].click(); break; }
    }
  " 2>/dev/null
  sleep 1

  # Click Export / 「匯出」/「导出」 button
  safari-browser js "
    var EXPORT_LABELS = ['Export', '匯出', '导出'];
    var btns = document.querySelectorAll('button');
    for (var i = 0; i < btns.length; i++) {
      var t = btns[i].textContent.trim();
      if (EXPORT_LABELS.indexOf(t) !== -1 && btns[i].offsetParent !== null) {
        btns[i].click();
        break;
      }
    }
  " 2>/dev/null
}

MAX_CONSECUTIVE_FAIL=${3:-5}  # Stop after N consecutive both-fail (untranscribed)
CONSEC_FAIL=0

i=0
while IFS= read -r line; do
  [ -z "$line" ] && continue
  i=$((i + 1))
  HASH=$(echo "$line" | cut -d'|' -f1)
  NAME=$(echo "$line" | cut -d'|' -f4-)

  MD_FILE="$OUTPUT_DIR/${NAME}_notes.md"
  DOCX_FILE="$OUTPUT_DIR/${NAME}_notes.docx"

  NEED_MD=false
  NEED_DOCX=false

  if [ ! -f "$MD_FILE" ] || [ "$(stat -f%z "$MD_FILE" 2>/dev/null || echo 0)" -lt 100 ]; then
    NEED_MD=true
  fi
  if [ ! -f "$DOCX_FILE" ] || [ "$(stat -f%z "$DOCX_FILE" 2>/dev/null || echo 0)" -lt 1000 ]; then
    NEED_DOCX=true
  fi

  if [ "$NEED_MD" = false ] && [ "$NEED_DOCX" = false ]; then
    echo "[$i/$TOTAL] SKIP $NAME"
    continue
  fi

  echo "[$i/$TOTAL] $NAME"

  # Navigate and setup
  navigate_to_file "$HASH"

  # Check if file is untranscribed
  FILE_STATE=$(safari-browser js "var t = document.body.innerText; t.indexOf('Ready to generate') !== -1 ? 'untranscribed' : t.indexOf('Transcription failed') !== -1 ? 'failed' : 'ok'" 2>&1 | grep -v '^\[' | tr -d '"')

  if [ "$FILE_STATE" = "untranscribed" ] || [ "$FILE_STATE" = "failed" ]; then
    echo "  SKIP ($FILE_STATE — not yet transcribed)"
    CONSEC_FAIL=$((CONSEC_FAIL + 1))
    FAILED_MD="$FAILED_MD\n  #$i $NAME ($FILE_STATE)"
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

  install_interceptor
  click_summary_tab

  # === Notes MD ===
  if [ "$NEED_MD" = true ]; then
    EXPORT_RESULT=$(open_export_notes)
    if echo "$EXPORT_RESULT" | grep -q "NOT_FOUND"; then
      echo "  Notes MD: FAILED (no export btn)"
      FAILED_MD="$FAILED_MD\n  #$i $NAME"
    else
      sleep 2
      # Reset captured exports before exporting
      safari-browser js "window.__capturedExports = {};" 2>/dev/null
      export_as_format "Markdown"
      sleep 4

      # Extract MD content
      MD_RESULT=$(safari-browser js "
        var exp = window.__capturedExports;
        var keys = Object.keys(exp);
        keys.length > 0 ? exp[keys[0]].content : 'NOT_FOUND';
      " 2>&1)

      if echo "$MD_RESULT" | grep -qv "NOT_FOUND"; then
        echo "$MD_RESULT" | python3 -c "
import sys, json
raw = sys.stdin.read().strip()
if raw.startswith('\"') and raw.endswith('\"'):
    content = json.loads(raw, strict=False)
else:
    content = raw
with open(sys.argv[1], 'w') as f:
    f.write(content)
import os
print(f'  Notes MD: OK ({os.path.getsize(sys.argv[1])}B)')
" "$MD_FILE" 2>/dev/null
        if [ $? -ne 0 ]; then
          echo "  Notes MD: FAILED (parse error)"
          FAILED_MD="$FAILED_MD\n  #$i $NAME"
        fi
      else
        echo "  Notes MD: FAILED (no blob)"
        FAILED_MD="$FAILED_MD\n  #$i $NAME"
      fi
    fi
  else
    echo "  Notes MD: SKIP"
  fi

  # === Notes DOCX ===
  if [ "$NEED_DOCX" = true ]; then
    safari-browser js "performance.clearResourceTimings()" 2>/dev/null

    EXPORT_RESULT=$(open_export_notes)
    if echo "$EXPORT_RESULT" | grep -q "NOT_FOUND"; then
      echo "  Notes DOCX: FAILED (no export btn)"
      FAILED_DOCX="$FAILED_DOCX\n  #$i $NAME"
    else
      sleep 2
      export_as_format "DOCX"
      sleep 5

      DOCX_URL=$(safari-browser js "
        var entries = performance.getEntriesByType('resource');
        var url = null;
        for (var i = 0; i < entries.length; i++) {
          var name = entries[i].name;
          if ((name.indexOf('summary-') !== -1 || name.indexOf('document-download') !== -1) && name.indexOf('.docx') !== -1) {
            url = name;
          }
        }
        url ? url : 'NOT_FOUND';
      " 2>&1 | tr -d '"')

      if echo "$DOCX_URL" | grep -qv "NOT_FOUND"; then
        curl -sL "$DOCX_URL" -o "$DOCX_FILE"
        SIZE=$(stat -f%z "$DOCX_FILE" 2>/dev/null)
        if [ "$SIZE" -gt 1000 ]; then
          echo "  Notes DOCX: OK (${SIZE}B)"
        else
          echo "  Notes DOCX: FAILED (too small: ${SIZE}B)"
          FAILED_DOCX="$FAILED_DOCX\n  #$i $NAME"
          rm -f "$DOCX_FILE"
        fi
      else
        echo "  Notes DOCX: FAILED (no URL)"
        FAILED_DOCX="$FAILED_DOCX\n  #$i $NAME"
      fi
    fi
  else
    echo "  Notes DOCX: SKIP"
  fi

  echo ""
done < "$FILE_LIST"

echo "=== SUMMARY ==="
MD_COUNT=$(ls "$OUTPUT_DIR"/*_notes.md 2>/dev/null | wc -l | tr -d ' ')
DOCX_COUNT=$(ls "$OUTPUT_DIR"/*_notes.docx 2>/dev/null | wc -l | tr -d ' ')
echo "Notes MD: $MD_COUNT / $TOTAL"
echo "Notes DOCX: $DOCX_COUNT / $TOTAL"
if [ -n "$FAILED_MD" ]; then
  echo ""
  echo "Failed Notes MD:"
  echo -e "$FAILED_MD"
fi
if [ -n "$FAILED_DOCX" ]; then
  echo ""
  echo "Failed Notes DOCX:"
  echo -e "$FAILED_DOCX"
fi
if [ -z "$FAILED_MD" ] && [ -z "$FAILED_DOCX" ]; then
  echo "ALL SUCCESSFUL!"
fi
