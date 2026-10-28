"""限定 120 秒負載，記錄真實 CPU HPA 與 PROA 狀態及獨立收件總數。"""
import json
import subprocess
import time
import urllib.request
from pathlib import Path
from cluster import ROOT, NAMESPACE, kube

samples=[]
with (ROOT/'scripts/sustained_load.py').open('rb') as source, (ROOT/'reports/scaling-load-run.txt').open('w') as output:
    process=subprocess.Popen(['docker','compose','--profile','test','run','--rm','-T','--no-deps','tests','python','-'],
        cwd=ROOT,stdin=source,stdout=output,stderr=subprocess.STDOUT)
    while process.poll() is None:
        hpa=json.loads(kube('-n',NAMESPACE,'get','hpa','hdpcm-exchange','-o','json'))
        try:
            with urllib.request.urlopen('http://127.0.0.1:8171/status',timeout=5) as response:
                proa=json.load(response)
        except Exception:
            proa={'status':'unavailable'}
        samples.append({'time':time.time(),'min_replicas':hpa['spec']['minReplicas'],
                        'current_replicas':hpa.get('status',{}).get('currentReplicas'),
                        'desired_replicas':hpa.get('status',{}).get('desiredReplicas'),
                        'cpu_metrics':hpa.get('status',{}).get('currentMetrics'), 'proa':proa})
        time.sleep(10)
    if process.returncode!=0:
        raise RuntimeError('Sustained load failed')
load=json.loads((ROOT/'reports/sustained-load.json').read_text())
key=load['run']
count=0
for _ in range(120):
    query="SELECT count(*) FROM receipts WHERE document->>'source_id'='"+key+"'"
    count=int(subprocess.check_output(['docker','exec','iot-exchange-postgres-1','psql','-U','lab_owner','-d','collector','-Atc',query]))
    if count==load['requests']: break
    time.sleep(1)
assert count==load['requests'], (count,load['requests'])
assert load['errors']==0, load
report={'load':load,'independent_collector_receipts':count,
        'max_observed_replicas':max(s['current_replicas'] or 0 for s in samples),
        'proa_applied_observed':any(s['proa'].get('applied') for s in samples),
        'samples':samples,'note':'Bounded laboratory load; no claim that prediction outperforms CPU HPA.'}
(ROOT/'reports/scaling-acceptance.json').write_text(json.dumps(report,indent=2))
print(json.dumps({k:v for k,v in report.items() if k!='samples'},indent=2))
