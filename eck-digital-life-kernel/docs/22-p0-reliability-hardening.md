# P0 Reliability Hardening

**Status: Implemented**

本輪整修的目標不是增加表面功能，而是確保 ECK 長時間運行時不會因重複排程、工作程序中斷、模型競爭或監控輪詢而浪費資源及製造錯誤的學習紀錄。

## 任務可靠性

- 每個任務具有可重現的 `idempotency_key`；相同內容在既有任務仍為 `queued`、`waiting_approval` 或 `running` 時不會重複建立。
- 執行中的任務受到能力別逾時限制。暫時性錯誤及可逆任務使用指數退避重試，並保留 `attempts`、`next_attempt_at` 與 `last_error`。
- 重啟不會重設嘗試次數。不可逆中斷或已用盡重試預算的任務進入阻擋／死信狀態，等待人工或後續修復，而不會無限循環。
- 中間重試不寫入正向經驗、知識、反思或技能；只有完成驗證的終態才進入既有學習准入流程。
- 核心啟動時會將遺留的 `running` 任務排入安全重試，並將沒有實際工作程序的研究執行紀錄標記為失敗及可稽核的中斷結果。

## 排程與模型資源

- 自主課程使用到期時間排程，不再於每個輪詢週期重複查詢及嘗試派題。
- 共用本機模型仲裁器序列化 Ollama 推理，優先順序依序為使用者對話、媒體生成、一般推理、研究、監督者。
- 圖片及影片生成會取得獨占資源槽，先釋放 Ollama VRAM，再啟動媒體 Worker，避免同一張 GPU 同時載入多個大型模型。
- 監督者重複課題替換時會同步重建評估、建議及目標，不再留下與新課題不相干的舊內容。

## 儲存與監控

- SQLite 在初始化時設定 WAL 與 `synchronous=FULL`，不再於每次連線重複變更 journal mode。
- 首次啟動及睡眠整理仍執行完整事件鏈驗證；五秒監控輪詢只驗證上次已驗證序號之後的新事件。
- 健康狀態使用 SQL 計數及最新一筆查詢，不再載入大量完整記錄後於 Python 計算。
- Ollama、Forge 與 Docker 健康檢查具短期快取；技能樹只有在記憶或技能修訂值改變時才重新下載，避免反覆傳輸大型 JSON。

## 隔離技能 Worker

- 免費 Python 相依套件安裝至容器內 `/tmp/python`，符合唯讀根檔案系統設定。
- 驗證模式會先載入 `skill.py` 並確認存在可呼叫的 `execute`，再執行測試，避免「測試通過但技能不可執行」。

## 設定

`config/eck.yaml` 新增：

- `task_execution_timeout_seconds`
- `task_retry_backoff_seconds`
- `task_retry_backoff_max_seconds`
- `brain_health_cache_seconds`

能力特定的圖片、影片、研究及 Docker 逾時仍優先使用各自既有設定。全域 `max_task_attempts` 與任務 Success Contract 的 `max_attempts` 取較小值。

## 已知邊界

- 這是單機 SQLite 與單核心程序的至少一次執行模型，不等同分散式工作流引擎或跨主機 exactly-once 保證。
- 增量事件鏈驗證保護新追加事件；舊事件的完整重新驗證在啟動、睡眠整理、匯出及明確驗證流程執行。
- 死信目前沿用 `blocked` 任務狀態，並由 `TaskDeadLettered` 事件及 `last_error` 區分原因。

## 驗證要求

合併前必須通過：

1. 任務去重、退避、逾時、死信與重啟恢復測試。
2. 模型仲裁優先順序及健康快取測試。
3. 自主課程不忙迴圈測試。
4. SQLite 事件鏈完整與增量驗證測試。
5. 全套 pytest、Ruff、mypy、前端 JavaScript 語法與 release verifier。
