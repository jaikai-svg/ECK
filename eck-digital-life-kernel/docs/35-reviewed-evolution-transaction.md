# Reviewed Evolution Transaction v1

## 定位

本階段把隔離核心候選補成可稽核的結構更新交易。它證明的是 ECK 可以在固定邊界內
提出、評估、核准、提交、重啟及回滾自身程式；它不證明開放式遞迴自我增強、AGI 或
模型權重已被訓練。

## 單一真實來源

- 核心候選程式與 patch 仍由 `CoreEvolutionLabService` 管理。
- SQLite 的 `evolution_transactions` 只保存生命週期、指紋、核准及啟動狀態。
- `evolution_evaluations` 保存相同測試包對 baseline 與 candidate 的實際結果。
- `evolution_boot_receipts` 保存新程序觀察到的 Git SHA，不把「已提交」當成「已啟動」。
- 不建立第二套任務、技能、知識或成果資料來源。

## 狀態機

```text
drafted
  -> fixed_gates_failed
  -> awaiting_heldout_evaluation
  -> heldout_failed | no_measurable_improvement
  -> awaiting_human_approval
  -> approved
  -> activation_applying
  -> restart_pending
  -> absorbed | startup_mismatch
  -> rollback_restart_pending
  -> rolled_back
```

`validated_awaiting_human` 不再直接代表可核准。固定回歸測試通過後，交易先進入
`awaiting_heldout_evaluation`。只有預先註冊、位於候選工作樹之外且 SHA-256 未改變的
測試包，才可產生核准證據。

## 候選完整性

1. 候選只在 detached Git worktree 修改。
2. 只暫存本輪實際產生的檔案；暫存清單必須等於候選歸屬清單。
3. patch 包含已追蹤檔與新檔，並保存 SHA-256。
4. Git `write-tree` 保存完整 candidate tree SHA。
5. compile、Ruff、MyPy、pytest 強制透過候選 `src` 的 `PYTHONPATH` 執行，不能意外匯入
   目前 live source 後宣稱候選通過。
6. manifest、patch 或 staged tree 任一改變，核准與啟動都會 fail closed。

## 受保護表面

`config/evolution-protected-paths.json` 分成：

- `immutable_recovery_boundary`：交易服務、重啟信號、啟動腳本及保護規則本身，候選不得修改。
- `owner_approval_required`：API、Kernel、Policy、SQLite 與 Application 接線等高耦合表面。
- `ordinary_structural_candidate`：仍需完整測試與人工核准，但不屬於救援邊界。

分類與路徑正規化由確定性程式執行，不由 LLM 決定。

## 隱藏評估

評估包位於 `workspace/evolution/heldout_packs/<pack_id>/`，不在候選 Git worktree 中。
註冊時固定：

- 測試檔清單與每檔 SHA-256。
- 變更種類。
- 最低效能改善門檻。
- 維護／重構是否允許「全數通過但沒有量化提升」的 non-regression 結果。

同一測試逐項在 source commit baseline 與 candidate worktree 執行。只有候選解決 baseline
失敗案例、通過預先固定的效能門檻，或明確登記的維護 non-regression，才可進入人工核准。
若兩者都通過但沒有達到門檻，狀態是 `no_measurable_improvement`，不得稱為成功進化。

## 核准與啟動

核准者必須回傳完整 candidate tree SHA。啟動前再次驗證 manifest、patch、staged tree、
live HEAD 與 live working tree：

1. live repository 必須乾淨且 HEAD 等於 candidate base commit。
2. patch 先執行 `git apply --check --index`。
3. 套用後 `write-tree` 必須等於人工核准的 tree SHA。
4. 以 ECK 本機身分建立正常 Git commit，不略過 hooks。
5. 寫入 `activation_applying` 後才套用 patch，關閉「commit 完成但 pending 尚未落盤」的崩潰視窗。
6. API 回應後提出 graceful restart；CLI 以相同 host/port 替換目前程序。
7. 新 Kernel 啟動後比對實際 HEAD，吻合才寫入 receipt 並標記 `absorbed`。

Windows 桌面啟動器另外使用不依賴 ECK import 的父程序。若新程序在十分鐘恢復窗內、
HEAD 精確等於 pending commit，或可證明為 `activation_applying` 的精確 staged/committed
tree，且工作樹沒有未歸屬內容時，父程序才可執行 `git reset --hard <previous_commit>`。
它會保存外部失敗 receipt，將交易改成
`rollback_restart_pending`，再啟動上一版；條件不完整時一律拒絕破壞性回退。

## 回滾

回滾要求乾淨工作樹與 HEAD 仍等於已吸收 commit，使用 `git revert` 保存歷史，不覆寫
既有提交。revert 後同樣需要重啟與精確 startup receipt，完成才標記 `rolled_back`。

## 已知限制

- 現階段是 graceful restart activation，不是 dual-kernel 零停機 handoff。
- 災難性啟動回退目前只接入 Windows 桌面 `start-eck.ps1` 路徑；直接執行 `eck serve`、
  Linux 與 Docker 尚未具有同等外部 watchdog，因此不能宣稱跨平台自動救援。
- hidden pack 必須在候選完成前由獨立評估者或操作者準備；候選不可替自己定義唯一考題。
