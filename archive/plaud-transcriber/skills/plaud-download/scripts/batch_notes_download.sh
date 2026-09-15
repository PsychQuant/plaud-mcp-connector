#!/bin/bash
# Plaud Batch Notes Download (Markdown + DOCX)
# Usage: bash batch_notes_download.sh <file_list.txt> <output_dir>
#
# file_list.txt format (one per line):
#   hash|||filename
#
# Requires: safari-browser (Safari already logged in)
#
# Download flow per file (~25 sec):
#   1. Navigate to file detail page
#   2. Install Blob interceptor (for MD capture)
#   3. Click Summary tab
#   4. Export notes as Markdown (Blob → extract via chunks)
#   5. Export notes as DOCX (API → S3 URL → curl)

set -uo pipefail

FILE_LIST="${1:?Usage: batch_notes_download.sh <file_list.txt> <output_dir>}"
OUTPUT_DIR="${2:?Usage: batch_notes_download.sh <file_list.txt> <output_dir>}"
mkdir -p "$OUTPUT_DIR"

# Blob interceptor: captures URL.createObjectURL content for extraction
# Must be reinstalled after EVERY page navigation (JS context resets)
INTERCEPTOR_JS='
window.__capturedExports = {};
const origCreate = URL.createObjectURL;
URL.createObjectURL = function(blob) {
  const url = origCreate.call(URL, blob);
  const reader = new FileReader();
  reader.onload = () => {
    window.__capturedExports[blob.type] = {content: reader.result, size: blob.size, type: blob.type};
  };
  reader.readAsText(blob);
  return url;
};
"interceptor installed";
'

total=$(wc -l < "$FILE_LIST" | tr -d ' ')
count=0
ok=0
fail=0

while IFS='|' read -r hash _ _ name; do
  [ -z "$hash" ] && continue
  count=$((count + 1))
  md_file="$OUTPUT_DIR/${name}_notes.md"
  docx_file="$OUTPUT_DIR/${name}_notes.docx"

  # Skip if both exist
  if [ -f "$md_file" ] && [ -f "$docx_file" ]; then
    echo "[$count/$total] SKIP (both exist): $name"
    ok=$((ok + 1))
    continue
  fi

  echo "[$count/$total] Processing: $name"

  # 1. Navigate to file detail page
  safari-browser js "performance.clearResourceTimings()" > /dev/null 2>&1
  sleep 1
  safari-browser open "https://web.plaud.ai/file/$hash" > /dev/null 2>&1
  sleep 5

  # 2. Install blob interceptor (must be done AFTER page load)
  safari-browser js "$INTERCEPTOR_JS" > /dev/null 2>&1

  # 3. Click Summary tab
  safari-browser js "
    const btn = Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === 'Summary');
    if (btn) btn.click();
    btn ? 'ok' : 'no summary btn';
  " > /dev/null 2>&1
  sleep 2

  # === Download Markdown notes ===
  if [ ! -f "$md_file" ]; then
    # Open dropdown → Export notes
    safari-browser js "
      const trigger = document.querySelector('.el-dropdown .el-tooltip__trigger');
      if (trigger) trigger.click();
    " > /dev/null 2>&1
    sleep 1

    safari-browser find text "Export notes" click > /dev/null 2>&1
    sleep 1

    # Select Markdown format
    safari-browser js "document.querySelector('.el-select__wrapper')?.click()" > /dev/null 2>&1
    sleep 1
    safari-browser js "
      const items = document.querySelectorAll('.el-select-dropdown__item');
      for (const item of items) {
        if (item.textContent.includes('Markdown')) { item.click(); break; }
      }
      'ok';
    " > /dev/null 2>&1
    sleep 1

    # Click Export confirm button
    safari-browser js "document.querySelector('[data-testid=\"share-export-confirm-button\"]')?.click()" > /dev/null 2>&1
    sleep 3

    # Extract captured Markdown content
    md_content_size=$(safari-browser js "
      const md = window.__capturedExports['text/markdown;charset=utf-8;'];
      md ? String(md.content.length) : '0';
    " 2>&1 | tr -d '"')

    if [ "$md_content_size" -gt 0 ] 2>/dev/null; then
      # Extract in 10KB chunks (avoid eval output too long)
      chunk=10000
      > "$md_file"
      offset=0
      while [ $offset -lt $md_content_size ]; do
        safari-browser js "window.__capturedExports['text/markdown;charset=utf-8;'].content.substring($offset, $((offset + chunk)))" 2>&1 | \
          sed 's/^"//;s/"$//' | \
          sed 's/\\n/\n/g; s/\\t/\t/g; s/\\"/"/g; s/\\\\/\\/g' >> "$md_file"
        offset=$((offset + chunk))
      done
      echo "  MD: $(ls -lh "$md_file" | awk '{print $5}')"
    else
      echo "  MD: FAIL (no content captured)"
    fi
  else
    echo "  MD: exists"
  fi

  # === Download DOCX notes ===
  if [ ! -f "$docx_file" ]; then
    safari-browser js "performance.clearResourceTimings()" > /dev/null 2>&1

    # Open dropdown → Export notes
    safari-browser js "
      const trigger = document.querySelector('.el-dropdown .el-tooltip__trigger');
      if (trigger) trigger.click();
    " > /dev/null 2>&1
    sleep 1

    safari-browser find text "Export notes" click > /dev/null 2>&1
    sleep 1

    # Select DOCX format
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

    # Click Export confirm button
    safari-browser js "document.querySelector('[data-testid=\"share-export-confirm-button\"]')?.click()" > /dev/null 2>&1
    sleep 5

    # Get DOCX S3 URL (notes use "summary-" prefix)
    s3_url=$(safari-browser js "
      const entries = performance.getEntriesByType('resource');
      const docxUrl = entries.find(e => (e.name.includes('summary-') || e.name.includes('document-download')) && e.name.includes('.docx'));
      docxUrl ? docxUrl.name : 'NOT_FOUND';
    " 2>&1 | grep -oE 'https://[^"]+' | head -1)

    if [ -n "$s3_url" ] && [ "$s3_url" != "NOT_FOUND" ]; then
      curl -sL "$s3_url" -o "$docx_file"
      if [ -f "$docx_file" ] && [ "$(stat -f%z "$docx_file" 2>/dev/null || stat -c%s "$docx_file" 2>/dev/null)" -gt 100 ]; then
        echo "  DOCX: $(ls -lh "$docx_file" | awk '{print $5}')"
      else
        echo "  DOCX: FAIL (download failed)"
        rm -f "$docx_file"
      fi
    else
      echo "  DOCX: FAIL (no S3 URL)"
    fi
  else
    echo "  DOCX: exists"
  fi

  # Check if both succeeded
  if [ -f "$md_file" ] && [ -f "$docx_file" ]; then
    ok=$((ok + 1))
  else
    fail=$((fail + 1))
  fi

done < "$FILE_LIST"

echo ""
echo "=== Done: $ok complete (both formats), $fail incomplete, out of $total ==="
