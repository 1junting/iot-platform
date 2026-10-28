# 驗收紀錄與第一年實驗報告

日期：2026-10-28（Asia/Taipei）。Mac Docker Desktop／Linux ARM64 VM，10 vCPU、約 8 GiB 記憶體。四個 Kubernetes 節點都是同一實體主機內的容器，不是四台實機。

| 項目 | 實際結果 | 證據 |
| --- | --- | --- |
| 契約、授權、SQL 交易與競爭 | 18 passed，1 個套件棄用提示 | unit-tests.txt |
| 七原生入口子集合 | 七種來源均持久接收，並查獨立目的端 | protocol-acceptance.json |
| 五種輸出 | HTTP、MQTT、AMQP、WebSocket、CoAP 均有獨立 collector 收件 | protocol-acceptance.json |
| LwM2M 生命週期 | 註冊、更新、過期拒絕、解除註冊均通過 | lwm2m-lifecycle.json |
| 三級邊緣 | Tier 3→2→1→中央→collector／analytics；含異常事件與配置模型結果 | edge-acceptance.json |
| 恢復與去重 | 目的端停止時另一目的端持續收件；worker 重啟補送；收件後確認遺失、重送仍僅一筆 | recovery-acceptance.json |
| 備份還原 | 八個服務逐一備份並還原到臨時資料庫查核 | backup-verification.json |
| Istio 灰度 | 50/50 配置下觀察到 stable 26、canary 34 | cluster-acceptance.json |
| Istio 熔斷 | 真實 5xx 目的端觸發 ejections_enforced_total=2，後續無健康目的端時回覆 503 | cluster-acceptance.json |
| Pod 自癒 | 替換 Pod 約 5.026 秒達 Ready | cluster-acceptance.json |
| 接入節點故障 | 過渡期間 5 次健康請求失敗，55.027 秒內恢復連續成功並查到獨立收件；節點已重啟恢復 | cluster-acceptance.json |
| 監控／日誌／追蹤 | 12 個 targets up、Grafana 六面板、ELK 可搜尋、跨服務 trace、firing／resolved webhook 落庫 | observability-acceptance.json |
| Jenkins | 測試 job build 2 SUCCESS，從 Git 提交執行 18 項測試；沒有部署憑證或主機 Docker socket | ci-acceptance.json |

## 分級負載

三個層級均為 0 錯誤，並確認每一筆獨立收件。此輪使用一個 Compose worker 與兩個 Kubernetes workers。下表的目的端延遲是 100ms 輪詢可見的上界，包含批次送出後才開始查詢的等待，不是硬體量測延遲。

| 並行 | 筆數 | 接受 rps | 目的端 rps | 接受 p95 ms | 目的端可見 p95 ms | 最大觀察積壓 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 20 | 88.3 | 38.286 | 12.702 | 236.335 | 3 |
| 5 | 50 | 164.213 | 66.182 | 41.406 | 335.946 | 23 |
| 10 | 100 | 181.125 | 85.09 | 69.382 | 599.853 | 59 |

各層另有 p50／p99、payload 大小及執行環境，詳見 load-report.json。load-report.single-worker.json 為較早單 worker 的實驗記錄，程式及環境曾更新，不作嚴格 A/B 優劣結論。

## 持續負載與擴展

設定上限 60 rps／120 秒，實際 129.863 秒完成 7200 筆，平均約 55.44 rps，0 錯誤。獨立 collector 查到 7200 筆。觀察副本從 2 擴至 4；PROA 對具名 HPA 的調整有成功紀錄。

scaling-acceptance.json 保存每次 CPU HPA、min／current／desired replicas、LSTM 樣本數、時序測試 MAE／持續值基準與 applied 狀態。這證明機制可運作，不證明預測優於 CPU HPA、冷啟動延遲改善或達到產業容量目標。

## 完成範圍與剩餘事項

已交付的是第一年核心架構的可攜軟體與實驗室驗收。七協定的子集合、模型與資料流限制都列於 PROTOCOLS.md／REQUIREMENTS.md。正式設備、工業模型精度、跨實體主機與供電故障、控制平面／資料庫 HA、公開 TLS、第二年 EdgeX／Hyperledger、論文與場域研究成果仍需另外完成。

持續 CD 尚未啟用：自動核准審核拒絕讓 Jenkins／Docker-in-Docker 持有持久部署憑證。已改用測試專用資料庫完成 CI；發布管線與最小部署權限方案在 CI.md，待明確授權。沒有將待授權或未實測事項列為通過。

所有演練只操作本專案，保留其他專案與既有資料卷。最終環境與副本快照見 environment.json。
