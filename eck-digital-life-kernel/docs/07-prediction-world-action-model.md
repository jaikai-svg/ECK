# Volume VII — Prediction & World Action Model

**版本：** 0.1.0  
**狀態：** Future / Research；v0.1沒有通用WAM

## 1. 文件目的

本卷定義未來Prediction Layer的契約，避免將「影片預測模型」「LLM想像」或「模擬器」任一項直接稱為完整世界模型。v0.1只有Capability執行後的Outcome，沒有通用反事實預測。

## 2. Prediction責任

輸入：

- current state；
- candidate action；
- environment/embodiment descriptor；
- horizon；
- uncertainty budget。

輸出：

- predicted next/future states；
- success probability；
- side effects；
- failure modes；
- confidence/calibration；
- time/cost estimate；
- model and evidence provenance。

Prediction不能自行選擇Action。Planner比較多個Prediction，Policy決定能否執行，Verifier檢查實際Outcome。

## 3. Prediction Contract草案

```yaml
schema_version: prediction.v1alpha
prediction_id: pred_...
state_ref: state_...
action_ref: action_...
horizon:
  steps: 5
outcomes:
  - state_delta: {}
    probability: 0.65
    side_effects: []
uncertainty:
  epistemic: 0.20
  aleatoric: 0.15
assumptions: []
model_provenance:
  provider: ""
  version: ""
```

此schema尚未凍結，不得由第三方依賴。

## 4. WAM分層

未來可以同時存在：

- Symbolic transition model；
- learned latent dynamics；
-物理模擬器；
-規則引擎；
-LLM tool model；
-實際環境probe。

Prediction Router依任務選擇模型，並保存不同模型的分歧。不得用平均值掩蓋重大安全分歧。

## 5. Calibration

世界模型的重要指標不只是top-1準確率，而是：

- Brier score；
- expected calibration error；
- negative log likelihood；
-罕見失敗召回；
-跨環境外推；
-prediction error隨Experience下降速度。

模型若聲稱90%信心，長期同類預測應約90%正確。未校準confident prediction不可用於高風險操作。

## 6. Prediction error event

未來實際Outcome到達後：

```text
Prediction
→ Action
→ Outcome
→ Align state representation
→ Compute error
→ Classify cause
→ Update experience
```

錯誤分類：

- observation error；
- action execution error；
- hidden variable；
- dynamics model error；
- goal/contract error；
- environment drift。

只因Prediction失敗不能直接改模型權重；先建立Reflection Proposal。

## 7. GridWorld關係

v0.1 GridWorld不是WAM。它是確定性Capability，內部用BFS與保存路徑完成驗收。未來可將GridWorld改為部分可觀測環境，Prediction Layer只看到鄰近格，必須從Action/Observation學習轉移規則。

## 8. 具身適配

Brain不應知道特定機器人API。Embodiment Adapter提供：

- observation schema；
- action schema；
-單位與座標；
-能力限制；
-安全區域；
-緊急停止；
-模擬器/實機識別。

Prediction Contract只參照標準Action/State。Adapter負責轉換，不得偷偷改變目標。

## 9. 研究假說

H1：明確分離Prediction與Decision能提高錯誤診斷性。  
H2：保存模型分歧比單一置信度更能預測失敗。  
H3：以prediction error選擇Experience，可提高每單位經驗的能力增益。  
H4：通用世界模型未必是一個網路，而可能是可路由模型集合。

每項假說都需要對照實驗。尚未有ECK結果支持。

## 10. 導入門檻

Prediction v1進入Implemented前必須：

1.凍結State/Action/Prediction schema。
2.建立至少兩種可替換predictor。
3.使用同一測試集比較calibration。
4.確保Prediction不能繞過Policy。
5.將prediction/outcome/error寫入事件。
6.加入版本migration。
7.完成部分可觀測GridWorld驗收。

