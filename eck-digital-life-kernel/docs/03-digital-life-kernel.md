# Volume III — Digital Life Kernel

**版本：** 0.1.0  
**狀態：** Implemented

## 1. 定義

Digital Life Kernel是維持ECK身分、狀態、時間節奏及工作循環的最小核心。它不等同於Brain，也不負責解答任務內容。

核心責任：

1. 建立或恢復持久身分。
2. 管理生命週期狀態。
3. 週期性Heartbeat。
4. 順序處理已獲准任務。
5. 執行Sleep/Memory consolidation。
6. 記錄非乾淨關閉與復原。
7. 對API提供可觀測狀態。

## 2. Boot protocol

`start()`的順序不得變更：

1. 確認目前為Stopped或Faulted。
2. 將記憶體phase設為Starting。
3. 呼叫`begin_boot(identity)`，在交易內增加boot count並標記`clean_shutdown=0`。
4. 若上一狀態未乾淨關閉，發出`KernelRecovered`，否則`KernelStarted`。
5. 持久化Running與heartbeat。
6. 啟動單一Life Loop task。

在事件發出之前就將`clean_shutdown`設為0，是為了程序在任何後續步驟崩潰時仍能被下次啟動辨識。

## 3. Life Loop

```text
while not stop:
    if RUNNING:
        if queued task:
            execute one task
            continue
        if sleep requested or due:
            run sleep cycle
        if heartbeat due:
            persist heartbeat
    await poll interval
```

v0.1不執行「自己閱讀、自己出題、自己上網」等高階自主行為。Life Loop提供將來的排程位置，但預設只做維持生命、執行已提交任務及整理記憶。

### 排程公平性

現行`next_queued()`依建立時間倒序取一筆，實作上可能讓大量新任務影響舊任務。正式多任務排程器屬v0.2修正項目；v0.1驗收一次只提交少量任務。

## 4. Heartbeat

Heartbeat證明程序仍在運行，但不是健康狀態的充分條件。每次Heartbeat包含：

- phase；
- queued task數；
-持久化時間；
-事件序號與hash。

預設5秒。長期執行可提高間隔以減少事件量。Health API另檢查事件鏈、Brain可用性及安全設定。

## 5. Pause與Resume

Pause：

- 不取出新queued task；
- 不刪除或取消已持久化任務；
- 可繼續接受API查詢；
- 記錄`KernelPaused`。

Resume：

- 恢復Life Loop取任務；
- 記錄`KernelResumed`。

v0.1不支援在Capability執行途中搶占。高成本能力必須自行實作timeout與取消；目前內建能力均為短時間、確定性操作。

## 6. Sleep cycle

Sleep不是意識或夢境模擬。v0.1流程：

1. phase轉為Sleeping。
2. 發出`SleepStarted`。
3. 從Genesis開始驗證整條事件hash鏈。
4. 統計Experience、Knowledge、Reflection與Skill。
5. 發出`MemoryConsolidated`。
6. 發出`SleepFinished`。
7. 回到Running。

Sleep使用Lock避免API請求和定時器同時執行兩個週期。

未來可加入：

- Episode摘要；
- 矛盾偵測；
- 低價值記憶封存；
- 可逆壓縮；
- Regression suite；
- 候選技能重測。

任何未來整理都不得修改歷史事件；只能新增衍生事件或Archive索引。

## 7. Shutdown與Recovery

乾淨關閉：

```text
Running/Paused
→ Stopping
→ cancel life loop
→ KernelStopped(clean=true)
→ Stopped + clean_shutdown=1
```

非乾淨模擬或程序崩潰保留`clean_shutdown=0`。下次`begin_boot`回傳`recovered=true`。

Recovery v0.1保證：

- database仍可開啟；
- identity與boot count延續；
- queued task仍存在；
-事件、經驗、技能仍存在；
-發出Recovery事件。

不保證：

- 重新執行崩潰中的非冪等Capability；
- 復原外部設備的中間狀態；
- 自動解決資料庫檔案本身損壞。

因此未來外部Capability必須定義idempotency key與reconciliation protocol。

## 8. KernelStatus

公開欄位：

- `identity`
- `phase`
- `boot_count`
- `started_at`
- `last_heartbeat_at`
- `pending_tasks`
- `pending_approvals`
- `event_count`

Status是觀測資料，不應用作業務成功證據。例如`phase=running`不能證明某任務成功。

## 9. 故障處理

Life Loop未處理例外：

1. phase設Faulted；
2.持久化Faulted；
3.發出`KernelFaulted`，包含例外類型與安全摘要；
4.停止執行新任務。

API handler與Capability boundary各自將可預期錯誤轉成狀態。敏感資訊與完整堆疊不應在公開API回傳。

## 10. 驗收條件

- 首次boot count為1。
- 非乾淨停止後重建，boot count為2。
- 新程序可讀到前一程序的Observation事件。
- Recovery事件存在。
- Sleep產生Started、Consolidated及Finished事件。
- 事件鏈仍有效。

對應測試：

- `tests/integration/test_lifecycle.py`
- `tests/unit/test_storage.py`
