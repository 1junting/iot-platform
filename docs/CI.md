# CI 與部署權限

`Jenkinsfile` 是預設測試管線：從本倉庫的功能分支取得來源，執行契約／隔離 PostgreSQL schema 測試，並驗證 Kubernetes YAML。Jenkins 僅取得專用 `ci_test` 資料庫與專用測試身份，不掛載主機 Docker socket、不啟動 Docker-in-Docker、不取得正式服務密碼或 Kubernetes 部署 token。

`python3 scripts/start.py --ci-test` 啟動測試模式。Jenkins 帳號 admin，密碼取自本機 `.env` 的 JENKINS_PASSWORD。首次可在原生 Jenkins 觸發 iot-exchange job；遠端分支更新後可重跑。未設定外部 GitHub webhook，不宣稱每次 commit 已自動部署。

`Jenkinsfile.release` 與 `scripts/ci_deploy.py` 是待授權的完整發布方案：建置以 commit SHA 命名的 OCI 映像、在獨立 daemon 測試、發布至本機 Registry，最後只 PATCH 本專案的 hdpcm-exchange／exchange-worker Deployment，等待新 generation 的 rollout 完成。不可將測試 job 直接改用 release 管線而不先設定與審閱權限。

本次自動核准審核拒絕啟用持有持久 Kubernetes token 的 Jenkins／Docker-in-Docker，理由是持續部署權限未獲明確授權。此邊界維持未啟用。較小的發布權限建議是：專用隔離建置 daemon、不掛載主機 socket、短效 token、只允許本命名空間兩個具名 Deployment 的 GET/PATCH；不得讀取 Secrets 或操作其他專案。

預設叢集保留具名 deployer Role 的宣告，沒有建立持久 token 或把憑證交給 Jenkins。人工 `scripts/cluster.py` 使用本專案專用容器內 kubectl，已與持續 CI 部署權限分開。
