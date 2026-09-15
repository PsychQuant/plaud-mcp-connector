#!/bin/bash
# Plaud Batch DOCX Download
# Usage: batch_docx_download.sh <file_list.txt> <output_dir>
#
# file_list.txt format (one per line):
#   hash|||filename
#
# Requires: safari-browser (Safari already logged in)
#
# Export flow per file (~15 sec):
#   1. Navigate to file detail page
#   2. Trigger DOCX export via UI (dropdown → Export transcript → DOCX → Export)
#   3. Capture DOCX S3 URL from performance entries
#   4. Download with curl

set -uo pipefail

FILE_LIST="${1:?Usage: batch_docx_download.sh <file_list.txt> <output_dir>}"
OUTPUT_DIR="${2:?Usage: batch_docx_download.sh <file_list.txt> <output_dir>}"
mkdir -p "$OUTPUT_DIR"

total=$(wc -l < "$FILE_LIST" | tr -d ' ')
count=0
ok=0
fail=0

while IFS='|' read -r hash _ _ name; do
  [ -z "$hash" ] && continue
  count=$((count + 1))
  docx_file="$OUTPUT_DIR/${name}.docx"

  # Skip if already exists
  if [ -f "$docx_file" ]; then
    echo "[$count/$total] SKIP (exists): $name"
    ok=$((ok + 1))
    continue
  fi

  echo "[$count/$total] Processing: $name"

  # 1. Clear performance timings
  safari-browser js "performance.clearResourceTimings()" > /dev/null 2>&1

  # 2. Navigate to file detail page
  safari-browser open "https://web.plaud.ai/file/$hash" > /dev/null 2>&1
  sleep 5

  # 3. Open export dropdown (el-dropdown trigger)
  safari-browser js "
    const trigger = document.querySelector('.el-dropdown .el-tooltip__trigger') || document.querySelector('[class*=el-dropdown]');
    if (trigger) trigger.click();
    'ok';
  " > /dev/null 2>&1
  sleep 1

  # 4. Click "Export transcript"
  safari-browser find text "Export transcript" click > /dev/null 2>&1
  sleep 1

  # 5. Open format selector and pick DOCX
  safari-browser js "document.querySelector('.el-select__wrapper')?.click()" > /dev/null 2>&1
  sleep 1
  safari-browser js "
    const items = document.querySelectorAll('.el-select-dropdown__item');
    for (const item of items) {
      if (item.textContent.includes('DOCX')) { item.click(); break; }
    }
    'ok';
  " > /dev/null 2>&1
  sleep 1

  # 6. Click Export confirm button
  safari-browser js "document.querySelector('[data-testid=\"share-export-confirm-button\"]')?.click()" > /dev/null 2>&1
  sleep 4

  # 7. Get DOCX S3 URL from performance entries
  s3_url=$(safari-browser js "
    const entries = performance.getEntriesByType('resource');
    const docxUrl = entries.find(e => e.name.includes('document-download') || e.name.includes('.docx'));
    docxUrl ? docxUrl.name : 'NOT_FOUND';
  " 2>&1 | grep -oE 'https://[^"]+' | head -1)

  if [ -z "$s3_url" ] || [ "$s3_url" = "NOT_FOUND" ]; then
    echo "  FAIL: No DOCX URL found"
    fail=$((fail + 1))
    continue
  fi

  # 8. Download DOCX
  curl -sL "$s3_url" -o "$docx_file"

  if [ -f "$docx_file" ] && [ "$(stat -f%z "$docx_file" 2>/dev/null || stat -c%s "$docx_file" 2>/dev/null)" -gt 100 ]; then
    size=$(ls -lh "$docx_file" | awk '{print $5}')
    echo "  OK: $size"
    ok=$((ok + 1))
  else
    echo "  FAIL: Download failed or empty"
    rm -f "$docx_file"
    fail=$((fail + 1))
  fi

done < "$FILE_LIST"

echo ""
echo "=== Done: $ok success, $fail failed, out of $total ==="
