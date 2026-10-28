"""分級負載，記錄接受延遲、持久目的端延遲、積壓與錯誤率。"""
import concurrent.futures
import json
import os
import platform
import time
import uuid
from pathlib import Path

import httpx
import numpy as np

headers = {'x-service-token': os.environ['SERVICE_TOKEN']}
client = httpx.Client(headers=headers, timeout=30)
prefix = 'load-' + uuid.uuid4().hex[:10]


def call(path, **kwargs):
    response = client.request(kwargs.pop('method', 'GET'), 'http://exchange:8050' + path, **kwargs)
    response.raise_for_status()
    return response.json()


def main():
    call('/api/v1/routes/' + prefix, method='PUT', json={
        'expected_version': 0, 'device_prefix': prefix,
        'destination': {'protocol': 'http', 'url': 'http://collector:8050/receipts'}})
    report = {'run': prefix, 'python': platform.python_version(), 'platform': platform.platform(),
              'hardware': 'Docker Desktop VM; host CPU and memory recorded separately',
              'levels': []}
    for concurrency, count in [(1, 20), (5, 50), (10, 100)]:
        started, errors, records = time.monotonic(), [], []
        def send(index):
            key = prefix + '-' + str(concurrency) + '-' + str(index)
            before = time.monotonic()
            document = {'message_id': key, 'source_id': prefix, 'device_id': prefix,
                        'observed_at': None, 'values': {'temperature': {'value': index % 50, 'unit': 'C'}}}
            response = client.post('http://exchange:8050/ingest', json=document)
            return key, before, time.monotonic() - before, response.status_code, len(json.dumps(document).encode())
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            records = list(pool.map(send, range(count)))
        ingress_duration = time.monotonic() - started
        completed, end_to_end, max_backlog = set(), [], 0
        until = time.monotonic() + 120
        while len(completed) < count and time.monotonic() < until:
            response = client.get('http://collector:8050/receipts').json()['items']
            ids = {r['document']['message_id'] for r in response}
            for key, before, _, status, _ in records:
                if key not in completed and key in ids:
                    completed.add(key)
                    end_to_end.append(time.monotonic() - before)
            jobs = call('/api/v1/deliveries')['items']
            max_backlog = max(max_backlog, sum(1 for j in jobs if j['message_id'].startswith(prefix) and j['state'] in {'PENDING','SENDING','RETRY'}))
            time.sleep(.1)
        assert len(completed) == count, 'Unreceived load messages'
        times = [r[2] * 1000 for r in records]
        def percentiles(values):
            return {f'p{n}_ms': round(float(np.percentile(values, n)), 3) for n in (50,95,99)}
        report['levels'].append({'concurrency': concurrency, 'messages': count,
            'payload_bytes': records[0][4], 'ingress_rps': round(count / ingress_duration, 3),
            'destination_rps': round(count / (time.monotonic() - started), 3),
            'errors': sum(r[3] != 200 for r in records),
            'ingress_latency': percentiles(times),
            'destination_observed_latency': percentiles([x*1000 for x in end_to_end]),
            'receipt_poll_interval_ms': 100, 'max_observed_pending': max_backlog})
    Path('/reports/load-report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
