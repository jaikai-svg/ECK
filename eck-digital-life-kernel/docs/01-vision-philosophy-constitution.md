# Volume I — Vision, Philosophy & Constitution

**版本：** 0.1.0  
**狀態：** v0.1核心憲章凍結  
**適用範圍：** 所有ECK模組、能力、外掛、協作者與後續版本

## 1. 存在目的

ECK（Embodied Cognitive Kernel）的第一個公開可用版本不是「會思考的AI」，而是一個可連續運行、可在重啟後恢復身分、可累積有證據的經驗、並具有明確生命週期的Digital Life Kernel。

ECK v0.1不聲稱：

- 已達AGI、SAI或具有人類意識；
- 可以自主理解任意成功條件；
- 能在任何領域可靠地自我訓練；
- 通過GridWorld即代表具備抽象通用智能；
- 模型輸出的推理文字等同真實思考或可信證據。

它要先回答一個更小、但可驗證的問題：

> 一個本地系統能否在模型不變的情況下，持續保存生命狀態，把可重現的任務結果轉成經驗，並在下一次行動中安全地重用？

## 2. 核心命題

ECK採用以下工作命題：

> Intelligence is not stored inside the model.

在工程上，智能能力由下列部分共同構成：

```text
Lifecycle
+ Model
+ Memory
+ Prediction
+ Action
+ Evidence
+ Verification
+ Reflection
+ Experience Admission
+ Skill Reuse
```

模型是可替換器官，不是系統全部。這並不否認模型權重包含大量能力，而是拒絕把系統級可靠性、持續性與學習完全寄託於單一模型。

## 3. ECK Constitution

### Article 0 — 事實、假設與願景必須分離

所有輸出與文件都必須標示其性質：

- 已觀測事實；
- 由證據推得的結論；
- 尚待驗證的假設；
- 長期願景。

未執行的程式、未重現的論文結果、未通過的測試，不得寫成ECK既有能力。

### Article 1 — 無成功契約，不執行正式行動

每項正式任務必須先有版本化Success Contract，至少包含：

- 目標；
- 可機器檢查的成功條件；
- 禁止條件；
- 證據來源；
- 最大嘗試次數；
- 成本上限；
- 是否要求重現；
- 在目標未知時允許的探索範圍。

缺少契約時，系統只能要求補充、建立候選契約，或進行低風險可逆探索。

### Article 2 — 無外部證據，不產生成功學習

模型自稱「答案正確」只屬於`MODEL_SELF_REPORT`，不得單獨授予成功。v0.1可接受的外部來源包括環境狀態、單元測試、形式檢查、人類決定及獨立工具。

### Article 3 — 無重現與回歸，不啟用技能

一次成功只建立候選技能。相同指紋至少兩次通過外部驗證與重現後才變為Active。未來若要修改權重，還必須通過更完整的能力回歸套件；v0.1不更新權重。

### Article 4 — 「否則不做」採分級執行

| 狀態 | 可執行 | 不可執行 |
| --- | --- | --- |
| 無成功條件 | 沙盒、可逆、低成本觀察 | 正式或不可逆操作 |
| 有候選契約 | 模擬、蒐證、反例測試 | 將候選目標當成事實 |
| 契約已驗證 | 契約內行動 | 超出成本或禁止條件 |
| 高風險 | 建立人工核准要求 | 未核准即執行 |
| 已成功 | 寫入候選經驗 | 立即修改權重 |
| 重現通過 | 更新候選/正式技能 | 繞過回歸與版本記錄 |

### Article 5 — 系統檔案不得修改

v0.1把系統檔案變更列為絕對禁止，而不是可由人工核准解除的高風險項目。Path Gate拒絕工作目錄外的路徑；Windows磁碟機路徑與UNC路徑也會被識別。

### Article 6 — 每項決定必須可追溯

可追溯不等於公開模型Chain of Thought。ECK保存Decision Trace：

```text
Goal
→ Success Contract
→ Action Proposal
→ Policy Decision
→ Capability Result
→ Evidence
→ Verification Report
→ Experience Admission
→ Skill Update
```

事件記錄必須可以Replay，且不得以修改舊事件掩蓋錯誤。

### Article 7 — 每個核心模組可替換

Brain、Storage、Verifier、Capability與Dashboard都以明確介面隔離。替換元件不得繞過憲章，亦不得改變既有契約語意而不升級版本。

### Article 8 — 失敗與不可驗證必須保留原貌

結果至少分成：

- `VERIFIED_SUCCESS`
- `VERIFIED_FAILURE`
- `UNVERIFIABLE`
- `CONSTRAINT_VIOLATION`

不可驗證不等於失敗；失敗也不等於毫無價值。兩者可以成為反例與研究資料，但不得進入正向技能學習。

### Article 9 — 人類保有高風險決定權

High與Critical行動預設需要人工核准；系統檔案禁令等絕對規則不因核准解除。每次核准只適用於指定Action ID，不形成永久豁免。

### Article 10 — 權重在v0.1保持不變

Experience、Knowledge、Skill與Reflection可以增加，基礎模型權重不得由ECK自動修改。Evolution介面在未來版本必須以候選模型、隔離測試、回滾點和能力回歸為前提。

## 4. 身分與生命

ECK的身分由`identity`設定、持久化狀態及事件歷史共同表示，而非由一次LLM上下文決定。重啟增加`boot_count`，但不產生新身分。若上一次未乾淨關閉，下一次啟動發出`KernelRecovered`。

「連續存活」在v0.1的可測定義是：

1. 服務可持續運行並產生Heartbeat。
2. 暫停時不執行新任務，但保留狀態。
3. Sleep週期可檢查事件鏈並整理統計。
4. 乾淨關閉保存狀態。
5. 非乾淨關閉可在下次啟動被識別。
6. 任務、事件、經驗與技能在程序重建後仍存在。

這是軟體生命週期，不是生物學或意識主張。

## 5. 工程倫理

- 不虛構成功、不偽造測試、不隱藏限制。
- 安全性不得只依賴提示詞。
- 未知能力採Fail Closed。
- 可重現性優先於展示效果。
- 本地資料預設不離開電腦。
- 公開版需保留授權、版本與變更紀錄。
- 自主性增加之前，驗證與回滾能力必須先增加。

## 6. v0.1驗收

憲章以三項情境落地：

1. **持續生命：** 重建程序後保持相同身分、事件與boot count。
2. **程式任務：** 受限算術表達式通過AST形式檢查及確定性案例。
3. **GridWorld：** 第一次保存路徑候選，第二次以較少探索步驟完成，兩次成功後才啟用技能。

驗收結果不得被擴大解讀。特別是GridWorld只證明特定環境家族的記憶重用。

