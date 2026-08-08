# P1 Command and Media Reliability

## 目標

P1 讓本機對話介面可以快速選擇內建命令，並修正直式圖片與影片在尺寸合約、模型原生解析度及失敗訊息上的可靠性問題。

## 斜線命令

- 在聊天輸入框輸入 `/` 會顯示可篩選的命令選單。
- 支援滑鼠、方向鍵、`Enter`、`Tab` 與 `Escape`。
- 需要提示詞的命令只填入範本；`/status`、`/help` 與 `/remove-bg` 可直接執行。
- 命令清單由 `GET /v1/chat/commands` 提供，避免前端與後端各自維護不同清單。
- 初始命令涵蓋圖片、影片、背景移除、核心狀態與命令說明。

## 圖片尺寸驗證

先前圖像能力把單邊尺寸限制在 768 像素，導致聊天路由要求的 9:16 `504×896` 被實際送成 `504×768`，最後遭尺寸合約拒絕。

P1 將支援上限與對話尺寸規則統一為 1536 像素，並直接讀取生成 PNG 的 IHDR 寬高。驗證器因此比較實際檔案尺寸，而不是只相信工作程序回報的中繼資料。

## CogVideoX 輸出尺寸

CogVideoX-2B 的已驗證原生生成解析度是 `720×480`。直接要求模型以 `432×768` 推理會偏離模型支援設定，可能產生接近純色或無有效動態的影格。

P1 採用以下流程：

1. 永遠以模型原生 `720×480` 生成影格。
2. 先在原生影格上執行細節、動態與色彩通道塌縮品質檢查。
3. 依要求比例做置中裁切與 Lanczos 縮放。
4. 再封裝為具 fast-start 中繼資料的 MP4。
5. 在結果中同時記錄模型尺寸、輸出尺寸、裁切範圍與前後品質指標。

直式輸出是由原生橫式影格裁切而來，不等同原生直式生成。提示編譯器會要求主體保持在安全中央區域，以降低裁掉主體的機率。

## 失敗與重試

- 合約失敗會列出實際未通過的檢查，不再一律顯示「不可重現」。
- 前端會顯示要求尺寸與實際尺寸，或工作程序的具體錯誤。
- `near-featureless`、`no meaningful motion` 與 `artifact rejected` 被視為可重試的隨機生成失敗。
- 低步數造成的綠色／單色通道塌縮不再算成功；正式煙霧測試預設使用 25 steps。
- 重試仍受任務最大嘗試次數控制，避免無限消耗 GPU。

## 來源

- [CogVideoX-2B model card](https://huggingface.co/zai-org/CogVideoX-2b)
- [CogVideo official repository](https://github.com/zai-org/CogVideo)
- [Diffusers CogVideoX documentation](https://huggingface.co/docs/diffusers/api/pipelines/cogvideox)
