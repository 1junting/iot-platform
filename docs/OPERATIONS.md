# 部署與維護

Mac 與 Ubuntu 都在本倉庫執行 `python3 scripts/start.py`。Linux 主機另需 Docker Compose v2；ARM64 與 AMD64 使用套件對應 wheel，不預設為單一硬體架構。Mac 容器實測不等於 Linux 工控機驗收。

專案固定名稱 `iot-exchange`。網路、資料卷、主機 ports 與其他版本分開。不要在其他專案目錄執行本專案操作；不要使用 `down -v` 清理資料。

停止本專案、保留資料：

```sh
docker compose -f compose.yaml -f compose.ops.yaml -f compose.cluster.yaml -f compose.ci.yaml --profile test stop
```

`.env` 權限 0600，保存服務身份、JWT secret、各資料庫與原生產品的獨立密碼。不要將它加入 Git 或貼進命令輸出。換一台主機先產生新秘密，經安全管道移交所需設定。公開入口需 TLS 憑證、受控防火牆與 Broker 身份驗證；LwM2M／CoAP 的本實驗配置沒有 DTLS，不能直接開到公網。

資料庫初次啟動依序建立八個角色與各自資料庫。應用角色不是 superuser。CI 使用另建的 ci_test 資料庫及測試身份，與運行資料分開。Schema v1 在服務啟動時建立，不依賴產品前端 migration。

容量：最多 100,000 個待送工作，超出時 HTTP 回覆 503，不先回成功再丟棄。其他協定只在提交後確認；Broker 接受與 HDPCM 接受必須分開查核。資料庫／磁碟失效時交易不成功；部署者需持續監控磁碟與備份容量。尚未提供任意長時間離線或無限資料保存保證。

`python3 scripts/backup.py` 逐一 pg_dump，並將每份備份還原到新建的 verify_* 資料庫，查核後刪除該一次性資料庫。原始運行資料庫不覆蓋。備份保存在不進 Git 的 reports/private/backups；請另外移至受控備份媒體。跨服務備份不是單一分散式一致快照；需要精確跨服務時點時先停止接入與 worker。

真正回復時先還原到新的服務資料庫，驗證 marker／收件／待送工作，再在停機維護期間將該服務 DATABASE_URL 切換到新庫。不得直接用測試腳本覆蓋原庫。機敏設定需另行備份，pg_dump 不包含 `.env`。

歷史清理預設只預覽：

```sh
docker compose --profile test run --rm --no-deps tests python scripts/retention.py --days 30
```

只有明確加 `--apply` 才清理已完成工作及其收件；PENDING／RETRY／SENDING／DEAD 的依賴資料保留。這是單服務保留政策；跨服務原文 reference、備份與下游稽核需求也需納入保留期，預設不排程刪除。

Kubernetes 只存在於本專案的 k3s 容器，不設定主機全域 context。四個容器節點共用同一主機；真實場域要將 Tier 3 的 DB／接入程序部署在現場獨立主機，配置時間同步、節點標籤、站點來源及持久儲存，再重新跑網路中斷、主機故障與效能驗收。控制平面及 PostgreSQL HA 尚未在本機配置。

PROA 的容量參數預設 20 rps，為實驗設定，不能直接當作主機容量。看 test_mae、baseline_mae、預測曲線及冷啟動時間後，才能評估是否優於原生 HPA。更換場域必須重新採樣、校準與比較；示範線性模型也必須換成受驗證的應用模型。
