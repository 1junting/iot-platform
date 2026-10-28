"""僅暫停本專案的目的端與 worker，驗證停機、補送與確認遺失。"""
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
env = dict(line.split('=', 1) for line in (ROOT / '.env').read_text().splitlines() if '=' in line)
PREFIX = 'recovery-' + uuid.uuid4().hex[:10]
HEADERS = {'x-service-token': env['SERVICE_TOKEN'], 'Content-Type': 'application/json'}
DC = ['docker', 'compose', '-f', 'compose.yaml', '-f', 'compose.ops.yaml', '-f', 'compose.cluster.yaml']


def dc(*args, check=True):
    return subprocess.run(DC + list(args), check=check, capture_output=True, text=True)


def api(port, path, data=None, method=None):
    request = urllib.request.Request('http://127.0.0.1:' + str(port) + path,
              data=None if data is None else json.dumps(data).encode(), headers=HEADERS,
              method=method or ('POST' if data is not None else 'GET'))
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def wait(function, timeout=90):
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            result = function()
            if result:
                return result
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(.4)
    raise AssertionError('Recovery timed out')


def document(suffix):
    return {'message_id': PREFIX + suffix, 'device_id': PREFIX, 'source_id': PREFIX,
            'observed_at': None, 'values': {'temperature': {'value': 35, 'unit': 'C'}}}


def receipts(key):
    return api(8111, '/receipts?message_id=' + key)['items']


def main():
    result = {'run': PREFIX}
    try:
        for name in ('collector', 'analytics'):
            api(8110, '/api/v1/routes/' + PREFIX + '-' + name,
                {'expected_version': 0, 'device_prefix': PREFIX,
                 'destination': {'protocol': 'http', 'url': 'http://' + name + ':8050/receipts'}}, 'PUT')
        dc('stop', 'collector')
        first = document('-offline')
        api(8110, '/ingest', first)
        # analytics 在目的端停止時仍收件；兩者沒有共同產品前端依賴。
        check = dc('run', '--rm', '--no-deps', 'tests', 'python', '-c',
                   "import os,httpx,time; h={'x-service-token':os.environ['SERVICE_TOKEN']}; "
                   "\nfor _ in range(100):\n r=httpx.get('http://analytics:8050/receipts',headers=h,params={'message_id':'" + first['message_id'] + "'}).json()['items'];\n if r: break\n time.sleep(.2)\nassert r")
        result['other_destination_continued'] = check.returncode == 0
        jobs = api(8110, '/api/v1/deliveries?message_id=' + first['message_id'])['items']
        assert any(j['state'] in {'PENDING', 'SENDING', 'RETRY'} for j in jobs)
        dc('stop', 'worker')
        second = document('-worker-stopped')
        api(8110, '/ingest', second)
        assert all(j['state'] == 'PENDING' for j in api(8110, '/api/v1/deliveries?message_id=' + second['message_id'])['items'])
        dc('start', 'collector')
        dc('start', 'worker')
        wait(lambda: receipts(first['message_id']))
        wait(lambda: receipts(second['message_id']))
        result['offline_and_worker_restart_recovered'] = True
        # 子程序在目的端已保存後直接中斷，未完成本地狀態提交。
        dc('stop', 'worker')
        crash = dc('run', '--rm', '--no-deps', '-e', 'LEASE_SECONDS=3', 'tests',
                   'python', 'scripts/crash_probe.py', PREFIX + '-acklost', check=False)
        assert crash.returncode == 9, crash.stderr
        before = receipts(PREFIX + '-acklost')
        assert len(before) == 1
        started = time.monotonic()
        dc('start', 'worker')
        def completed():
            rows = api(8110, '/api/v1/deliveries?message_id=' + PREFIX + '-acklost')['items']
            return rows and all(r['state'] == 'DESTINATION_RECEIVED' for r in rows)
        wait(completed)
        after = receipts(PREFIX + '-acklost')
        assert len(after) == 1 and before[0]['delivery_id'] == after[0]['delivery_id']
        result.update(acknowledgement_loss_deduplicated=True,
                      recovery_seconds=round(time.monotonic() - started, 3))
        Path('reports/recovery-acceptance.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
    finally:
        dc('start', 'collector', 'worker', check=False)


if __name__ == '__main__':
    main()
