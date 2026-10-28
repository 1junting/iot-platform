"""只管理本專案的 Kubernetes；不讀取或變更主機 kubeconfig。"""
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
CLUSTER = 'iot-exchange-k3s-1'
NAMESPACE = 'iot-exchange'


def run(args, data=None, timeout=300):
    return subprocess.run(args, input=data, check=True, capture_output=True,
                          timeout=timeout).stdout


def kube(*args, data=None):
    return run(['docker', 'exec', '-i', CLUSTER, 'kubectl', *args], data)


def apply(obj):
    kube('apply', '-f', '-', data=json.dumps(obj).encode())


def address(service):
    obj = json.loads(run(['docker', 'inspect', 'iot-exchange-' + service + '-1']))[0]
    return obj['NetworkSettings']['Networks']['iot-exchange_lab']['IPAddress']


def deploy():
    env = dict(line.split('=', 1) for line in (ROOT / '.env').read_text().splitlines() if '=' in line)
    image_id = run(['docker', 'image', 'inspect', 'iot-exchange-app:local', '--format', '{{.Id}}']).decode().strip().split(':')[1][:16]
    image = 'registry:5000/iot-exchange:' + image_id
    run(['docker', 'tag', 'iot-exchange-app:local', 'localhost:5098/iot-exchange:' + image_id])
    run(['docker', 'push', 'localhost:5098/iot-exchange:' + image_id])
    apply({'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': NAMESPACE, 'labels': {'istio-injection': 'enabled'}}})
    # Kubernetes 服務名對應隔離 Compose 目的端；不依賴 Docker DNS 穿越 Pod。
    for service, ports in {
        'collector': [('http',8050,'TCP'),('coap',5683,'UDP')],
        'analytics': [('http',8050,'TCP')],
        'mqtt': [('mqtt',1883,'TCP')],
        'rabbitmq': [('amqp',5672,'TCP')],
    }.items():
        items = [{'name': name, 'port': port, 'protocol': protocol} for name,port,protocol in ports]
        apply({'apiVersion':'v1','kind':'Service','metadata':{'name':service,'namespace':NAMESPACE},'spec':{'ports':items}})
        apply({'apiVersion':'v1','kind':'Endpoints','metadata':{'name':service,'namespace':NAMESPACE},
               'subsets':[{'addresses':[{'ip':address(service)}],'ports':items}]})
    settings = {'SERVICE_NAME': 'exchange', 'SERVICE_ROLE': 'exchange', 'RUN_WORKER': 'false',
                'DATABASE_URL': f"postgresql://exchange_app:{env['EXCHANGE_DB_PASSWORD']}@{address('postgres')}:5432/exchange",
                'AMQP_URL': f"amqp://lab:{env['RABBITMQ_PASSWORD']}@{address('rabbitmq')}:5672/%2F",
                'MQTT_HOST': address('mqtt'), 'SERVICE_TOKEN': env['SERVICE_TOKEN'], 'JWT_SECRET': env['JWT_SECRET'],
                'LOG_HOST': address('logstash'), 'ZIPKIN_URL': 'http://' + address('jaeger') + ':9411/api/v2/spans'}
    apply({'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 'exchange-config', 'namespace': NAMESPACE}, 'stringData': settings})
    apply({'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': 'proa-config', 'namespace': NAMESPACE},
           'data': {'PROMETHEUS_URL': 'http://cluster-prometheus:9090', 'METRICS_QUERY': 'sum(rate(exchange_received_total{job="cluster-exchange"}[30s])) or vector(0)',
                    'PROA_APPLY': 'true', 'PROA_MIN_REPLICAS': '2', 'PROA_CAPACITY_RPS': '20', 'PROA_MAX_REPLICAS': '4', 'PROA_SAMPLE_SECONDS': '2'}})
    kube('apply', '-f', '-', data=(ROOT / 'deploy/kubernetes/monitoring.yaml').read_bytes())
    document = (ROOT / 'deploy/kubernetes/platform.yaml').read_text().replace('IMAGE_PLACEHOLDER', image)
    kube('apply', '-f', '-', data=document.encode())
    for name in ('hdpcm-exchange', 'exchange-worker', 'proa'):
        print(kube('-n', NAMESPACE, 'rollout', 'status', 'deployment/' + name, '--timeout=180s').decode())
    print('Cluster deployments ready; image=' + image_id)


def install_mesh():
    private = ROOT / 'reports/private'
    private.mkdir(exist_ok=True, parents=True)
    config = run(['docker', 'exec', CLUSTER, 'cat', '/etc/rancher/k3s/k3s.yaml']).decode().replace('127.0.0.1:6443', 'k3s:6443')
    p = private / 'kubeconfig'
    p.write_text(config)
    p.chmod(0o600)
    name = 'iot-exchange-istio-installer'
    subprocess.run(['docker', 'rm', name], capture_output=True)
    run(['docker', 'create', '--user', '0:0', '--name', name, '--network', 'iot-exchange_lab',
         'istio/istioctl:1.29.7', 'install', '--kubeconfig', '/tmp/kubeconfig', '-y', '-f', '/tmp/istio.yaml'])
    run(['docker', 'cp', str(p), name + ':/tmp/kubeconfig'])
    run(['docker', 'cp', str(ROOT / 'deploy/kubernetes/istio.yaml'), name + ':/tmp/istio.yaml'])
    subprocess.run(['docker', 'start', '-a', name], check=True)
    code = run(['docker', 'inspect', name, '--format', '{{.State.ExitCode}}']).decode().strip()
    if code != '0':
        raise RuntimeError('Istio installation failed')
    p.unlink()
    run(['docker', 'rm', name])


if __name__ == '__main__':
    install_mesh() if 'mesh' in sys.argv else deploy()
