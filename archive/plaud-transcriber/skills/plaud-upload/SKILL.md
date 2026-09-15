---
name: plaud-upload
description: |
  上傳音訊/影片到 Plaud 並啟動轉錄。
  當用戶提到「上傳到 Plaud」、「Plaud 轉錄」、「transcribe」時使用。
  支援單檔和批次上傳。
argument-hint: "[file_path_or_glob]"
---

# Plaud Upload & Transcribe

將音訊/影片上傳到 web.plaud.ai 並啟動 AI 轉錄。

> **⚠ Plaud 白畫面／資源載入異常的根因（2026-07-18 確認）**：Safari「防止跨網站追蹤」勾選會導致 web.plaud.ai 故障。遇到白畫面先檢查此設定，不要清 cache / 註銷 service worker（那不是根因）。持久狀態變更仍需先告知使用者，見全域 rule `common-browser-automation.md`。

## 帳號資訊

- Email：（維護者的 Plaud 帳號；封存時已移除，見 archive/README.md「Scrub delta」）
- 方案：（封存時已移除；配額細節依當時帳號）
- 密碼：存在 macOS Keychain

```bash
# 讀取密碼
security find-generic-password -s "plaud" -a "<plaud-account-email>" -w
```

## 檔案限制

- 支援格式：MP3, MP4, WAV, AAC, OGG, RMVB, RM, DIVX, TS, M2TS, 3GP, F4V, ASR
- 單檔最大 500MB、最長 5 小時

### 前置檢查

```bash
# 檢查每個檔案長度
for f in "{路徑}"/*.mp3; do
  duration=$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$f" 2>/dev/null)
  mins=$(echo "$duration / 60" | bc)
  echo "$f: $mins min"
done

# 裁剪超過 5 小時的檔案
ffmpeg -y -i "{原檔}.mp3" -t 17940 -c copy "{原檔}_trimmed.mp3"
```

### 影片轉音訊（mp4 → mp3）

上傳影片前**必須先轉成 mp3**。原因：
1. mp3 檔案比 mp4 小 10-20 倍，上傳更快
2. Plaud 只需要音軌，影像部分浪費頻寬
3. Plaud 的 500MB 上限，大影片可能超過

```bash
# 單檔轉換（-q:a 2 ≈ 192kbps，轉錄品質足夠）
ffmpeg -y -i "{影片}.mp4" -vn -acodec libmp3lame -q:a 2 "{輸出}.mp3"

# 批次轉換：將目錄下所有 mp4 轉成 mp3
for f in "{路徑}"/*.mp4; do
  out="${f%.mp4}.mp3"
  if [ ! -f "$out" ]; then
    ffmpeg -y -i "$f" -vn -acodec libmp3lame -q:a 2 "$out"
    echo "Converted: $f → $out"
  fi
done
```

**自動處理邏輯**：當使用者提供 `.mp4`/`.mov`/`.mkv` 檔案時，Claude 應該：
1. 自動用 ffmpeg 轉成 `.mp3`（存到同目錄或 `/tmp/`）
2. 上傳轉出的 `.mp3`
3. 轉錄完成後可刪除暫存的 `.mp3`

**注意**：如果 mp4 是 Git LFS pointer file（~130 bytes），表示真正的影片不在本機。
需要先從 LFS backend 或 Google Drive 下載實際影片，再轉換。
檢查方式：`head -1 file.mp4` 如果輸出 `version https://git-lfs.github.com/spec/v1` 就是 pointer。

### 合併多個音訊檔案

當用戶提供多個檔案要求「合併上傳」時，先用 ffmpeg concat 合併：

```bash
# 建立 concat 清單（必須用絕對路徑）
printf "file '%s'\n" /abs/path/to/file1.m4a /abs/path/to/file2.m4a > /tmp/concat_list.txt

# 合併並轉 mp3
ffmpeg -y -f concat -safe 0 -i /tmp/concat_list.txt -vn -acodec libmp3lame -q:a 2 /tmp/{名稱}_merged.mp3
```

**注意**：concat 清單中的路徑必須是**絕對路徑**，不能用相對路徑（否則 ffmpeg 會在 /tmp 下找不到檔案）。

## 執行步驟

### Step 0: 開啟 Plaud 並清除 Cookie 同意框

**每次都必須先清除 cookie 同意框**，否則會擋住所有後續操作。

```bash
# 若 Plaud tab 已開則不重開；否則開新 tab（不搶前景焦點）
if ! safari-browser documents 2>/dev/null | grep -q "web.plaud.ai"; then
  safari-browser open "https://web.plaud.ai"
  sleep 3
fi
safari-browser get url --url plaud
# URL 含 login → 請用戶手動登入後再繼續
# URL 不含 login → 已登入，清除 cookie 框：
safari-browser js "var b=document.querySelector('.cky-btn-accept'); if(b)b.click(); else document.querySelectorAll('[class*=cky]').forEach(e=>e.remove()); 'done'" --url plaud   # 點接受按鈕，沒有就 force-remove
```

**多視窗支援（#26，safari-browser v2.4.0+）**：所有步驟都用 `--url plaud` 明確 target Plaud document — 不管 Plaud 在哪個 window / 哪個 tab、不管使用者當前焦點在哪，skill 都能正確執行。不再需要手動切 tab。

### Step 1: 點擊「新增音訊」→「匯入音訊」

**用 JS class selector 點擊**（比 `find text` 更可靠，不受 cookie 框干擾）：

```bash
# 點「新增音訊」（用 .recording-button class）
safari-browser js "document.querySelector('.recording-button').click(); 'clicked'" --url plaud
sleep 2

# 點「匯入音訊」（用 .menu-item + textContent 匹配）
safari-browser js "
  var items = document.querySelectorAll('.menu-item');
  for(var i=0;i<items.length;i++){
    if(items[i].textContent.trim()==='匯入音訊'){items[i].click();break;}
  }
  'clicked';
" --url plaud
sleep 1
```

### Step 2: 上傳檔案

**一律使用 `--native --url plaud`**（#24 教訓 + #26 native-path resolver）：

```bash
# #26: --native 現在接受 --url plaud — resolver 自動找到 Plaud 所在 window，
# 必要時先 tab-switch 再 briefly raise 再 keystroke。
# 不需要提前 safari-browser open，不需要手動切 tab。
safari-browser upload --native "input[type='file']" "{完整檔案路徑}" --url plaud

sleep 30  # 等待上傳完成，大檔案可能需要更久（131 MB 約 30-60 秒）
```

**⚠️ 上傳期間（~1-2 秒）請不要切換 app 或按鍵盤。** `safari-browser upload --native --url plaud` 會：
1. Resolve `--url plaud` → 找到 Plaud 所在的 window + tab（同一個 process，race-free）
2. 若 Plaud 是 background tab → 先 `set current tab of window N to tab T`（briefly switch tab）
3. Briefly raise 目標 window 到前景
4. Activate Safari + 驗證 frontmost（#15 race condition guard）
5. 用 `el.click()` 開啟 file dialog
6. 鍵盤模擬 Cmd+Shift+G → 貼上路徑 → Enter → 點 Upload 按鈕
7. 整個流程在**單一** osascript 裡完成

**多 match fail-closed（#26）**：若 Safari 有多個 Plaud tab（e.g. `https://web.plaud.ai/file/a` 和 `https://web.plaud.ai/file/b`），`--url plaud` 會丟 `ambiguousWindowMatch` 列出所有 match。改用更具體 substring：`--url "plaud.ai/file/abc"`。

**為什麼不用 `--js`**（#24 的 lesson）：`--js` 用 base64 chunking + DataTransfer 注入，V8 字串串接對大檔 O(n²) — 131 MB 檔案會產生 ~500 MB+ transient 記憶體，Safari 崩潰並回報 AppleScript -609 連線錯誤。實測在 131 MB MP3 上 chunk 500/913 時 Safari 失去回應。

**`--js` 的適用場景**：safari-browser #24 已 enforce 10 MB hard cap — `--js` 搭配超過 10 MB 的檔案會在 `validate()` 就被拒絕，錯誤訊息指向 `--native --url plaud`。對於教學影片、會議錄音等常見 >50 MB 的 use case，**一律 `--native --url plaud`**。

**安全檢查**：`--native` 路徑已經在 `b8f3ef0` (#15) 修好 focus race condition，#26 進一步把 window 解析下沉到同一 AppleScript session，消除 documents → upload 之間的 race。

### Step 2 fallback：base64 in-page 注入（native + --js 都失敗時）

`--native` 與 `--js` 在某些環境會**雙雙失效**。實測 macOS 26（Darwin 27）+ safari-browser 2.6.0：

| 路徑 | 症狀 | 根因 |
|------|------|------|
| `--native` | "Controlling keyboard" 有出現、AX 權限正常，但檔案沒選進去 | 新版 NSOpenPanel 結構/時序與 safari-browser 的 AppleScript 不相容 |
| `--js` | 注入後 `input.files.length` 仍為 0（靜默失敗） | DataTransfer 注入在此 Safari/Plaud 組合不生效 |

兩條官方路徑都斷時，用 bundled script 在頁面內自建 `File` 物件、直接餵給 Plaud 的 dropzone（`safari-browser js --file` 注入一段內嵌 base64 的 JS）：

```bash
bash {skill_dir}/scripts/inject_upload.sh "{完整檔案路徑}"
```

關鍵理解（踩過才知道）：

- **判準是 modal 的「<檔名> ✓ 成功」，不是 `input.files`** —— Plaud dropzone 監聽 `change` 事件、會在事件裡讀走 `dt.files` 並隨即 reset input，所以事後讀 `input.files.length===0` 是**假象**，不代表失敗。
- **同一 modal 可連續注入多檔**：modal 標題會變「匯入音訊(N)」，全部注入完再一起 Step 3 抓 hash。
- **歸資料夾**：上傳落到「**當前開著的資料夾 view**」。要直接把檔案歸到某資料夾，呼叫 script 前先在該資料夾 view 開 modal。
- **重複偵測**：同檔名再上傳，Plaud 會跳「重複檔案」對話框（script 回報 `DUPLICATE`）。這是**免費的真相** —— 證明檔案真的送進去了、且該檔之前已上傳；人工選「取消匯入」（用舊檔）或「繼續匯入」（以新名存）。
- **大小限制**：base64 整包塞進 JS，原檔 >~3MB 注入可能失敗。大檔（教學影片、長會議）仍走 `--native`；此法是給語音備忘錄等**小音檔**的 fallback。

### Step 3: 關閉上傳 modal 並取得 file hash

上傳完成後 modal 會殘留，**最可靠的做法是直接導航回首頁**（在 Plaud document 上用 `open`）：

```bash
safari-browser open "https://web.plaud.ai" --url plaud
sleep 3
safari-browser js "var b=document.querySelector('.cky-btn-accept'); if(b)b.click(); else document.querySelectorAll('[class*=cky]').forEach(e=>e.remove()); 'done'" --url plaud   # 點接受按鈕，沒有就 force-remove

# 取得剛上傳的檔案 hash（最新的在最上方）
safari-browser js "
  var items = document.querySelectorAll('[data-testid^=\"file-list-item-\"]');
  var result = [];
  items.forEach(function(e){
    var title = e.querySelector('.file-title, [class*=title], h3, p');
    result.push(e.getAttribute('data-testid').replace('file-list-item-','') + ' | ' + (title ? title.textContent.trim() : e.textContent.trim().substring(0,60)));
  });
  result.slice(0,3).join('\n');
" --url plaud
# 確認第一筆是剛上傳的檔案，記下 hash
```

> **踩坑：「最新在最上方」不一定成立**。被**置頂（pinned）**的檔案會固定在列表頂端，把新上傳擠下去 → 上面那段抓到的會是置頂檔案的 hash，不是你剛傳的。更可靠的做法是**用檔名搜尋抓 hash**：點側邊欄「搜尋」，用 React-safe 方式設值（直接 `input.value=...` 不會觸發 Vue，要用 prototype setter）：
>
> ```bash
> safari-browser js "(function(){var i=document.querySelector('input[placeholder*=\"搜尋\"]');var s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;s.call(i,'voice_12119');i.dispatchEvent(new Event('input',{bubbles:true}));return 'x';})();" --url plaud
> sleep 3
> # 搜尋結果項是 .search-list-item；列表項的 hash 在 li[id^="file-list-item-"] 的 **id**（不是 data-testid）
> safari-browser js "(function(){var ls=document.querySelectorAll('li[id^=file-list-item-]');for(var i=0;i<ls.length;i++){if(ls[i].textContent.indexOf('voice_12119')>=0)return ls[i].id.replace('file-list-item-','');}return 'NOT_FOUND';})();" --url plaud
> ```

### Step 4: 導航到檔案頁面並啟動轉錄

```bash
safari-browser open "https://web.plaud.ai/file/{hash}" --url plaud
sleep 4
safari-browser js "var b=document.querySelector('.cky-btn-accept'); if(b)b.click(); else document.querySelectorAll('[class*=cky]').forEach(e=>e.remove()); 'done'" --url plaud   # 點接受按鈕，沒有就 force-remove

# 點「產生」
safari-browser js "
  var btns = document.querySelectorAll('.liner-btn-txt, button, span, div');
  for(var i=0;i<btns.length;i++){
    var t=btns[i].textContent.trim();
    if(t==='產生'||t==='Generate'){btns[i].click();break;}
  }
  'clicked';
" --url plaud
sleep 3

# 點「立即產生」
safari-browser js "
  var btns = document.querySelectorAll('button, div, span');
  for(var i=0;i<btns.length;i++){
    var t=btns[i].textContent.trim();
    if(t==='立即產生'||t==='Generate now'){btns[i].click();break;}
  }
  'clicked';
" --url plaud
sleep 5

# 確認轉錄開始
safari-browser js "
  var text = document.body.innerText;
  var generating = text.indexOf('產生中') >= 0 || text.indexOf('Generating') >= 0;
  generating ? 'OK: 轉錄已啟動' : 'WARN: 未偵測到轉錄狀態';
" --url plaud
```

### 批次上傳

對每個檔案重複 Step 1-4。轉錄在雲端並行處理，所以可以連續上傳不用等轉錄完成。每個指令都 `--url plaud` 明確 target，使用者可以在背景做別的事不受干擾。

```bash
FILES=("/path/to/file1.mp3" "/path/to/file2.mp3")

for f in "${FILES[@]}"; do
  name=$(basename "$f" .mp3)

  # Step 1: 新增音訊 → 匯入音訊
  safari-browser js "document.querySelector('.recording-button').click(); 'clicked'" --url plaud
  sleep 2
  safari-browser js "var items=document.querySelectorAll('.menu-item');for(var i=0;i<items.length;i++){if(items[i].textContent.trim()==='匯入音訊'){items[i].click();break;}} 'clicked'" --url plaud
  sleep 1

  # Step 2: 上傳 — 一律用 --native --url plaud（#24 + #26）
  safari-browser upload --native "input[type='file']" "$f" --url plaud
  sleep 30

  # Step 3: 回首頁取 hash
  safari-browser open "https://web.plaud.ai" --url plaud
  sleep 3
  safari-browser js "var b=document.querySelector('.cky-btn-accept'); if(b)b.click(); else document.querySelectorAll('[class*=cky]').forEach(e=>e.remove()); 'done'" --url plaud   # 點接受按鈕，沒有就 force-remove
  HASH=$(safari-browser js "var item=document.querySelector('[data-testid^=\"file-list-item-\"]');item?item.getAttribute('data-testid').replace('file-list-item-',''):'NOT_FOUND'" --url plaud)

  # Step 4: 啟動轉錄
  safari-browser open "https://web.plaud.ai/file/$HASH" --url plaud
  sleep 4
  safari-browser js "var b=document.querySelector('.cky-btn-accept'); if(b)b.click(); else document.querySelectorAll('[class*=cky]').forEach(e=>e.remove()); 'done'" --url plaud   # 點接受按鈕，沒有就 force-remove
  safari-browser js "var btns=document.querySelectorAll('.liner-btn-txt,button,span,div');for(var i=0;i<btns.length;i++){var t=btns[i].textContent.trim();if(t==='產生'||t==='Generate'){btns[i].click();break;}} 'clicked'" --url plaud
  sleep 3
  safari-browser js "var btns=document.querySelectorAll('button,div,span');for(var i=0;i<btns.length;i++){var t=btns[i].textContent.trim();if(t==='立即產生'||t==='Generate now'){btns[i].click();break;}} 'clicked'" --url plaud
  sleep 5

  echo "Started: $name"
done
```

