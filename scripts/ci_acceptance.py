"""觸發僅測試的 Jenkins job，保存來源提交與實際結果。"""
import base64
import http.cookiejar
import json
import re
import time
import urllib.request
from pathlib import Path

root=Path(__file__).resolve().parents[1]
env=dict(line.split('=',1) for line in (root/'.env').read_text().splitlines() if '=' in line)
base='http://127.0.0.1:8098'
headers={'Authorization':'Basic '+base64.b64encode(('admin:'+env['JENKINS_PASSWORD']).encode()).decode()}
opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def request(path, body=None):
    req=urllib.request.Request(base+path,headers=headers,data=body)
    with opener.open(req,timeout=15) as response:
        return response.read()


for _ in range(60):
    try:
        crumb=json.loads(request('/crumbIssuer/api/json'))
        break
    except Exception:
        time.sleep(2)
else:
    raise RuntimeError('Jenkins not ready')
headers[crumb['crumbRequestField']]=crumb['crumb']
job=json.loads(request('/job/iot-exchange/api/json'))
number=job['nextBuildNumber']
request('/job/iot-exchange/build',b'')
for _ in range(150):
    try:
        result=json.loads(request('/job/iot-exchange/'+str(number)+'/api/json'))
        if not result.get('building',True) and result.get('result'):
            break
    except Exception:
        pass
    time.sleep(2)
else:
    raise RuntimeError('Jenkins test job timed out')
console=request('/job/iot-exchange/'+str(number)+'/consoleText').decode()
# 只保存沒有秘密的測試摘要；完整 console 留在原生 Jenkins。
report={'build':number,'result':result['result'],'job':'iot-exchange',
        'test_summary':re.findall(r'\d+ passed[^\n]*',console),
        'revision':next((a.get('lastBuiltRevision',{}).get('SHA1') for a in result.get('actions',[]) if a.get('lastBuiltRevision')),None),
        'deployment_credentials':False,'docker_socket_mounted':False}
(root/'reports/ci-acceptance.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
if result['result']!='SUCCESS':
    safe='\n'.join(line for line in console.splitlines() if any(x in line for x in ('ERROR','Error','FAILED','failed','Exception','Invalid','No such')))
    print(safe[-4000:])
    raise SystemExit(1)
