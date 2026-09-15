---
name: plaud-download
description: |
  下載 Plaud 轉錄結果（SRT 字幕、Word 文件、摘要）。
  當用戶提到「下載 SRT」、「Plaud 下載」、「取得字幕」、「下載 Word」、「下載摘要」時使用。
argument-hint: "[file_name | folder_name | all] [format: srt|word|summary|all] [output_dir]"
---

# Plaud Download

從 web.plaud.ai 下載轉錄完成的檔案。支援多種格式。

> **⚠ Plaud 白畫面／資源載入異常 — 兩個已確認根因（判別關鍵：跨 session vs 同 session 突發）**：
> 1. **防止跨網站追蹤（2026-07-18 確認）**：Safari「設定 > 隱私權 > **防止跨網站追蹤**」勾選會導致 web.plaud.ai 故障（白畫面、載入資源被當檔案下載）。一開 Plaud 就壞、跨 session 持續 → 先檢查此設定。
> 2. **ES module MIME 快取毒化（2026-07-19 確認）**：**同一 session 前一刻還正常**、某次導航後整站白畫面（連首頁都不 mount、開新分頁也一樣）→ 是 entry `type=module` 的 import graph 裡某資源被以 `text/plain` MIME 快取，module 嚴格 MIME 檢查讓整包拒絕執行。診斷與免清快取的修法見 [§ 整站白畫面：module MIME 快取毒化](#整站白畫面module-mime-快取毒化2026-07-19-實測)。
>
> 兩者都**不要**反射性清 cache / 註銷 service worker（2026-07-15 的教訓：那不是根因，清了也沒用）。持久狀態變更（清 cache/SW/storage）仍需先告知使用者，見全域 rule `common-browser-automation.md`。

## 支援格式

| 格式 | 說明 | 來源 | 下載機制 |
|------|------|------|----------|
| **srt** | SRT 字幕檔（含時間軸 + 說話者） | 從 `trans_result` JSON 轉換 | Performance API → S3 URL，失敗時 fallback 到 file detail API |
| **transcript-docx** | 逐字稿 Word 文件 | Export transcript → DOCX | API → S3 URL（20 分鐘） |
| **notes-md** | AI 摘要 Markdown | Export notes → Markdown | Client-side Blob（需 JS 攔截） |
| **notes-docx** | AI 摘要 Word 文件 | Export notes → DOCX | API → S3 URL（20 分鐘） |

如果用戶未指定格式，**詢問用戶要哪種**。

## Bundled Scripts

本 skill 包含以下腳本（在 `scripts/` 目錄）：

### 推薦（合併版，含 auto re-login）

- **`batch_srt_docx.sh`**：批次下載 SRT + 逐字稿 DOCX（合併在同一次導航中）
- **`batch_notes.sh`**：批次下載筆記 MD + DOCX（含 Blob 攔截器）

### 舊版（保留但不推薦）

- **`batch_download.sh`**：僅 SRT 批次（無 auto re-login）
- **`batch_docx_download.sh`**：僅逐字稿 DOCX 批次（無 auto re-login）
- **`batch_notes_download.sh`**：僅筆記 MD+DOCX 批次（無 auto re-login）

### 通用工具

- **`json_to_srt.py`**：將 Plaud trans_result JSON 轉換為 SRT 字幕格式，**預設啟用 ASR hallucination 偵測**（見下方章節）
- **`dom_transcript_to_srt.py`**（2026-06 新增）：token-free 的第三層 SRT fallback。當 Performance API + detail API 都失敗(Plaud 改版把 token 收進 in-memory store)，從 `.transcript-module` DOM scrape 的扁平逐字稿 parse 成 SRT。用法見 [§ 格式 A 方法 3](#方法-3-dom-extract-fallback方法-12-都-not_found-時)

## ASR Hallucination Detection（json_to_srt.py 預設行為）

### 為什麼需要

Plaud 的 trans_result JSON **沒有** confidence / no_speech_prob 欄位（schema 只有 `content / start_time / end_time / speaker / original_speaker / embeddingKey`）。長時間沉默或環境噪音會被 Whisper 模型生成 ghost token（典型如 `中文。`、`謝謝觀看`、`字幕由 Amara.org 社群提供`）填入逐字稿。沒有偵測機制時這些幻覺會被當成真語音流入講義整理。

### 三個信號（互斥，命中任一即 flag）

#### Signal 1: 低 chars-per-second（沉默被填詞）

最強信號。規則：

```
duration > 15 秒 AND chars-per-second < 0.3
```

**Tuning rationale**（從一份家教錄音校準）：

| 場景 | chars / duration | cps | 是否 flag |
|------|------------------|-----|-----------|
| `中文。` 3 chars / 41s | | 0.07 | ✅ flag(真幻覺) |
| `中文。` 3 chars / 57s | | 0.05 | ✅ flag |
| StudentA 慢速問句 `這個範例句是合成的慢速問句。` 12 chars / 22s | | 0.55 | ❌ 不 flag(真語音,只是慢) |

cps gap 在「沉默幻覺」(~0.05) vs 「真實慢速語音」(~0.5+) 之間夠大,0.3 是安全 threshold。

#### Signal 2: Whisper ghost phrase exact match

詞庫(從 StudentA / StudentB / StudentC session 觀察累積):

```python
WHISPER_GHOST_PHRASES = {
    # YouTube 字幕屬性洩漏(訓練資料污染)
    "謝謝觀看", "謝謝大家", "下次見", "再會",
    "感謝您觀看", "感謝您收看", "感謝您收看時局新聞，再會",
    "中文字幕由 Amara.org 社群提供",
    "繁體中文字幕由Amara.org社群提供",
    # 講義中常見的沉默 token
    "中文", "中文。", "繁體中文",
    "字幕", "字幕。", "OK。", "嗯。",
}
```

**只 exact match**（normalized 後）— 不做 substring match,因為「OK。」可能是學生的真實短答。

#### Signal 3: 連續重複(consecutive repetition)

Whisper 進入生成 loop 時會反覆出 *相鄰* 的同樣 chunk:

```
他自己他自己他自己他自己他自己   ← 真幻覺(連續 5 次)
標準誤越小代表估得越準...所以標準誤越大有效性就越小   ← 不是幻覺(老師強調術語,散落 5 次)
```

規則:任一 3-12 char 子字串**連續**重複 ≥ 3 次。**不偵測散落重複**(會誤判老師強調的術語)。

### 預設行為:flag 不刪

```bash
# 預設:flag 加註但保留 segment
python3 json_to_srt.py input.json output.srt
# → SRT 內容變成: "[Speaker 4] (疑似雜音/沉默幻覺 [low_cps: 0.07 chars/s over 41s]) 中文。"
```

```bash
# Strip 模式:整段刪掉(慎用)
python3 json_to_srt.py input.json output.srt --strip-hallucinations

# Legacy 模式:完全停用偵測
python3 json_to_srt.py input.json output.srt --no-flag
```

**為什麼預設 flag 不 strip**:flag 是 conservative default,讓 reviewer 看得到再決定。lecture-add Step 1 SRT 校正階段會看到這些 marker,人工確認後可批次刪除或保留。

### 跟 lecture-add 的銜接

`lecture-add` 的 SRT 階段一基礎校正 step 看到 `(疑似雜音/沉默幻覺 [...])` marker 時,直接 sed 替換成 `(計算中)` 或 `(非課堂內容)`,或整段 grep -v 刪除。

```bash
# 在 lecture-add Step 1 內可以加:
sed -i '' '/(疑似雜音\/沉默幻覺/d' "$SRT"  # 整段刪除
# 或
sed -i '' 's/(疑似雜音\/沉默幻覺[^)]*) /(計算中) /g' "$SRT"  # 替換成計算中
```

### Plaud 的 diarization 信號(原始 speaker)

`original_speaker` 欄位攜帶 Plaud 的 raw 分群結果:1-on-1 家教真實 speaker 通常只有 `Speaker 1`(老師)+ `Speaker 2`(學生)。當 `Speaker 3` / `Speaker 4` 出現,**Plaud 認定這是另一個聲源** — 多半是環境噪音、Whisper 沉默幻覺、或鄰桌人聲。

預設輸出用 `speaker`(merged label)。要保留原始 diarization 信號用 `--keep-original-speaker`:

```bash
python3 json_to_srt.py input.json output.srt --keep-original-speaker
# → "[Speaker 3] ..." 而不是 "[StudentA] ..."
```

這在 debug Plaud 把不同人都歸成 `[StudentA]` 的情境很有用 — 看 raw speaker 才知道有沒有混入第三人聲源。

## 批次下載工作流程（整個資料夾 4 格式）

這是經過 700+ 檔案驗證的最佳流程。

> **使用情境**：本章節的 `collect_files.js` snippet 是**列出整個資料夾的所有檔案**用的，後續會餵給批次下載 script。**不是**「user 想要哪個檔案就抓 row 1」的 single-file selector — 那種情境請走 [§ Vague-query disambiguation](#vague-query-disambiguation)。

### Step 1：收集檔案清單

```bash
# 導航到資料夾（用 <a> 元素搜尋，不要用 find text）
safari-browser js "
  var links = document.querySelectorAll('a');
  for (var i = 0; i < links.length; i++) {
    if (links[i].textContent.indexOf('{資料夾關鍵字}') !== -1) {
      links[i].click(); break;
    }
  }
" --url plaud
sleep 3

# 收集檔案清單
safari-browser js "$(cat {skill_dir}/scripts/collect_files.js)" --url plaud

# 存到暫存檔（去掉第一行的 "N files"）
echo "{eval_output}" | tail -n +2 > /tmp/plaud_files_xxx.txt
```

如果檔案數 ≤ 30 且全部在可視範圍內，也可以直接用 JS 收集（不需要 async scrolling）：

```bash
safari-browser js "
  var items = document.querySelectorAll('[data-testid^=\"file-list-item-\"]');
  var result = [];
  for (var i = 0; i < items.length; i++) {
    var testid = items[i].getAttribute('data-testid');
    var hash = testid.replace('file-list-item-', '');
    var nameEl = items[i].querySelector('.file-list-item__filename');
    var name = nameEl ? nameEl.textContent.trim() : 'UNKNOWN';
    result.push(hash + '|||' + name);
  }
  result.join('\n');
" --url plaud
```

### Step 2：下載 SRT + 逐字稿 DOCX

```bash
bash {skill_dir}/scripts/batch_srt_docx.sh /tmp/plaud_files_xxx.txt "/path/to/output"
```

### Step 3：下載筆記 MD + DOCX

```bash
bash {skill_dir}/scripts/batch_notes.sh /tmp/plaud_files_xxx.txt "/path/to/output"
```

### Step 4：重跑 SRT+DOCX 補 timing failures

SRT 偶爾因 Performance API 快取/時間差失敗（DOCX 成功但 SRT 沒抓到 URL）。
重跑即可恢復，腳本會自動跳過已完成的檔案：

```bash
bash {skill_dir}/scripts/batch_srt_docx.sh /tmp/plaud_files_xxx.txt "/path/to/output"
```

如果仍有 1-2 個 SRT 失敗，再跑一次通常就全部恢復。

### 批次腳本特性

- **斷點續傳**：檢查檔案是否存在且大小合理（SRT ≥ 100B, DOCX ≥ 1000B），自動跳過已完成
- **Auto re-login**：檢查 `window.location.href` 是否含 `login`，自動用 Keychain 重新登入
- **連續失敗自動停止**：連續 5 個檔案都失敗時自動停止（避免浪費時間在未轉錄檔案上）
- **每日上限偵測**（`batch_generate.sh`）：偵測到 "daily limit" 時自動停止
- **結果摘要**：結束時顯示成功/失敗統計和失敗清單
- **速度**：SRT+DOCX ~15 秒/檔、Notes MD+DOCX ~25 秒/檔

## 關鍵知識

### URL 模式

Plaud 檔案詳情頁 URL 格式：
```
https://web.plaud.ai/file/{file_hash}
```
可以直接導航到任何檔案，**不需要在列表中找到再點擊**。

### Performance API 取得 S3 URL

**每次導航到新的檔案詳情頁之前，必須先清除 performance timings**，否則會取到舊 URL：

```bash
safari-browser js "performance.clearResourceTimings()" --url plaud
sleep 1
safari-browser open "https://web.plaud.ai/file/{hash}" --url plaud
sleep 5
```

### Token 已移出 localStorage（2026-06）

> **CRITICAL 改版**：2026-06 起 Plaud 把 API bearer token 從 `localStorage.pld_tokenstr` 搬走。實測掃過 **localStorage / sessionStorage / cookie / IndexedDB（6 個 DB 全開）都沒有** API token(`plaud-storage` 只有 voiceprint、firebase DB 那個是 firebase auth 不是 Plaud API token) — 研判收進 in-memory Vuex/Pinia store，**JS 完全讀不到**。

影響：下面的 **File Detail API Fallback 已失效**(token 拿不到 → `Authorization` header 帶不出去 → 回 `{"status":-3900,"msg":"invalid auth header"}`)。**改走「方法 3: 匯出按鈕」**（[§ 格式 A 方法 3](#方法-3-匯出按鈕直接下載到-downloads2026-06-改版後首選-fallback)）——這條**不依賴 token、也不依賴 Performance API 側錄**，且 **SRT / Notes MD / DOCX / TXT 都能拿**（匯出選單直接觸發瀏覽器原生下載）：SRT 走「匯出轉錄 → SRT」、Notes MD 走「匯出筆記 → Markdown」（2026-07-15 實測兩者都成功）。DOM extract（方法 4）因虛擬捲動殘缺（77 段只抓到 10 段），只當最後手段。

掃 token 位置的探查 JS（之後若要找新位置可重跑）：

```javascript
// async 掃所有 IndexedDB store 找含 token/bearer/eyJ 的值
(async function(){
  var dbs = await indexedDB.databases();
  for (var d of dbs){
    var db = await new Promise(r=>{var q=indexedDB.open(d.name);q.onsuccess=()=>r(q.result);});
    for (var s of Array.from(db.objectStoreNames)){
      var all = await new Promise(r=>{var q=db.transaction(s).objectStore(s).getAll();q.onsuccess=()=>r(q.result);});
      for (var v of all){ if(/token|bearer|eyJ/i.test(JSON.stringify(v))) console.log(d.name,s,JSON.stringify(v).slice(0,120)); }
    }
    db.close();
  }
})();
```

### File Detail API Fallback（⚠ 2026-06 起失效，見上）

當 Performance API 沒抓到 `trans_result` URL 時（常見於預設載入摘要 tab 的檔案），
**過去**可用 `/file/detail/{hash}` API 直接取得 signed URL。**2026-06 token 移走後此路斷**，保留於下供 token 路徑修復後參考：

```javascript
var token = JSON.parse(localStorage.getItem("pld_tokenstr"));
var xhr = new XMLHttpRequest();
xhr.open('GET', 'https://api-apse1.plaud.ai/file/detail/' + hash, false);
xhr.setRequestHeader('Authorization', token);
xhr.send();
var match = xhr.responseText.match(/https[^"]*trans_result[^"]*/);
match ? match[0] : 'NOT_FOUND';
```

- **Token 位置**：`JSON.parse(localStorage.getItem("pld_tokenstr"))`(注意:JSON-encoded,要 parse;格式 `bearer eyJ...`)
- **API 域名**：`api-apse1.plaud.ai`（從 `localStorage.getItem('plaud_user_api_domain')` 可確認）
- **Response**：JSON，包含 `trans_result_url` 欄位（S3 signed URL，5 分鐘有效）
- **batch_srt_docx.sh 已整合此 fallback**：Performance API 失敗時自動使用

### 虛擬列表 (vue-recycle-scroller)

Plaud 使用 vue-recycle-scroller，只有可見的項目存在於 DOM。
- 滾動容器：`.vue-recycle-scroller.file-list-container__wrapper`
- 檔案名稱：`.file-list-item__filename`（不是 `[class*=name]`，後者會抓到 metadata）
- File hash：`data-testid="file-list-item-{hash}"`
- 少量檔案（≤ 30）可能全部在 DOM 中，不需要滾動

### JS 語法

**批次腳本的 `eval` 中必須用 `var`**，不要用 `const`/`let`。
Plaud 的 Webpack 環境中 `const`/`let` 偶爾會和已有變數衝突。

**（2026-07-15 實測，CRITICAL）單檔互動的 js 一律包成 IIFE**：即使用 `var`，**頂層**宣告常見變數名（`it` / `c` / `i` / `a` 等）在 Plaud 的 Webpack 全域環境仍會衝突，症狀是 `safari-browser js` **回傳 `undefined`** —— 但**副作用照樣生效**（`.click()` / `dispatchEvent` 都有作用），只是回傳值顯示 undefined，害你誤判「操作失敗」而反覆重試。單行、無迴圈的表達式（`document.querySelectorAll('.x').length`、`'a'+'b'`）不受影響。解法：**把邏輯包進 IIFE 並顯式 `return`**，用 `$(cat file.js)` 傳入避免 shell 破壞中文/引號：

```bash
# ❌ 頂層 var + for → 回傳 undefined（即使邏輯完全正確、副作用有生效）
safari-browser js "var a=document.querySelectorAll('.x'); var n=0; for(var i=0;i<a.length;i++){n++;} n;" --url plaud

# ✅ IIFE 隔離作用域 + String() 回傳 → 正常
cat > step.js <<'JSEOF'
(function(){
  var a = document.querySelectorAll('.x');
  var n = 0;
  for (var i = 0; i < a.length; i++) { n++; }
  return String(n);
})()
JSEOF
safari-browser js "$(cat step.js)" --url plaud
```

IIFE 內回傳中文字串（如 dialog innerText）也 OK；數字回傳用 `String(...)` 包最穩。含 `'\n'` 拼接或 `JSON.stringify(array)` 的回傳偶爾仍 undefined，改用 `' | '` 之類分隔符串接。

### 側邊欄資料夾導航

`find text` 和 CSS selector 都不可靠。用 `<a>` 元素搜尋文字：

```bash
safari-browser js "
  var links = document.querySelectorAll('a');
  for (var i = 0; i < links.length; i++) {
    if (links[i].textContent.indexOf('{關鍵字}') !== -1) {
      links[i].click(); break;
    }
  }
" --url plaud
```

## 中文 UI 與 Cookie consent dialog（CRITICAL）

> **某次 StudentC 家教 session 學到的教訓**:中文版 Plaud + 新 session 同時觸發,batch_notes.sh 報「no export btn」,SRT 抓得到但 Notes MD/DOCX 全失敗。三個 root cause 都跟 i18n + 對話框遮蔽有關。

### Cookie consent dialog（CookieYes，class `cky-*`）

新登入或 cookies 被清掉時,Plaud 會跳第三方 CookieYes consent dialog。**這個 dialog 是 overlay**,在它顯示期間 `document.body` textContent 會被它的文字塞滿,所有依賴 textContent 找按鈕的 selector 都會誤判,且部分 click event 會被 dialog 攔截。

**症狀**:
- 抓 `main` 或 `body` textContent 只看到「我們重視您的隱私 我們使用 cookies 來提升您的瀏覽體驗...」
- batch_notes.sh 失敗,error 訊息含「no export btn」
- 點按鈕後跳出無關的 dialog(例如點「匯出筆記」結果跳「邀請成員」表單)

**Pre-step（每個導航之後加）**:

```bash
safari-browser js "
var b = document.querySelector('.cky-btn-accept');
if (b) { b.click(); 'clicked-accept'; }
else if (document.querySelector('.cky-consent-container')) { 'dialog-but-no-btn'; }
else { 'no-dialog'; }
" --url plaud
sleep 1

# 按鈕點不動 → force-remove fallback（2026-07-18 恢復：cookie 橫幅屬輕量 DOM workaround）
safari-browser js "
var ckys = document.querySelectorAll('.cky-overlay, .cky-consent-container, .cky-preference-center, [class^=cky]');
for (var i = 0; i < ckys.length; i++) ckys[i].remove();
'removed ' + ckys.length + ' cky elements';
" --url plaud
```

**Verify dismissed**:

```bash
safari-browser js "
var dlg = document.querySelector('.cky-consent-container');
(dlg && dlg.offsetParent !== null) ? 'STILL_VISIBLE' : 'OK';
" --url plaud
```

### 中文 UI string mapping

Plaud 介面 i18n 跟隨 browser locale。**所有 selector 找按鈕文字時必須雙語檢測**,以下是 dropdown menu 完整 mapping:

| 英文 | 繁中 | 簡中 |
|------|------|------|
| Copy transcript | 複製轉錄 | 复制转录 |
| Copy notes | 複製筆記 | 复制笔记 |
| Export recording | 匯出錄音 | 导出录音 |
| Export transcript | 匯出轉錄 | 导出转录 |
| **Export notes** | **匯出筆記** | **导出笔记** |
| Export mind map | 匯出心智圖 | 导出思维导图 |
| Move to folder | 移至資料夾 | 移至文件夹 |
| Re-transcribe | 重新轉錄 | 重新转录 |
| Move to trash | 移至垃圾桶 | 移至回收站 |
| Markdown | Markdown | Markdown |
| DOCX | DOCX | DOCX |
| Export(按鈕) | 匯出 | 导出 |

**雙語 selector pattern**:

```javascript
var items = document.querySelectorAll('.el-dropdown-menu__item');
var EXPORT_NOTES_LABELS = ['Export notes', 'Export Summary', '匯出筆記', '导出笔记'];
for (var i = 0; i < items.length; i++) {
  var t = items[i].textContent.trim();
  if (EXPORT_NOTES_LABELS.indexOf(t) !== -1 && items[i].offsetParent !== null) {
    items[i].dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
    break;
  }
}
```

> **注意**:用 `dispatchEvent(new MouseEvent('click', {bubbles: true}))` 而不是 `.click()` —— 部分 Vue 元件對 `.click()` 反應不一致(會觸發預設動作而非 menu action)。

### File action dropdown 結構

SKILL.md 早期版本說「`.el-dropdown .el-tooltip__trigger`」是 export trigger,但實際上頁面有 **4 個 `.el-dropdown`**:

| index | 用途 | data-testid / 內容 |
|-------|------|---------------------|
| 0 | Share(分享按鈕) | `data-testid="share-button"` |
| **1** | **File actions(含匯出)** | 通常無 testid,trigger 是 icon button |
| 2 | Tab 切換(摘要/逐字稿) | 含「摘要」/ Summary 文字 |
| 3 | Editor floating menu | `data-testid="editor-floating-menu-handle-button"` |

**正確找法**:遍歷所有 `.el-dropdown`,點開後檢查 menu 是否含「匯出筆記/Export notes」item — 是 → 這就是 file actions dropdown。

### Notes export 在中文版可能不走 Blob

中文版 Plaud「匯出筆記」item 點下去**有時不跳 dialog 也不觸發 Blob**(直接觸發後端 download 或預設處理)。這時 `URL.createObjectURL` 攔截器抓不到內容。

**Fallback: 直接從 `.summary-module` DOM 抽取 markdown**

```javascript
var sm = document.querySelector('.summary-module');
var content = sm.querySelector('.note-myeditor') || sm;
function extractMd(node) {
  var out = '';
  for (var i = 0; i < node.childNodes.length; i++) {
    var c = node.childNodes[i];
    if (c.nodeType === 3) { out += c.textContent; continue; }
    if (c.nodeType !== 1) continue;
    var tag = c.tagName.toLowerCase();
    var txt = c.textContent.trim();
    if (!txt && tag !== 'br') continue;
    if (tag === 'h1') out += '\n# ' + txt + '\n\n';
    else if (tag === 'h2') out += '\n## ' + txt + '\n\n';
    else if (tag === 'h3') out += '\n### ' + txt + '\n\n';
    else if (tag === 'h4') out += '\n#### ' + txt + '\n\n';
    else if (tag === 'li') out += '- ' + txt + '\n';
    else if (tag === 'ul' || tag === 'ol') out += extractMd(c) + '\n';
    else if (tag === 'p' || tag === 'div') out += extractMd(c) + '\n';
    else if (tag === 'strong' || tag === 'b') out += '**' + txt + '**';
    else if (tag === 'em' || tag === 'i') out += '*' + txt + '*';
    else if (tag === 'br') out += '\n';
    else if (tag === 'textarea') out += '# ' + (c.value || '').trim() + '\n\n';
    else out += extractMd(c);
  }
  return out;
}
extractMd(content).replace(/\n{3,}/g, '\n\n');
```

**何時用 fallback**:
- batch_notes.sh 對某檔報「no export btn」但 Summary tab 內容確實有渲染(用 `document.querySelector('.summary-module')` 確認)
- 想要 markdown 但不在意 docx(DOM fallback 無法產生 docx,只能拿 md)
- 不想等 export 流程的 UI 互動成本

DOM extract 出來的 markdown 結構**接近**但不完全等於 Plaud 官方匯出格式(action items 內聯成一行而非條列、缺少 metadata frontmatter),但對 lecture-add Step 2「通讀逐字稿擴充講義」流程已足夠當 reference。

## Vague-query disambiguation

當使用者用模糊用語呼叫 plaud-download（沒帶 hash），**必須**先走本章節決定要抓哪個檔案，**禁止**直接抓 file-list row 1。

### Trigger

下列任一命中即進入 disambiguation：

- 用語含「最新」「剛剛」「那個」「latest」「recent」「just transcribed」
- 用語沒帶 hash（`https://web.plaud.ai/file/{hash}` 形式）
- 用語沒指明描述性檔名

### 3-step protocol

#### Step A — Multi-signal ranking

導航到 file-list 後，列 top 5–10 rows，對每 row 計算 signal score 綜合：

| Signal | 取值 | 權重直覺 |
|--------|------|---------|
| **Sort position** | `createdAt desc` 預設排序，row index 越小越近期 | 中（不是唯一信號）|
| **Filename keyword match** | 跟 session context 的 keyword overlap（grep recent ~20 messages 找專有名詞 / project / topic）| **高** — 通常決定性 |
| **Timestamp filename demotion** | regex `^[0-9]{4}-[0-9]{2}-[0-9]{2}.*[0-9]{2}:[0-9]{2}:[0-9]{2}$` 命中 → 強烈降權重 | 高（負向）|
| **Recency timing** | 對照「剛剛」≈ 幾小時內、「昨天」≈ 24h 內、「最近」≈ 一週內 | 低（消歧用）|

**Timestamp filename = "未命名" signal**：Plaud 對未命名檔案 fallback 用 timestamp（如 `YYYY-MM-DD HH:MM:SS`）；AI 自動命名的描述性檔案才是 user 真在乎的。命中時 demote 到 ranking 後段。

#### Step B — AskUserQuestion fallback

若 ranking 後仍 ambiguous（top 2 candidates 同 priority、或 row 1 是 timestamp filename 但 row 2/3 有 session-context match），**必須** AskUserQuestion 列 top 5 candidates，警告：

```
Row 1 是 timestamp filename「{filename}」（無描述性命名）— 可能不是你想要的。
Row 2「{descriptive filename}」跟 session context 的 {keywords} match。
要哪個？
```

User override path 永遠保留 — 不直接 AI 決定。

#### Step C — Audit trail

選定後印一行讓 user 看到 reasoning：

```
→ Selected: {hash} ({filename})
  Reason: sort=row {N}, keyword match {keywords}, recency match {timeframe}
```

### Caution tale（某次 CollaboratorX research session）

| Step | Bad path（**禁止**）| Good path |
|------|--------------------|-----------|
| User says | `/plaud-download 剛剛轉路的最新檔案` | 同左 |
| Session context | 整 session 都在 CollaboratorX 的研究主題（該 session 的多張 issue 全部與之相關）| 同左 |
| File-list row 1 | `YYYY-MM-DD HH:MM:SS` (timestamp, an unrelated workshop, **無關** CollaboratorX) | 同左 |
| File-list row 2 | `MM-DD <與 CollaboratorX 研究主題相符的錄音標題>` (CollaboratorX match!) | 同左 |
| AI action | **直接抓 row 1**（用 batch workflow 的 collect snippet）→ 下載 AI workshop → user 介入 → 重抓 row 2 → 5 分鐘浪費 + recordings/ 污染 | 走本章節 → ranking 看到 row 1 timestamp demoted + row 2 keyword match CollaboratorX → 直接抓 row 2 |
| Outcome | User 必須說「你可以看 plaud 標題的名稱來決定要選哪一個吧」AI 才轉抓 row 2。Audit trail（來源 marketplace repo 的第一張 issue）記錄為 bug | 0 round trip，0 cleanup |

### 鐵律

- **禁止**直接抓 row 1 在 vague query 下。Row 1 = sort top，**不一定** = user intent
- 必須印 audit trail 一行給 user 看
- session context grep 限制：只看 plaud-download 同 invocation 之前的最近 ~20 turns，且只抓專有名詞（issue refs、repo/project names），不抓動詞或泛用詞，避免 false positive

---

## 執行步驟（單檔）

> **Pre-step**：若 invocation 是 vague query（沒帶 hash 也沒指明描述性檔名），**先走 § Vague-query disambiguation** 決定 hash 後再進 Step 2。

### 0. 載入登入狀態

**多視窗環境**：所有後續操作都加 `--url plaud` 鎖定 Plaud document。Step 0 的第一個 `open` 不能加 `--url plaud`（會 `documentNotFound`）— 用 `documents` precheck 判斷。

```bash
if ! safari-browser documents 2>/dev/null | grep -q plaud; then
  safari-browser open "https://web.plaud.ai"
fi
safari-browser get url --url plaud
```

- URL 不含 `login` → 已登入，繼續
- URL 含 `login` → 狀態過期，需在 Safari 手動登入

### 1. 確認已登入

```bash
safari-browser snapshot --url plaud
```

- 看到「Recent files」或「Add audio」→ OK

### 2. 導航到檔案

```bash
safari-browser js "performance.clearResourceTimings()" --url plaud
sleep 1
safari-browser open "https://web.plaud.ai/file/{hash}" --url plaud
sleep 5
```

---

## 格式 A：下載 SRT

```bash
# 方法 1: Performance API（導航後自動載入）
safari-browser js "
  var entries = performance.getEntriesByType('resource');
  var url = null;
  for (var i = 0; i < entries.length; i++) {
    if (entries[i].name.indexOf('trans_result') !== -1) url = entries[i].name;
  }
  url ? url : 'NOT_FOUND';
" --url plaud

# 方法 2: File Detail API Fallback（Performance API 失敗時用這個）
safari-browser js "
  var token = JSON.parse(localStorage.getItem("pld_tokenstr"));
  var xhr = new XMLHttpRequest();
  xhr.open('GET', 'https://api-apse1.plaud.ai/file/detail/{hash}', false);
  xhr.setRequestHeader('Authorization', token);
  xhr.send();
  var match = xhr.responseText.match(/https[^\"]*trans_result[^\"]*/);
  match ? match[0] : 'NOT_FOUND';
" --url plaud

# 下載並轉換
curl -sL "{S3_URL}" | gunzip > /tmp/plaud_transcript.json
python3 {skill_dir}/scripts/json_to_srt.py /tmp/plaud_transcript.json "{output_path}.srt"
```

### 方法 3: 匯出按鈕直接下載到 ~/Downloads（2026-06 改版後**首選** fallback）

**何時用**：方法 1（Performance API）回 `NOT_FOUND` 且方法 2（detail API）回 `invalid auth header` / `status -3900`（token 已移入 in-memory store，見 [§ Token 已移出 localStorage](#token-已移出-localstorage2026-06)）。

**原理**：Plaud 檔案頁的**匯出選單**（檔案動作 dropdown 內的「匯出轉錄」「匯出筆記」「匯出錄音」）點下去會**直接觸發瀏覽器原生下載到 `~/Downloads`** —— 這條路**不依賴 token、也不依賴 Performance API 側錄**，是改版後**最可靠且能拿到完整檔**的方式（2026-06-12 實測：DOM extract 只抓到 10/77 段，匯出按鈕拿到完整 77 段）。

```bash
# 1. 導到檔案頁、清 cookie dialog
safari-browser open "https://web.plaud.ai/file/{hash}" --url plaud
sleep 6
safari-browser js "var b=document.querySelector('.cky-btn-accept');if(b)b.click();else{var c=document.querySelectorAll('[class^=cky]');for(var i=0;i<c.length;i++)c[i].remove();}'ok';" --url plaud   # 點接受按鈕，沒有就 force-remove

# 2. 開「檔案動作選單」——入口是 .file-action-trigger（2026-07-15 更正）。
#    ⚠️ 不要遍歷 4 個 .el-dropdown 猜 index：dropdown[1] 展開的是「移至資料夾/重新轉錄/
#       移至垃圾桶」組（不含匯出）；只有點 .file-action-trigger 才展開完整匯出選單
#       （複製轉錄/複製筆記/匯出錄音/匯出轉錄/匯出筆記/匯出心智圖）。
cat > open_menu.js <<'JSEOF'
(function(){
  var t = document.querySelector('.file-action-trigger');
  if (!t) return 'NO_TRIGGER';
  t.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}));
  return 'OPENED';
})()
JSEOF
safari-browser js "$(cat open_menu.js)" --url plaud
sleep 1

# 3. 點「匯出轉錄」開啟格式 dialog。用 PLAIN .click()，不要 dispatchEvent。
#    有時要點兩次（item + 內層 span）dialog 才跳出 —— 一併點：
safari-browser js "
  var items=document.querySelectorAll('.el-dropdown-menu__item');
  for(var i=0;i<items.length;i++){
    if(items[i].textContent.trim()==='匯出轉錄' && items[i].offsetParent!==null){
      items[i].click();
      var sp=items[i].querySelector('span'); if(sp) sp.click();
      break;
    }
  }
  'clicked';
" --url plaud
sleep 2

# 3b. 「匯出轉錄」dialog 的控件用 data-testid 精確定位（2026-07-15 實測，比文字匹配穩）：
#     - 格式 select：  [data-testid="share-export-format-select"]（轉錄預設 SRT，通常不用改）
#     - 時間戳記 toggle：[data-testid="share-export-toggle-with_timestamp"]（預設 OFF）
#     - 說話者 toggle：  [data-testid="share-export-toggle-with_speaker"]（預設 ON）
#     - 匯出確認按鈕：  [data-testid="share-export-confirm-button"]
#
#     ⚠️ 兩個 toggle 檢查 event.isTrusted → **合成點擊全部無效**（.click() / dispatchEvent /
#        pointerdown+up 序列都改不動狀態）。但選 SRT 時**根本不用碰 toggle**：
#          • SRT 格式結構固有時間軸（00:00:01,000 --> ...），不勾 with_timestamp 也有；
#          • with_speaker 預設就 ON，輸出 `Speaker N:` / `<name>:` 前綴；
#        → 直接跳到 3c 點匯出即可，得到含時間軸+說話者的完整 SRT。
#        （toggle 狀態判讀：SVG rect fill='#C2C2C2' 灰=OFF、非灰=ON。真要改 toggle 只能靠
#         safari-browser 原生 UI 點擊，但那需把 tab 切到前景、會干擾使用者，通常不值得。）

# 3c. 點 dialog 的「匯出」確認按鈕觸發下載（testid 比文字匹配穩；button .click() 不擋 isTrusted）
cat > click_confirm.js <<'JSEOF'
(function(){
  var b = document.querySelector('[data-testid="share-export-confirm-button"]');
  if (!b) return 'NO_BTN';
  b.click();
  return 'CLICKED';
})()
JSEOF
safari-browser js "$(cat click_confirm.js)" --url plaud
sleep 5

# 4. 檔案下載到 ~/Downloads，命名 <原檔名>-transcript.srt（選 SRT 時）。
#    移到目標位置（raw 逐字稿請確認已被 .gitignore 擋）：
find ~/Downloads -name "*-transcript.srt" -mmin -2   # 確認檔名
mv ~/Downloads/"{原檔名}-transcript.srt" "{output_dir}/"
```

匯出選單對照：**匯出轉錄** → 跳格式 dialog（SRT 預設／TXT／DOCX／PDF + 時間戳記/說話者 checkbox；選 SRT 得 `-transcript.srt`、選 TXT 得 `-transcript.txt`）｜**匯出筆記** → 摘要｜**匯出錄音** → `.ogg` 音檔。

**匯出筆記 MD（同流程、換兩個點；2026-07-15 實測）**：Step 2 開選單後點「**匯出筆記**」而非「匯出轉錄」→ dialog 標題變「匯出摘要」、格式 select **預設 TXT**（不是 Markdown！）。要先開 select、選 Markdown，再點 3c 的匯出：

```bash
# 開格式 select
cat > open_fmt.js <<'JSEOF'
(function(){
  var sel = document.querySelector('[data-testid="share-export-format-select"] .el-select__wrapper');
  if (!sel) return 'NO_SELECT';
  sel.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}));
  return 'OPENED';
})()
JSEOF
safari-browser js "$(cat open_fmt.js)" --url plaud; sleep 1
# 選 Markdown（.el-select-dropdown__item 的 .click() 有效、不擋 isTrusted）
cat > sel_md.js <<'JSEOF'
(function(){
  var it = document.querySelectorAll('.el-select-dropdown__item');
  for (var i = 0; i < it.length; i++) {
    if (it[i].textContent.trim() === 'Markdown' && it[i].offsetParent !== null) { it[i].click(); return 'SELECTED'; }
  }
  return 'NOTFOUND';
})()
JSEOF
safari-browser js "$(cat sel_md.js)" --url plaud; sleep 1
# 再點 share-export-confirm-button（同 3c）→ 下載 <原檔名>-Summary.md
```

> **2026-06-21 實測**：批次匯出多檔時，`find -mmin -1` 可能抓到上一個剛下載的檔（時序），改用各檔的明確檔名 `voice_<id>-transcript.srt` 比對才不會誤判成功/失敗。

> **踩坑（2026-06-12 實測）**：
> - **點選項用 plain `.click()`，不要 `dispatchEvent`** —— Vue 對 dispatchEvent 反應不一致，有時把「匯出轉錄」當導航、開新分頁而非下載。
> - **多重分頁會亂掉狀態**：操作前先關光重複的 plaud 分頁（`safari-browser close --window N --tab-in-window M`），只留一個乾淨分頁。`--url` 撞多個時加 `--first-match`。
> - **點完一定要 verify `~/Downloads`**：行為偶爾不一致（同一動作有時下載、有時 spawn tab），沒看到檔就重點一次。
> - **下載的 `-transcript.txt` / `.ogg` 是 raw 第三方逐字**：放進 repo 的 recordings/ 前確認 `.gitignore` 有擋（`*-transcript.txt` / `*.ogg` / `*.opus` / `*.srt` / `*_notes.md`）。
> - **背景 tab 無法截圖（2026-07-15）**：`safari-browser screenshot` 對 background tab 直接報錯（非干擾設計、不會替你切 tab）。要看 dialog 長相改用 `safari-browser snapshot -i --url plaud`（拿互動元素 ref）或 js 讀 DOM（`document.evaluate` 找節點 → IIFE 回傳 `innerText`），不要指望截圖。
> - **下載檔名慣例（2026-07-15）**：轉錄=`<原檔名>-transcript.srt`、筆記=`<原檔名>-Summary.md`（檔名裡的 `:` 被 Plaud 換成 `_`）。兩者都**不是**本 skill 命名慣例的 `_notes.md`；歸檔到 tracked repo 前要改名讓 `.gitignore` 擋得住（SRT 的 `*.srt` 已擋；`-Summary.md` 要改成 `_notes.md` 結尾才會被 `**/*_notes.md` 擋）。

### 方法 4: DOM extract（**殘缺，最後手段**）

> ⚠️ **只在匯出按鈕也失靈時用，且結果可能不完整**。`.transcript-module` 是**虛擬捲動**，DOM 只渲染**已捲到的可見段**——2026-06-12 實測一份 77 段的逐字稿只抓到 10 段。若用此法，務必先把逐字稿 pane 捲到底載入全部再抓，並核對段數/時長是否合理。

```bash
# 切到逐字稿 tab → 抓扁平 textContent → parse 成 SRT
safari-browser js "
  var labels=['逐字稿','轉錄','Transcript','Transcription'];
  var btns=document.querySelectorAll('button, [role=tab], .el-tabs__item, span');
  for(var i=0;i<btns.length;i++){ var t=btns[i].textContent.trim();
    if(labels.indexOf(t)!==-1 && btns[i].offsetParent!==null){ btns[i].click(); break; } }
  'ok';
" --url plaud
sleep 5
safari-browser js "var m=document.querySelector('.transcript-module'); m?m.textContent.trim():'NONE';" --url plaud \
  | python3 {skill_dir}/scripts/dom_transcript_to_srt.py "{output_path}.srt"
```

`dom_transcript_to_srt.py` 用 `(HH:MM:SS)(Speaker N)` regex 切段、清尾巴 UI 殘留。產出 `[Speaker N] text`。**因虛擬捲動可能殘缺，優先用方法 3 的匯出按鈕。**

> **限制**：DOM 只渲染**已載入**的 segment。長錄音若 `.transcript-module` 用虛擬捲動，可能只抓到可視範圍 — 抓完先確認段數/時長合理，必要時捲到底再抓。短會議(數分鐘)通常一次全載。

---

## 格式 B：下載 DOCX（逐字稿）

```bash
# 1. 清除 performance
safari-browser js "performance.clearResourceTimings()" --url plaud

# 2. 開啟 export 下拉選單
safari-browser js "
  var trigger = document.querySelector('.el-dropdown .el-tooltip__trigger');
  if (trigger) trigger.click();
" --url plaud
sleep 1

# 3. 點擊 Export transcript（用 span-click，更可靠）
safari-browser js "
  var spans = document.querySelectorAll('.el-dropdown-menu__item span');
  for (var i = 0; i < spans.length; i++) {
    if (spans[i].textContent.trim() === 'Export transcript') { spans[i].click(); break; }
  }
" --url plaud
sleep 2

# 4. 選 DOCX 格式
safari-browser js "
  var sel = document.querySelector('.el-select__wrapper');
  if (sel) sel.click();
" --url plaud
sleep 1
safari-browser js "
  var items = document.querySelectorAll('.el-select-dropdown__item');
  for (var i = 0; i < items.length; i++) {
    if (items[i].textContent.trim() === 'DOCX') { items[i].click(); break; }
  }
" --url plaud
sleep 1

# 5. 點擊 Export 按鈕
safari-browser js "
  var btns = document.querySelectorAll('button');
  for (var i = 0; i < btns.length; i++) {
    if (btns[i].textContent.trim() === 'Export') { btns[i].click(); break; }
  }
" --url plaud
sleep 5

# 6. 取得 DOCX S3 URL（逐字稿用 trans- 前綴）
safari-browser js "
  var entries = performance.getEntriesByType('resource');
  var url = null;
  for (var i = 0; i < entries.length; i++) {
    var name = entries[i].name;
    if ((name.indexOf('trans-') !== -1 || name.indexOf('document-download') !== -1) && name.indexOf('.docx') !== -1) {
      url = name;
    }
  }
  url ? url : 'NOT_FOUND';
" --url plaud

# 7. 下載
curl -sL "{DOCX_S3_URL}" -o "{output_path}.docx"
```

---

## 格式 C：下載筆記（Notes）

### Blob 攔截器（Markdown 必需）

必須在 `open` 後、Export 前安裝。每次換頁都要重裝：

```javascript
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
```

### 筆記下載流程

```bash
# 1. 安裝 Blob 攔截器
safari-browser js "{INTERCEPTOR_JS}" --url plaud

# 2. 切換到 Summary tab
safari-browser js "
  var btns = document.querySelectorAll('button');
  for (var i = 0; i < btns.length; i++) {
    if (btns[i].textContent.trim() === 'Summary') { btns[i].click(); break; }
  }
" --url plaud
sleep 2

# 3. Export notes → Markdown
#    先清除 capturedExports
safari-browser js "window.__capturedExports = {};" --url plaud

#    開啟 dropdown → Export notes
safari-browser js "
  var trigger = document.querySelector('.el-dropdown .el-tooltip__trigger');
  if (trigger) trigger.click();
" --url plaud
sleep 1

safari-browser js "
  var spans = document.querySelectorAll('.el-dropdown-menu__item span');
  for (var i = 0; i < spans.length; i++) {
    var text = spans[i].textContent.trim();
    if (text === 'Export notes' || text === 'Export Summary') {
      spans[i].click(); break;
    }
  }
" --url plaud
sleep 2

#    選 Markdown → Export
#    （選格式 + 按 Export 按鈕，同 DOCX 流程）

# 4. 擷取 Markdown 內容
safari-browser js "
  var exp = window.__capturedExports;
  var keys = Object.keys(exp);
  keys.length > 0 ? exp[keys[0]].content : 'NOT_FOUND';
" --url plaud

# 5. Export notes → DOCX（同 transcript DOCX 流程，但 S3 URL 前綴為 summary-）
```

### Notes Export 特殊情況

- **Q&A 類型檔案**：「Export notes」可能是子選單，展開後有「Export Summary」等選項
- **Blob 攔截器必須每頁重裝**：JS context 在頁面導航後重置
- **Markdown MIME type**：`text/markdown;charset=utf-8;`（注意結尾的分號）
- **中文版 / Cookie consent dialog**：見 [§ 中文 UI 與 Cookie consent dialog](#中文-ui-與-cookie-consent-dialogcritical) — 中文版「匯出筆記」可能不走 Blob,需用 DOM extract fallback;Cookie dialog 開啟期間所有按鈕 selector 都會失效

---

## 下載失敗處理

### 失敗類型與處理

| 失敗類型 | 症狀 | 原因 | 處理方式 |
|---------|------|------|---------|
| **SRT timing** | SRT no URL，DOCX 成功 | Performance API 快取 | 重跑批次腳本即可 |
| **SRT 兩路都 NOT_FOUND** | 方法1 NOT_FOUND + 方法2 `invalid auth header`/`-3900` | 2026-06 token 移出 localStorage(JS 摸不到) + 逐字稿從 IDB 快取渲染不發網路請求 | **首選**用匯出按鈕（[§ 方法 3](#方法-3-匯出按鈕直接下載到-downloads2026-06-改版後首選-fallback)）：dropdown→匯出轉錄→直接下載完整檔到 ~/Downloads。DOM extract（方法 4）殘缺、僅最後手段 |
| **未轉錄** | 所有格式都失敗 | 檔案在 Plaud 上從未轉錄 | 到檔案頁面按 Generate 啟動轉錄 |
| **平台錯誤** | Try Again 後仍失敗 | 音檔損壞或格式問題 | 標記跳過 |
| **Notes no export btn** | 筆記匯出按鈕不存在 | 該檔案未生成摘要 **或** 中文版 UI（按鈕變「匯出筆記」） | 確認 Summary tab 有內容 → 用 DOM extract fallback（見上節）|
| **Notes no blob** | MD 無內容 | 攔截器未正確安裝 **或** 中文版「匯出筆記」不走 Blob 路徑 | 改用 DOM extract fallback |
| **Cookie consent dialog** | textContent 都是「我們重視您的隱私」、按鈕點下去跳無關 dialog | 新 session / 清過 cookies → CookieYes overlay 顯示 | dismiss `.cky-btn-accept` 或強制 `removeAll('.cky-*')` 元素 |
| **整站白畫面（module MIME）** | 同 session 突發；`#app` 空、body 只剩 inline script、`webpackChunkplaud_web` undefined、開新分頁也一樣；但 XHR 抓 bundle 卻 200 + 正確 MIME | HTTP cache 有壞的 `text/plain` 條目，ES module 嚴格 MIME 檢查 → 整個 import graph 拒絕執行 | `fetch(url, {cache:'reload'})` 刷新全部 JS/CSS 快取條目 → 重新導航（見 [§ 整站白畫面](#整站白畫面module-mime-快取毒化2026-07-19-實測)，**不需**清快取） |
| **Session expired** | 被導向登入頁 | Session 過期 | Safari session 永久保持，極少發生 |

### 重試策略

1. **批次完成後**：直接重跑同一個腳本，會自動跳過已完成的檔案
2. **SRT timing failures**：重跑 `batch_srt_docx.sh`，通常第 2-3 次可恢復
3. **未轉錄檔案**：無法透過腳本恢復，需在 Plaud 網頁手動操作
4. **反覆失敗**：標記為平台問題，跳過

### 整站白畫面：module MIME 快取毒化（2026-07-19 實測）

**情境**：同一 session 內第一個檔案匯出成功，導航到第二個檔案後整站白畫面——首頁也白、關分頁開新分頁也白、`location.reload()` 無效。與根因 1（防止跨網站追蹤）的判別點：**前一刻還正常**（設定沒變過），所以不是設定問題。

**症狀鏈**（依序確認，全部命中即此根因）：

1. `#app` 存在但 `innerHTML.length === 0`；`document.body.textContent` 只剩 inline script 文字（Stripe loader）
2. `document.readyState === 'complete'`、`navigator.serviceWorker.controller === null`（SW 不是兇手）
3. `typeof window.webpackChunkplaud_web === 'undefined'` —— entry bundle **完全沒執行**（不是執行到一半 crash）
4. 用 XHR 同步抓 entry / chunk URL 卻都 **200 + 正確 JS MIME + 完整內容** —— 網路沒問題

**關鍵診斷**（一發直接拿到根因錯誤訊息）——entry 是 `type=module`，動態 re-import 會重演失敗並吐出原因：

```javascript
(function(){
  var s = document.querySelectorAll('script[type=module]');
  if (!s.length) return 'NO_MODULE_SCRIPT';
  window.__importResult = 'pending';
  import(s[0].src).then(function(){ window.__importResult = 'OK'; })
    .catch(function(e){ window.__importResult = 'FAIL: ' + String(e).slice(0, 200); });
  return 'FIRED';
})()
// sleep 8 後讀 window.__importResult →
// "FAIL: TypeError: 'text/plain' is not a valid JavaScript MIME type."  ← 即此根因
```

**原理**：ES module 的 MIME 檢查是「全有全無」——import graph 裡**任一個** chunk 拿到非 JS MIME（此例：HTTP cache 裡一筆壞的 `text/plain` 回應），整個 entry 完全不執行，連 webpack runtime prologue 都不跑。這跟 classic script 部分失敗還能跑的行為完全不同。

**修法**（不清快取、不註銷 SW、不動任何持久狀態）：`fetch(url, {cache:'reload'})` 繞過快取讀取但**會把新鮮回應寫回快取**，等於逐條目修復。對 Performance API 列出的全部 JS/CSS 資源各發一發，完成後重新導航即恢復：

```javascript
(function(){
  var res = performance.getEntriesByType('resource');
  var urls = {};
  for (var j = 0; j < res.length; j++) {
    var n = res[j].name;
    if (n.indexOf('.js') !== -1 || n.indexOf('.css') !== -1) urls[n] = 1;
  }
  var list = Object.keys(urls);
  window.__refreshDone = 0;
  for (var i = 0; i < list.length; i++) {
    fetch(list[i], {cache: 'reload'})
      .then(function(){ window.__refreshDone++; })
      .catch(function(){ window.__refreshDone++; });
  }
  return 'FIRED ' + list.length;
})()
// sleep 10 → 確認 window.__refreshDone === 總數 → safari-browser open 重新導航 → app 恢復 mount
```

> 注意：Performance API 的 `transferSize === 0 && decodedBodySize === 0` 在 Safari **不可靠**（cache hit / 無 Timing-Allow-Origin 都會回 0），不能單獨當「資源載入失敗」的證據——要用上面的動態 import 錯誤訊息定根因。

---

## 檔案命名規範

> **改名操作**請使用 `plaud-manage` skill（`/plaud-manage rename`）。

輸出檔案應與原始音訊同名：
```
第01堂-Python基礎.srt            <- SRT 字幕
第01堂-Python基礎.docx           <- Word 逐字稿
第01堂-Python基礎_notes.md       <- AI 筆記 Markdown
第01堂-Python基礎_notes.docx     <- AI 筆記 Word
```

## 帳號資訊

- Email：（維護者的 Plaud 帳號；封存時已移除，見 archive/README.md「Scrub delta」）
- 方案：（封存時已移除；配額細節依當時帳號）
- 密碼：存在 macOS Keychain
