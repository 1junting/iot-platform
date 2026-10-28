"""以固定上限負載驗證真實 Kubernetes CPU HPA 與 PROA 資料來源。"""
import concurrent.futures
import json
import os
import time
import uuid
from pathlib import Path

import httpx

headers={'x-service-token':os.environ['SERVICE_TOKEN']}
key='sustain-'+uuid.uuid4().hex[:10]
client=httpx.Client(headers=headers,timeout=10)
response=client.put('http://exchange:8050/api/v1/routes/'+key,json={'expected_version':0,'device_prefix':key,'destination':{'protocol':'http','url':'http://collector:8050/receipts'}})
response.raise_for_status()
rate=int(os.getenv('LOAD_RPS','60'))
seconds=int(os.getenv('LOAD_SECONDS','120'))
start=time.monotonic()
errors=[]
latencies=[]


def send(index):
    before=time.monotonic()
    try:
        response=client.post('http://k3s:30050/ingest',json={'message_id':key+'-'+str(index),'device_id':key,'source_id':key,'observed_at':None,'values':{'temperature':{'value':index%50,'unit':'C'}}})
        return response.status_code, time.monotonic()-before
    except Exception:
        return 0,time.monotonic()-before


with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
    futures=[]
    for index in range(rate*seconds):
        futures.append(pool.submit(send,index))
        delay=start+(index+1)/rate-time.monotonic()
        if delay>0: time.sleep(delay)
    results=[future.result() for future in futures]
report={'run':key,'requests':len(results),'target_rps':rate,'duration_seconds':round(time.monotonic()-start,3),'errors':sum(status!=200 for status,_ in results),'note':'Kubernetes NodePort traffic; HPA/PROA snapshots recorded separately.'}
Path('/reports/sustained-load.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report))
