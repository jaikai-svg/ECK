# P8 Autonomous Evolution Director

## 目的

P8 將既有事件鏈、程式庫自我模型、隔離核心候選與 Evolution Transaction 串成受治理閉環：

`真實失敗 → 去重聚合 → 改善機會 → 獨立 held-out pack → 隔離候選 → 固定回歸 → A/B 評估 → 人工核准 → 重啟收據`

它不把一般反思、模型文字或重複心跳視為改進證據，也不允許 ECK 同時發明問題、答案與評分標準。

## 權威資料

- 失敗證據：既有 tamper-evident `events`。
- 改善機會：補充表 `evolution_opportunities`，只保存事件 ID、序號、狀態及關聯，不複製事件內容。
- 候選與固定閘門：既有 `CoreEvolutionLabService`。
- held-out 評估、核准、啟用及回滾：既有 `EvolutionTransactionService`。

## 安全與品質門檻

1. 相同正規化失敗簽章至少出現三次。
2. 必須能映射至明確程式檔與既有測試範圍。
3. 必須事先綁定內容雜湊有效的獨立 held-out pack。
4. 同一時間不得建立第二個進行中的結構演化交易。
5. 候選必須通過 compile、Ruff、MyPy 及完整 pytest。
6. held-out A/B 必須證明改善或事先允許的 maintenance non-regression。
7. 即使全部通過，仍停在人工核准，不會自動修改運行核心。

## 排程

- 初次掃描延遲預設 5 分鐘。
- 後續預設每小時掃描一次。
- 掃描本身不呼叫模型；沒有 ready opportunity 時不佔用 GPU。
- 資源壓力門檻沿用核心背景工作節流。

## API

- `GET /v1/evolution/opportunities`
- `GET /v1/evolution/opportunities/{opportunity_id}`
- `POST /v1/evolution/opportunities/scan`
- `POST /v1/evolution/opportunities/{opportunity_id}/attach-pack`
- `POST /v1/evolution/opportunities/{opportunity_id}/run`

## 非目標

- 不自動生成與候選共享答案的隱藏測試。
- 不開放熱修改運行中的 Python 核心。
- 不自動核准結構變更。
- 不宣稱一次候選成功等於遞迴自我改進或 AGI。

## 2026-08-27 驗證結果

- Ruff、151 個 MyPy source modules、架構穩定性閘門全部通過。
- 完整 pytest 共 313 項通過；TypeScript 型別檢查、5 項前端測試與建置通過。
- 正式本機資料庫原地升級後，ECK 以 boot count 106 正常啟動。
- P8 掃描真實事件後建立一筆 `AutonomousProjectFailed` 觀察項目；因只有一次證據且未綁定 held-out pack，狀態維持 `observing`、`ready_count=0`，沒有建立候選或呼叫程式模型。
- Workspace system API 與靜態 CSS 分別正常回傳 P8 狀態及 `text/css`。
