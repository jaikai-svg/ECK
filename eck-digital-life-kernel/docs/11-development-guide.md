# Volume XI — Development Guide

**版本：** 0.1.0  
**狀態：** Implemented工程規則

## 1. 開發哲學

ECK採Specification-Driven Development，但文件不是阻止實驗的理由。流程：

```text
Research note
→ Success criteria
→ ADR
→ Contract
→ Test
→ Implementation
→ Verification
→ Specification update
→ Release
```

重大設計先文件化；小型內部重構可直接以測試證明不改語意。

## 2. 本地準備

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

品質命令：

```bash
ruff check .
mypy src/eck
coverage run -m pytest
coverage report
```

最低coverage 80%，目前release驗證高於門檻。

## 3. Directory ownership

- `domain/`不能依賴FastAPI或Ollama。
- `storage/`不能執行Capability。
- `api/`不得直接寫SQLite。
- `brain/`不得授予成功。
- `capabilities/`不得自行提升Experience。
- `verification/`不得執行Action。
- `memory/`只接收已有Verification的Task。
- `kernel/`負責排程，不理解任務領域。

違反方向的import視為架構問題。

## 4. 新增Capability

1.定義唯一name與明確description。
2.選擇不可降低的default risk。
3.聲明deterministic、network、system-file mutation。
4.只接受Action payload，不直接接收任意服務物件。
5.輸出結構化CapabilityResult。
6.提供真正外部Evidence；若不能，結果必須Unverifiable。
7.新增Policy、成功、失敗、例外與重現測試。
8.更新Volume IX。

Capability不得：

-執行任意Shell字串；
-讀寫工作目錄外路徑；
-把LLM評分冒充Unit Test；
-在內部改Task status；
-在內部直接寫Skill。

## 5. 新增Evidence Provider

需要ADR回答：

- Source如何獨立於模型？
- Provider版本如何記錄？
-輸入是否可能被Action操控？
-如何防止偽造？
-Evidence有效期限？
-失敗如何表示？

新增枚舉是API變更，需評估版本相容。

## 6. Schema變更

非破壞性：

-新增有預設值的可選欄位；
-新增新event type；
-新增endpoint。

破壞性：

-更名/刪除欄位；
-改enum value；
-改hash material；
-改同一schema version語意；
-改Task狀態意義。

破壞性變更需要新version、migration、回歸與ADR。

## 7. Error handling

- Domain輸入錯誤：Pydantic validation。
- 不存在資源：KeyError由API轉404。
- 狀態衝突：ValueError由API轉409。
- Capability例外：Task Service轉Tool Evidence與Verification。
- Kernel loop例外：Faulted事件。
- 不捕捉後靜默忽略。

錯誤訊息避免洩漏環境變數、完整路徑與秘密。

## 8. Async規則

- 外部I/O使用async。
- SQLite方法保持短同步交易。
- 不在event handler做長時間阻塞。
- Life Loop一次一項Task。
- background task必須有停止與取消路徑。
-測試使用pytest-asyncio。

## 9. Test doubles

MockBrain是正式測試工具，不應在測試中假裝Ollama。外部HTTP使用fake client或transport。時間敏感測試應注入Clock（目前Clock abstraction已定義，Kernel尚待完整注入）。

## 10. Git與Review

Pull Request至少包含：

- 問題與成功條件；
-變更範圍；
-風險；
-測試證據；
-文件更新；
-回滾方式。

任何跳過Policy或Verifier的變更不得合併。

## 11. Python/Rust邊界

Python是v0.1主runtime，理由：

- AI與Web整合成熟；
-原型速度快；
-易於檢查Contract；
-開發者門檻較低。

Rust `eck-integrity`是Experimental獨立event verifier，理由是建立第二實作信任邊界。v0.1 Kernel不依賴Rust，因此沒有Rust工具鏈也能執行。未來只有經profiling證明瓶頸或安全價值時才將元件移往Rust。

## 12. Release checklist

- [ ] 版本一致。
- [ ] Ruff通過。
- [ ] MyPy strict通過。
- [ ] 測試全通過。
- [ ] Coverage ≥80%。
- [ ] 三項驗收通過。
- [ ] Event chain驗證通過。
- [ ] Docker build通過。
- [ ] Rust CI通過。
- [ ] 文件狀態標記正確。
- [ ] CHANGELOG更新。
- [ ] ZIP不含`.env`、DB、模型、venv。
- [ ] DOCX完成render QA。

