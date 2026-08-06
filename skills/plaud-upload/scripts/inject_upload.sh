#!/bin/bash
# Plaud 上傳 fallback：base64 in-page File 注入
#
# 何時用：safari-browser upload --native / --js 都失敗時。
#   - macOS 26 (Darwin 27) 上實測 --native 的 file dialog 雖開、keystroke 也送
#     （"Controlling keyboard" 有出現、AX 權限正常），但檔案沒選進去（NSOpenPanel
#     結構/時序與 safari-browser 的 AppleScript 不相容）。
#   - 同一台 --js 注入後 input.files.length 仍為 0（DataTransfer 注入靜默失敗）。
#   - 兩條官方路徑都斷時，這條在頁面內自建 File 物件、直接餵給 Plaud 的 dropzone。
#
# 原理：把音檔 base64 內嵌進一段 JS，在頁面內 atob → Uint8Array → File → DataTransfer
#   → input.files → dispatch change/input。Plaud 的 dropzone 監聽 change，會在事件裡
#   讀走 dt.files 並隨即 reset input —— 所以事後讀 input.files.length===0 是假象，
#   判準要看 modal 是否顯示「<檔名> ✓ 成功」。
#
# 限制：base64 後整包塞進 JS 檔，原檔 > ~3MB 時 JS 過大、注入可能失敗。
#   大檔（教學影片、長會議）仍應走 --native；此法是給語音備忘錄等小音檔的 fallback。
#
# 用法：bash inject_upload.sh <audio_file_path>
# 前提：Plaud tab 已開、已登入、cookie 框已清。會自動確保「匯入音訊」modal 開著。
#   想把檔案歸到特定資料夾：呼叫前先在該資料夾 view 開 modal，上傳會落到當前 view。
set -euo pipefail

FILE="${1:?用法: bash inject_upload.sh <audio_file_path>}"
[ -f "$FILE" ] || { echo "❌ 檔案不存在: $FILE"; exit 1; }
NAME="$(basename "$FILE")"

# 依副檔名推 MIME（Plaud 不嚴格驗，但給對的較保險）
case "${NAME##*.}" in
  aac) MIME="audio/aac" ;;
  mp3) MIME="audio/mpeg" ;;
  m4a) MIME="audio/mp4" ;;
  wav) MIME="audio/wav" ;;
  ogg) MIME="audio/ogg" ;;
  *)   MIME="application/octet-stream" ;;
esac

SIZE=$(stat -f%z "$FILE" 2>/dev/null || stat -c%s "$FILE")
if [ "$SIZE" -gt 3145728 ]; then
  echo "⚠️  $NAME 為 $((SIZE/1024/1024))MB（>3MB）— base64 注入可能失敗，建議改用 --native 或先壓縮"
fi

# 確保「匯入音訊」modal 開著（input[type=file] 存在）
if [ "$(safari-browser js "document.querySelector('input[type=file]')?'y':'n'" --url plaud)" != "y" ]; then
  safari-browser js "var rb=document.querySelector('.recording-button');if(rb)rb.click();'x'" --url plaud >/dev/null
  sleep 2
  safari-browser js "var m=document.querySelectorAll('.menu-item');for(var i=0;i<m.length;i++){if(m[i].textContent.trim()==='匯入音訊'){m[i].click();break;}}'x'" --url plaud >/dev/null
  sleep 2
fi
[ "$(safari-browser js "document.querySelector('input[type=file]')?'y':'n'" --url plaud)" = "y" ] \
  || { echo "❌ 無法開啟匯入音訊 modal（input[type=file] 不存在）"; exit 1; }

# 生成注入 JS（base64 去換行後內嵌）
JS=$(mktemp /tmp/plaud_inject_XXXX.js)
trap 'rm -f "$JS"' EXIT
B64=$(base64 -i "$FILE" | tr -d '\n')
cat > "$JS" <<EOF
(function(){
  var b64="${B64}";
  var bin=atob(b64), bytes=new Uint8Array(bin.length);
  for(var i=0;i<bin.length;i++) bytes[i]=bin.charCodeAt(i);
  var file=new File([bytes],"${NAME}",{type:"${MIME}"});
  var dt=new DataTransfer(); dt.items.add(file);
  var input=document.querySelector('input[type=file]');
  if(!input) return 'NO_INPUT';
  input.files=dt.files;
  input.dispatchEvent(new Event('change',{bubbles:true}));
  input.dispatchEvent(new Event('input',{bubbles:true}));
  return 'injected';
})();
EOF

safari-browser js --file "$JS" --url plaud >/dev/null
sleep 4

# 驗證：偵測重複對話框 vs 成功。檔名去副檔名當 stem 比對 modal 文字。
STEM="${NAME%.*}"
RESULT=$(safari-browser js "(function(){
  var b=document.body.innerText;
  if(b.indexOf('重複')>=0 && b.indexOf('已存在')>=0) return 'DUPLICATE';
  if(b.indexOf('成功')>=0 && b.indexOf('${STEM}')>=0) return 'OK';
  if(b.indexOf('${STEM}')>=0) return 'SHOWN';
  return 'UNKNOWN';
})();" --url plaud)

case "$RESULT" in
  OK)        echo "✅ $NAME 已上傳" ;;
  SHOWN)     echo "✅ $NAME 已注入（modal 顯示檔名，等其轉為『成功』）" ;;
  DUPLICATE) echo "⚠️  $NAME 已存在於 Plaud — 跳出『重複檔案』對話框，需人工選『取消匯入』或『繼續匯入』" ;;
  *)         echo "❓ $NAME 注入後狀態未知 — 請截圖確認 modal" ;;
esac
