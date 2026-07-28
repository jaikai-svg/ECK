# Volume VI — Brain Runtime

**版本：** 0.1.0  
**狀態：** Provider介面、Mock、Ollama Health/Chat為Implemented；自主規劃為Future

## 1. Brain在ECK中的位置

Brain是提出文字、結構化候選或解題建議的模型服務。它不是：

- Policy Gate；
- Verifier；
- Event Store；
- Approval authority；
- Capability executor；
- 生命身分唯一來源。

此邊界讓模型可以更換，而Success Contract與安全語意保持穩定。

## 2. BrainProvider

介面：

```python
async def health() -> BrainHealth

async def chat(
    messages: list[dict[str, str]],
    format_schema: dict | None = None,
) -> BrainResponse
```

`BrainHealth`：

- provider；
- available；
- model；
- detail。

`BrainResponse`：

- content；
- model；
- raw provider response。

Raw只用於診斷，不應直接成為外部Evidence。

## 3. MockBrainProvider

用途：

- 無GPU CI；
-確定性單元與整合測試；
- Ollama未設定時驗證其他模組；
-復現Task flow而不受模型版本影響。

Mock不假裝具有推理能力，只回傳固定結構摘要。

## 4. OllamaBrainProvider

### Health

1. `GET /api/tags`。
2. 列出本機模型。
3. 若`ECK_OLLAMA_MODEL`未設定，available=false並說明可用模型。
4. 若設定模型不存在，available=false。
5. 連線錯誤不得讓Kernel停止；Health顯示不可用。

### Chat

`POST /api/chat`，固定：

- `stream=false`
- `temperature=0`
-可選JSON Schema format。

ECK不自動pull模型，原因：

- 模型體積可能很大；
-授權與來源需由使用者確認；
-下載需要網路；
-不同16GB VRAM機器適合不同量化版本；
-避免安裝流程擅自消耗磁碟與頻寬。

## 5. 硬體配置

目標機器：

- Ryzen 7 9800X3D，8C/16T；
- 64GB DDR5；
- RTX 4070 Ti SUPER 16GB；
- 1.2–1.5TB可用NVMe；
- Windows 11 + WSL2 + Docker Desktop。

建議模型原則，而非寫死名稱：

- 優先選擇能完整放入16GB VRAM的4-bit/5-bit小型推理或程式模型；
-保留Context/KV cache空間；
-不在v0.1同時常駐多個大型模型；
-使用者先透過`ollama list`確認模型；
-以實際tokens/s、VRAM與任務正確率決定，而不是參數量宣傳。

目前設定不指定模型名稱，避免文件時間一久變成錯誤預設。

## 6. Structured output

未來Brain若建立Success Contract，輸出必須先通過Pydantic schema：

1.拒絕未知欄位。
2.至少一個machine-checkable check。
3. Evidence需求不可只有self-report。
4.成本與嘗試上限必須存在。
5. Contract ID與schema version由Kernel生成或驗證。

模型生成Contract只是Candidate；對高風險任務必須由人類或可信模板確認。

## 7. Prompt與Trace政策

ECK不保存或公開隱藏Chain of Thought作為可靠證據。可保存：

-輸入訊息hash與授權後內容；
-模型名稱與版本；
-sampling參數；
-結構化Proposal；
-Decision Trace；
-外部Evidence。

若需要可解釋性，系統產生簡潔、可核對的rationale，不依賴無法驗證的內部推理文字。

## 8. Provider切換

切換流程：

1.設定`brain_provider`與provider參數。
2.啟動時Health檢查。
3.保留相同BrainProvider contract。
4.對關鍵Task suite重新執行。
5.記錄ProviderChanged事件（Future；v0.1尚未自動發出）。

模型切換不得：

-改變Success Contract定義；
-跳過Policy；
-把新模型評分當外部Evidence；
-自動遺忘舊Experience。

## 9. Failure modes

| 失敗 | v0.1處理 |
| --- | --- |
| Ollama未啟動 | Health unavailable，Kernel仍運行 |
| 模型未設定 | 明確顯示設定需求 |
| 模型不存在 | 列出已安裝名稱 |
| timeout | chat拋出受控HTTP error |
| 非JSON輸出 | 未來structured parser拒絕 |
| hallucinated action | Capability Registry/Policy拒絕 |
| self-verified answer | Verifier判定無外部證據 |

## 10. v0.1限制

內建驗收情境不要求Brain生成Action，原因是先隔離驗證生命核心和證據閉環。下一個里程碑才應加入：

- Brain產生Safe Expression候選；
-候選不通過時重新提案；
-比較Mock與Ollama的成功率；
-保留Token與時間成本；
-仍由固定Contract與Unit Tests判定。

