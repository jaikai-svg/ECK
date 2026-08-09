# P3 Objective Evaluation

**版本：** 0.1.0 P3
**狀態：** Implemented

## 1. 目的

P3 將「執行很多次研究」與「能力確實增強」分開。ECK 不得再以事件、經驗、反思或
既有技能的成功次數，替代新的可執行能力與同條件評測改善。

## 2. 固定本機診斷

`POST /v1/evaluations/objective` 使用 20 個固定題目，涵蓋：

- 推理；
- 證據支持、反證與不足判斷；
- ECK 已註冊工具的路由；
- 軟體工程可靠性。

題目一次批次送入本機 Brain，答案只能選擇預先定義 token，再由程式碼 exact-match
評分。預設執行兩輪，保存每題結果、構面分數、答案重現率、延遲、題集 SHA-256、
模型名稱、Ollama 模型 digest 與主機資源快照。

## 3. 可比較條件

`GET /v1/evaluations/compare` 只有在下列條件都相同時才計算增退：

- benchmark version；
- 模型名稱；
- 非空且相同的模型 artifact hash。

第一筆結果只建立 baseline。模型、題集或模型雜湊改變時標記 `conditions_changed`，
不得把差異描述為進步。

## 4. 誠實成長稽核

`GET /v1/evaluations` 額外提供最近 24 小時：

- 已准入經驗；
- 研究准入；
- 新建立的程序記憶；
- ECK 生成技能候選；
- 通過隔離測試並啟用的生成技能；
- 研究轉為活躍生成技能的比率。

若已有至少 10 筆研究准入但沒有新生成技能啟用，狀態必須是
`research_without_executable_skill_growth`。此時介面明確顯示「研究未轉技能」，
不得宣稱可執行能力持續增強。

## 5. API

```text
GET  /v1/evaluations
GET  /v1/evaluations/compare?suite=eck_p3_objective
POST /v1/evaluations/objective
POST /v1/evaluations/runs
```

請求：

```json
{
  "repetitions": 2
}
```

`repetitions` 允許 1 至 3。所有執行都使用本機 Brain，不產生付費 API 費用。

## 6. 能力宣稱邊界

ECK P3 Objective 是公開、可讀原始碼的本機診斷，因此適合做煙霧測試、版本回歸與
重現性檢查，不是保留題集，也不能證明：

- 達到世界前 1% 軟體工程能力；
- 通過 MMLU、GSM8K、FrontierScience 或 SWE-bench；
- 模型權重已訓練；
- ECK 已成為 AGI。

能力增益宣稱仍需至少 20 個未進入學習上下文的保留真實任務、確定性驗證器、安全
回歸與資源成本比較。P3 是建立該制度的第一層，不是最終能力證明。

## 7. 下一階段

安全核心自我改進需建立：隔離 Git worktree、repository self-model、候選演化 archive、
歷史任務 shadow replay、隱藏真實任務、回復機制與人工批准切換。線上核心不得直接
覆寫自身後跳過 P3 與完整回歸。
