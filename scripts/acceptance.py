"""七種真實網路入口、五種輸出與獨立資料庫收件驗收。"""
import asyncio
import json
import os
import time
import uuid
from pathlib import Path

import aiocoap
import httpx
import paho.mqtt.publish as publish
import pika
import websockets

PREFIX = 'e2e-' + uuid.uuid4().hex[:10]
HEADERS = {'x-service-token': os.environ['SERVICE_TOKEN']}
CLIENT = httpx.Client(headers=HEADERS, timeout=15)
BASE = 'http://exchange:8050'
REPORT = {'run': PREFIX, 'inputs': {}, 'outputs': {}, 'checks': {}}


def request(method, url, **kwargs):
    response = CLIENT.request(method, url, **kwargs)
    response.raise_for_status()
    return response.json()


def wait(function, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = function()
        if result:
            return result
        time.sleep(.3)
    raise AssertionError('Timed out waiting for independent persistence')


def document(label):
    return {'message_id': PREFIX + '-' + label, 'source_id': PREFIX,
            'device_id': PREFIX + '-' + label, 'observed_at': '2026-10-28T00:00:00Z',
            'values': {'temperature': {'value': 35, 'unit': 'C'}}}


def persisted(key):
    return request('GET', BASE + '/api/v1/messages', params={'message_id': key})['items']


def receipts(key):
    return request('GET', 'http://collector:8050/receipts', params={'message_id': key})['items']


async def coap_send(obj):
    context = await aiocoap.Context.create_client_context(transports=['udp6'])
    try:
        response = await context.request(aiocoap.Message(
            code=aiocoap.POST, uri='coap://exchange:5683/ingest',
            payload=json.dumps(obj).encode(), content_format=50)).response
        assert response.code.is_successful(), response
    finally:
        await context.shutdown()


async def ws_send(obj):
    async with websockets.connect('ws://exchange:8050/ws', additional_headers=HEADERS) as ws:
        await ws.send(json.dumps(obj))
        assert json.loads(await ws.recv())['state'] == 'PERSISTED'


def configure_route(name, protocol, url, input_protocol='*', device_prefix=PREFIX):
    return request('PUT', BASE + '/api/v1/routes/' + PREFIX + '-' + name,
                   json={'expected_version': 0, 'device_prefix': device_prefix,
                         'input_protocol': input_protocol,
                         'destination': {'protocol': protocol, 'url': url}})


def main():
    configure_route('http', 'http', 'http://collector:8050/receipts')
    configure_route('ws-amqp', 'amqp', 'amqp://rabbitmq/lab.destination', 'websocket')
    for protocol in ('http', 'mqtt', 'amqp', 'websocket', 'coap'):
        obj = document(protocol)
        if protocol == 'http':
            assert request('POST', BASE + '/ingest', json=obj)['state'] == 'PERSISTED'
        elif protocol == 'mqtt':
            publish.single('lab/ingress/json', json.dumps(obj), qos=1, hostname='mqtt')
        elif protocol == 'amqp':
            with pika.BlockingConnection(pika.URLParameters(os.environ['AMQP_URL'])) as conn:
                channel = conn.channel()
                channel.queue_declare(queue='lab.ingress', durable=True)
                channel.confirm_delivery()
                channel.basic_publish('', 'lab.ingress', json.dumps(obj).encode(), mandatory=True,
                                      properties=pika.BasicProperties(delivery_mode=2, content_encoding='json'))
        elif protocol == 'websocket':
            asyncio.run(ws_send(obj))
        else:
            asyncio.run(coap_send(obj))
        record = wait(lambda: persisted(obj['message_id']))[0]
        proof = wait(lambda: receipts(obj['message_id']))
        assert record['protocol'] == protocol
        assert proof[0]['document']['values']['temperature']['value'] == 35
        REPORT['inputs'][protocol] = {'message_id': obj['message_id'], 'receiver_protocol': record['protocol'], 'receipts': len(proof)}
    for protocol, extra in [
        ('modbus', {'host': 'fixtures', 'port': 5020,
                    'registers': [{'address': 0, 'type': 'uint16', 'field': 'temperature', 'unit': 'C', 'scale': .1}]}),
        ('lwm2m', {'endpoint': 'fixture-lwm2m',
                   'resources': [{'path': '/3303/0/5700', 'field': 'temperature', 'unit': 'C', 'type': 'number'}]})]:
        source = {'expected_version': 0, 'protocol': protocol,
                  'device_id': PREFIX + '-' + protocol, 'interval_seconds': 2, **extra}
        key = PREFIX + '-' + protocol
        request('PUT', BASE + '/api/v1/sources/' + key, json=source)
        def received_source():
            rows = request('GET', BASE + '/api/v1/messages')['items']
            return [r for r in rows if r['source_id'] == key]
        record = wait(received_source)[0]
        proof = wait(lambda: receipts(record['id']))
        assert record['protocol'] == protocol
        assert record['document']['observed_at'] is None
        assert proof[0]['document']['values']['temperature']['value'] == 35
        REPORT['inputs'][protocol] = {'message_id': record['id'], 'receiver_protocol': protocol, 'receipt_id': proof[0]['delivery_id']}
        request('PUT', BASE + '/api/v1/sources/' + key, json={**source, 'expected_version': 1, 'enabled': False})
    targets = {'mqtt': 'mqtt://mqtt/lab/destination/json',
               'amqp': 'amqp://rabbitmq/lab.destination',
               'websocket': 'ws://collector:8050/ws',
               'coap': 'coap://collector:5683/receipts'}
    output = document('output')
    for protocol, url in targets.items():
        configure_route('out-' + protocol, protocol, url, 'http', output['device_id'])
    request('POST', BASE + '/ingest', json=output)
    proof = wait(lambda: (rows if len(rows := receipts(output['message_id'])) == 5 else []))
    assert {r['transport'] for r in proof} == {'http', 'mqtt', 'amqp', 'websocket', 'coap'}
    REPORT['outputs'] = {r['transport']: r['delivery_id'] for r in proof}
    assert request('POST', BASE + '/ingest', json=output)['duplicate'] is True
    conflict = CLIENT.post(BASE + '/ingest', json={**output, 'values': {'temperature': 99}})
    assert conflict.status_code == 409
    REPORT['checks']['dedupe_and_conflict'] = True
    wrong = CLIENT.post(BASE + '/ingest', content=b'{invalid')
    assert wrong.status_code == 422
    unknown = CLIENT.post(BASE + '/ingest', json={**document('unknown'), 'profile': 'missing'})
    assert unknown.status_code == 422
    REPORT['checks']['invalid_and_unknown_quarantined'] = True
    credentials = request('POST', 'http://gateway:8080/auth/api/v1/token', json={'username': 'admin', 'password': os.environ['ADMIN_PASSWORD']})
    auth_headers = {'authorization': 'Bearer ' + credentials['access_token']}
    response = httpx.put('http://gateway:8080/devices/api/v1/devices/' + PREFIX,
                         headers=auth_headers, json={'id': PREFIX, 'name': 'Lab fixture'})
    assert response.status_code == 200
    assert httpx.get(BASE + '/api/v1/messages').status_code == 401
    REPORT['checks']['jwt_gateway_devices'] = True
    # WS→AMQP 必須有 consumer 落庫，不以 publisher confirm 代替。
    ws_proof = wait(lambda: [x for x in receipts(PREFIX + '-websocket') if x['transport'] == 'amqp'])
    REPORT['checks']['websocket_to_amqp_consumer_receipt'] = len(ws_proof)
    Path('/reports').mkdir(exist_ok=True)
    Path('/reports/protocol-acceptance.json').write_text(json.dumps(REPORT, indent=2))
    print(json.dumps(REPORT, indent=2))


if __name__ == '__main__':
    main()
