# Volume IV — Memory System

**版本：** 0.1.0  
**狀態：** Event/Task/Experience/Knowledge ledger/Reflection/Skill為Implemented；語意Knowledge Graph為Future

## 1. 記憶哲學

ECK不把「把文字放入向量資料庫」視為完整記憶。每項可靠知識必須盡可能回答：

- 從哪個Task產生？
- 使用哪一版Success Contract？
- 執行哪個Action？
- 外部Evidence是什麼？
- 哪些檢查通過或失敗？
- 是否重現？
- 是否違反限制？
- 何時被提升為Skill？

v0.1以關聯資料與append-only event log建立可追溯骨架。它已實作逐Task的
Knowledge ledger與固定模板Reflection，但尚未實作跨概念推理的Knowledge Graph。

## 2. 記憶層級

| 層 | v0.1表示 | 目的 |
| --- | --- | --- |
| Working | 記憶體中的Task/Result | 一次執行 |
| Event | `events` hash chain | 不可變歷史 |
| Operational | tasks/approvals/kernel_state | 可恢復狀態 |
| Episodic | experiences | 一次任務的結果與證據 |
| Semantic ledger | knowledge_items | 經Verifier標記可信度的Task claim |
| Reflective | reflections | 不改寫歷史的固定模板教訓與下一步 |
| Procedural | skills | 可重用程序 |
| Semantic graph | 尚未實作 | 跨事件概念、衝突與規則 |
| Archive | 尚未實作 | 長期冷儲存與壓縮 |

## 3. Event memory

Event是已發生狀態轉移的紀錄，不是目前狀態本身。欄位：

- sequence：SQLite單調遞增序號；
- event_id：全域唯一；
- event_type；
- aggregate_id；
- correlation_id；
- canonical payload；
- previous_hash；
- event_hash；
- created_at。

### 寫入規則

- 事件先持久化，再通知記憶體subscriber。
- hash交易使用`BEGIN IMMEDIATE`避免兩個writer讀到相同前hash。
-舊事件禁止UPDATE/DELETE。
-事件schema若升級，payload必須帶版本或event type升級。

### 完整性與限制

Hash chain可以偵測內容改變、順序改變和中間事件刪除。它不能阻止有權限的攻擊者重建整條鏈。未來可加入週期性簽章、外部checkpoint或透明度日誌。

## 4. Operational memory

### `kernel_state`

以identity為主鍵，記錄phase、boot count、started/heartbeat、clean shutdown。

### `tasks`

保存完整Success Contract與Action JSON。Result及Verification在任務結束後寫入。Status不能任意跳轉；合法流程由Task Service控制。

### `approvals`

每個Task最多一個approval。Decision只能從Pending轉Approved或Rejected。v0.1沒有撤銷已執行Action的語意。

## 5. Episodic memory

Experience保留所有有VerificationReport的任務，不只成功：

```text
experience_id
task_id
capability
outcome
summary
evidence_ids
admitted
admission_reason
created_at
```

`admitted=false`不代表刪除。Verified Failure可用於避免重複錯誤；Unverifiable可等待未來補證；Constraint Violation是安全反例。

## 6. Evidence-grounded Knowledge ledger

每個完成Verification的Task建立一筆`knowledge_items`：

```text
knowledge_id
task_id
capability
claim
outcome
evidence_ids
externally_grounded
reproducible
admitted
created_at
```

這是可追溯帳本，不是向量記憶或Knowledge Graph。Claim由固定模板根據Task與
VerificationReport產生；`MODEL_SELF_REPORT`不會使`externally_grounded=true`。
沒有外部證據的項目可保留以便稽核，但`admitted=false`，不得當作正向知識使用。

## 7. Reflection memory

每個完成Verification的Task也建立一筆`reflections`，包含observation、lesson、
next_step、verification report ID、evidence IDs及generator。v0.1的generator固定為
`deterministic-template.v1`，依四種VerificationStatus選擇已版本化模板。

Reflection不會修改Task歷史、Knowledge、Skill或模型權重。它的功能是保存
「這次結果允許學到什麼、下一步被允許做什麼」；由LLM自行提出根因或新實驗仍屬Future。

## 8. Procedural memory

Skill欄位：

- fingerprint；
- name；
- capability；
- procedure；
- verification basis；
- success/failure count；
- active；
- created/updated timestamps。

v0.1 activation threshold為兩次成功。第一次建立Candidate，第二次更新並Active。這只是最低門檻；真實高風險技能未來必須採更高門檻、跨情境測試與時效性。

### Fingerprint風險

目前fingerprint由Capability output提供，故只信任內建Capability。未來第三方Capability不得自行決定全域指紋；必須由Kernel依版本化Canonical Skill Schema計算。

## 9. Replay

EventBus可從任意sequence依頁重播。Replay handler必須：

- 幂等；
- 不重新執行外部副作用；
- 區分「重建衍生狀態」與「執行新Action」；
- 記錄自身checkpoint。

v0.1測試確認同步與非同步handler均可使用，並支援小page size。

## 10. Retention與Forget

v0.1不自動刪除。長期不能無限制保存所有Heartbeat與中間Trace，因此未來Forget流程必須是：

```text
Observe usage
→ Classify retention
→ Build archive
→ Verify archive
→ Write ArchiveCreated event
→ Remove only materialized copies
```

原始安全、核准、驗證與模型更新證據應採更長保存期限。Forget決定必須有理由和可追溯紀錄。

## 11. Privacy

- v0.1資料預設保存在本地named volume。
- API不應回傳模型內部秘密或系統環境變數。
-匯出事件可能含任務payload，使用者分享前必須檢查。
-未來文件攝取要加入來源授權、PII分類和刪除請求索引。

## 12. Migration策略

v0.1以`CREATE TABLE IF NOT EXISTS`初始化，適合alpha。第一個破壞性schema變更前必須導入版本化migration：

1.備份資料庫。
2.讀取schema version。
3.在交易內套用migration。
4.驗證event chain及row counts。
5.失敗時保留原檔並回滾。
6.寫入`MigrationCompleted`。

不得要求使用者刪除資料庫來升級正式版本。
