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

# 把任意字串安全轉成 JS 字串常值（含跳脫與外層引號）。
# 為什麼需要：檔名是使用者資料，含 " \ ' 都合法。裸插進 JS 字串會產生語法錯誤 ——
# 而 set -euo pipefail 會讓腳本在「檔案其實已經上傳成功」之後才崩潰，最糟的失敗時機。
# 實測會壞的例子：my"quote.mp3、back\slash.mp3（雙引號情境）、Mom's meeting.m4a（單引號情境）。
js_str() { python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"; }

SIZE=$(stat -f%z "$FILE" 2>/dev/null || stat -c%s "$FILE")
if [ "$SIZE" -gt 3145728 ]; then
  echo "⚠️  $NAME 為 $((SIZE/1024/1024))MB（>3MB）— base64 注入可能失敗，建議改用 --native 或先壓縮"
fi

# 確保「匯入音訊」modal 開著（input[type=file] 存在）
if [ "$(safari-browser js "document.querySelector('input[type=file]')?'y':'n'" --url plaud)" != "y" ]; then
  safari-browser js "var rb=document.querySelector('.recording-button');if(rb)rb.click();'x'" --url plaud >/dev/null
  sleep 2
  # 選單項同時比對中英（SKILL.md Step 1 已這樣做，這支 script 原本漏了 —— 同一次移植的漏網處）
  safari-browser js "var m=document.querySelectorAll('.menu-item');for(var i=0;i<m.length;i++){var t=m[i].textContent.trim();if(t==='匯入音訊'||t==='Import Audio'){m[i].click();break;}}'x'" --url plaud >/dev/null
  sleep 2
fi
[ "$(safari-browser js "document.querySelector('input[type=file]')?'y':'n'" --url plaud)" = "y" ] \
  || { echo "❌ 無法開啟匯入音訊 modal（input[type=file] 不存在）"; exit 1; }

# 生成注入 JS（base64 去換行後內嵌）
JS=$(mktemp /tmp/plaud_inject_XXXX.js)
VJS=""
trap 'rm -f "$JS" "${VJS:-}"' EXIT
B64=$(base64 -i "$FILE" | tr -d '\n')
NAME_JS=$(js_str "$NAME")
MIME_JS=$(js_str "$MIME")
cat > "$JS" <<EOF
(function(){
  var b64="${B64}";
  var bin=atob(b64), bytes=new Uint8Array(bin.length);
  for(var i=0;i<bin.length;i++) bytes[i]=bin.charCodeAt(i);
  var file=new File([bytes],${NAME_JS},{type:${MIME_JS}});
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

# 驗證：判斷順序刻意如下，每一步都有理由 ——
#
#   1. DUPLICATE 最先判，且**不要求檔名出現**。這保留原版語意：Plaud 的重複對話框
#      可能是通用範本文字（「此檔案已存在，是否繼續匯入？」）而不重複顯示檔名，
#      或長檔名被截斷成「…」。若把它擋在 stemFound 之後，本來能正確攔截的重複
#      會退化成 UNKNOWN —— 使用者從明確警示變成「請截圖」，反而更可能誤按繼續匯入。
#   2. FAILED 獨立成一支。失敗 toast 也含檔名，若沒有這支就會落到 SHOWN，
#      把「上傳失敗」誤報成「已注入，等待成功」—— 方向完全相反的誤導。
#   3. stemFound 才是 OK / SHOWN 的前提（檔名有出現 = Plaud 確實處理到這次上傳）。
#
# 語言處理：中文訊號已在作者的中文介面帳號實測；英文關鍵字是依常見措辭補的次要訊號，
# **未在英文介面實機驗證**。英文若措辭不同（例如 "Import complete" 不含 success），
# 最壞情況是退回 SHOWN（保守誤判），不會誤報成失敗。歡迎英文介面使用者回報實際文案。
STEM="${NAME%.*}"
STEM_JS=$(js_str "$STEM")
VJS=$(mktemp /tmp/plaud_verify_XXXX.js)
cat > "$VJS" <<EOF
(function(){
  var b=document.body.innerText;
  var bl=b.toLowerCase();
  var stem=${STEM_JS};

  if((b.indexOf('重複')>=0 && b.indexOf('已存在')>=0) ||
     bl.indexOf('duplicate')>=0 || bl.indexOf('already exists')>=0) return 'DUPLICATE';

  var stemFound = stem.length>0 && b.indexOf(stem)>=0;
  if(!stemFound) return 'UNKNOWN';

  if(b.indexOf('失敗')>=0 || b.indexOf('不支援')>=0 ||
     bl.indexOf('failed')>=0 || bl.indexOf('unsupported')>=0) return 'FAILED';

  if(b.indexOf('成功')>=0 || bl.indexOf('success')>=0) return 'OK';
  return 'SHOWN';
})();
EOF
RESULT=$(safari-browser js --file "$VJS" --url plaud)

case "$RESULT" in
  OK)        echo "✅ $NAME 已上傳" ;;
  SHOWN)     echo "✅ $NAME 已注入（modal 顯示檔名，等其轉為『成功』）" ;;
  DUPLICATE) echo "⚠️  $NAME 已存在於 Plaud — 跳出『重複檔案』對話框，需人工選『取消匯入』或『繼續匯入』" ;;
  FAILED)    echo "❌ $NAME 上傳失敗 — 畫面出現失敗訊息，請勿直接重試，先看 modal 說明原因" ;;
  *)         echo "❓ $NAME 注入後狀態未知 — 請截圖確認 modal" ;;
esac
