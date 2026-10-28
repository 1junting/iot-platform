"""CI 只獲得具名 Deployment 的 GET/PATCH 權限。"""
import json
import os
import ssl
import time
import urllib.request
from pathlib import Path

private = Path('/var/jenkins_home/cluster')
context = ssl.create_default_context(cafile=str(private / 'ca.crt'))
token = (private / 'token').read_text().strip()
headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/merge-patch+json'}
base = 'https://k3s:6443/apis/apps/v1/namespaces/iot-exchange/deployments/'


def request(name, body=None):
    req = urllib.request.Request(base + name, headers=headers,
          data=None if body is None else json.dumps(body).encode(),
          method='GET' if body is None else 'PATCH')
    with urllib.request.urlopen(req, context=context, timeout=15) as response:
        return json.load(response)


result = {'image': os.environ['IMAGE'], 'revision': os.environ.get('GIT_COMMIT')}
for name, container in [('hdpcm-exchange', 'exchange'), ('exchange-worker', 'worker')]:
    # strategic merge patch 只變更具名容器映像，保留 sidecar 及其餘配置。
    headers['Content-Type'] = 'application/strategic-merge-patch+json'
    patched = request(name, {'spec': {'template': {'spec': {'containers': [{'name': container, 'image': os.environ['IMAGE']}]}}}})
    generation = patched['metadata']['generation']
    until = time.monotonic() + 180
    while time.monotonic() < until:
        status = request(name)
        state = status.get('status', {})
        replicas = status['spec'].get('replicas', 1)
        if state.get('observedGeneration', 0) >= generation and state.get('updatedReplicas') == replicas and state.get('readyReplicas') == replicas and state.get('replicas') == replicas:
            break
        time.sleep(3)
    else:
        raise RuntimeError('Rollout timeout: ' + name)
    result[name] = 'rolled_out'
path = Path('reports/ci-result.json')
path.parent.mkdir(exist_ok=True)
path.write_text(json.dumps(result, indent=2))
print(json.dumps(result))
