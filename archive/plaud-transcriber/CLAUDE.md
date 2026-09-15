# Plaud Transcriber Plugin

## 架構

```
plaud-transcriber/
├── README.md
├── CLAUDE.md
└── skills/
    ├── plaud-upload/SKILL.md        # 上傳 + 啟動轉錄
    ├── plaud-status/                # 檢查進度
    │   ├── SKILL.md
    │   └── scripts/collect_files.js  # 虛擬列表檔案收集
    └── plaud-download/              # 下載轉錄結果
        ├── SKILL.md
        └── scripts/
            ├── json_to_srt.py           # JSON → SRT 轉換
            ├── batch_srt_docx.sh        # SRT + 逐字稿 DOCX 批次（含 auto re-login）
            ├── batch_notes.sh           # 筆記 MD + DOCX 批次（含 auto re-login + blob 攔截）
            ├── batch_generate.sh        # 批次觸發轉錄（未轉錄檔案用）
            ├── batch_download.sh        # [舊版] 僅 SRT 批次
            ├── batch_docx_download.sh   # [舊版] 僅 DOCX 批次
            └── batch_notes_download.sh  # [舊版] 僅 Notes 批次
```

**推薦腳本**：`batch_srt_docx.sh` + `batch_notes.sh`（合併版，含 auto re-login）
**轉錄觸發**：`batch_generate.sh`（批次觸發未轉錄檔案的 Generate）

## 核心概念

### safari-browser session

safari-browser 使用 Safari 的原生 session，登入狀態由 Safari 自動管理（cookies 持久化），無需手動 state save/load。批次腳本內建 auto re-login 作為 fallback。

### 下載機制

有三種不同的下載機制，處理方式完全不同：

1. **Performance API → S3 URL**（SRT）：頁面載入時自動出現在 performance entries
2. **Export UI → API → S3 URL**（transcript DOCX、notes DOCX）：需觸發 export 流程，API 產生 DOCX 上傳到 S3
3. **Export UI → Blob 攔截**（notes MD）：Client-side Blob 下載，headless browser 無法攔截檔案下載，必須覆寫 `URL.createObjectURL`

### S3 URL 前綴

- `trans_result` — SRT 用（5 分鐘有效）
- `trans-` — 逐字稿 DOCX 用（20 分鐘有效）
- `summary-` — 筆記 DOCX 用（20 分鐘有效）

### 虛擬列表

Plaud 用 vue-recycle-scroller，DOM 中只有可見項目。
- 滾動容器：`.vue-recycle-scroller.file-list-container__wrapper`
- 檔案名稱 selector：`.file-list-item__filename`（不要用 `.file-name` 或 `innerText`，會混入 metadata）
- File hash：`data-testid="file-list-item-{hash}"`
- 收集：`scrollTop += 200` + `setTimeout 300ms` 循環
- 少量檔案（≤ 30）可能不需要滾動，直接從 DOM 收集

## 批次工作流程（整個資料夾 4 格式下載）

經驗證的最佳流程：

```bash
# 0. [如有未轉錄檔案] 批次觸發轉錄，等 1-2 小時完成後再下載
bash {skill_dir}/scripts/batch_generate.sh /tmp/plaud_files_untranscribed.txt

# 1. 收集檔案清單（導航到資料夾後執行 collect_files.js）
#    存到 /tmp/plaud_files_xxx.txt

# 2. 下載 SRT + 逐字稿 DOCX
bash {skill_dir}/scripts/batch_srt_docx.sh /tmp/plaud_files_xxx.txt "/path/to/output"

# 3. 下載筆記 MD + DOCX
bash {skill_dir}/scripts/batch_notes.sh /tmp/plaud_files_xxx.txt "/path/to/output"

# 4. 重跑 SRT+DOCX 補 timing failures（SRT 偶爾因 Performance API 快取失敗，retry 通常可恢復）
bash {skill_dir}/scripts/batch_srt_docx.sh /tmp/plaud_files_xxx.txt "/path/to/output"
```

### batch_generate.sh

批次觸發未轉錄檔案的 Generate。每個檔案約 10 秒：
- 導航到 `web.plaud.ai/file/{hash}`
- 檢查狀態：`ready` / `already_generating` / `generated` / `failed`
- `ready` → 點擊 `.liner-btn-txt` → 確認 "Generate now"
- `failed` → 嘗試 "Try Again" → "Generate now"
- 含 auto re-login

## 側邊欄資料夾導航

**重要**：`find text` 和 CSS selector 都不可靠。用 `<a>` 元素搜尋文字：

```bash
safari-browser js "
  var links = document.querySelectorAll('a');
  var found = false;
  for (var i = 0; i < links.length; i++) {
    if (links[i].textContent.indexOf('{關鍵字}') !== -1) {
      links[i].click();
      found = true;
      break;
    }
  }
  found ? 'clicked' : 'not found';
"
```

## JS 語法注意

批次腳本中的 `eval` 必須用 `var` 而非 `const`/`let`。
Plaud 的 Webpack 環境中 `const`/`let` 偶爾會衝突。

## 失敗類型

| 失敗類型 | 症狀 | 可恢復 | 處理 |
|---------|------|--------|------|
| **SRT timing** | SRT no URL，DOCX 成功 | 是 | 重跑批次腳本 |
| **未轉錄** | 所有格式都失敗 | 是 | 用 `batch_generate.sh` 批次觸發 |
| **平台錯誤** | Try Again 後仍失敗 | 否 | 標記跳過 |
| **Notes no export btn** | 筆記匯出按鈕不存在 | 否 | 跳過或手動檢查 |
| **Session expired** | 被導向登入頁 | 是 | 腳本自動處理 |
| **每日上限** | "Request exceeds daily limit" | 是 | 等 24 小時後重試 |

## 常見陷阱

- **白畫面／資源被當檔案下載（2026-07-18 確認根因）**：Safari「設定 > 隱私權 > **防止跨網站追蹤**」勾選會導致 web.plaud.ai 故障。遇到先檢查此設定，**不要**清 cache / 註銷 service worker（2026-07-15 曾誤判為 cache 問題，清了沒用還一度背鍋 → 2026-07-16 v1.11.1 全面禁令 → 2026-07-18 根因澄清後解除，v1.12.0 恢復 force-remove fallback）
- **上傳後 modal 殘留**：上傳完成後「匯入音訊」modal 會殘留在 DOM，擋住所有後續點擊。`remove()` 不可靠（Vue 狀態未清理）。最可靠做法：`safari-browser open "https://web.plaud.ai"` 直接導航離開，徹底重載頁面
- `performance.clearResourceTimings()` 必須在每次 `open` 前執行，否則取到舊 URL
- `safari-browser find text "Export"` 會匹配太多元素（7+），改用 JS querySelector 或 role=menuitem
- `.el-dropdown .el-tooltip__trigger` 打開的是檔案管理選單，不是 Export 選單
- Export dropdown 觸發元素是 `<span data-testid="share-button">`（不是 `<button>`），用 `[data-testid="share-button"]` selector
- "Copy & export" 文字在某些頁面不存在（只有圖標），不要用文字搜尋找觸發按鈕
- "Export notes" 是子選單，需先 click 展開後選 "Export Summary" 等子項目
- Cookie overlay（CookieYes）會阻擋按鈕偵測，需先用 `button[data-cky-tag="accept-button"]` 關閉
- Blob 攔截器在每次頁面導航後會失效（JS context 重置），必須重裝
- 部分檔案的「Export notes」是子選單（Q&A 類型有多種筆記），需額外處理
- Export 確認按鈕：用 `data-testid="share-export-confirm-button"` 或搜尋 text `'Export'` 的 button

## 帳號

- Email：（維護者的 Plaud 帳號；封存時已移除，見 archive/README.md「Scrub delta」）
- 方案：（封存時已移除；配額細節依當時帳號）
- 密碼：`security find-generic-password -s "plaud" -a "<plaud-account-email>" -w`
- 超過每日上限時會顯示 "Request exceeds daily limit"，需等 24 小時後重試

## 編輯指南

- SKILL.md 內容基於實際測試經驗，修改前請確認 Plaud UI 是否有變動
- 批次腳本都支援斷點續傳（已存在的檔案自動跳過）
- 下載腳本連續 5 個檔案都失敗時自動停止（避免浪費時間在未轉錄檔案上）
- `batch_generate.sh` 偵測到帳號的每日轉錄上限時自動停止
- 新增格式時，確認下載機制（S3 URL vs Blob）並加到格式支援表
- 推薦用合併版腳本（`batch_srt_docx.sh` + `batch_notes.sh`），舊版分離腳本保留但不推薦
