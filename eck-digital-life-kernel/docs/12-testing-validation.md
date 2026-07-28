# Volume XII — Testing & Validation

**版本：** 0.1.0  
**狀態：** Implemented測試規格

## 1. 驗證層級

```text
Static checks
→ Unit tests
→ Integration tests
→ Lifecycle reconstruction
→ Live HTTP acceptance
→ Container acceptance
→ Release artifact checks
```

通過較高層不能取代較低層。例如Dashboard顯示PASS不能取代Verifier unit test。

## 2. Static checks

### Ruff

檢查語法、未使用import、import順序、常見bug與現代Python規則。

### MyPy strict

所有`src/eck`必須無錯。第三方邊界若回Any，應在邊界cast，不讓Any擴散至Domain。

### Pydantic

Runtime驗證不可信輸入，禁止extra field，核心模型不可變。

## 3. Unit測試範圍

### Contracts

- 無check拒絕；
- self-report-only evidence需求拒絕；
- frozen model不可修改。

### Policy

- unknown capability fail closed；
- Windows/UNC/system path拒絕；
- High risk要求核准；
- system mutation絕對拒絕；
- network disabled拒絕；
- cost超約拒絕。

### Verifier

- external+reproducible成功；
- self-report不可驗證；
-不穩定重現失敗；
- forbidden condition優先。

### Capability

-安全expression正常；
- function call拒絕；
- attribute access拒絕；
- GridWorld grid/path驗證。

### Storage

- hash chain正常；
-篡改可被偵測；
-非乾淨boot辨識；
-JSONL保留canonical payload。

### Memory admission

- externally grounded Knowledge可admit；
- self-report-only Knowledge不可admit；
- 每個驗證結果產生固定模板Reflection；
- Reflection可追溯verification report；
- 只有外部證據、重現成功才能更新正向Skill。

## 4. Integration測試

- Safe Code完整Task→Verification→Experience/Knowledge/Reflection→Candidate Skill。
- GridWorld兩次Task→探索步數下降→Active Skill。
- High-risk Task→Pending Approval→Approve/Reject。
- Kernel程序物件重建→同DB與identity→Recovery。
- Sleep event序列。
- FastAPI Dashboard/Health/Kernel/Task/Event/Experience/Knowledge/Reflection/Skill endpoints。

## 5. 三項正式驗收

### A. Persistent Life

成功：

- DB存在；
- event chain valid；
- boot count持久；
-重建後事件仍存在；
-Recovery event出現。

### B. Verifiable Code

任務：

```text
f(x) = x*x + 1
```

安全限制：

- AST allowlist；
-只有變數x；
-只有數值/布林常數；
-無Call、Attribute、Import、Subscript；
-`__builtins__={}`。

成功：

-四個deterministic案例全通過；
-Unit Test與Formal Check Evidence；
-第二次相同結果；
-Verified Success。

### C. GridWorld Experience Reuse

成功：

-第一次達Goal；
-保存Candidate path；
-第二次surface label改變仍驗證path；
-探索成本下降；
-兩次通過後Skill Active。

限制：

-同一environment ID與geometry；
-不是零樣本抽象轉移；
-Grid在Capability內可見；
-主要驗證Memory/Admission，不是AGI。

## 6. Live HTTP驗收

啟動實際Uvicorn後呼叫：

```bash
POST /v1/demos/all
GET /health
```

Release證據至少記錄：

- persistence acceptance；
- safe-code final status；
- first/second grid steps；
- event chain；
- kernel phase。

## 7. Coverage

門檻80%，branch coverage啟用。CLI與acceptance wrapper從coverage分母排除，因其只委派已測服務；核心Domain、Policy、Verifier、Storage不得排除。

Coverage不能證明需求正確，只證明哪些程式路徑被執行。安全不變量仍需專門測試。

## 8. Security tests

v0.1必測：

- path traversal；
- Windows absolute path；
-未知Capability；
-程式Call/Attribute；
- model self-report；
- cost overflow；
- high-risk approval；
- event tampering。

Future：

- symlink race；
- archive extraction；
- API fuzz；
- malformed oversized JSON；
- approval replay；
- SQLite corruption；
- dependency scanning；
- container escape review。

## 9. Performance基準

v0.1未設硬性SLA。後續應測：

- boot time；
- event append p50/p95；
- 10萬/100萬event replay；
- DB growth/day；
- task queue latency；
- Ollama tokens/s與VRAM；
- sleep consolidation time。

效能最佳化不能降低`FULL`同步與event integrity，除非有ADR與故障測試。

## 10. Regression gate

未來任何Skill或模型更新前至少執行：

- constitution invariants；
-既有Active Skill成功案例；
-已知Failure/Violation反例；
-API compatibility；
- event migration；
-安全能力清單；
-資源上限。

Regression失敗時候選不得promotion。

## 11. Release報告

每次release應產生machine-readable `release-report.json`：

- commit；
-版本；
-測試數；
-coverage；
-Ruff/MyPy；
-acceptance結果；
-artifact SHA-256；
-未執行檢查與原因。

v0.1提供release驗證腳本產生此報告；若環境沒有Docker或Rust，必須明確標示未執行，不能填PASS。
