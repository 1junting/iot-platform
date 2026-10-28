"""依明確格式與欄位規則轉譯，不推測廠商私有語意。"""
import base64
import hashlib
import io
import json
import math
from datetime import datetime, timezone

import cbor2
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from xml.etree.ElementTree import ParseError

LIMIT = 262144
UNITS = {
    'C': ('temperature', 1, 273.15), 'F': ('temperature', 5/9, 255.3722222222222),
    'K': ('temperature', 1, 0), 'Pa': ('pressure', 1, 0),
    'kPa': ('pressure', 1000, 0), 'hPa': ('pressure', 100, 0),
    'm': ('length', 1, 0), 'cm': ('length', .01, 0),
    'mm': ('length', .001, 0), 's': ('time', 1, 0),
    'ms': ('time', .001, 0), '%': ('fraction', .01, 0),
    'ratio': ('fraction', 1, 0),
}


def now():
    return datetime.now(timezone.utc).isoformat()


def json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def decode(raw: bytes, codec: str) -> dict:
    if len(raw) > LIMIT:
        raise ValueError('Payload exceeds 256 KiB')
    if codec == 'json':
        obj = json.loads(raw, object_pairs_hook=json_pairs)
    elif codec == 'cbor':
        stream = io.BytesIO(raw)
        obj = cbor2.CBORDecoder(stream).decode()
        if stream.read():
            raise ValueError('Trailing CBOR data')
    elif codec == 'xml':
        try:
            root = ElementTree.fromstring(raw, forbid_dtd=True)
        except (DefusedXmlException, ParseError):
            raise ValueError('Invalid or unsafe XML') from None
        if root.tag != 'message':
            raise ValueError('XML root must be message')
        obj = {k: root.findtext(k) for k in
               ('message_id', 'device_id', 'observed_at', 'profile', 'source_id')
               if root.findtext(k) is not None}
        obj['values'] = {}
        for item in root.findall('./values/field'):
            name = item.attrib['name']
            if name in obj['values']:
                raise ValueError('Duplicate XML field')
            kind, value = item.attrib.get('type', 'string'), item.text or ''
            if kind == 'number':
                value = float(value)
            elif kind == 'boolean':
                if value not in {'true', 'false'}:
                    raise ValueError('Invalid boolean')
                value = value == 'true'
            elif kind != 'string':
                raise ValueError('Unknown XML type')
            obj['values'][name] = {'value': value, 'unit': item.attrib.get('unit', '')}
    else:
        raise ValueError('Unsupported codec')
    if not isinstance(obj, dict):
        raise ValueError('Message must be an object')
    for key in ('message_id', 'device_id'):
        if not isinstance(obj.get(key), str) or not 1 <= len(obj[key]) <= 128:
            raise ValueError('Missing or invalid ' + key)
    for key in ('source_id', 'profile'):
        if key in obj and (not isinstance(obj[key], str) or not 1 <= len(obj[key]) <= 128):
            raise ValueError('Invalid ' + key)
    if obj.get('observed_at') is not None:
        stamp = datetime.fromisoformat(obj['observed_at'].replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError('Timestamp must include timezone')
    if not isinstance(obj.get('values'), dict) or not 1 <= len(obj['values']) <= 100:
        raise ValueError('values must contain 1-100 fields')
    json.dumps(obj, allow_nan=False)
    return obj


def validate_rules(rules):
    if len({r['source'] for r in rules}) != len(rules):
        raise ValueError('Duplicate mapping source')
    if len({r['target'] for r in rules}) != len(rules):
        raise ValueError('Duplicate mapping target')
    for rule in rules:
        source, target = rule.get('source_unit', ''), rule.get('target_unit', '')
        if source != target:
            if source not in UNITS or target not in UNITS or UNITS[source][0] != UNITS[target][0]:
                raise ValueError('Incompatible units')


def flatten_values(values, prefix='', depth=0):
    if depth > 8:
        raise ValueError('Field path exceeds eight levels')
    result = {}
    for key, value in values.items():
        if not isinstance(key, str):
            raise ValueError('Field key must be a string')
        path = prefix + key
        if isinstance(value, dict) and 'value' not in value:
            nested = flatten_values(value, path + '.', depth + 1)
        else:
            nested = {path: value}
        if result.keys() & nested.keys():
            raise ValueError('Ambiguous field paths')
        result.update(nested)
    if not 1 <= len(result) <= 100:
        raise ValueError('Expected 1-100 leaf fields')
    return result


def normalize(obj, rules, protocol, raw, codec):
    validate_rules(rules)
    mapped = {}
    values = flatten_values(obj['values'])
    for rule in rules:
        if rule.get('required', False) and rule['source'] not in values:
            raise ValueError('Missing required field ' + rule['source'])
    by_field = {r['source']: r for r in rules}
    for name, original in values.items():
        if not isinstance(name, str) or not 1 <= len(name) <= 128:
            raise ValueError('Invalid field name')
        item = original if isinstance(original, dict) else {'value': original}
        value, unit = item['value'], item.get('unit', '')
        if not isinstance(unit, str) or len(unit) > 32:
            raise ValueError('Invalid unit')
        if not isinstance(value, (int, float, str, bool)) and value is not None:
            raise ValueError('Field value must be scalar')
        target = name
        if name in by_field:
            rule = by_field[name]
            if unit != rule.get('source_unit', ''):
                raise ValueError('Unit mismatch for ' + name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError('Numeric mapping required')
            dest = rule.get('target_unit', '')
            # 舊版明確校正係數相容；未指定時依單位維度換算。
            if 'scale' in rule or 'offset' in rule:
                value = value * rule.get('scale', 1) + rule.get('offset', 0)
            elif unit != dest:
                _, scale, offset = UNITS[unit]
                _, dest_scale, dest_offset = UNITS[dest]
                value = (value * scale + offset - dest_offset) / dest_scale
            target, unit = rule['target'], dest
            if not math.isfinite(value):
                raise ValueError('Nonfinite mapped value')
        if target in mapped:
            raise ValueError('Mapping produces duplicate target fields')
        mapped[target] = {'value': value, 'unit': unit,
                          'mapping': 'explicit' if name in by_field else 'unchanged'}
    return {
        'api_version': 'v1', 'schema_version': '1',
        'message_id': obj['message_id'], 'source_id': obj.get('source_id', 'default'),
        'device_id': obj['device_id'], 'observed_at': obj.get('observed_at'),
        'observation_time_known': obj.get('observed_at') is not None,
        'received_at': now(), 'source': protocol,
        'profile': obj.get('profile', 'identity'), 'values': mapped,
        'raw': {'codec': codec, 'sha256': hashlib.sha256(raw).hexdigest(),
                'base64': base64.b64encode(raw).decode()},
        'hops': obj.get('hops', []),
        'source_protocol': obj.get('source_protocol', protocol),
        'source_artifact': obj.get('source_artifact'),
        'acquired_at': obj.get('acquired_at'),
        'provenance': obj.get('provenance', []),
    }


def fingerprint(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()
