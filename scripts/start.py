"""從標準映像啟動獨立平台，選擇性加入叢集或無部署權限 CI。"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--cluster', action='store_true')
parser.add_argument('--ci-test', action='store_true')
args = parser.parse_args()


def run(command):
    subprocess.run(command, cwd=ROOT, check=True)


run([sys.executable, 'scripts/init_env.py'])
dc = ['docker','compose','-f','compose.yaml','-f','compose.ops.yaml']
run(dc + ['build','exchange','postgres','logstash'])
run(dc + ['up','-d','--no-build'])
if args.cluster:
    run([sys.executable,'scripts/init_cluster_secret.py'])
    full = dc + ['-f','compose.cluster.yaml']
    run(full + ['build','k3s'])
    run(full + ['up','-d','registry','k3s','tier2','tier3a','tier3b'])
    for _ in range(60):
        result = subprocess.run(['docker','exec','iot-exchange-k3s-1','kubectl','get','nodes'],capture_output=True)
        if result.returncode == 0 and result.stdout.count(b'Ready') >= 4:
            break
        time.sleep(2)
    else:
        raise SystemExit('Cluster nodes did not become Ready')
    with subprocess.Popen(['docker','save','prom/prometheus:v3.2.1'],stdout=subprocess.PIPE) as producer:
        subprocess.run(['docker','exec','-i','iot-exchange-k3s-1','ctr','images','import','-'],stdin=producer.stdout,check=True)
    check = subprocess.run(['docker','exec','iot-exchange-k3s-1','kubectl','get','crd','gateways.networking.istio.io'],capture_output=True)
    if check.returncode != 0:
        run([sys.executable,'scripts/cluster.py','mesh'])
    run([sys.executable,'scripts/cluster.py'])
if args.ci_test:
    run([sys.executable,'scripts/init_ci_test.py'])
    run(dc + ['-f','compose.ci.yaml','up','-d','--build','jenkins'])
print('API: http://localhost:8100 | OpenAPI: http://localhost:8110/docs')
