# Volume XIII — Roadmap & Research

**版本：** 0.1.0  
**狀態：** Roadmap；未來項目不是既有功能

## 1. 研究方向

ECK長期研究問題：

> 如何讓一個本地系統在陌生任務中，以更少經驗、更少風險和更低成本取得可重用能力？

核心指標不是最後背了多少知識，而是：

```text
Learning Efficiency =
  verified capability gain
  / (experience + compute + time + risk)
```

能力增益必須由未見過的評測或保留集證明。

## 2. 已完成M0–M1

### M0 Architecture Freeze

- Constitution；
-模組邊界；
-Success Contract；
-分級Otherwise Do Nothing；
-版本與文件結構。

### M1 Digital Life Kernel v0.1

-持久生命週期；
-事件hash鏈與Replay；
-Task/Approval/Experience/Knowledge ledger/Reflection record/Skill；
-Policy與Verifier；
-Ollama/Mock；
-CLI/API/Dashboard；
-三項驗收；
-Docker與CI。

## 3. M2 — Contract-Guided Brain

目標：讓Ollama模型產生候選，而不是硬編碼expression。

驗收：

1.固定Contract由系統提供。
2.Brain只產生expression candidate。
3.AST拒絕不安全候選。
4.Unit Tests決定成功。
5.最多N次修正。
6.與Mock/Random baseline比較。

不得將LLM文字當成功證據。

## 4. M3 — Experience Graph

將現有關聯表擴充為可查詢graph：

```text
Goal
→ Contract
→ Plan
→ Action
→ Prediction
→ Outcome
→ Evidence
→ Reflection
→ Skill
```

驗收：

-任何Skill可追溯到所有Evidence；
-矛盾Experience可同時存在；
-查詢不依賴向量相似度；
-Graph可由Events重建；
- migration與backup通過。

## 5. M4 — Partial-Observation World

將GridWorld改成真正互動：

- Agent只見鄰近狀態；
-規則/Goal不直接提供；
-每一步有成本；
-Action可揭露Evidence；
-跨level共享規則而非完整地圖。

評估：

-首次成功率；
-發現Goal所需步數；
-第二/第三level適應速度；
-隱藏測試；
-與BFS、random、LLM-only baseline比較。

## 6. M5 — Planner與Prediction

-版本化State/Action/Prediction；
-至少symbolic和learned兩predictor；
-候選Plan搜尋；
-不確定性校準；
-prediction error event；
-cost-aware stopping。

Planner不能直接執行，仍經Policy。

## 7. M6 — Reflection與Curiosity

先做deterministic Reflection taxonomy，再加入LLM候選。Curiosity只能選擇預先核准的本地sandbox實驗。

驗收：

-相同失敗重複率下降；
-每成本單位資訊增益；
-錯誤根因被後續證實比例；
-無權限擴大。

## 8. M7 — Controlled Evolution

第一次允許參數更新前必須具備：

- immutable base model；
- candidate LoRA；
-隔離訓練資料；
-來源與授權；
-train/validation/holdout；
-能力與安全回歸；
-artifact hash；
-人工promotion；
-一鍵rollback。

v0.1的「不更新權重」在這些條件完成前保持。

## 9. M8 — Embodiment

順序：

1.純軟體模擬器。
2.MuJoCo/Isaac/Habitat Adapter之一。
3.低能量桌面裝置。
4.具備實體Emergency Stop。
5.最後才考慮移動平台。

實體行動必須增加：

-空間安全區；
-速度/力限制；
-watchdog；
-hardware E-stop；
-人類在環；
-責任與法規審查。

## 10. ARC-AGI-3

ARC-AGI-3可作長期互動適應benchmark，但不是唯一目標，也不等同完整AGI。

導入前：

-固定API adapter；
-避免把測試關卡洩漏進Skill；
-記錄action budget；
-分離公開開發環境與hidden evaluation；
-報告效率而不只成功率。

## 11. Mamba/Hymba/FlashTTS位置

這些是可替換Implementation：

- Mamba/Hymba：Brain Provider背後的模型架構；
- MIMO：模型內推理效率；
- FlashTTS：test-time scaling serving；
- LoRA/GRPO：未來Evolution候選。

ECK核心Contract、Evidence與Lifecycle不得依賴某一模型潮流。只有經相同任務、相同成本與相同硬體比較後才採用。

## 12. 研究實驗模板

每項研究必填：

```yaml
hypothesis:
baseline:
independent_variable:
dependent_metrics:
success_threshold:
falsification_condition:
dataset_or_environment:
holdout:
compute_budget:
risk_boundary:
reproduction_count:
artifact_plan:
```

沒有falsification condition的想法只能是願景。

## 13. 長期治理

公開專案採Apache-2.0。未來如成立ECK Foundation，需要：

-技術治理與安全治理分離；
-公開ADR與Roadmap；
-可重現benchmark；
-第三方Capability審查；
-負責任披露；
-商標與規格相容測試；
-不得用通過內部Demo宣稱AGI。

## 14. 成功定義

ECK未來是否有研究價值，不以程式碼行數、文件頁數或「永不停機」宣傳判定，而以：

-不同模型能否共用Contract；
-不同環境能否保留可追溯Experience；
-新任務學習成本是否下降；
-失敗是否能被外部證據發現；
-能力增加是否不破壞既有能力；
-整個結果是否能被其他人重現。

## 15. 研究依據與名詞校正

以下資料只支持Roadmap中的研究動機，不代表相關技術已整合、已適用16GB VRAM，
或已通過ECK驗收。正式導入仍須依本卷的研究實驗模板，在目標硬體上做對照實驗。

- Lahoti et al., *Mamba-3: Improved Sequence Modeling using State Space
  Principles*, arXiv:2603.15569, 2026。論文提出MIMO state-space formulation，
  但不等於ECK已採用Mamba-3。<https://arxiv.org/abs/2603.15569>
- Dong et al., *Hymba: A Hybrid-head Architecture for Small Language Models*,
  arXiv:2411.13676, 2024。<https://arxiv.org/abs/2411.13676>
- Guo et al., *DeepSeek-R1: Incentivizing Reasoning Capability in LLMs via
  Reinforcement Learning*, arXiv:2501.12948, 2025。v0.1不做RL或權重更新。
  <https://arxiv.org/abs/2501.12948>
- Chen et al., *Democratizing Agentic AI with Fast Test-Time Scaling on the
  Edge*, arXiv:2509.00195, 2025。論文系統名稱是 **FlashTTS**，不是FastTTS；
  論文目標硬體為24GB消費級GPU，不能直接推定16GB配置也有相同結果。
  <https://arxiv.org/abs/2509.00195>
- Liao and Gu, *ARC-AGI Without Pretraining*, arXiv:2512.06104, 2025。
  CompressARC是特定ARC方法，不是「任何任務皆可零預訓練」的證明。
  <https://arxiv.org/abs/2512.06104>
- ARC Prize Foundation, *ARC-AGI-3*。這是未來互動式適應能力評估候選，
  v0.1的GridWorld只驗證同一環境身分與幾何下的路徑經驗重用。
  <https://arcprize.org/arc-agi/3>
