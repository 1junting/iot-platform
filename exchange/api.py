"""各服務依角色提供獨立 API，所有管理寫入均驗證權限。"""
import asyncio
import json
import os
import secrets
import threading
import re
import time
import uuid
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import db, runtime, sources, tracing
from .model import LIMIT, fingerprint, validate_rules
from .security import issue, require

ROLE = os.getenv('SERVICE_ROLE', 'exchange')
PENDING = Gauge('exchange_pending', 'Durable pending deliveries')


@asynccontextmanager
async def lifespan(app):
    db.init()
    tracing.start()
    task = None
    if ROLE in {'exchange', 'edge3', 'edge2', 'edge1', 'collector'}:
        runtime.start()
        if ROLE in {'exchange', 'edge3', 'collector'}:
            task = asyncio.create_task(sources.coap_server())
        if ROLE in {'exchange', 'edge3'}:
            threading.Thread(target=sources.poll_loop, daemon=True).start()
    yield
    runtime.STOP.set()
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title='IoT ' + ROLE, version='1.0.0', lifespan=lifespan)


@app.middleware('http')
async def release_header(request, call_next):
    stamp, started = time.time(), time.monotonic()
    incoming = request.headers.get('x-b3-traceid', '')
    trace_id = incoming if re.fullmatch(r'[0-9a-f]{32}', incoming) else uuid.uuid4().hex
    request.state.trace_id = trace_id
    response = await call_next(request)
    response.headers['x-trace-id'] = trace_id
    if request.url.path not in {'/health', '/metrics'}:
        tracing.emit(trace_id, request.method + ' ' + request.url.path, stamp, time.monotonic() - started)
    response.headers['x-release'] = os.getenv('RELEASE_VERSION', 'stable')
    response.headers['x-instance'] = os.getenv('HOSTNAME', 'local')
    return response


@app.get('/health')
def health():
    with db.connect() as conn:
        conn.execute('SELECT 1')
    return {'status': 'ready', 'role': ROLE, 'adapters': runtime.HEALTH}


@app.get('/metrics')
def metrics():
    with db.connect() as conn:
        pending = conn.execute("SELECT count(*) AS n FROM deliveries WHERE state IN ('PENDING','RETRY','SENDING')").fetchone()['n']
    PENDING.set(pending)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Login(Strict):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


if ROLE == 'auth':
    @app.post('/api/v1/token')
    def login(body: Login):
        if not (secrets.compare_digest(body.username, 'admin') and
                secrets.compare_digest(body.password, os.environ['ADMIN_PASSWORD'])):
            raise HTTPException(401, 'Invalid credentials')
        return {'access_token': issue('admin', ['*']), 'token_type': 'bearer', 'expires_in': 1800}


class Device(Strict):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')
    name: str = Field(min_length=1, max_length=128)
    metadata: dict = Field(default_factory=dict)


if ROLE == 'devices':
    @app.get('/api/v1/devices', dependencies=[Depends(require('devices:read'))])
    def devices():
        with db.connect() as conn:
            return conn.execute('SELECT * FROM devices ORDER BY id').fetchall()

    @app.put('/api/v1/devices/{key}', dependencies=[Depends(require('devices:write'))])
    def device(key: str, body: Device):
        if key != body.id:
            raise HTTPException(422, 'Path and body ID mismatch')
        with db.connect() as conn:
            conn.execute('INSERT INTO devices VALUES (%s,%s) ON CONFLICT(id) DO UPDATE SET document=excluded.document',
                         (key, db.Jsonb(body.model_dump())))
        return body

    @app.delete('/api/v1/devices/{key}', dependencies=[Depends(require('devices:write'))])
    def remove_device(key: str):
        with db.connect() as conn:
            row = conn.execute('DELETE FROM devices WHERE id=%s RETURNING id', (key,)).fetchone()
        if not row:
            raise HTTPException(404, 'Unknown device')
        return row


async def read_body(request):
    result = bytearray()
    async for chunk in request.stream():
        result.extend(chunk)
        if len(result) > LIMIT:
            raise HTTPException(413, 'Payload exceeds 256 KiB')
    return bytes(result)


if ROLE in {'collector', 'analytics'}:
    @app.post('/receipts', dependencies=[Depends(require('data:write'))])
    async def receipt(request: Request):
        try:
            return await asyncio.to_thread(runtime.accept_receipt, await read_body(request), 'http')
        except (ValueError, KeyError):
            raise HTTPException(409, 'Invalid receipt or conflicting delivery ID') from None

    @app.get('/receipts', dependencies=[Depends(require('data:read'))])
    def receipts(message_id: str | None = None):
        with db.connect() as conn:
            if message_id:
                rows = conn.execute("SELECT * FROM receipts WHERE document->>'message_id'=%s ORDER BY received_at", (message_id,)).fetchall()
            else:
                rows = conn.execute('SELECT * FROM receipts ORDER BY received_at DESC LIMIT 200').fetchall()
        return jsonable_encoder({'items': rows})

    @app.get('/api/v1/summary', dependencies=[Depends(require('data:read'))])
    def summary():
        with db.connect() as conn:
            count = conn.execute('SELECT count(*) AS n FROM receipts').fetchone()['n']
            rows = conn.execute('SELECT document FROM receipts ORDER BY received_at DESC LIMIT 1000').fetchall()
        fields = {}
        for row in rows:
            for key, item in row['document']['values'].items():
                value = item['value']
                if isinstance(value, (float, int)) and not isinstance(value, bool):
                    fields.setdefault((key, item['unit']), []).append(value)
        return {'total_receipts': count, 'window_limit': 1000,
                'fields': [{'field': key, 'unit': unit, 'n': len(values),
                            'min': min(values), 'max': max(values),
                            'mean': sum(values)/len(values)}
                           for (key, unit), values in fields.items()]}


if ROLE == 'analytics':
    @app.post('/api/v1/alerts', dependencies=[Depends(require('alerts:write'))])
    async def alert(request: Request):
        document = json.loads(await read_body(request))
        key = str(uuid.uuid4())
        with db.connect() as conn:
            conn.execute('INSERT INTO alerts VALUES (%s,%s,now())', (key, db.Jsonb(document)))
        return {'id': key}

    @app.get('/api/v1/alerts', dependencies=[Depends(require('alerts:read'))])
    def alerts():
        with db.connect() as conn:
            return jsonable_encoder({'items': conn.execute('SELECT * FROM alerts ORDER BY created_at DESC LIMIT 100').fetchall()})

    @app.post('/api/v1/feedback', dependencies=[Depends(require('data:write'))])
    async def feedback(request: Request):
        document = json.loads(await read_body(request))
        if not isinstance(document, dict) or not isinstance(document.get('comment'), str) or not 1 <= len(document['comment']) <= 4000:
            raise HTTPException(422, 'Feedback needs a 1-4000 character comment')
        key = str(uuid.uuid4())
        with db.connect() as conn:
            conn.execute('INSERT INTO feedback VALUES (%s,%s,now())', (key, db.Jsonb(document)))
        return {'id': key}


class Rule(Strict):
    source: str = Field(min_length=1, max_length=128)
    target: str = Field(min_length=1, max_length=128)
    source_unit: str = Field(default='', max_length=32)
    target_unit: str = Field(default='', max_length=32)
    required: bool = False


class Profile(Strict):
    expected_version: int = Field(ge=0)
    rules: list[Rule] = Field(max_length=100)


class Destination(Strict):
    protocol: Literal['http', 'mqtt', 'amqp', 'websocket', 'coap']
    url: str = Field(max_length=1024)

    @model_validator(mode='after')
    def validate_url(self):
        parsed = urlsplit(self.url)
        schemes = {'http': {'http', 'https'}, 'mqtt': {'mqtt'},
                   'amqp': {'amqp'}, 'websocket': {'ws', 'wss'}, 'coap': {'coap'}}
        if parsed.scheme not in schemes[self.protocol] or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError('Invalid destination URL; credentials belong in service configuration')
        if self.protocol in {'mqtt', 'amqp'} and not parsed.path.strip('/'):
            raise ValueError('Topic/queue is required')
        return self


class Route(Strict):
    expected_version: int = Field(ge=0)
    enabled: bool = True
    input_protocol: str = Field(default='*', max_length=32)
    device_prefix: str = Field(default='', max_length=128)
    destination: Destination


class Register(Strict):
    address: int = Field(ge=0, le=65534)
    type: Literal['uint16', 'int16', 'uint32', 'int32', 'float32']
    field: str = Field(min_length=1, max_length=128)
    unit: str = Field(default='', max_length=32)
    scale: float = 1
    offset: float = 0
    word_order: Literal['big', 'little'] = 'big'


class Resource(Strict):
    path: str = Field(pattern=r'^/\d+/\d+/\d+$')
    field: str = Field(min_length=1, max_length=128)
    type: Literal['number', 'string']
    unit: str = Field(default='', max_length=32)


class Source(Strict):
    expected_version: int = Field(ge=0)
    protocol: Literal['modbus', 'lwm2m']
    enabled: bool = True
    device_id: str = Field(min_length=1, max_length=128)
    interval_seconds: float = Field(default=10, ge=1, le=86400)
    profile: str = 'identity'
    host: str | None = Field(default=None, max_length=253)
    port: int = Field(default=502, ge=1, le=65535)
    unit_id: int = Field(default=1, ge=0, le=247)
    function: Literal[3, 4] = 3
    registers: list[Register] = Field(default_factory=list, max_length=100)
    endpoint: str | None = Field(default=None, max_length=64)
    resources: list[Resource] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def validate_source(self):
        if self.protocol == 'modbus' and (not self.host or not self.registers):
            raise ValueError('Modbus requires host and register map')
        if self.protocol == 'lwm2m' and (not self.endpoint or not self.resources):
            raise ValueError('LwM2M requires endpoint and object/resource profile')
        return self


if ROLE in {'exchange', 'edge3', 'edge2', 'edge1'}:
    @app.post('/ingest', dependencies=[Depends(require('data:write'))])
    async def ingest(request: Request, codec: str = 'json'):
        try:
            return await asyncio.to_thread(runtime.ingest, await read_body(request), codec, 'http', request.state.trace_id)
        except OverflowError:
            raise HTTPException(503, 'Pending capacity exhausted') from None
        except ValueError as error:
            raise HTTPException(409 if 'conflict' in str(error) else 422, str(error)) from None

    @app.get('/api/v1/config/{kind}', dependencies=[Depends(require('config:read'))])
    def configurations(kind: Literal['profiles', 'routes', 'sources']):
        with db.connect() as conn:
            return jsonable_encoder(conn.execute(f'SELECT * FROM {kind} ORDER BY id').fetchall())

    def save_config(kind, key, body, value):
        if not 1 <= len(key) <= 128:
            raise HTTPException(422, 'Invalid config ID')
        try:
            version = db.config(kind, key, value, body.expected_version)
            return {'id': key, 'version': version}
        except ValueError as error:
            raise HTTPException(409, str(error)) from None

    @app.put('/api/v1/profiles/{key}', dependencies=[Depends(require('config:write'))])
    def profile(key: str, body: Profile):
        if key == 'identity':
            raise HTTPException(409, 'Identity profile is immutable')
        rules = [r.model_dump() for r in body.rules]
        try:
            validate_rules(rules)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        return save_config('profiles', key, body, rules)

    @app.put('/api/v1/routes/{key}', dependencies=[Depends(require('config:write'))])
    def route(key: str, body: Route):
        return save_config('routes', key, body, body.model_dump(exclude={'expected_version'}))

    @app.put('/api/v1/sources/{key}', dependencies=[Depends(require('config:write'))])
    def source(key: str, body: Source):
        return save_config('sources', key, body, body.model_dump(exclude={'expected_version'}))

    @app.get('/api/v1/messages', dependencies=[Depends(require('data:read'))])
    def messages(message_id: str | None = None):
        with db.connect() as conn:
            rows = conn.execute('SELECT * FROM messages WHERE (%s::text IS NULL OR id=%s) ORDER BY received_at DESC LIMIT 200',
                                (message_id, message_id)).fetchall()
        return jsonable_encoder({'items': rows})

    @app.get('/api/v1/deliveries', dependencies=[Depends(require('data:read'))])
    def deliveries(message_id: str | None = None):
        with db.connect() as conn:
            rows = conn.execute('SELECT * FROM deliveries WHERE (%s::text IS NULL OR message_id=%s) ORDER BY available_at DESC LIMIT 200',
                                (message_id, message_id)).fetchall()
        return jsonable_encoder({'items': rows})

    @app.get('/api/v1/quarantine', dependencies=[Depends(require('data:read'))])
    def quarantine():
        with db.connect() as conn:
            return jsonable_encoder({'items': conn.execute('SELECT * FROM quarantine ORDER BY id DESC LIMIT 100').fetchall()})

    @app.post('/api/v1/deliveries/{key}/replay', dependencies=[Depends(require('config:write'))])
    def replay(key: uuid.UUID):
        with db.connect() as conn:
            row = conn.execute("UPDATE deliveries SET state='PENDING',attempts=0,available_at=now(),error=NULL "
                               "WHERE id=%s AND state IN ('DEAD','RETRY') RETURNING id", (key,)).fetchone()
            if row:
                conn.execute('INSERT INTO audit(action,subject) VALUES (%s,%s)', ('replay', str(key)))
        if row is None:
            raise HTTPException(409, 'Only DEAD or RETRY can be replayed')
        return {'id': str(key), 'state': 'PENDING'}


if ROLE in {'exchange', 'edge3', 'collector'}:
    @app.websocket('/ws')
    async def websocket(ws: WebSocket):
        if not secrets.compare_digest(ws.headers.get('x-service-token', ''), os.environ['SERVICE_TOKEN']):
            await ws.close(code=1008)
            return
        await ws.accept()
        try:
            while True:
                raw = (await ws.receive_text()).encode()
                try:
                    result = await asyncio.to_thread(runtime.broker_receive, raw, 'json', 'websocket')
                except ValueError as error:
                    result = {'error': str(error)}
                await ws.send_json(result)
        except WebSocketDisconnect:
            pass
