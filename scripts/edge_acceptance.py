"""資料經 Tier 3→Tier 2→Tier 1→中央後，驗證處理鏈與獨立收件。"""
import json
import os
import time
import uuid
from pathlib import Path

import httpx

client = httpx.Client(headers={'x-service-token': os.environ['SERVICE_TOKEN']}, timeout=15)
key = 'tier-' + uuid.uuid4().hex[:10]


def req(method, service, path, **kwargs):
    response = client.request(method, 'http://' + service + ':8050' + path, **kwargs)
    response.raise_for_status()
    return response.json()


def main():
    for source, destination in [('edge3', 'edge2'), ('edge2', 'edge1'), ('edge1', 'exchange'), ('exchange', 'collector'), ('exchange', 'analytics')]:
        req('PUT', source, '/api/v1/routes/' + key + '-' + destination,
            json={'expected_version': 0, 'device_prefix': key,
                  'destination': {'protocol': 'http', 'url': 'http://' + destination + ':8050/' + ('receipts' if destination in {'collector','analytics'} else 'ingest')}})
    document = {'message_id': key, 'device_id': key, 'source_id': key,
                'observed_at': '2026-10-28T00:00:00Z', 'values': {'temperature': {'value': 45, 'unit': 'C'}}}
    req('POST', 'edge3', '/ingest', json=document)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        rows = req('GET', 'collector', '/receipts', params={'message_id': key})['items']
        if rows:
            break
        time.sleep(.3)
    assert rows, 'No independent edge-chain receipt'
    final = rows[0]['document']
    assert final['hops'] == ['edge3', 'edge2', 'edge1', 'exchange']
    assert final['processing'][0]['tier'] == 2
    assert final['processing'][0]['anomalies'][0]['value'] == 45
    assert final['processing'][1]['tier'] == 1
    assert final['processing'][1]['model_loaded'] is True
    assert abs(final['processing'][1]['score'] - .4) < 1e-8
    assert req('GET', 'analytics', '/receipts', params={'message_id': key})['items']
    result = {'message_id': key, 'hops': final['hops'], 'processing': final['processing'],
              'collector_delivery_id': rows[0]['delivery_id'],
              'note': 'Linear inference uses explicit demonstration weights; no trained industrial model or physical edge hosts claimed.'}
    Path('/reports/edge-acceptance.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
