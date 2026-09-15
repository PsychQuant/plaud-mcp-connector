---
name: plaud-search
description: |
  搜尋 Plaud 上的檔案（按日期、關鍵字、資料夾）。
  當用戶提到「找 Plaud 檔案」、「Plaud 搜尋」、「Plaud 有沒有某個錄音」、「找某天的錄音」時使用。
argument-hint: "[keyword | date (YYYY-MM-DD) | folder_name]"
---

# Plaud Search

在 web.plaud.ai 上搜尋檔案。支援關鍵字、日期、資料夾篩選。

## 搜尋策略

### Step 0：開啟 Plaud

```bash
# 多視窗環境：用 documents precheck 確認 Plaud 是否已開
if ! safari-browser documents 2>/dev/null | grep -q plaud; then
  safari-browser open "https://web.plaud.ai"
  sleep 3
fi
```

確認 URL 是 `web.plaud.ai`（非 login 頁）。如果被導向登入頁 → 需用戶在 Safari 手動登入。

**所有後續操作都加 `--url plaud`** 鎖定 Plaud document，避免被其他視窗（gmail/github 等）干擾。

### 策略 1：用 All files 列表搜尋（推薦，最快）

All files 按時間排序顯示所有檔案，適合找近期錄音。

```bash
# 點 All files
safari-browser js "
  var links = document.querySelectorAll('a');
  for (var i = 0; i < links.length; i++) {
    if (links[i].textContent.indexOf('All files') !== -1) {
      links[i].click(); break;
    }
  }
" --url plaud
sleep 3

# 取得可見檔案清單（含日期和資料夾標籤）
safari-browser snapshot --url plaud
```

從 snapshot 中的 `listitem` 找到匹配的檔案。每個 listitem 格式如：
```
listitem: 檔案名稱 MM-DD HH:MM | 時長 資料夾名稱
```

### 策略 2：Plaud 內建搜尋

```bash
# 點 Search
safari-browser js "
  var links = document.querySelectorAll('a');
  for (var i = 0; i < links.length; i++) {
    if (links[i].textContent.trim() === 'Search') {
      links[i].click(); break;
    }
  }
" --url plaud
sleep 2

# snapshot 取得搜尋框 ref
safari-browser snapshot --url plaud

# 填入關鍵字
safari-browser fill @{ref} "搜尋關鍵字" --url plaud
safari-browser press Enter --url plaud
sleep 3

# 查看結果
safari-browser snapshot --url plaud
```

**注意**：Plaud 搜尋只匹配檔案名稱，不搜尋轉錄內容。

### 策略 3：指定資料夾內搜尋

先導航到資料夾，再掃描檔案清單：

```bash
# 導航到資料夾（用 <a> 元素搜尋，最穩定）
safari-browser js "
  var links = document.querySelectorAll('a');
  for (var i = 0; i < links.length; i++) {
    if (links[i].textContent.indexOf('{資料夾關鍵字}') !== -1) {
      links[i].click(); break;
    }
  }
" --url plaud
sleep 3

# 少量檔案直接 snapshot
safari-browser snapshot --url plaud

# 大量檔案用 collect_files.js（plaud-status skill 的 scripts/）
safari-browser js "$(cat {plaud-status_skill_dir}/scripts/collect_files.js)" --url plaud
```

### 策略 4：按日期搜尋（跨資料夾）

如果知道錄音日期但不確定在哪個資料夾：

1. 先用策略 1（All files）找，因為按時間排序
2. 如果日期太遠（超出可視範圍），需要滾動或用策略 2 搜尋日期字串（如 `03-12`）

```bash
# All files 頁面，用 JS 掃描可見項目中的日期
safari-browser js "
  var items = document.querySelectorAll('[data-testid^=\"file-list-item-\"]');
  var result = [];
  for (var i = 0; i < items.length; i++) {
    var text = items[i].textContent;
    if (text.indexOf('{MM-DD}') !== -1) {
      var testid = items[i].getAttribute('data-testid');
      var hash = testid.replace('file-list-item-', '');
      var nameEl = items[i].querySelector('.file-list-item__filename');
      var name = nameEl ? nameEl.textContent.trim() : 'UNKNOWN';
      result.push(hash + ' ||| ' + name);
    }
  }
  result.length > 0 ? result.join('\n') : 'NOT_FOUND';
" --url plaud
```

## 搜尋結果格式

回報時包含：
- 檔案名稱
- file hash（供 download/manage skill 使用）
- 錄音日期與時長
- 所在資料夾

```
| 檔案名稱 | Hash | 日期 | 時長 | 資料夾 |
|---------|------|------|------|--------|
| StudentD家教 | 0000...01 | MM-DD HH:MM | 1h 9m | <folder> |
```

## 關鍵知識

- **JS 用 `var`**，不要用 `const`/`let`（Plaud Webpack 環境相容性）
- **虛擬列表**：大量檔案時只有可見的在 DOM，需 async scrolling 收集完整清單
- **檔案名稱 selector**：`.file-list-item__filename`
- **File hash**：`data-testid="file-list-item-{hash}"`
- **直接導航**：找到 hash 後可直接 `https://web.plaud.ai/file/{hash}`

## 帳號資訊

- Email：（維護者的 Plaud 帳號；封存時已移除，見 archive/README.md「Scrub delta」）
- 方案：（封存時已移除；配額細節依當時帳號）
- 密碼：存在 macOS Keychain
