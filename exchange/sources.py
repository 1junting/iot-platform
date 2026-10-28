"""依明確 Register Map 與 LwM2M Object Profile 讀取設備。"""
import asyncio
import json
import os
import re
import socket
import struct
import threading
import uuid
from urllib.parse import parse_qs

import aiocoap
import aiocoap.resource

from . import db, runtime
from .model import now


def read_exact(sock, size):
    result = bytearray()
    while len(result) < size:
        chunk = sock.recv(size - len(result))
        if not chunk:
            raise ConnectionError('Truncated Modbus response')
        result.extend(chunk)
    return bytes(result)


def modbus_read(config):
    values = {}
    artifacts = []
    for field in config['registers']:
        size = 2 if field['type'] in {'float32', 'uint32', 'int32'} else 1
        transaction = uuid.uuid4().int & 65535
        function = config.get('function', 3)
        unit = config.get('unit_id', 1)
        request = struct.pack('>HHHBBHH', transaction, 0, 6, unit,
                              function, field['address'], size)
        with socket.create_connection((config['host'], config.get('port', 502)), timeout=4) as sock:
            sock.settimeout(4)
            sock.sendall(request)
            tx, proto, length, device = struct.unpack('>HHHB', read_exact(sock, 7))
            if tx != transaction or proto != 0 or device != unit or not 3 <= length <= 254:
                raise ValueError('Invalid Modbus MBAP')
            body = read_exact(sock, length - 1)
        if body[0] != function or body[1] != size * 2 or len(body) != 2 + size * 2:
            raise ValueError('Modbus exception or invalid register count')
        artifacts.append({'request_hex': request.hex(), 'response_hex': (struct.pack('>HHHB', tx, proto, length, device) + body).hex()})
        raw = body[2:]
        if field.get('word_order', 'big') == 'little' and size == 2:
            raw = raw[2:] + raw[:2]
        fmt = {'uint16': '>H', 'int16': '>h', 'float32': '>f',
               'uint32': '>I', 'int32': '>i'}[field['type']]
        value = struct.unpack(fmt, raw)[0] * field.get('scale', 1) + field.get('offset', 0)
        values[field['field']] = {'value': value, 'unit': field.get('unit', '')}
    return values, artifacts


async def lwm2m_read(config):
    with db.connect() as conn:
        registered = conn.execute('SELECT * FROM registrations WHERE endpoint=%s AND expires_at>now()',
                                  (config['endpoint'],)).fetchone()
    if registered is None:
        raise ValueError('LwM2M endpoint not registered or expired')
    context = await aiocoap.Context.create_client_context(transports=['udp6'])
    try:
        values = {}
        artifacts = []
        for item in config['resources']:
            instance = '/' + '/'.join(item['path'].strip('/').split('/')[:2])
            if '<' + instance + '>' not in registered['links']:
                raise ValueError('Object instance not registered')
            response = await asyncio.wait_for(context.request(aiocoap.Message(
                code=aiocoap.GET, uri='coap://' + registered['address'] + item['path'],
                accept=0)).response, 5)
            if not response.code.is_successful() or response.opt.content_format not in (None, 0):
                raise ValueError('LwM2M text Read failed')
            artifacts.append({'resource': item['path'], 'payload_hex': response.payload.hex(), 'content_format': int(response.opt.content_format or 0)})
            value = response.payload.decode()
            if item['type'] == 'number':
                value = float(value)
            values[item['field']] = {'value': value, 'unit': item.get('unit', '')}
        return values, artifacts
    finally:
        await context.shutdown()


def poll_loop():
    due = {}
    import time
    while not runtime.STOP.is_set():
        try:
            with db.connect() as conn:
                rows = conn.execute('SELECT * FROM sources ORDER BY id').fetchall()
            for row in rows:
                config = row['config']
                if not config.get('enabled', True) or time.monotonic() < due.get(row['id'], 0):
                    continue
                due[row['id']] = time.monotonic() + config.get('interval_seconds', 10)
                error = None
                try:
                    values, artifacts = modbus_read(config) if config['protocol'] == 'modbus' else asyncio.run(lwm2m_read(config))
                    document = {'message_id': str(uuid.uuid4()), 'device_id': config['device_id'],
                                'source_id': row['id'], 'observed_at': None,
                                'acquired_at': now(), 'profile': config.get('profile', 'identity'),
                                'values': values, 'source_artifact': artifacts}
                    runtime.ingest(json.dumps(document).encode(), 'json', config['protocol'])
                except Exception:
                    error = 'Read failed; verify endpoint, resource/register map and profile'
                with db.connect() as conn:
                    conn.execute('UPDATE sources SET last_error=%s,last_polled=now() WHERE id=%s',
                                 (error, row['id']))
        except Exception:
            runtime.HEALTH['sources'] = False
        runtime.STOP.wait(.5)


class DataResource(aiocoap.resource.Resource):
    async def render_post(self, request):
        try:
            if os.getenv('SERVICE_ROLE') == 'collector':
                result = await asyncio.to_thread(runtime.accept_receipt, request.payload, 'coap')
            else:
                codec = {50: 'json', 41: 'xml', 60: 'cbor'}.get(request.opt.content_format)
                result = await asyncio.to_thread(runtime.ingest, request.payload, codec, 'coap')
            return aiocoap.Message(code=aiocoap.CHANGED,
                                  payload=json.dumps(result).encode(), content_format=50)
        except ValueError as error:
            return aiocoap.Message(code=aiocoap.BAD_REQUEST, payload=str(error).encode())
        except Exception:
            return aiocoap.Message(code=aiocoap.SERVICE_UNAVAILABLE)


class Registration(aiocoap.resource.Resource):
    def __init__(self, site, key=None):
        super().__init__()
        self.site, self.key = site, key

    async def render_post(self, request):
        query = parse_qs('&'.join(request.opt.uri_query))
        try:
            lifetime = int(query.get('lt', ['86400'])[0])
            if not 1 <= lifetime <= 86400:
                raise ValueError('Lifetime outside lab range')
            address = request.remote.hostinfo
            with db.connect() as conn:
                if self.key:
                    row = conn.execute('UPDATE registrations SET expires_at=now()+make_interval(secs=>%s) '
                                       'WHERE id=%s AND address=%s RETURNING id',
                                       (lifetime, self.key, address)).fetchone()
                    return aiocoap.Message(code=aiocoap.CHANGED if row else aiocoap.NOT_FOUND)
                endpoint = query['ep'][0]
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', endpoint) or query.get('lwm2m') != ['1.0']:
                    raise ValueError('Supports LwM2M 1.0 registration subset')
                if request.opt.content_format != 40 or query.get('b', ['U']) != ['U']:
                    raise ValueError('Requires link-format and UDP binding')
                links = request.payload.decode()
                if not re.fullmatch(r'</\d+/\d+>(,</\d+/\d+>)*', links):
                    raise ValueError('Requires explicit object instance links')
                key = uuid.uuid4().hex
                existing = conn.execute('SELECT address FROM registrations WHERE endpoint=%s', (endpoint,)).fetchone()
                if existing and existing['address'] != address:
                    return aiocoap.Message(code=aiocoap.FORBIDDEN)
                conn.execute('INSERT INTO registrations(id,endpoint,address,links,expires_at) '
                             'VALUES (%s,%s,%s,%s,now()+make_interval(secs=>%s)) '
                             'ON CONFLICT(endpoint) DO UPDATE SET links=excluded.links,expires_at=excluded.expires_at RETURNING id',
                             (key, endpoint, address, links, lifetime))
                key = conn.execute('SELECT id FROM registrations WHERE endpoint=%s', (endpoint,)).fetchone()['id']
            self.site.add_resource(['rd', key], Registration(self.site, key))
            return aiocoap.Message(code=aiocoap.CREATED, location_path=('rd', key))
        except Exception as error:
            return aiocoap.Message(code=aiocoap.BAD_REQUEST, payload=str(error)[:100].encode())

    async def render_delete(self, request):
        with db.connect() as conn:
            row = conn.execute('DELETE FROM registrations WHERE id=%s AND address=%s RETURNING id',
                               (self.key, request.remote.hostinfo)).fetchone()
        return aiocoap.Message(code=aiocoap.DELETED if row else aiocoap.NOT_FOUND)


async def coap_server():
    site = aiocoap.resource.Site()
    site.add_resource(['ingest'], DataResource())
    site.add_resource(['receipts'], DataResource())
    if os.getenv('SERVICE_ROLE') != 'collector':
        site.add_resource(['rd'], Registration(site))
        with db.connect() as conn:
            for row in conn.execute('SELECT id FROM registrations'):
                site.add_resource(['rd', row['id']], Registration(site, row['id']))
    context = await aiocoap.Context.create_server_context(site, bind=('0.0.0.0', 5683), transports=['udp6'])
    runtime.HEALTH['coap'] = True
    try:
        await asyncio.Future()
    finally:
        await context.shutdown()
