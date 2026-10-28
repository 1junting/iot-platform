# 版本化資料契約

HTTP 接入範例：

```json
{
  "message_id": "sample-001",
  "source_id": "source-a",
  "device_id": "device-a",
  "observed_at": "2026-10-28T00:00:00Z",
  "profile": "identity",
  "values": {"temperature": {"value": 35, "unit": "C"}}
}
```

`message_id` 與 `source_id` 合起來去重。相同 ID／相同內容回覆 duplicate；內容不同回覆 409 並隔離。不提供 `source_id` 時使用 `default`，多個來源應明確配置。缺少設備觀測時間時使用 null，`observation_time_known=false`；網關讀取時間另存 acquired_at，不能冒充設備量測時間。

正規化契約包含 api_version、schema_version、profile_version、rule_version、來源協定、觀測／收件時間、欄位值與單位、原文 hash、原文查詢關聯、每目的端 delivery_id，以及逐跳 provenance。每個服務保存自己的原始輸入；對外投遞攜帶 raw reference，避免逐跳無限膨脹 base64。原生 Modbus 保存請求／回應 hex，LwM2M 保存資源路徑與回應 payload。

Profile 使用明確 source／target 欄位路徑、必填條件與單位。巢狀 JSON 路徑以點表示，最多八層、一百個葉欄位。支援溫度、壓力、長度、時間與比例的同維度換算；不以欄位名稱猜單位。正反向規則分別定義。Register Map 的縮放／offset 為設備明確校正值。

設定 API 使用 optimistic version：新設定 expected_version=0，更新必須帶目前版本。既有工作保留目的端、規則版本與輸出快照。`hops` 防止服務回流循環，最多八跳。

| API | 方法與責任 |
| --- | --- |
| `/ingest?codec=json` | POST；JSON／XML／CBOR，成功代表交易已持久提交 |
| `/api/v1/config/{profiles,routes,sources}` | GET；持久設定與版本 |
| `/api/v1/{profiles,routes,sources}/{id}` | PUT；驗證與版本比較更新 |
| `/api/v1/messages?message_id=...` | GET；收件、原文與轉換 |
| `/api/v1/deliveries?message_id=...` | GET；快照、投遞 ID、嘗試次數與狀態 |
| `/api/v1/quarantine` | GET；失敗原因與原文 |
| `/api/v1/deliveries/{uuid}/replay` | POST；重放 RETRY／DEAD 的相同快照 |
| `/api/v1/devices/{id}` | Devices 的 PUT／DELETE，GET collection |
| `/receipts` | Collector／Analytics 的 POST／GET 持久收據 |
| `/api/v1/summary` | Analytics 依欄位與單位統計最近一千筆收據 |
| `/api/v1/alerts` | Analytics 保存／查詢 Grafana 通知 |
| `/api/v1/feedback` | Analytics 保存使用者意見 |
| `/health`、`/metrics` | 資料庫就緒、各適配器狀態與 Prometheus 指標 |

管理 API 接受 `Authorization: Bearer <JWT>`；JWT 檢查 issuer、audience、iat、exp 及 scope。內部服務使用 `x-service-token`。入口最大 256 KiB；CoAP／MQTT 受其協定封包限制。API Gateway 與 WebSocket 也有大小限制。

投遞狀態：PENDING、SENDING、RETRY、DEAD、BROKER_ACCEPTED、DESTINATION_RECEIVED。MQTT PUBACK／AMQP publisher confirm 只產生 BROKER_ACCEPTED。HTTP／WebSocket／CoAP 的受控目的端必須回傳相同 delivery_id 才標為 DESTINATION_RECEIVED。任意第三方 HTTP 2xx 不自動升級為持久收件證據；獨立目的端資料庫仍須另外查核。

範例 Profile：

```json
{"expected_version":0,"rules":[{"source":"readings.temp_f","target":"temperature","source_unit":"F","target_unit":"C","required":true}]}
```

範例 Route：

```json
{"expected_version":0,"enabled":true,"input_protocol":"mqtt","device_prefix":"lab-","destination":{"protocol":"http","url":"http://collector:8050/receipts"}}
```
