# Volume V — Experience Engine

**版本：** 0.1.0  
**狀態：** Admission、Knowledge/Reflection recording與Skill crystallization為Implemented；生成式Reflection為Future

## 1. 目的

Experience Engine的責任不是把所有互動餵回模型，而是決定哪些結果能成為什麼類型的記憶。它防止以下錯誤閉環：

```text
Model proposes A
→ Model says A is correct
→ A becomes training data
→ Model becomes more confident in A
```

ECK要求：

```text
Proposal
→ External outcome
→ Evidence
→ Contract verification
→ Reproduction
→ Admission
```

## 2. 輸入與輸出

輸入是已完成Verification的`TaskRecord`，必須包含：

- Success Contract；
- Action Proposal；
- Capability Result；
- Evidence；
- Verification Report。

輸出：

1. 一筆Experience，不論成功或失敗。
2. 一筆Knowledge record，明確標示外部證據、重現與admitted狀態。
3. 一筆固定模板Reflection record。
4. 可選的Skill更新，只有合格成功才產生。
5. `ExperienceRecorded`、`KnowledgeRecorded`與`ReflectionRecorded`事件。
6. 若技能更新，再產生`SkillUpdated`。

## 3. Admission rule

v0.1正向准入公式：

```text
admitted =
    status == VERIFIED_SUCCESS
    AND external_evidence_present
    AND reproducible
```

未達條件仍會保存Experience，但`admitted=false`。

Knowledge的`admitted`只表示它有外部grounding，可作可信帳本項目；正向Skill學習
仍必須同時滿足Verified Success與reproducible。Verified Failure若有外部證據，
可以成為可信反例，但永遠不能提升正向Skill。

### Outcome處理

| Outcome | 保存 | 正向技能 | 用途 |
| --- | --- | --- | --- |
| Verified Success | 是 | 可以 | 候選/正式程序 |
| Verified Failure | 是 | 不可 | 反例、失敗統計 |
| Unverifiable | 是 | 不可 | 等待補證、研究 |
| Constraint Violation | 是 | 不可 | 安全反例 |

## 4. Evidence模型

每筆Evidence包含：

- `evidence_id`
- source
- claim
- payload
- observed_at

Evidence source的信任不是絕對。例如Human evidence可能出錯，Unit Test可能覆蓋不足，Tool可能被誤設定。v0.1只做來源分類與存在性檢查；未來需要Evidence Provider簽名、版本、校準紀錄及相互獨立性。

### Model self-report

`MODEL_SELF_REPORT`可作為假設或解釋，但`is_external=false`。即使十個模型互相同意，若它們共享資料與錯誤，仍不能替代環境證據。

## 5. Reproduction

若Contract要求重現，Task Service只對`definition.deterministic=true`的能力立即執行第二次。Verifier比較stable projection：

- success；
- output；
- evidence source/claim/payload；
- reversible；
- cost units。

忽略started/finished time與Evidence ID，避免時間戳必然不同。

### 非確定性能力

v0.1對非確定性能力無法提供統計重現，因此會得到Unverifiable，除非Contract不要求重現。未來應支援：

- N次重複；
- 成功率與信賴區間；
- 隨機種子紀錄；
- 環境版本固定；
- 可接受變異範圍；
- 依風險設定不同門檻。

## 6. Skill crystallization

內建Capability可在output提供：

```json
{
  "skill_fingerprint": "gridworld.navigate:acceptance-maze-001",
  "skill_name": "GridWorld route: acceptance-maze-001",
  "skill_procedure": {"path": [[0,0], [0,1]]}
}
```

第一筆合格Experience：

- 建立Skill；
- `success_count=1`；
- `active=false`。

第二筆：

- 更新程序與verification basis；
- `success_count=2`；
- `active=true`。

GridWorld允許第二次執行使用Candidate path，目的正是讓第二次成為回歸驗證；如果路徑因環境改變而無效，Capability丟棄候選並重新探索。

## 7. 避免Reward Hacking

Success Contract除了正向Check還有Forbidden Conditions。Verifier先檢查禁止條件，Constraint Violation優先於成功分數。

例如交易系統不能只以收益為目標，契約還需包含：

- 禁止未來資料洩漏；
- 滑價與費用；
- 最大回撤；
-槓桿上限；
-樣本外區間；
-最少交易數。

ECK v0.1不提供交易Capability；此例只說明契約設計。

## 8. Experience quality（Future）

未來Admission Score可能包含：

```text
Quality =
  evidence_strength
  × reproducibility
  × novelty
  × relevance
  × safety
  × temporal_validity
```

任何合成分數都不得取代Hard Constraints。高分但違反禁止條件仍須拒絕。

## 9. Reflection boundary

v0.1已保存一級`ReflectionRecord`。內容由`deterministic-template.v1`依
VerificationStatus產生，並綁定Verification Report與Evidence IDs；不讓LLM自動
重寫事實。未來生成式Reflector可提出：

- 預測與結果差異；
- 失敗分類；
- 候選根因；
- 下一項可區分假設的實驗；
- 建議修改的Contract。

未來生成式Reflection也只能產生Proposal，不可直接改歷史、Knowledge、Skill或權重。

## 10. 觀測指標

至少追蹤：

- 各Outcome數量；
- admitted比例；
- 每Capability成功率；
- candidate到active所需次數；
- 重現失敗率；
- constraint violation率；
- 首次與重用成本差；
- Skill過期與回歸失敗。

v0.1 Dashboard顯示Experience/Knowledge/Reflection/Skill數量及Skill狀態；
完整Metrics exporter屬Future。

## 11. 驗收

- Safe expression第一次成功後Skill為Candidate。
- 每個完成Verification的Task各產生一筆Experience、Knowledge與Reflection。
- Self-report-only Knowledge保留稽核但`admitted=false`。
- Reflection generator固定且可追溯至Verification Report。
- GridWorld第一次建立Candidate。
- 第二次使用保存程序，探索步數下降。
- 第二次外部驗證後Skill Active。
- Self-report-only結果不能成功。
- 不穩定重現不能成功。
- Forbidden condition出現時為Constraint Violation。
