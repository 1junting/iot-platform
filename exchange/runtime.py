"""接入與投遞分離；資料提交後才回覆成功。"""
import base64
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
import pika
from prometheus_client import Counter, Gauge, Histogram

from . import db, tracing
from .model import decode, fingerprint, normalize, now, json_pairs
from .transport import deliver

STOP = threading.Event()
HEALTH = {}
RECEIVED = Counter('exchange_received', 'Persisted accepted messages', ['protocol'])
DELIVERED = Counter('exchange_delivered', 'Acknowledged deliveries', ['protocol'])
WORKER_READY = Gauge('exchange_worker_ready', 'Worker database claim readiness')
LATENCY = Histogram('exchange_delivery_seconds', 'Destination acknowledgement duration')
LOG = logging.getLogger('exchange')


def log(event, **fields):
    record = {'event': event, 'service': os.getenv('SERVICE_NAME', 'exchange'),
              'timestamp': now(), **fields}
    LOG.warning(json.dumps(record))
    host = os.getenv('LOG_HOST')
    if host:
        import socket
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.sendto(json.dumps(record).encode(), (host, 5045))
        except OSError:
            pass


def stage(document):
    role = os.getenv('SERVICE_ROLE', 'exchange')
    document['processing'] = list(document.get('processing', []))
    if role == 'edge2':
        thresholds = json.loads(os.getenv('ANOMALY_THRESHOLDS', '{}'))
        anomalies = []
        for field, bounds in thresholds.items():
            value = document['values'].get(field, {}).get('value')
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if value < bounds['min'] or value > bounds['max']:
                    anomalies.append({'field': field, 'value': value, 'bounds': bounds})
        document['processing'].append({'tier': 2, 'operation': 'threshold_detection',
                                       'anomalies': anomalies, 'processed_at': now()})
    elif role == 'edge1':
        # 部署者提供模型權重；未配置時明記無模型，避免假造 AI 結果。
        model_path = os.getenv('INFERENCE_MODEL_PATH')
        result = {'tier': 1, 'operation': 'linear_inference', 'model_loaded': False}
        if model_path:
            from pathlib import Path
            model = json.loads(Path(model_path).read_text())
            score = float(model['bias'])
            for field, weight in model['weights'].items():
                value = document['values'][field]['value']
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError('Inference input must be numeric')
                score += weight * value
            result.update(model_loaded=True, model_version=model['version'], score=score)
        document['processing'].append(result)
    return document


def ingest(raw, codec, protocol, trace_id=None):
    try:
        obj = decode(raw, codec)
        name = os.getenv('SERVICE_NAME', 'exchange')
        hops = obj.get('hops', [])
        if not isinstance(hops, list) or len(hops) > 8 or any(not isinstance(x, str) for x in hops):
            raise ValueError('Invalid forwarding hops')
        if name in hops:
            raise ValueError('Forwarding loop rejected')
        with db.connect() as conn:
            profile = conn.execute('SELECT * FROM profiles WHERE id=%s',
                                   (obj.get('profile', 'identity'),)).fetchone()
            routes = conn.execute('SELECT * FROM routes ORDER BY id').fetchall()
        if profile is None:
            raise ValueError('Unknown mapping profile')
        document = normalize(obj, profile['rules'], protocol, raw, codec)
        document['trace_id'] = obj.get('trace_id', trace_id or uuid.uuid4().hex)
        document['profile_version'] = profile['version']
        document['provenance'] = list(document['provenance']) + [{'service': name, 'profile': document['profile'], 'profile_version': profile['version'], 'protocol': protocol, 'raw_sha256': document['raw']['sha256']}]
        document['hops'] = hops + [name]
        if 'processing' in obj:
            document['processing'] = obj['processing']
        document = stage(document)
        jobs = []
        for route in routes:
            config = route['config']
            if not config.get('enabled', True):
                continue
            if config.get('input_protocol') not in (None, '*', protocol):
                continue
            if not document['device_id'].startswith(config.get('device_prefix', '')):
                continue
            delivery_id = str(uuid.uuid4())
            outgoing = {**document, 'delivery_id': delivery_id,
                        'rule_version': route['version'], 'route_id': route['id']}
            # 下層映射已完成；上層收到的統一格式採 identity，保留原始版本鏈。
            outgoing['profile'] = 'identity'
            outgoing['raw'] = {'codec': codec, 'sha256': document['raw']['sha256'], 'reference': {'service': name, 'source_id': document['source_id'], 'message_id': document['message_id']}}
            jobs.append({'id': delivery_id, 'route_id': route['id'],
                         'route_version': route['version'],
                         'snapshot': {'document': outgoing, 'destination': config['destination']}})
        fresh = db.save(document, fingerprint(obj), jobs)
        if fresh:
            RECEIVED.labels(protocol).inc()
            log('persisted', message_id=document['message_id'], protocol=protocol)
        return {'message_id': document['message_id'], 'duplicate': not fresh,
                'delivery_id': obj.get('delivery_id'), 'state': 'PERSISTED'}
    except OverflowError:
        raise
    except Exception as error:
        # 只隔離解析或語意失敗；資料庫或容量故障須讓來源重試。
        from psycopg import Error as DatabaseError
        if isinstance(error, DatabaseError):
            raise
        with db.connect() as conn:
            conn.execute('INSERT INTO quarantine(protocol,reason,raw_base64) VALUES (%s,%s,%s)',
                         (protocol, str(error)[:200], base64.b64encode(raw[:262144]).decode()))
        raise ValueError(str(error)[:200]) from None


def worker():
    while not STOP.is_set():
        row = None
        try:
            row = db.claim()
            HEALTH['worker'] = True
            WORKER_READY.set(1)
            if row is None:
                STOP.wait(.15)
                continue
            started = time.monotonic()
            stamp = time.time()
            broker = deliver(row['snapshot'])
            tracing.emit(row['snapshot']['document'].get('trace_id', uuid.uuid4().hex), 'deliver ' + row['snapshot']['destination']['protocol'], stamp, time.monotonic() - started)
            if not db.finish(row, True, broker):
                log('stale_worker_result_ignored', delivery_id=str(row['id']))
                continue
            LATENCY.observe(time.monotonic() - started)
            DELIVERED.labels(row['snapshot']['destination']['protocol']).inc()
            log('delivery_ack', delivery_id=str(row['id']), broker_only=broker)
        except Exception:
            if row:
                try:
                    db.finish(row, False, error='Destination unavailable or invalid acknowledgement')
                except Exception:
                    HEALTH['worker'] = False
                    WORKER_READY.set(0)
            else:
                HEALTH['worker'] = False
                WORKER_READY.set(0)
            STOP.wait(.5)


def accept_receipt(raw, protocol):
    if len(raw) > 262144:
        raise ValueError('Receipt exceeds size limit')
    obj = json.loads(raw, object_pairs_hook=json_pairs)
    if not isinstance(obj, dict) or not isinstance(obj.get('delivery_id'), str) or not isinstance(obj.get('values'), dict):
        raise ValueError('Invalid receipt contract')
    key = str(uuid.UUID(obj['delivery_id']))
    digest = fingerprint(obj)
    with db.connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('receipt:' + key,))
        old = conn.execute('SELECT fingerprint FROM receipts WHERE delivery_id=%s', (key,)).fetchone()
        if old and old['fingerprint'] != digest:
            raise ValueError('Receipt ID conflict')
        conn.execute('INSERT INTO receipts(delivery_id,document,transport,fingerprint) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                     (key, db.Jsonb(obj), protocol, digest))
    return {'delivery_id': key, 'state': 'DESTINATION_RECEIVED', 'duplicate': bool(old)}


def broker_receive(raw, codec, protocol):
    if os.getenv('SERVICE_ROLE') == 'collector':
        return accept_receipt(raw, protocol)
    return ingest(raw, codec, protocol)


def mqtt_loop():
    collector = os.getenv('SERVICE_ROLE') == 'collector'
    topic = 'lab/destination/#' if collector else ('$share/edge3/edge3/ingress/#' if os.getenv('SERVICE_ROLE') == 'edge3' else '$share/exchange/lab/ingress/#')
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id=os.getenv('SERVICE_NAME', 'exchange') + '-' + os.getenv('HOSTNAME', 'one'),
                         manual_ack=True)
    def connected(client, userdata, flags, reason, properties):
        HEALTH['mqtt'] = not reason.is_failure
        client.subscribe(topic, qos=1)
    def receive(client, userdata, msg):
        try:
            codec = msg.topic.rsplit('/', 1)[-1]
            broker_receive(msg.payload, codec if codec in {'json', 'xml', 'cbor'} else 'json', 'mqtt')
            client.ack(msg.mid, msg.qos)
        except ValueError:
            client.ack(msg.mid, msg.qos)
        except Exception:
            client.disconnect()
    client.on_connect, client.on_message = connected, receive
    while not STOP.is_set():
        try:
            client.connect(os.getenv('MQTT_HOST', 'mqtt'), 1883, 20)
            client.loop_forever()
        except Exception:
            HEALTH['mqtt'] = False
            STOP.wait(2)


def amqp_loop():
    queue = 'lab.destination' if os.getenv('SERVICE_ROLE') == 'collector' else ('edge3.ingress' if os.getenv('SERVICE_ROLE') == 'edge3' else 'lab.ingress')
    while not STOP.is_set():
        try:
            with pika.BlockingConnection(pika.URLParameters(os.environ['AMQP_URL'])) as conn:
                channel = conn.channel()
                channel.queue_declare(queue=queue, durable=True)
                channel.basic_qos(prefetch_count=10)
                HEALTH['amqp'] = True
                for method, properties, raw in channel.consume(queue, auto_ack=False, inactivity_timeout=1):
                    if STOP.is_set():
                        break
                    if method is None:
                        continue
                    try:
                        broker_receive(raw, properties.content_encoding or 'json', 'amqp')
                    except ValueError:
                        pass
                    channel.basic_ack(method.delivery_tag)
        except Exception:
            HEALTH['amqp'] = False
            STOP.wait(2)


def start():
    STOP.clear()
    role = os.getenv('SERVICE_ROLE')
    functions = [worker] if role != 'collector' and os.getenv('RUN_WORKER', 'true') == 'true' else []
    if role in {'exchange', 'collector', 'edge3'}:
        functions += [mqtt_loop, amqp_loop]
    for fn in functions:
        threading.Thread(target=fn, daemon=True, name=fn.__name__).start()
