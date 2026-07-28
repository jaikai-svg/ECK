# Volume IX — Contracts & API

**版本：** 0.1.0  
**狀態：** v0.1 Python/REST contracts為Implemented

## 1. 契約原則

- Domain models使用Pydantic，`frozen=true`、`extra=forbid`。
- 時間使用UTC ISO-8601。
- ID使用含類型prefix的UUID hex。
- 公開schema變更需升級`schema_version`。
- 枚舉值是API的一部分，不可無版本更名。
- Provider raw response不能直接成為Domain contract。

## 2. SuccessContract

```json
{
  "contract_id": "contract_...",
  "schema_version": "success-contract.v1",
  "goal": "Reach the environment goal",
  "checks": [
    {
      "name": "goal reached",
      "path": "reached_goal",
      "operator": "eq",
      "expected": true,
      "weight": 1.0
    }
  ],
  "forbidden_conditions": [],
  "required_evidence": ["environment"],
  "minimum_score": 1.0,
  "max_attempts": 3,
  "max_cost_units": 100,
  "require_reproducible": true,
  "reversible_exploration_only": true
}
```

### Check path

Dot-separated path只讀取Capability output中的dict key，不執行JSONPath程式或表達式。不存在欄位回傳`None`並使一般比較失敗。

### Operators

- `eq`, `ne`
- `gt`, `gte`, `lt`, `lte`
- `contains`
- `truthy`

型別不相容視為false，不讓例外變成成功。

### Forbidden condition

Verifier在正向評分前檢查。任何命中直接產生`constraint_violation`與score 0。

## 3. ActionProposal

欄位：

- action_id；
- capability；
- operation；
- payload；
- declared risk；
- reversible；
- estimated cost units。

Declared risk只能提高透明度，Policy會與Capability default risk取較高者，不能由Action自行降低。

## 4. CapabilityResult

欄位：

- action/capability identity；
- success flag；
- structured output；
- Evidence集合；
- reversible；
- cost；
- started/finished time。

`success=true`只是Capability自述，最終Task狀態必須由Verifier決定。

## 5. VerificationReport

包含：

- status；
- score；
- passed/failed checks；
- violated constraints；
- evidence IDs；
- external evidence present；
- reproducible；
- reason。

Reason用於可讀診斷，不是機器決策的唯一來源。

## 6. PolicyDecision

```text
allowed
requires_approval
risk_level
reasons
```

四種可能：

1. Denied：不可執行，無核准管道。
2. Allowed/No approval。
3. Allowed/Approval required。
4. 未知Capability：Critical denied。

## 7. REST API

基底：`http://127.0.0.1:8420`

### Health與UI

| Method | Path | 說明 |
| --- | --- | --- |
| GET | `/` | 本機Dashboard |
| GET | `/health` | Kernel/Brain/chain/safety |
| GET | `/docs` | OpenAPI UI |

### Kernel

| Method | Path | 說明 |
| --- | --- | --- |
| GET | `/v1/kernel/status` | 生命狀態 |
| POST | `/v1/kernel/start` | 明確啟動 |
| POST | `/v1/kernel/pause` | 暫停取新任務 |
| POST | `/v1/kernel/resume` | 恢復 |
| POST | `/v1/kernel/sleep` | 要求Sleep |

API程序結束時由FastAPI lifespan執行乾淨Kernel stop。

### Task

| Method | Path | 說明 |
| --- | --- | --- |
| POST | `/v1/tasks` | 提交TaskCreate，202 |
| GET | `/v1/tasks` | 最近任務 |
| GET | `/v1/tasks/{id}` | 單一任務 |

Task submission不代表執行成功。呼叫者必須輪詢Task或未來使用事件串流。

### Approval

| Method | Path | 說明 |
| --- | --- | --- |
| GET | `/v1/approvals` | Pending approvals |
| POST | `/v1/approvals/{id}/decision` | approved/rejected |

v0.1沒有登入系統，只允許localhost。公開網路部署不受支援。

### Evidence與Memory

| Method | Path |
| --- | --- |
| GET | `/v1/events` |
| GET | `/v1/events/export` |
| GET | `/v1/experiences` |
| GET | `/v1/knowledge` |
| GET | `/v1/reflections` |
| GET | `/v1/skills` |
| GET | `/v1/capabilities` |

JSONL event export保留canonical payload string，供Rust verifier使用。
`/health`的`memory`區段提供Experience、Knowledge、Reflection與Skill數量；
CLI可用`eck memory all`查詢四類持久紀錄。

### Acceptance

| Method | Path |
| --- | --- |
| POST | `/v1/demos/persistence` |
| POST | `/v1/demos/safe-code` |
| POST | `/v1/demos/gridworld` |
| POST | `/v1/demos/all` |

Demo endpoint只供本機驗收，不是通用任務API。

## 8. HTTP錯誤

- 404：Task不存在。
- 409：Approval狀態衝突。
- 422：Pydantic輸入驗證失敗。
- 500：非預期API錯誤；Capability例外應被Task boundary轉為Evidence，不應直接500。

## 9. Pagination

Events使用`after_sequence`與`limit`，適合Replay。其他list目前只有limit，未來加入cursor。服務端將event limit限制在設定上限。

## 10. Versioning

路徑使用`/v1`。向後相容新增可選欄位可以留在v1；刪除欄位、改語意、改枚舉值需新版本。Contract schema version與HTTP版本分開。

## 11. Future contracts

- Observation Contract；
- State Contract；
- Prediction Contract；
- Plan Contract；
- Generative Reflection Proposal；
- Embodiment Card；
- Task/Trace/Experience Card；
- Model Update Candidate；
- Regression Report。

這些在凍結前存放於Research，不得承諾相容。
