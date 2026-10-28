"""真正使用目的協定投遞；協定確認與應用收件分開回報。"""
import asyncio
import json
import os
import uuid
from urllib.parse import urlsplit

import aiocoap
import httpx
import paho.mqtt.client as mqtt
import pika
import websockets


def headers():
    return {'x-service-token': os.environ['SERVICE_TOKEN']}


def deliver(snapshot):
    target, document = snapshot['destination'], snapshot['document']
    protocol, url = target['protocol'], target['url']
    raw = json.dumps(document, allow_nan=False).encode()
    parsed = urlsplit(url)
    if protocol == 'http':
        response = httpx.post(url, json=document, headers={**headers(), 'x-b3-traceid': document.get('trace_id', uuid.uuid4().hex), 'x-b3-spanid': uuid.uuid4().hex[:16], 'x-b3-sampled': '1'}, timeout=6)
        response.raise_for_status()
        if response.json().get('delivery_id') != document['delivery_id']:
            raise ValueError('Destination acknowledgement mismatch')
        return False
    if protocol == 'mqtt':
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.connect(parsed.hostname, parsed.port or 1883, 10)
        client.loop_start()
        try:
            result = client.publish(parsed.path.lstrip('/'), raw, qos=1)
            result.wait_for_publish(timeout=6)
            if not result.is_published():
                raise TimeoutError('MQTT PUBACK timeout')
        finally:
            client.disconnect()
            client.loop_stop()
        return True
    if protocol == 'amqp':
        with pika.BlockingConnection(pika.URLParameters(os.environ['AMQP_URL'])) as conn:
            channel = conn.channel()
            queue = parsed.path.lstrip('/')
            channel.queue_declare(queue=queue, durable=True)
            channel.confirm_delivery()
            channel.basic_publish('', queue, raw, mandatory=True,
                                  properties=pika.BasicProperties(delivery_mode=2,
                                  message_id=document['delivery_id']))
        return True
    if protocol == 'websocket':
        async def send():
            async with websockets.connect(url, additional_headers=headers(),
                                          open_timeout=5, max_size=262144) as ws:
                await ws.send(raw.decode())
                ack = json.loads(await asyncio.wait_for(ws.recv(), 6))
                if ack.get('delivery_id') != document['delivery_id']:
                    raise ValueError('WebSocket acknowledgement mismatch')
        asyncio.run(send())
        return False
    if protocol == 'coap':
        async def send():
            context = await aiocoap.Context.create_client_context(transports=['udp6'])
            try:
                request = aiocoap.Message(code=aiocoap.POST, uri=url, payload=raw,
                                          content_format=50)
                response = await asyncio.wait_for(context.request(request).response, 6)
                if not response.code.is_successful() or json.loads(response.payload).get('delivery_id') != document['delivery_id']:
                    raise ValueError('CoAP acknowledgement mismatch')
            finally:
                await context.shutdown()
        asyncio.run(send())
        return False
    raise ValueError('Unsupported output protocol')
