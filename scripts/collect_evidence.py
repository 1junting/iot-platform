"""保存不含秘密的硬體、容器及節點證據。"""
import json
import subprocess
from pathlib import Path
from cluster import ROOT, NAMESPACE, kube


def output(args):
    return subprocess.check_output(args).decode()


info=json.loads(output(['docker','info','--format','{{json .}}']))
report={k:info.get(k) for k in ('ServerVersion','Architecture','NCPU','MemTotal','OperatingSystem')}
report['git_revision']=output(['git','rev-parse','HEAD']).strip()
report['containers']=[json.loads(line) for line in output(['docker','stats','--no-stream','--format','{{json .}}']).splitlines() if 'iot-exchange-' in line]
nodes=json.loads(kube('get','nodes','-o','json'))['items']
report['nodes']=[{'name':n['metadata']['name'],'tier':n['metadata']['labels'].get('tier'),
                  'addresses':n['status']['addresses'],
                  'ready':next(c['status'] for c in n['status']['conditions'] if c['type']=='Ready')} for n in nodes]
pods=json.loads(kube('-n',NAMESPACE,'get','pods','-o','json'))['items']
report['pods']=[{'name':p['metadata']['name'],'node':p['spec'].get('nodeName'),
                'images':[c['image'] for c in p['spec']['containers']],
                'phase':p['status']['phase']} for p in pods]
(ROOT/'reports/environment.json').write_text(json.dumps(report,indent=2))
print('Saved runtime evidence without environment values or credentials')
