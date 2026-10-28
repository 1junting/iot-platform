"""格式、規則、來源協定封包與授權的回歸測試。"""
import json
import os
import uuid

import cbor2
import pytest
from fastapi.testclient import TestClient

from exchange.model import decode, normalize, validate_rules
from exchange.security import issue
from exchange.api import app


def document():
    return {'message_id': uuid.uuid4().hex, 'device_id': 'fixture',
            'observed_at': None, 'values': {'temperature': {'value': 95, 'unit': 'F'}}}


def test_cbor_trailing_and_nonfinite_and_json_duplicates():
    for raw, codec in [(cbor2.dumps(document()) + b'\x01', 'cbor'),
                       (b'{"a":1,"a":2}', 'json'),
                       (json.dumps({**document(), 'values': {'bad': float('nan')}}).encode(), 'json')]:
        with pytest.raises(ValueError):
            decode(raw, codec)


def test_units_reverse_missing_and_original_time():
    obj = document()
    rules = [{'source': 'temperature', 'target': 'temperature', 'source_unit': 'F', 'target_unit': 'C', 'required': True}]
    result = normalize(obj, rules, 'http', json.dumps(obj).encode(), 'json')
    assert result['values']['temperature']['value'] == pytest.approx(35)
    assert result['observation_time_known'] is False
    assert result['observed_at'] is None
    reverse = [{'source': 'temperature', 'target': 'temperature', 'source_unit': 'C', 'target_unit': 'F'}]
    assert normalize(result, reverse, 'http', b'{}', 'json')['values']['temperature']['value'] == pytest.approx(95)
    with pytest.raises(ValueError, match='Incompatible units'):
        validate_rules([{'source': 'a', 'target': 'b', 'source_unit': 'C', 'target_unit': 'Pa'}])
    with pytest.raises(ValueError, match='Missing required'):
        normalize({**obj, 'values': {'x': 1}}, rules, 'http', b'{}', 'json')


def test_api_scope_and_immutable_profile():
    client = TestClient(app)
    assert client.get('/api/v1/messages').status_code == 401
    read_token = issue('viewer', ['data:read'])
    assert client.put('/api/v1/routes/no', headers={'authorization': 'Bearer ' + read_token}, json={}).status_code == 403
    assert client.put('/api/v1/profiles/identity', headers={'x-service-token': os.environ['SERVICE_TOKEN']},
                      json={'expected_version': 1, 'rules': []}).status_code == 409
    assert client.post('/ingest', headers={'x-service-token': os.environ['SERVICE_TOKEN']}, content=b'x' * 262145).status_code == 413


def test_nested_profile_and_unsafe_xml():
    obj = {**document(), 'values': {'readings': {'temperature': {'value': 95, 'unit': 'F'}}}}
    result = normalize(obj, [{'source': 'readings.temperature', 'target': 'temperature', 'source_unit': 'F', 'target_unit': 'C', 'required': True}], 'http', b'{}', 'json')
    assert result['values']['temperature']['value'] == pytest.approx(35)
    with pytest.raises(ValueError, match='unsafe XML'):
        decode(b'<!DOCTYPE message [<!ENTITY a SYSTEM "file:///etc/passwd">]><message>&a;</message>', 'xml')
