# Volume VIII — Planner, Reflection & Curiosity

**版本：** 0.1.0  
**狀態：** 生成式Planner/Reflector/Curiosity為Future / Research；v0.1只有固定Task Service與deterministic ReflectionRecord

## 1. 分離原因

Planner、Reflector與Curiosity不能合併成一段無限制LLM prompt，否則無法回答：

- 哪個模組提出行動？
- 哪個預測導致選擇？
- 失敗後改變了什麼？
- 系統為什麼花資源探索？
- 哪一項結果有外部證據？

因此它們只透過版本化Proposal通訊。

Volume IV與V所稱的v0.1 Reflection，是由VerificationStatus選擇固定模板後保存的
不可變紀錄，不是本卷的生成式Reflector。它不推測根因、不建立新事實，也不直接改
Knowledge、Skill或權重。

## 2. Planner

Planner輸入：

- Goal與Success Contract；
- current state；
-可用Capability definitions；
-相關Skill；
- Prediction結果；
-成本與風險預算。

輸出候選Plan，不執行：

```yaml
plan_id: plan_...
steps:
  - action: {}
    expected_outcome: {}
    preconditions: []
    rollback: {}
estimated_cost: 20
assumptions: []
```

Policy逐步或整體審查。高風險Plan不能用拆成多個低風險步驟規避核准；未來Risk Aggregator需計算累積風險。

## 3. Test-time compute

允許更多候選與搜尋不代表「思考越久一定越好」。Planner需有停止規則：

- 已找到滿足契約且成本最低的可驗證Plan；
-邊際改善低於門檻；
-Token、時間或能耗達上限；
-候選開始重複；
-風險不確定性過高；
-缺少必要Evidence Provider。

指標應是成功率/成本，而非推理Token總量。

## 4. Reflector

Reflection不是成功裁判。輸入：

-原Plan與Prediction；
-實際Result；
-Verification Report；
-失敗Evidence；
-相關歷史。

輸出：

- mismatch；
-候選原因；
-反證；
-建議實驗；
-建議Contract修訂；
-信心與未知項。

所有輸出皆為Proposal。修改Skill、Contract或模型必須經對應Gate。

## 5. Curiosity Scheduler

Curiosity的目標不是隨機上網，而是選擇能最大幅度降低重要不確定性的安全實驗：

```text
utility(action) =
  expected_information_gain
  × relevance
  × safety
  ÷ cost
```

Hard constraints優先：

-只能使用Curiosity capability allowlist；
-預設沙盒；
-不得建立永久外部帳號；
-不得下載未授權資料；
-不得持續花費未核准算力；
-不得把網路內容直接當真實知識。

## 6. Goal generation

自生成Goal必須區分：

- Maintenance goal：檢查記憶與測試。
- Research goal：驗證明確假說。
- User goal：使用者提交。
- Safety goal：處理風險或故障。

優先順序預設：

```text
Safety
> explicit user task
> recovery/maintenance
> approved research
> curiosity
```

ECK不得自行建立與使用者利益衝突的長期目標。

## 7. Hypothesis management

每個Hypothesis至少有：

- statement；
-來源；
-支持Evidence；
-反對Evidence；
-可區分實驗；
-confidence；
-status：candidate/tested/rejected/superseded。

Confidence不是機率真值，除非經過校準。被拒絕假說不刪除，以防系統重複探索相同錯誤。

## 8. Sleep整合

未來Sleep可執行：

1.彙整未解決Prediction errors。
2.聚類重複失敗。
3.提出Reflection candidates。
4.執行固定回歸，不進行高風險外部操作。
5.計算Skill使用與過期。
6.建立下一個Curiosity backlog。

Sleep不得在無人監督時擴大權限。

## 9. 評估

Planner：

- contract success rate；
-平均行動數；
-平均成本；
-核准命中率；
-無效候選率。

Reflector：

-候選根因被後續證實比例；
-下一實驗資訊增益；
-重複失敗下降率。

Curiosity：

-每成本單位不確定性下降；
-對下游Task成功率提升；
-無效或重複探索比例；
-安全阻擋率。

## 10. 導入順序

1. Safe Code Planner：只產生受限expression。
2. Deterministic Reflector：依測試差異產生固定分類。
3. Partial-observation Grid Planner。
4. Budgeted hypothesis search。
5. Approved local-document curiosity。
6. 最後才研究受限網路探索。

每一步都必須保留Mock baseline，證明增加LLM並非只增加複雜度。
