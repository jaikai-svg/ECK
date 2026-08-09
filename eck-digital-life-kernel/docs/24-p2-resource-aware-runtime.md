# P2 Resource-Aware Runtime

**版本：** 0.1.0 P2  
**狀態：** Implemented

## 1. 目的

P2讓ECK在長時間運行時能看見主機資源、避免大型模型常駐造成換頁，並清楚說明影片執行失敗究竟是硬體總量不足，還是可回收的暫時記憶體不足。

## 2. 已實作

- `GET /v1/system/resources`提供實體記憶體、Commit、ECK核心程序工作集、磁碟容量與專案邏輯檔案量；
-專案掃描只在系統資訊頁觸發並將結果跨重啟快取30分鐘，不加入每5秒首頁刷新；
-資源達臨界門檻時只暫緩非緊急背景任務，人類指令與`priority:urgent`任務可繼續；
-Forge完成圖片後閒置180秒會自動停止，降低RAM與VRAM長期占用；
-CogVideoX在可用RAM偏低時先停止Forge、卸載Ollama，再重新檢查，不再於清理前直接宣告不可用；
-系統資訊頁分開呈現「總RAM不足」與「釋放資源後可執行」。

## 3. 專案大小的定義

介面顯示的是可讀一般檔案的`logical_readable_file_size`：

-包含原始碼、虛擬環境、本機模型、快取與生成成果；
-不追蹤符號連結；
-無權限路徑會計入掃描錯誤數；
-硬連結、稀疏檔案、壓縮與Windows配置單位會使邏輯總量不同於實際`size on disk`。

因此此數字適合回答「ECK可讀內容總量」，不能取代Windows磁碟內容的物理占用分析。

## 4. RAM與SSD 100%的判讀

RTX 3060不是目前CogVideoX-2B無法啟動的直接原因。既有實機煙霧測試曾以FP16、循序CPU offload、VAE slicing與tiling成功產出可播放MP4，峰值GPU記憶體約3.8GB。先前錯誤發生在清理Forge與Ollama之前，將暫時低可用RAM誤當成永久不可用。

影片模型載入、循序CPU offload與VAE解碼會在RAM與SSD之間搬移大量資料。短暫100% SSD活動時間可以是預期尖峰；若長時間維持100%且電腦明顯卡頓，通常代表實體RAM不足而持續換頁，並非健康的長期狀態。P2會先釋放閒置模型並暫緩背景工作，但不能把16GB級主機變成24GB以上的實體記憶體。

## 5. 設定

```yaml
forge_idle_shutdown_seconds: 180
resource_monitor_enabled: true
resource_sample_seconds: 5
resource_project_scan_seconds: 1800
resource_background_min_available_ram_gb: 2
resource_background_min_disk_free_gb: 10
resource_pressure_event_seconds: 300
```

將`forge_idle_shutdown_seconds`設為`0`可停用閒置關閉。降低資源門檻可能增加換頁與系統卡頓，不代表學習效率提升。

## 6. 能力宣稱邊界

P2證明的是資源可觀測、可回收與可節流。它不證明ECK已成為AGI，也不代表本機思考模型權重已自我訓練；能力增強仍須由固定基準、保留任務與可重現真實成果證明。
