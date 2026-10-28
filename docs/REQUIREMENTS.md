# 第一年度需求對照

核對本機提供的 11 頁第一年計畫書與整體架構圖；不將官方 PDF 或機敏現場資料放入此倉庫。產品選型採 Python／FastAPI、PostgreSQL、RabbitMQ、Nginx、Kubernetes、Istio、Prometheus／Grafana、ELK、Jenkins。

| 文件頁 | 需求 | 交付與證據 | 尚需驗收 |
| --- | --- | --- | --- |
| 1、7–10 | 異質協定接入、解析、雙向語意與格式統一 | 七原生入口子集合；JSON／XML／CBOR；正反向單位轉換、必填與巢狀路徑；protocol-acceptance.json、unit-tests.txt | 真實廠商 schema／PLC；LwM2M 完整功能不在已宣告子集合內 |
| 8–9 | 緩衝、非同步、重送、確認、去重 | 同交易 Outbox、獨立 worker、SKIP LOCKED、租約 token、目的端 UUID 去重、隔離與 replay；recovery-acceptance.json | 受控實驗之外的網路／斷電故障域 |
| 9 表 3 | MQTT／CoAP→REST、WS→AMQP、AMQP→REST、LwM2M→統一格式 | 真正網路發送、Broker 與獨立 consumer／collector 持久收據 | 實際 ERP／MES API 契約 |
| 10–11 | 設備管理、協議、分析、安全授權服務與統一 API | 四個獨立角色程序、資料庫、JWT／scope、Gateway 限流與 DNS；CONTRACT.md | 多租戶身份供應者與正式 PKI／TLS |
| 4–5 | Docker、Kubernetes、服務發現、故障修復 | 可從標準基底建置，獨立四容器節點、Service／ConfigMap／Secret／RBAC、Pod 替換；cluster-deploy.txt | 控制平面及資料庫 HA |
| 3 | Ingress／Istio 流量治理、熔斷、灰度、追蹤 | 真正 Gateway／VirtualService／DestinationRule、實際 canary 與錯誤目的端試驗；Jaeger 追蹤 | 公開入口 TLS 與真實多版本長時間實驗 |
| 3 | PROA＋LSTM 與 HPA | NumPy LSTM／BPTT，逐 Pod 真實指標、時序切分 MAE／持續值基準、具名 HPA 最小副本與 CPU HPA | 跨負載基準、冷啟動優勢與實際資源效益 |
| 5–7 | DEND 三級、預處理、AI、備援 | 真正 Tier 3→2→1→中央網路鏈；明確閾值／事件、配置模型推論；兩個接入節點與故障演練 | 不同實體主機、功耗／頻寬／10ms 目標、正式工業模型 |
| 2、11 | 監控、Grafana 告警、ELK 日誌 | 原生產品 provision、指標、搜尋、firing／resolved webhook 與資料庫收件 | 未指定外部 Email／簡訊收件人，不代為發訊息 |
| 11 | CI/CD、提交即部署 | Jenkinsfile 的無部署權限測試管線；release 管線與具名部署權限設計 | 自動審核拒絕的持續部署權限，需使用者明確授權後啟用 |
| 1 | 高頻模擬、性能測試與報告 | 分級並行負載、接受與目的端延遲 p50／p95／p99、錯誤與積壓；load-report.json | 長時間飽和、實體場域、能源／頻寬與跨策略對照 |
| 1 | 使用者意見、成果推廣 | 受權限保護的 feedback API；操作與交接文件 | 受試者回饋、論文、專利、技術移轉屬後續研究活動 |
| 1、11 | 第二年與開源整合方向 | 保留獨立服務／API 擴充邊界 | 第二年詳細規格、EdgeX、Hyperledger 與場域安全驗證未提供，不能以本原型宣稱完成 |

實作、當次實測與研究成效分開記錄。測試設備是明確標示的網路替身，不將其視為實體 PLC 或工業場域證據。詳細最終通過／未通過項目以 VERIFICATION.md 及 reports 的實際結果為準。
