"""驗證原生監控、集中日誌、跨服務 trace 與 firing/resolved 通知。"""
import base64
import json
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
env=dict(line.split('=',1) for line in (ROOT/'.env').read_text().splitlines() if '=' in line)
headers={'x-service-token':env['SERVICE_TOKEN']}
report={}


def get(url, custom=None):
    request=urllib.request.Request(url,headers=custom or headers)
    with urllib.request.urlopen(request,timeout=15) as response:
        return json.load(response)


def wait(function, timeout=90):
    started=time.monotonic()
    while time.monotonic()-started<timeout:
        try:
            result=function()
            if result: return result
        except Exception:
            pass
        time.sleep(2)
    raise AssertionError('Monitoring acceptance timed out')


def main():
    targets=wait(lambda: (data if (data:=get('http://localhost:9098/api/v1/targets'))['status']=='success' else None))
    report['prometheus_targets']=[{'job':t['labels']['job'],'health':t['health']} for t in targets['data']['activeTargets']]
    basic=base64.b64encode(('admin:'+env['GRAFANA_PASSWORD']).encode()).decode()
    dashboard=get('http://localhost:3098/api/dashboards/uid/iot-exchange',{'Authorization':'Basic '+basic})
    report['grafana_dashboard_panels']=len(dashboard['dashboard']['panels'])
    assert report['grafana_dashboard_panels']>=6
    kibana=wait(lambda: get('http://localhost:5696/api/status'))
    report['kibana_status']=kibana['status']['overall']['level']
    assert report['kibana_status']=='available'
    output=subprocess.check_output(['docker','compose','--profile','test','run','--rm','--no-deps','tests','python','-c',
        "import httpx,json; r=httpx.get('http://elasticsearch:9200/iot-exchange-*/_search',params={'size':1}); r.raise_for_status(); print(json.dumps(r.json()))"],cwd=ROOT,stderr=subprocess.DEVNULL)
    logs=json.loads(output)
    assert logs['hits']['total']['value']>0
    report['elasticsearch_indexed_events']=logs['hits']['total']['value']
    traces=get('http://localhost:8190/api/traces?service=exchange&limit=10')['data']
    assert traces
    cross=[t for t in traces if len({p['serviceName'] for p in t['processes'].values()})>=2]
    assert cross, 'No cross-service trace'
    report['cross_service_trace_id']=cross[0]['traceID']
    report['trace_services']=sorted({p['serviceName'] for p in cross[0]['processes'].values()})
    initial={x['id'] for x in get('http://localhost:8100/analytics/api/v1/alerts')['items']}
    try:
        subprocess.run(['docker','compose','stop','exchange'],cwd=ROOT,check=True,capture_output=True)
        def fresh(status):
            return [x for x in get('http://localhost:8100/analytics/api/v1/alerts')['items'] if x['id'] not in initial and x['document'].get('status')==status]
        firing=wait(lambda: fresh('firing'))
        report['firing_alert_id']=firing[0]['id']
        subprocess.run(['docker','compose','start','exchange'],cwd=ROOT,check=True,capture_output=True)
        resolved=wait(lambda: fresh('resolved'))
        report['resolved_alert_id']=resolved[0]['id']
    finally:
        subprocess.run(['docker','compose','start','exchange'],cwd=ROOT,check=False,capture_output=True)
    (ROOT/'reports/observability-acceptance.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
