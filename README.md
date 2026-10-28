# IoT Exchange Platform

純後端異質 IoT 資料交換平台。HDPCM 負責接入、語意轉換與可靠投遞；DEND 負責三級邊緣處理；DSMA 負責獨立部署、監控與擴展。設備管理、授權、資料分析與交換服務各自持有資料庫，經 API 整合。

這份交付以第一年核心架構與實驗室原型為範圍。實體設備、多主機場域、工業模型成效、第二年 EdgeX／Hyperledger，以及正式公開 TLS 部署仍需各自驗收。完整支援子集合與證據見 [驗收報告](docs/VERIFICATION.md)、[需求對照](docs/REQUIREMENTS.md)。

## 啟動

需要 Docker Engine／Docker Desktop、Compose v2、Python 3。一般 API／監控模式建議配置 6 GiB；包含四個 Kubernetes 容器節點及 ELK／Jenkins 時建議 12 GiB 以上。8 GiB 可分批驗收；所有節點仍共用同一實體主機。

```sh
python3 scripts/start.py
```

完整容器節點實驗：

```sh
python3 scripts/start.py --cluster
```

只執行測試的 Jenkins：

```sh
python3 scripts/start.py --ci-test
```

所有秘密在啟動時隨機產生並保存於 `.env`，不提交 Git。服務只綁定 localhost；匿名 MQTT、無 DTLS 的 CoAP／LwM2M 與未啟用安全模式的 ELK 只供隔離實驗。遠端部署須先按 [操作說明](docs/OPERATIONS.md) 設定網路與 TLS。

| 服務 | 本機位址 |
| --- | --- |
| API Gateway | http://localhost:8100 |
| HDPCM OpenAPI | http://localhost:8110/docs |
| 獨立目的端 OpenAPI | http://localhost:8111/docs |
| MQTT | localhost:1898 |
| AMQP | localhost:5697 |
| CoAP／LwM2M | localhost:5698/UDP |
| Prometheus | http://localhost:9098 |
| Grafana | http://localhost:3098 |
| Kibana | http://localhost:5696 |
| Jaeger | http://localhost:8190 |
| Kubernetes 原生入口／Istio 入口 | http://localhost:8120 ／ http://localhost:8121 |
| Kubernetes PROA | http://localhost:8171/status |
| Jenkins（測試模式） | http://localhost:8098 |

API 管理需 JWT 或服務身份。Gateway 登入端點是 `POST /auth/api/v1/token`，帳號 `admin`，密碼為 `.env` 的 `ADMIN_PASSWORD`。其他產品的管理密碼也各自獨立產生。沒有產品前端或瀏覽器工作階段依賴。

## 驗收

```sh
python3 scripts/verify.py
python3 scripts/recovery.py
python3 scripts/backup.py
```

第一個指令執行契約／PostgreSQL 交易測試、七協定與五輸出收件、三級邊緣鏈路及分級壓測。恢復測試只暫停本專案服務；會暫停本專案 Kubernetes workers，並在 finally 恢復原副本數。請勿與其他負載測試同時執行。備份會還原到新建的一次性資料庫，不覆蓋運行資料。

`reports/` 保存當次實測資料；`reports/private/` 保存不進 Git 的備份與機敏暫存檔。既有專案、資料卷與 Git 倉庫均不使用本專案名稱。

- [資料契約與 API](docs/CONTRACT.md)
- [協定支援矩陣](docs/PROTOCOLS.md)
- [架構與服務邊界](docs/ARCHITECTURE.md)
- [部署、備份與維護](docs/OPERATIONS.md)
- [CI 與待授權的 CD 邊界](docs/CI.md)
