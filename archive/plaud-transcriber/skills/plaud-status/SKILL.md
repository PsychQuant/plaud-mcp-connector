---
name: plaud-status
description: |
  列出 Plaud 上的所有資料夾和檔案，查看轉錄狀態，收集檔案 ID。
  當用戶提到「Plaud 進度」、「轉錄好了嗎」、「檢查字幕」、「Plaud 有哪些檔案」、「列出 Plaud 檔案」、「Plaud 資料夾」時使用。
argument-hint: "[folders | file_name | folder_name]"
---

# Plaud File List & Status

列出 web.plaud.ai 上的資料夾和檔案。

## 使用方式

- **無參數 / `folders`**：列出所有資料夾及檔案數
- **指定資料夾名稱**：列出該資料夾內的所有檔案及轉錄狀態
- **指定檔案名稱**：查詢該檔案的轉錄狀態

## Bundled Scripts

- **`scripts/collect_files.js`**：從虛擬列表收集所有檔案 ID 和名稱（Plaud 使用 vue-recycle-scroller）

## 關鍵知識

### URL 模式

- 首頁：`https://web.plaud.ai`
- 檔案詳情頁：`https://web.plaud.ai/file/{file_hash}`

### 虛擬列表

Plaud 使用 vue-recycle-scroller，只有可見的項目存在於 DOM。
收集完整檔案清單需要 async scrolling。
File hash 從 `data-testid="file-list-item-{hash}"` 取得。

### 左側資料夾選擇

`find text` 和 CSS selector（如 `.folder-list a[title*=...]`）都不可靠。
**用 `<a>` 元素搜尋文字內容**，這是實測最穩定的方法：

```bash
safari-browser js "
  var links = document.querySelectorAll('a');
  var found = false;
  for (var i = 0; i < links.length; i++) {
    if (links[i].textContent.indexOf('{資料夾關鍵字}') !== -1) {
      links[i].click();
      found = true;
      break;
    }
  }
  found ? 'clicked' : 'not found';
" --url plaud
sleep 3
```

**注意**：用 `var` 而非 `const`/`let`，Plaud 的 Webpack 環境中偶爾會衝突。

## 執行步驟

### 0. 檢查登入狀態

**多視窗環境**：所有後續操作都加 `--url plaud`。Step 0 用 `documents` precheck 避免 first-call 的 `documentNotFound`。

```bash
if ! safari-browser documents 2>/dev/null | grep -q plaud; then
  safari-browser open "https://web.plaud.ai"
fi
safari-browser get url --url plaud
```

- URL 不含 `login` → 已登入，繼續步驟 2
- URL 含 `login` → 如果 Safari 未登入，請用戶在 Safari 中手動登入 web.plaud.ai

### 1. 確認已登入

```bash
safari-browser snapshot --url plaud
```

看到「Recent files」或「Add audio」→ OK。

### 2. 列出資料夾

用 `snapshot`（非 `-i`）可以看到完整的頁面結構，包含左側資料夾列表：

```bash
safari-browser snapshot --url plaud
```

從 snapshot 文字中解析資料夾列表，格式如：
```
listitem:
  paragraph: "資料夾名稱 (檔案數)"
```

整理成表格回報給用戶。

### 3. 列出特定資料夾內的檔案

#### 方法 A：少量檔案（< 20 個）

直接用 snapshot 解析即可：

```bash
safari-browser find text "{資料夾名稱}" click --url plaud
sleep 3
safari-browser snapshot --url plaud
```

#### 方法 B：大量檔案（>= 20 個，有虛擬列表）

使用 bundled JS 腳本收集完整清單：

```bash
# 先導航到資料夾
safari-browser find text "{資料夾名稱}" click --url plaud
sleep 3

# 用 collect_files.js 收集所有檔案
# 將腳本內容作為 eval 參數，或 Read 後拼接
safari-browser js "$(cat {skill_dir}/scripts/collect_files.js)" --url plaud
```

輸出格式：
```
66 files
00000000000000000000000000000003|||001 課程錄音範例甲
00000000000000000000000000000004|||002 課程錄音範例乙
...
```

建議將結果存到暫存檔供 download skill 使用：
```bash
echo "{result}" | tail -n +2 > /tmp/plaud_files.txt
```

### 4. 列出最近的檔案（All files）

```bash
safari-browser find text "All files" click --url plaud
sleep 3
safari-browser snapshot --url plaud
```

### 5. 查詢特定檔案狀態

在列表中找到該檔案，回報狀態並建議下一步：

- **Generated** → 可以用 download skill 下載（SRT/Word/摘要）
- **Generating...** → 等待中，約 10-30 分鐘/檔案
- **未轉錄** → 需要進入詳情頁啟動轉錄

### 6. 截圖存檔

```bash
safari-browser screenshot /tmp/plaud-status.png
```

## 狀態對照表

| 狀態 | 意義 | 建議動作 |
|------|------|----------|
| **Generated** | 轉錄完成 | 可下載 SRT/Word/摘要 |
| **Generating...** | 轉錄中 | 等待，約 10-30 分鐘/檔案 |
| 無狀態標記 | 尚未啟動轉錄 | 進入詳情頁啟動轉錄 |

## 帳號資訊

- Email：（維護者的 Plaud 帳號；封存時已移除，見 archive/README.md「Scrub delta」）
- 方案：（封存時已移除；配額細節依當時帳號）
- 密碼：存在 macOS Keychain
