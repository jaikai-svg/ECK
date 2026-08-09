# P4 可稽核自我模型與隔離演化

**版本：** 0.1.0 P4  
**狀態：** 已實作並通過本機自動化測試  
**重要邊界：** 這不是 AGI 證明，也不是模型權重訓練

## 1. P4 解決的真實問題

P3 顯示 ECK 在 24 小時內可累積大量研究經驗，但研究結果沒有可靠轉成可執行技能。
同時，舊架構沒有持久化的程式庫自我模型，因此不能證明 ECK 理解自己的檔案、依賴與
測試，更不能安全宣稱已完成核心自我修改。

P4 把以下四件事分開計量：

1. 取得並驗證研究證據。
2. 建立記憶與知識紀錄。
3. 產生、測試並啟用隔離技能。
4. 建立、驗證但不自動啟用結構核心候選。

只有第三項通過 Docker Worker 測試並成為 `active`，才算新的可執行技能。研究筆數、
事件數、文字反思或候選草稿都不能冒充能力成長。

## 2. 個體身分與 SOUL

每個實例第一次啟動時建立：

- `data/identity/SOUL.md`：人類可讀的使命、性格、自我改進契約與能力誠實原則。
- `data/identity/soul.json`：穩定 `soul_id`、出生時間、修訂版、血統與內容 SHA-256。
- `identity/SOUL.template.md`：新實例模板，不代表某個既有 ECK 的唯一身分。
- `identity/soul.schema.json`：可驗證的機器格式。

`SOUL.md` 只描述身分與意圖，不會因文字宣稱而新增能力。能力仍由程式、測試、評估與
事件鏈決定。重新啟動不會產生新 `soul_id`；內容變更會增加 revision 並保留前一版雜湊。

## 3. Repository Self Model

`RepositorySelfModelService` 對專案原始碼建立：

- 來源檔清單、大小與 SHA-256。
- Python AST 類別、函式、行號及 import。
- `src/eck` 分區及跨模組依賴邊。
- Git commit、工作樹是否乾淨及來源樹總雜湊。
- 正式核心、驗證、執行期技能、結構候選、身分與大型檔案的邊界。

掃描器會在目錄層直接排除 `workspace`、`data`、`.git`、快取與模型產物，不會先遍歷
數十萬個大型模型檔後才過濾。`core.self_inspect` 能力讓 ECK 查詢或刷新這份自我模型。

## 4. 研究轉技能閉環

`ResearchSkillBridgeService` 每六小時最多進行一次背景轉換，流程如下：

1. 確認 Docker 技能 Worker 與映像可用。
2. 優先重驗既有 draft / failed 候選，避免不停產生重複技能。
3. 至少取得 12 筆完成、有結論、信心不低於 0.5 且具兩個來源的研究。
4. 只允許從供應的 `run_id` 選擇證據，不接受模型虛構來源。
5. 產生一個小型、可重用的技能規格，再交給既有 Skill Forge。
6. Docker 沙箱測試、必要時修復，通過後才能熱啟用。

若 Worker 不可用、證據不足、提案重複或測試失敗，狀態會如實保留，不增加技能數。

## 5. Core Candidate Laboratory

核心修改不得直接寫入正在運行的專案。P4 候選實驗室要求：

- 正式 Git 工作樹必須乾淨。
- 從目前 commit 建立 detached Git worktree。
- 每次最多指定三個既有目標檔；新檔只允許在 `src/eck/experimental` 或 `tests`。
- 模型必須回傳完整檔案內容，路徑、檔案大小與 Python AST 先經靜態驗證。
- 保存 `candidate.patch`、修改前後雜湊、來源 commit、模型與理由。
- 使用固定 `p4-fixed-v1` 評估器執行 compile、Ruff、strict MyPy 與完整 pytest。

所有閘門通過只會得到 `validated_awaiting_human`，不會 merge、啟用或取代正式核心。
P4 尚未配置私有保留任務，也尚未實作藍綠核心切換；這兩項列入 P5。

## 6. 可移植認知包

認知包格式升級為 `eck-cognitive-bundle.v2`，包含：

- SQLite 認知資料與事件鏈。
- ECK 生成技能原始碼。
- `SOUL.md`、`soul.json` 與血統。
- Repository Self Model 與研究轉技能狀態。
- 核心候選 manifest 與 patch，不包含外部 worktree checkout。
- 專案設定與模型 manifest。

大型模型權重、密鑰、帳號憑證與本機虛擬環境仍不包含在認知包內。

## 7. NAS 儲存原則

DS620slim 適合保存不活躍模型、LoRA、ControlNet、資料集、生成成果、認知包與歷代候選。
正在推理的模型、SQLite、Python 虛擬環境、Docker layers、GPU/CPU offload 與暫存檔應保留
在本機 NVMe。雙 1GbE Link Aggregation 提升多客戶端總吞吐與備援，但不保證單一 PC 的
單一連線變成 2Gbps。RAID 也不能取代第二份備份。

## 8. API

```text
GET  /v1/identity/soul
GET  /v1/self-model
POST /v1/self-model/refresh
GET  /v1/evolution/skill-bridge
POST /v1/evolution/skill-bridge/run
GET  /v1/evolution/core-candidates
POST /v1/evolution/core-candidates
GET  /v1/evolution/core-candidates/{candidate_id}
POST /v1/evolution/core-candidates/{candidate_id}/validate
```

## 9. P5 前置條件

1. 建立不放在公開原始碼中的新鮮保留軟體任務。
2. 比較正式核心與候選的正確率、回歸、安全、延遲、RAM、VRAM 與磁碟成本。
3. 將評估器更新限制在 epoch 邊界，禁止候選在同一輪修改自己的考卷。
4. 加入人工核准、簽章、藍綠 Worker 切換、健康檢查與一鍵回滾。
5. 持續使用新鮮任務，降低固定 benchmark 記憶或資料污染造成的假進步。
