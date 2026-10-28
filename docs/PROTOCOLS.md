# 協定與格式支援矩陣

| 協定 | 原生輸入子集合 | 輸出 | 邊界 |
| --- | --- | --- | --- |
| HTTP／REST | POST `/ingest`、JSON／XML／CBOR | POST，應用 ID 收據 | JWT／服務身份；256 KiB |
| MQTT 3.1.1 | QoS 1 訂閱 `lab/ingress/{json,xml,cbor}` | QoS 1 PUBLISH | PUBACK 是 Broker 接受；未宣稱未連線期間零遺失或 QoS 2 |
| AMQP 0-9-1 | RabbitMQ durable queue `lab.ingress`、提交後 manual ACK | durable queue／publisher confirms | content_encoding 指定 json／xml／cbor；Broker 確認需另查 consumer |
| WebSocket | `/ws` 文字 JSON、服務身份 | JSON＋delivery_id ACK | 非任意廠商 frame schema |
| CoAP RFC 7252 | POST `/ingest`；格式 50／41／60 | POST＋應用 ID 收據 | UDP 受控實驗；未啟用 DTLS／OSCORE |
| Modbus TCP | FC03／FC04、明確 register map、uint16／int16／uint32／int32／float32 | 未提供寫入適配器 | 支援大小 word order、縮放／offset；沒有 JSON 塞暫存器、RTU、自動識別 PLC 或寫控制 |
| LwM2M 1.0 子集合 | UDP binding U、`/rd` 註冊、更新、解除註冊、有效期、Object Instance link-format、文字 Resource Read | 未提供通用 LwM2M 管理輸出 | `/3303/0/5700` 等明確 Profile；無 bootstrap、DTLS、TLV、多資源實例、Observe／Execute／Write，不宣稱完整 LwM2M 認證 |

`edge3` 使用 `edge3/ingress/#` 與 `edge3.ingress`，避免與中央搶同一來源。來源不自動掃描網路；透過管理 API 指定目標 host、register map 或已註冊 LwM2M endpoint。

XML 使用 `<message>`、message_id／device_id／observed_at／profile、`<values><field name="..." type="number|string|boolean" unit="..."></field></values>`。禁止 DTD／外部實體。CBOR 接受 JSON 相容資料，不接受尾隨物件、bytes／tag／非有限數值。未知格式、未知 Profile、語意衝突與不安全 XML 會隔離。

輸出為統一 JSON；輸入能解 XML／CBOR，不代表能輸出所有廠商的 XML／CBOR schema。新廠商格式需提供規格／樣本，再擴充 decoder／Profile；不能將未知格式宣稱自動支援。

驗收使用真正 socket、Broker、CoAP 與獨立 collector 的資料庫。設備是可重現的網路替身，與實體 PLC／感測器驗收分開。

依據：[Modbus V1.1b3](https://www.modbus.org/file/secure/modbusprotocolspecification.pdf)、[OMA LwM2M 1.0.2](https://www.openmobilealliance.org/release/LightweightM2M/V1_0_2-20180209-A/OMA-TS-LightweightM2M-V1_0_2-20180209-A.pdf)、[RabbitMQ 確認層級](https://www.rabbitmq.com/docs/confirms)。
