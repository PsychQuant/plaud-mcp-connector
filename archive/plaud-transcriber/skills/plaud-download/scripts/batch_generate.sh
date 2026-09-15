#!/bin/bash
# Plaud batch generate - trigger transcription for untranscribed files
# Usage: bash /tmp/plaud_batch_generate.sh /tmp/plaud_files_untranscribed.txt

FILE_LIST="$1"

TOTAL=$(wc -l < "$FILE_LIST" | tr -d ' ')
PLAUD_EMAIL="${PLAUD_EMAIL:-}"   # archived copy: hardcoded value removed; required only when do_login runs
SUCCESS=0
FAIL=0

echo "=== Plaud Batch Generate ($TOTAL files) ==="
echo ""

do_login() {
  : "${PLAUD_EMAIL:?set PLAUD_EMAIL to the Plaud account e-mail before a login is needed}"
  echo "  [LOGIN] Re-logging in..."
  PLAUD_PW=$(security find-generic-password -s "plaud" -a "$PLAUD_EMAIL" -w)
  KEY_SEQ=$(echo "$PLAUD_PW" | sed 's/./&+/g' | sed 's/+$//')

  safari-browser open "https://web.plaud.ai" 2>/dev/null
  sleep 4
  safari-browser js "var btn = document.querySelector('button[data-cky-tag=\"accept-button\"]'); if (btn) btn.click();" 2>/dev/null
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

i=0
while IFS= read -r line; do
  [ -z "$line" ] && continue
  i=$((i + 1))
  HASH=$(echo "$line" | cut -d'|' -f1)
  NAME=$(echo "$line" | cut -d'|' -f4-)

  echo "[$i/$TOTAL] $NAME"

  # Navigate to file
  safari-browser open "https://web.plaud.ai/file/$HASH" 2>/dev/null
  sleep 5

  # Check login
  URL=$(safari-browser js "window.location.href" 2>&1 | grep -v '^\[' | tr -d '"')
  if echo "$URL" | grep -q "login"; then
    do_login
    safari-browser open "https://web.plaud.ai/file/$HASH" 2>/dev/null
    sleep 5
  fi

  # Dismiss popup
  safari-browser js "var btns = document.querySelectorAll('button'); for (var i = 0; i < btns.length; i++) { if (btns[i].textContent.trim() === 'Maybe later') { btns[i].click(); break; } }" 2>/dev/null
  sleep 1

  # Check if already generating or generated
  STATE=$(safari-browser js "var text = document.body.innerText; text.indexOf('Ready to generate') !== -1 ? 'ready' : text.indexOf('Transcription failed') !== -1 ? 'failed' : text.indexOf('Generating') !== -1 ? 'already_generating' : 'generated'" 2>&1 | grep -v '^\[' | tr -d '"')

  if [ "$STATE" = "already_generating" ]; then
    echo "  SKIP (already generating)"
    SUCCESS=$((SUCCESS + 1))
    continue
  fi

  if [ "$STATE" = "generated" ]; then
    echo "  SKIP (already generated)"
    SUCCESS=$((SUCCESS + 1))
    continue
  fi

  if [ "$STATE" = "failed" ]; then
    # Try clicking "Try Again"
    safari-browser js "
      var els = document.querySelectorAll('*');
      for (var i = 0; i < els.length; i++) {
        if (els[i].textContent.trim() === 'Try Again') { els[i].click(); break; }
      }
    " 2>/dev/null
    sleep 3

    # Check if dialog appeared
    DIALOG=$(safari-browser js "var text = document.body.innerText; text.indexOf('Generate now') !== -1 ? 'dialog' : 'no_dialog'" 2>&1 | grep -v '^\[' | tr -d '"')

    if [ "$DIALOG" = "dialog" ]; then
      safari-browser js "
        var els = document.querySelectorAll('*');
        for (var i = 0; i < els.length; i++) {
          if (els[i].textContent.trim() === 'Generate now') { els[i].click(); break; }
        }
      " 2>/dev/null
      sleep 3
      echo "  TRIGGERED (retry after failure)"
      SUCCESS=$((SUCCESS + 1))
    else
      echo "  FAILED (could not retry)"
      FAIL=$((FAIL + 1))
    fi
    continue
  fi

  # STATE = "ready" — click Generate
  safari-browser js "var el = document.querySelector('.liner-btn-txt'); if (el) el.click();" 2>/dev/null
  sleep 3

  # Check for daily limit
  LIMIT_HIT=$(safari-browser js "var text = document.body.innerText; text.indexOf('daily limit') !== -1 ? 'limit' : 'ok'" 2>&1 | grep -v '^\[' | tr -d '"')

  if [ "$LIMIT_HIT" = "limit" ]; then
    # Dismiss dialog and stop
    safari-browser js "var btns = document.querySelectorAll('button'); for (var j = 0; j < btns.length; j++) { if (btns[j].textContent.trim() === 'Got it') { btns[j].click(); break; } }" 2>/dev/null
    echo "  DAILY LIMIT REACHED — stopping batch"
    FAIL=$((FAIL + 1))
    LIMIT_REMAINING=$((TOTAL - i))
    break
  fi

  # Click "Generate now" in dialog
  safari-browser js "var els = document.querySelectorAll('*'); for (var j = 0; j < els.length; j++) { if (els[j].textContent.trim() === 'Generate now') { els[j].click(); break; } }" 2>/dev/null
  sleep 3

  # Verify
  RESULT=$(safari-browser js "var text = document.body.innerText; text.indexOf('Generating') !== -1 ? 'ok' : text.indexOf('daily limit') !== -1 ? 'limit' : 'fail'" 2>&1 | grep -v '^\[' | tr -d '"')

  if [ "$RESULT" = "ok" ]; then
    echo "  TRIGGERED"
    SUCCESS=$((SUCCESS + 1))
  elif [ "$RESULT" = "limit" ]; then
    safari-browser js "var btns = document.querySelectorAll('button'); for (var j = 0; j < btns.length; j++) { if (btns[j].textContent.trim() === 'Got it') { btns[j].click(); break; } }" 2>/dev/null
    echo "  DAILY LIMIT REACHED — stopping batch"
    FAIL=$((FAIL + 1))
    LIMIT_REMAINING=$((TOTAL - i))
    break
  else
    echo "  FAILED (unknown state after trigger)"
    FAIL=$((FAIL + 1))
  fi

done < "$FILE_LIST"

echo ""
echo "=== SUMMARY ==="
echo "Triggered/Skipped: $SUCCESS / $TOTAL"
echo "Failed: $FAIL"
if [ -n "$LIMIT_REMAINING" ]; then
  echo "Daily limit reached! $LIMIT_REMAINING files remaining — retry tomorrow"
fi
