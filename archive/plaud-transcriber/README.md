# plaud-transcriber

Plaud AI 轉錄工具，透過 `safari-browser` 自動化操作 web.plaud.ai。

## Skills

| Skill | 說明 |
|-------|------|
| `/plaud:upload` | 上傳音訊/影片到 Plaud 並啟動轉錄 |
| `/plaud:status` | 檢查轉錄進度、列出檔案 |
| `/plaud:download` | 下載轉錄結果（SRT、DOCX 逐字稿、筆記 MD/DOCX） |

## 支援格式

| 格式 | 說明 | 下載機制 |
|------|------|----------|
| **SRT** | 字幕檔（含時間軸 + 說話者） | Performance API → S3 URL |
| **transcript DOCX** | 逐字稿 Word 文件 | Export UI → API → S3 URL |
| **notes MD** | AI 筆記 Markdown | Export UI → Blob 攔截 |
| **notes DOCX** | AI 筆記 Word 文件 | Export UI → API → S3 URL |

## 使用方式

```
/plaud:upload /path/to/第01堂.mp3
/plaud:upload /path/to/*.mp3              # 批次上傳
/plaud:status                              # 檢查所有檔案進度
/plaud:download 第01堂 srt /output/dir    # 下載指定檔案 SRT
/plaud:download all 範例資料夾 /output/dir # 批次下載整個資料夾
```

## Bundled Scripts

`skills/download/scripts/` 目錄包含批次下載腳本：

- `batch_download.sh` — 批次 SRT 下載（~10 秒/檔）
- `batch_docx_download.sh` — 批次逐字稿 DOCX 下載（~15 秒/檔）
- `batch_notes_download.sh` — 批次筆記 MD+DOCX 下載（~25 秒/檔）
- `json_to_srt.py` — trans_result JSON → SRT 轉換

## 前置需求

- `safari-browser` CLI（`make install` from [PsychQuant/safari-browser](https://github.com/PsychQuant/safari-browser)）
- Safari 已登入 web.plaud.ai
- Plaud 帳號（密碼存在 macOS Keychain）
