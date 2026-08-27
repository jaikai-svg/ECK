# ECK Workspace 全面品質稽核：驗證結果

日期：2026-08-12
基準提交：`c7f57ac7be82daf8f197fa276754a000c7ce0b77`
回滾標籤：`rollback-eck-workspace-quality-audit-20260812`
持久化驗收專案：`mission_d86fff81c8434c58b042dcbd628e7dba`
已取消的重複自動草案：`mission_abe9c461117345c99b6f78b128a4a1d0`

## 自動化驗證

| 驗證 | 命令 | 結果 |
| --- | --- | --- |
| Ruff | `.venv\Scripts\python.exe -m ruff check .` | 通過 |
| MyPy strict | `.venv\Scripts\python.exe -m mypy src/eck` | 通過，146 個來源檔案無錯誤 |
| pytest | `.venv\Scripts\python.exe -m pytest` | 通過，303 項測試 |
| TypeScript 型別 | `npm run typecheck`（`src/eck/dashboard`） | 通過 |
| TypeScript 建置 | `npm run build`（`src/eck/dashboard`） | 通過 |
| 前端測試 | `npm test`（`src/eck/dashboard`） | 通過，5 項測試 |
| 架構門檻 | `.venv\Scripts\python.exe -m pytest tests/unit/test_architecture_boundaries.py` | 通過 |

pytest 只有 Starlette `TestClient` 使用 httpx 相容介面的既有棄用警告，沒有失敗或跳過本次驗收。

## 舊資料與真實服務

- 使用現有 SQLite 資料直接啟動新版服務，新增資料表採 additive migration；既有 REST API、技能 Manifest 與舊資料可讀。
- 首頁可用技能由錯誤的 24 修正為 14，與技能頁一致；仍保留 18 筆記憶技能總量供完整稽核。
- Library 投影由 1131 張卡片降為 1108 張唯一主張卡片；未解問題由 1131 個錯誤映射降為 0 個真實未解問題。
- 睡眠執行 `sleep_7fefb00a21cb4e0caabdd43499e75823` 完成事件鏈驗證與權威計數量測；所有差異為 0，且明確記錄沒有執行破壞性整理。
- 成果中心顯示 69 項可追溯成果，提供儲存位置篩選與徹底刪除預檢；瀏覽器驗證未對真實成果執行刪除。

## 瀏覽器回歸

- 首頁：CSS 與 `/static/modules/workspace.js?v=34` 正常載入，可用技能顯示 14，斜線選單可用。
- 草稿：首頁輸入與專案編輯內容在重新整理後保留，清除後不再回填。
- 專案：顯示 24 個持久化步驟、ReAct 結構化摘要、編輯理由、修訂歷史與回滾入口。
- 技能：顯示可用 14、學習中 0，完整清單 18 項。
- Library：顯示 1108 張知識卡片、0 個真實未解問題。
- 成果：顯示 69 項、儲存位置篩選、成果詳情與徹底刪除預檢按鈕。
- 更多：睡眠狀態為 `completed`，顯示真實階段、執行結果與「權威計數沒有變化」。
- Browser console：0 個 error。

## 三輪審查

1. 修正知識卡片穩定 ID、睡眠重啟復原與刪除失敗檔案回滾。
2. 修正 `target_month` 明確清除、舊 API 相容與任務修訂語意。
3. 修正無效封存篩選、日期邊界、專案分頁刷新及成果還原後詳情同步。

詳細紀錄位於同目錄的 `review-round-1.md`、`review-round-2.md` 與 `review-round-3.md`。

## 結論

本次程式與資料驗證均通過，持久化專案狀態為 `awaiting_review`；在建立者明確通過前，不宣告產品驗收完成。原本錯建且卡在公開參考檢索的自動 P6 草案已正式取消，沒有偽造或越過其 24 個內部步驟；最終專案改以符合本次外部 Codex 實作事實的 `manual` 執行模式保存成果與證據。
