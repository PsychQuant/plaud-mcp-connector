#!/bin/bash
# Plaud Batch SRT Download
# Usage: batch_download.sh <file_list.txt> <output_dir> [scripts_dir]
#
# file_list.txt format (one per line):
#   hash|||filename
#
# Requires: safari-browser (Safari already logged in)

set -euo pipefail

FILE_LIST="${1:?Usage: batch_download.sh <file_list.txt> <output_dir> [scripts_dir]}"
OUTPUT_DIR="${2:?Usage: batch_download.sh <file_list.txt> <output_dir> [scripts_dir]}"
SCRIPTS_DIR="${3:-$(dirname "$0")}"

DONE_FILE="/tmp/plaud_done_$(date +%Y%m%d).txt"
LOG_FILE="/tmp/plaud_download.log"

touch "$DONE_FILE"
> "$LOG_FILE"
mkdir -p "$OUTPUT_DIR"

total=$(wc -l < "$FILE_LIST" | tr -d ' ')
count=0
success=0
fail=0

while IFS='|' read -r hash _ _ name; do
  [ -z "$hash" ] && continue
  [ -z "$name" ] && continue

  count=$((count + 1))
  srt_file="$OUTPUT_DIR/${name}.srt"

  # Skip if already exists
  if [ -f "$srt_file" ]; then
    echo "[$count/$total] SKIP (exists): $name"
    success=$((success + 1))
    continue
  fi

  if grep -q "$hash" "$DONE_FILE" 2>/dev/null; then
    echo "[$count/$total] SKIP (done): $name"
    success=$((success + 1))
    continue
  fi

  echo "[$count/$total] Downloading: $name"

  # Clear perf timings and navigate directly to file detail page
  safari-browser js "performance.clearResourceTimings()" > /dev/null 2>&1
  sleep 1
  safari-browser open "https://web.plaud.ai/file/$hash" > /dev/null 2>&1
  sleep 5

  # Get S3 presigned URL from Performance API
  s3_url=$(safari-browser js "
    const entries = performance.getEntriesByType('resource');
    const transUrl = entries.find(e => e.name.includes('trans_result'));
    transUrl ? transUrl.name : 'NOT_FOUND';
  " 2>&1 | grep -oE 'https://[^"]+' | head -1)

  # Retry once if not found
  if [ -z "$s3_url" ] || echo "$s3_url" | grep -q "NOT_FOUND"; then
    sleep 3
    s3_url=$(safari-browser js "
      const entries = performance.getEntriesByType('resource');
      const transUrl = entries.find(e => e.name.includes('trans_result'));
      transUrl ? transUrl.name : 'NOT_FOUND';
    " 2>&1 | grep -oE 'https://[^"]+' | head -1)
  fi

  if [ -z "$s3_url" ] || echo "$s3_url" | grep -q "NOT_FOUND"; then
    echo "  FAIL: No S3 URL"
    echo "FAIL|$hash|$name|NO_S3_URL" >> "$LOG_FILE"
    fail=$((fail + 1))
    continue
  fi

  # Download gzipped JSON and convert to SRT
  curl -sL "$s3_url" | gunzip > /tmp/plaud_transcript.json 2>/dev/null

  if [ ! -s /tmp/plaud_transcript.json ]; then
    echo "  FAIL: Empty transcript"
    echo "FAIL|$hash|$name|EMPTY_JSON" >> "$LOG_FILE"
    fail=$((fail + 1))
    continue
  fi

  python3 "$SCRIPTS_DIR/json_to_srt.py" /tmp/plaud_transcript.json "$srt_file" 2>&1

  if [ $? -eq 0 ] && [ -f "$srt_file" ]; then
    echo "$hash" >> "$DONE_FILE"
    success=$((success + 1))
  else
    echo "  FAIL: SRT conversion"
    echo "FAIL|$hash|$name|SRT_CONVERT" >> "$LOG_FILE"
    fail=$((fail + 1))
  fi

done < "$FILE_LIST"

echo ""
echo "=== COMPLETE ==="
echo "Total: $total, Success: $success, Failed: $fail"
echo "Output: $OUTPUT_DIR"
[ -s "$LOG_FILE" ] && echo "Failures: $LOG_FILE"
