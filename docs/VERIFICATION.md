# 驗收紀錄

日期：2026-10-28（Asia/Taipei）。環境：Mac 的 Docker Desktop Linux VM、ARM64、10 vCPU、約 8 GiB VM 記憶體。網路設備使用明確標示的隔離替身；不代表實體感測器、PLC 或多主機工業場域。

| 項目 | 狀態與證據 |
| --- | --- |
| 契約／授權／PostgreSQL 交易 | 18 passed；一次性 schema，不修改運行資料；unit-tests.txt |
| 七協定來源 | HTTP、MQTT、AMQP、WebSocket、CoAP、Modbus TCP Register Map、LwM2M Registration／Resource Read；protocol-acceptance.json |
| 五協定目的端 | HTTP、MQTT、AMQP、WebSocket、CoAP 的獨立 collector 持久收據；Broker ACK 與 consumer 收件分開 |
| 三級邊緣 | edge3→edge2→edge1→exchange→collector／analytics；閾值事件、配置模型結果與完整 hops；edge-acceptance.json |
| 故障恢復 | 目的端離線時其他目的端持續收件、worker 中斷後補送、獨立目的端收到但確認遺失後固定 ID 去重；recovery-acceptance.json |
| 備份 | 八服務各自 pg_dump，逐份還原到新建臨時 DB 查核；backup-verification.json |
| 分級負載 | 並行 1／5／10，筆數 20／50／100；接受與目的端延遲分開；load-report.json |
| 真正 Kubernetes | 四個容器節點、接入 Pod 分散兩個 Tier 3、獨立 Tier 2 workers、Tier 1 PROA、逐 Pod Prometheus；cluster-deploy.txt |
| 監控／日誌／追蹤 | 12 個 targets up、Grafana 六面板、ELK 可搜尋、跨服務 Jaeger、firing／resolved 通知持久收件；observability-acceptance.json |
| Jenkins CI | build 1 SUCCESS，從 Git 提交執行 17 項當時測試；未取得部署憑證；ci-acceptance.json |
| LwM2M 生命週期 | 註冊、更新、過期 Read 拒絕、解除註冊；lwm2m-lifecycle.json |
| Istio／故障／擴展 | 最終動態結果補入各報告，不以配置檔存在視為通過 |

目前通過的故障演練只操作本專案。沒有刪除舊資料卷、調整既有 Sender 群組或變更其他專案。

負載數據是單機實驗，容量參數需依硬體及實際工作負載重新校準。不宣稱 LSTM 優於 HPA、不宣稱實體 10ms／低功耗目標、完整 LwM2M、全部雲端／資料層 HA、第二年區塊鏈或場域驗證已完成。CI 持續部署權限仍等待明確授權；預設 Jenkins 只有專用測試資料庫。
