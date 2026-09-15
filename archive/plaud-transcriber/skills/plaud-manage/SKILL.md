---
name: plaud-manage
description: |
  管理 Plaud 上的檔案：改名、搬移資料夾、刪除、觸發轉錄。
  當用戶提到「Plaud 改名」、「搬到資料夾」、「Plaud 刪除」、「重新轉錄」、「啟動轉錄」時使用。
argument-hint: "[rename | move | delete | generate] [file_name_or_hash]"
---

# Plaud File Management

管理 web.plaud.ai 上的檔案。

## 操作一覽

| 操作 | 說明 | 需要在哪個頁面 |
|------|------|--------------|
| **rename** | 重新命名檔案 | 檔案詳情頁 |
| **move** | 搬移到不同資料夾 | 檔案詳情頁 |
| **delete** | 刪除檔案 | 檔案列表或詳情頁 |
| **generate** | 啟動/重新觸發 AI 轉錄 | 檔案詳情頁 |

---

## 前置：開啟 Plaud

**多視窗環境**：所有後續操作都加 `--url plaud` 鎖定 Plaud document。Step 0 用 `documents` precheck 避免 first-call 的 `documentNotFound`。

```bash
if ! safari-browser documents 2>/dev/null | grep -q plaud; then
  safari-browser open "https://web.plaud.ai"
fi
```

確認 URL 是 `web.plaud.ai`（非 login 頁）。如果被導向登入頁 → 需用戶在 Safari 手動登入。

---

## Rename（改名）

檔案詳情頁有一個 `textbox "Enter the file name"` 可直接改名。

### 方法 1：用 safari-browser fill（推薦）

```bash
# 導航到檔案頁
safari-browser open "https://web.plaud.ai/file/{hash}" --url plaud
sleep 3

# snapshot 取得 textbox ref
safari-browser snapshot --url plaud

# fill 會自動清除舊內容
safari-browser fill @{ref} "新檔案名稱" --url plaud
```

### 方法 2：用 JS（批次改名時）

```bash
safari-browser js "
  var input = document.querySelector('.file-detail__name input, input[type=text]');
  if (input) {
    var nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    nativeInputValueSetter.call(input, '新檔案名稱');
    input.dispatchEvent(new Event('input', {bubbles: true}));
    input.blur();
    'DONE';
  } else {
    'INPUT_NOT_FOUND';
  }
" --url plaud
```

### 注意事項

- 改名後 Plaud 自動儲存，不需額外確認
- 改名只影響 Plaud 平台顯示，不影響已下載的本機檔案
- 建議在下載前先改名，下載時用正確名稱

---

## Move（搬移資料夾）

在檔案詳情頁，通常有一個資料夾標籤或 Move 按鈕。

```bash
# 導航到檔案頁
safari-browser open "https://web.plaud.ai/file/{hash}" --url plaud
sleep 3

# snapshot 找到 Move 或資料夾相關的按鈕
safari-browser snapshot --url plaud

# 通常在右側面板有資料夾名稱，點擊可更換
# 具體 UI 元素需要 snapshot 確認，Plaud 偶爾更新 UI
```

**注意**：Move 的 UI 元素不如 rename 穩定，每次操作前先 snapshot 確認。

---

## Delete（刪除）

### 從檔案列表刪除

```bash
# 在檔案列表中，右鍵或長按檔案項目會出現選單
# 具體操作需 snapshot 確認 UI 狀態
safari-browser snapshot --url plaud
```

### 從詳情頁刪除

```bash
# 檔案詳情頁通常有一個刪除按鈕（垃圾桶圖示）
safari-browser snapshot --url plaud
# 找到刪除按鈕的 ref 並點擊
# 會出現確認對話框
```

**警告**：刪除後檔案會移到 Trash，可在 Trash 中恢復。

---

## Generate（啟動轉錄）

對已上傳但未轉錄的檔案啟動 AI 轉錄。這與 upload 不同：upload 是上傳新檔案，generate 是對已存在的檔案觸發轉錄。

### 單檔觸發

```bash
# 導航到檔案詳情頁
safari-browser open "https://web.plaud.ai/file/{hash}" --url plaud
sleep 3
safari-browser snapshot --url plaud

# 找到 Generate / Transcribe 按鈕並點擊
# 按鈕文字可能是 "Generate", "Transcribe", 或 "Try Again"
safari-browser js "
  var btns = document.querySelectorAll('button');
  var clicked = false;
  for (var i = 0; i < btns.length; i++) {
    var text = btns[i].textContent.trim();
    if (text === 'Generate' || text === 'Transcribe' || text === 'Try Again') {
      btns[i].click();
      clicked = true;
      break;
    }
  }
  clicked ? 'CLICKED' : 'BUTTON_NOT_FOUND';
" --url plaud
sleep 2

# 可能需要選擇語言或模板
safari-browser snapshot --url plaud
```

### 批次觸發

使用 plaud-download skill 中的 `batch_generate.sh` 腳本：

```bash
bash {plaud-download_skill_dir}/scripts/batch_generate.sh /tmp/plaud_files.txt
```

**注意**：
- 轉錄約需 10-30 分鐘/檔案
- Plaud 有每日觸發上限（依方案而異）
- `batch_generate.sh` 會偵測 "daily limit" 並自動停止

---

## 批次操作

### 批次改名

```bash
# 準備改名清單（hash|||新名稱）
cat > /tmp/plaud_rename.txt << 'EOF'
00000000000000000000000000000001|||YYYYMMDD_01_StudentD家教
00000000000000000000000000000002|||YYYYMMDD_02_課程錄音範例
EOF

# 逐一改名
while IFS='|||' read -r hash name; do
  safari-browser open "https://web.plaud.ai/file/$hash" --url plaud
  sleep 3
  safari-browser snapshot --url plaud
  # 找到 textbox ref 並 fill
  # ...
done < /tmp/plaud_rename.txt
```

---

## 關鍵知識

- **JS 用 `var`**，不要用 `const`/`let`
- **每次操作前先 snapshot**：Plaud UI 經常更新，按鈕位置和文字可能變化
- **Auto-save**：改名和搬移操作會自動儲存
- **Session 過期**：約 10-15 分鐘，被導向登入頁時需重新登入

## 帳號資訊

- Email：（維護者的 Plaud 帳號；封存時已移除，見 archive/README.md「Scrub delta」）
- 方案：（封存時已移除；配額細節依當時帳號）
- 密碼：存在 macOS Keychain
