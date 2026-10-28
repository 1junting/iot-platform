"""真正驗證 Istio 權重、熔斷、Pod 替換與單一接入節點故障。"""
import copy
import json
import time
import urllib.request
import uuid
from pathlib import Path

from cluster import ROOT, NAMESPACE, apply, kube, run

env = dict(line.split('=',1) for line in (ROOT / '.env').read_text().splitlines() if '=' in line)
HEADERS = {'x-service-token':env['SERVICE_TOKEN'], 'Content-Type':'application/json'}
REPORT = {}


def request(port, path, body=None, method=None):
    req = urllib.request.Request('http://127.0.0.1:' + str(port) + path,
        data=None if body is None else json.dumps(body).encode(),headers=HEADERS,
        method=method or ('POST' if body is not None else 'GET'))
    with urllib.request.urlopen(req,timeout=4) as response:
        return json.load(response), dict(response.headers)


def route(stable, canary):
    patch = {'spec':{'http':[{'route':[
        {'destination':{'host':'hdpcm-exchange','port':{'number':8050},'subset':'stable'},'weight':stable},
        {'destination':{'host':'hdpcm-exchange','port':{'number':8050},'subset':'canary'},'weight':canary}], 'timeout':'3s'}]}}
    kube('-n',NAMESPACE,'patch','virtualservice','exchange-public','--type=merge','-p',json.dumps(patch))


def wait_ready(name):
    return kube('-n',NAMESPACE,'rollout','status','deployment/'+name,'--timeout=120s')


def main():
    stable = json.loads(kube('-n',NAMESPACE,'get','deployment','hdpcm-exchange','-o','json'))
    canary = {'apiVersion':'apps/v1','kind':'Deployment','metadata':{'name':'exchange-canary','namespace':NAMESPACE},'spec':copy.deepcopy(stable['spec'])}
    canary['spec']['replicas'] = 1
    canary['spec']['selector']['matchLabels']['version'] = 'canary'
    canary['spec']['template']['metadata']['labels']['version'] = 'canary'
    canary['spec']['template']['spec']['containers'][0]['env'] = [{'name':'RELEASE_VERSION','value':'canary'}]
    try:
        apply(canary)
        wait_ready('exchange-canary')
        route(50,50)
        time.sleep(2)
        versions = {}
        for _ in range(60):
            _, headers = request(8121,'/health')
            version = headers.get('x-release', headers.get('X-Release','unknown'))
            versions[version] = versions.get(version,0)+1
        assert versions.get('stable',0)>0 and versions.get('canary',0)>0, versions
        REPORT['canary_50_50_observed'] = versions
        # 錯誤端點是獨立測試應用，不在產品服務中開後門。
        container = canary['spec']['template']['spec']['containers'][0]
        container['command'] = ['uvicorn','fault_app:app','--app-dir','/service/tests','--host','0.0.0.0','--port','8050']
        apply(canary)
        wait_ready('exchange-canary')
        route(0,100)
        status = {}
        for _ in range(40):
            try:
                request(8121,'/fault')
                code = 200
            except Exception as error:
                code = getattr(error,'code',0)
            status[str(code)] = status.get(str(code),0)+1
            time.sleep(.15)
        gateway = json.loads(kube('-n','istio-system','get','pods','-l','istio=ingressgateway','-o','json'))['items'][0]['metadata']['name']
        stats = kube('-n','istio-system','exec',gateway,'--','pilot-agent','request','GET','stats?filter=outlier_detection').decode()
        ejections = [line for line in stats.splitlines() if 'ejections_' in line and 'canary' in line]
        assert any(int(line.rsplit(':',1)[-1].strip())>0 for line in ejections if 'ejections_active' in line or 'ejections_enforced_total' in line), stats
        REPORT['circuit_breaker'] = {'responses':status,'ejection_stats':ejections}
    finally:
        route(100,0)
        kube('-n',NAMESPACE,'delete','deployment','exchange-canary','--ignore-not-found=true')
    pods = json.loads(kube('-n',NAMESPACE,'get','pods','-l','app=hdpcm-exchange,version=stable','-o','json'))['items']
    old = pods[0]['metadata']['name']
    started = time.monotonic()
    kube('-n',NAMESPACE,'delete','pod',old,'--wait=false')
    wait_ready('hdpcm-exchange')
    names = [p['metadata']['name'] for p in json.loads(kube('-n',NAMESPACE,'get','pods','-l','app=hdpcm-exchange,version=stable','-o','json'))['items']]
    assert any(name != old for name in names)
    REPORT['pod_replacement_seconds'] = round(time.monotonic()-started,3)
    try:
        run(['docker','stop','iot-exchange-tier3a-1'])
        success, failures, started = 0,0,time.monotonic()
        while time.monotonic()-started<75:
            try:
                request(8121,'/health')
                success += 1
                if success>=5:
                    break
            except Exception:
                failures += 1
                success = 0
            time.sleep(.3)
        assert success>=5
        key = 'cluster-'+uuid.uuid4().hex[:10]
        request(8110,'/api/v1/routes/'+key,{'expected_version':0,'device_prefix':key,'destination':{'protocol':'http','url':'http://collector:8050/receipts'}},'PUT')
        request(8121,'/ingest',{'message_id':key,'source_id':key,'device_id':key,'observed_at':None,'values':{'temperature':{'value':35,'unit':'C'}}})
        proof = []
        for _ in range(100):
            proof = request(8111,'/receipts?message_id='+key)[0]['items']
            if proof: break
            time.sleep(.3)
        assert proof
        REPORT['tier3_node_failure'] = {'eventual_successes':success,'transitional_failures':failures,'observed_seconds':round(time.monotonic()-started,3),'independent_delivery_id':proof[0]['delivery_id']}
    finally:
        run(['docker','start','iot-exchange-tier3a-1'])
    REPORT['limits'] = 'Container-node fault only; control plane and PostgreSQL remain singletons on one physical host.'
    (ROOT / 'reports/cluster-acceptance.json').write_text(json.dumps(REPORT,indent=2))
    print(json.dumps(REPORT,indent=2))


if __name__ == '__main__':
    main()
