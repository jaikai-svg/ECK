# 最新資訊批判學習迴圈

**狀態：** v0.2 Development — 第一優先實作項目  
**目前套件版本：** v0.1.0；本功能完成不代表 v0.2.0 已發布

## 1. 目的

ECK 不把單篇新聞、網頁標題或模型摘要直接視為知識。`web.critical_research`
使用唯讀公開網頁 Worker 取得來源，抽取可查證主張，主動尋找支持資料與反例，
最後依可追溯快照、獨立內容群組與獨立網域判定主張狀態。

研究可產生 `supported`、`partially_supported`、`disputed`、`refuted` 或
`inconclusive`。其中 `inconclusive` 是成功執行研究程序後的有效負面結果，
但不得因此把原主張寫成已證實知識。

## 2. 資料分層決策

其他資料管道建議中的分層概念適合 ECK，但目前規模不需要同時導入 Redis、Celery、
Milvus、Qdrant 與 PostgreSQL。過早加入五套服務會增加故障面、記憶體與維運成本，
卻不會自動提高研究正確率。v0.2 初期採以下最小架構：

| 層級 | 保存內容 | 載體 | 生命週期 |
| --- | --- | --- | --- |
| Raw Buffer | HTTP 回應 bytes，單次最多 2 MB | Worker 記憶體 | 解析後釋放，不寫入資料庫 |
| Clean Content | 去除 script/style/導覽雜訊後的全文 | SQLite zlib BLOB | 預設 30 天，逾期自動清除全文 |
| Provenance | URL、內容 SHA-256、raw SHA-256、SimHash、標題、作者、時間、網域 | SQLite | 長期保留 |
| Claim Graph Seed | 主張、狀態、信心、支持/反駁摘錄、來源快照 | SQLite | 長期保留，供 Experience Graph 遷移 |

Trafilatura 是主要內文抽取器；缺少套件或解析失敗時使用內建 HTML parser 降級。
原始 HTML 不永久保存，也不擷取網路封包。PDF 與 Office 文件不交給 HTML Worker，
必須由後續隔離文件 Worker 處理。

向量嵌入與專用向量資料庫延後到語意檢索量、延遲或 SQLite 容量達到可量測門檻後
再引入。現階段永久保存可重建嵌入的乾淨內容雜湊與來源鏈，避免綁死 embedding 模型。

## 3. 去重與來源獨立性

- URL canonicalization 移除 fragment、`utm_*`、`fbclid`、`gclid` 等追蹤參數。
- SHA-256 判定完全相同內容；64-bit SimHash 判定近似內容。
- 重複來源仍保留為獨立來源快照，維持「哪些網站轉載了什麼」的證據鏈。
- 完全或近似重複內容只算一個 independence group。
- 同一網域的多篇文章在同一主張與立場中只算一次獨立證據。
- 主張至少需要兩個不同內容群組且不同網域的支持證據才標記 `supported`。
- 模型提出的引文必須逐字存在於保留快照，否則證據連結直接丟棄。
- 若小型本機模型無法穩定逐字引用，程式只允許以高詞彙重疊的原文句建立一筆
  `partially_supported` 單一來源連結；這個 fallback 不得自行建立第二筆獨立支持，
  因此不能把主張升級為 `supported`。

這個策略避免把新聞聯播稿的大量轉載誤認為多方獨立證實，同時不破壞可追溯性。

## 4. 研究流程

1. 接收主題與可選的使用者網址，固定時間窗與來源上限。
2. 本機 Brain 產生最多三個搜尋詞；失敗時使用原主題。
3. 透過免費 [GDELT DOC 2.0](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/)
   發現近期候選來源。
4. 唯讀 Worker 對每個網址重新做公開 IP、credentials、robots.txt、大小、內容類型與
   安全轉址檢查。
5. 清洗全文、計算雜湊、壓縮保存快照並建立來源 metadata。
6. 從初始來源抽取可查證主張，為每個主張產生查證搜尋詞。
7. 再次搜尋支持、反例與爭議來源。
8. Brain 只能提出 claim/source/stance/exact quote；程式確定引文存在並計算獨立性。
9. 產生可稽核的計畫、動作、來源、證據、結論與未知項目，不公開私有 chain-of-thought。
10. Success Contract 驗證研究程序；主張的真實性仍由 claim status 單獨表示。

## 5. 無結論品質門檻

「證據不足，暫時無法下結論」可避免硬猜，因此是合法研究結果；但高比例代表搜尋、
來源品質或主張切分需要改善。系統使用最近 10 次完成研究作為預設視窗：

- 歷史不足 10 次：`insufficient_history`；
- 無結論比例不高於 50%：`ok`；
- 完成至少 10 次且無結論比例高於 50%：`degraded`。

因此 10 次研究有 9 次無結論會明確觸發退化警報。監督者取得此指標後，應優先改善
來源、查證詞、反例搜尋或主張粒度，而不是持續建立更多相似主題製造假進度。

## 6. Worker 隔離邊界

`web.public_explore` 是唯讀研究能力，目前以 in-process 邏輯 Worker 邊界實作，
只接受 `read`、相容別名 `get` 與 `get_json`。click、post、login、publish、follow、
like、message 等會改變網站狀態的操作會在進行 DNS 或 HTTP 前拒絕。

狀態變更瀏覽器 Worker 必須是不同註冊能力、不同執行程序與不同權限設定，並遵守
平台條款、AI/ECK 身分揭露與 Policy Gate。此 v0.2 優先項目沒有啟用狀態變更 Worker，
也不會借用唯讀能力執行社群或帳號操作。把唯讀能力移出核心程序並完成程序級隔離，
仍屬後續 Worker Runtime 里程碑；目前不能宣稱已達成程序級完全隔離。

## 7. API

```text
POST /v1/research/critical
GET  /v1/research/runs
GET  /v1/research/runs/{run_id}
GET  /v1/research/quality
```

`POST /v1/research/critical` 接受 `topic`、可選 `url` 與 `timespan`。`timespan` 使用
`24h`、`7d`、`2w`、`3m` 等格式。任務仍經 Task Service、Policy Gate、Evidence 與
Verifier，不因 API 直接提交而繞過學習准入。

## 8. 目前限制與下一步

- GDELT 是發現索引，不代表文章內容正確或彼此獨立。
- 網站 terms of service 仍比 robots.txt 更具約束力；遇到禁止自動化的來源需停止。
- 動態 JavaScript 頁面、付費牆、登入頁、PDF 與 Office 文件目前可能無法取得正文。
- SimHash 是近似去重，不是抄襲或來源關係的完整證明。
- 本功能改善 ECK 的研究記憶與程序技能，不會直接更新 Qwen 模型權重。
- 下一步是把 Source、Claim、Evidence Link 與 Counterexample 遷入完整 Experience Graph，
  並以固定題集評估查證準確率、反例召回率、無結論率與每個有效結論的 GPU/網路成本。
