"""產生固定隔離名稱與服務資料庫的 Compose 設定。"""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
services = {}
roles = ('exchange', 'auth', 'devices', 'analytics', 'collector', 'edge1', 'edge2', 'edge3')
base_env = {'SERVICE_TOKEN': '${SERVICE_TOKEN}', 'JWT_SECRET': '${JWT_SECRET}',
            'AMQP_URL': 'amqp://lab:${RABBITMQ_PASSWORD}@rabbitmq:5672/%2F',
            'MQTT_HOST': 'mqtt', 'LOG_HOST': 'logstash'}
for name in roles:
    env = {**base_env, 'SERVICE_NAME': name, 'SERVICE_ROLE': name,
           'DATABASE_URL': f'postgresql://{name}_app:${{{name.upper()}_DB_PASSWORD}}@postgres:5432/{name}'}
    if name == 'auth':
        env['ADMIN_PASSWORD'] = '${ADMIN_PASSWORD}'
    if name == 'exchange':
        env['RUN_WORKER'] = 'false'
    if name == 'edge2':
        env['ANOMALY_THRESHOLDS'] = '{"temperature":{"min":0,"max":40}}'
    if name == 'edge1':
        env['INFERENCE_MODEL_PATH'] = '/service/inference-model.json'
    service = {'image': 'iot-exchange-app:local', 'build': '.', 'environment': env,
               'networks': ['lab'], 'restart': 'unless-stopped',
               'read_only': True, 'tmpfs': ['/tmp'], 'cap_drop': ['ALL'],
               'security_opt': ['no-new-privileges:true'],
               'command': ['uvicorn', 'exchange.api:app', '--host', '0.0.0.0', '--port', '8050', '--no-access-log', '--ws-max-size', '262144'],
               'depends_on': {'postgres': {'condition': 'service_healthy'}},
               'healthcheck': {'test': ['CMD', 'python', '-c', "import urllib.request; urllib.request.urlopen('http://localhost:8050/health',timeout=3)"],
                               'interval': '5s', 'timeout': '4s', 'retries': 20}}
    if name in {'exchange', 'collector', 'edge3'}:
        service['depends_on'].update({s: {'condition': 'service_healthy'} for s in ('mqtt', 'rabbitmq')})
    ports = {'exchange': 8110, 'collector': 8111, 'edge2': 8112, 'edge1': 8113, 'edge3': 8114}
    if name in ports:
        service['ports'] = [f'127.0.0.1:{ports[name]}:8050']
    if name in {'exchange', 'collector', 'edge3'}:
        port = {'exchange': 5698, 'collector': 5699, 'edge3': 5700}[name]
        service['ports'].append(f'127.0.0.1:{port}:5683/udp')
    if name == 'edge1':
        service['volumes'] = ['./infrastructure/inference-model.json:/service/inference-model.json:ro']
    services[name] = service
services['worker'] = {'image': 'iot-exchange-app:local',
    'command': ['python', '-m', 'exchange.worker'],
    'environment': services['exchange']['environment'], 'networks': ['lab'],
    'depends_on': {'exchange': {'condition': 'service_healthy'}}, 'restart': 'unless-stopped'}
services['postgres'] = {'build': 'infrastructure/postgres', 'environment': {
    'POSTGRES_USER': 'lab_owner', 'POSTGRES_PASSWORD': '${POSTGRES_PASSWORD}',
    **{name.upper() + '_DB_PASSWORD': '${' + name.upper() + '_DB_PASSWORD}' for name in roles}},
    'volumes': ['postgres-data:/var/lib/postgresql/data'],
    'networks': ['lab'], 'healthcheck': {'test': ['CMD-SHELL', 'pg_isready -U lab_owner -d postgres'], 'interval': '3s', 'timeout': '3s', 'retries': 30}}
services['mqtt'] = {'image': 'eclipse-mosquitto:2', 'ports': ['127.0.0.1:1898:1883'],
    'volumes': ['./infrastructure/mosquitto/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro', 'mqtt-data:/mosquitto/data'],
    'networks': ['lab'], 'healthcheck': {'test': ['CMD-SHELL', 'mosquitto_pub -t lab/health -m ok'], 'interval': '5s', 'timeout': '3s', 'retries': 20}}
services['rabbitmq'] = {'image': 'rabbitmq:3.13-alpine',
    'environment': {'RABBITMQ_DEFAULT_USER': 'lab', 'RABBITMQ_DEFAULT_PASS': '${RABBITMQ_PASSWORD}'},
    'ports': ['127.0.0.1:5697:5672'], 'volumes': ['rabbit-data:/var/lib/rabbitmq'],
    'networks': ['lab'], 'healthcheck': {'test': ['CMD', 'rabbitmq-diagnostics', '-q', 'ping'], 'interval': '5s', 'timeout': '5s', 'retries': 30}}
services['gateway'] = {'image': 'nginx:1.27-alpine', 'ports': ['127.0.0.1:8100:8080'],
    'volumes': ['./infrastructure/nginx/default.conf:/etc/nginx/conf.d/default.conf:ro'],
    'networks': ['lab'], 'depends_on': {name: {'condition': 'service_healthy'} for name in ('auth', 'devices', 'analytics', 'exchange')}}
services['proa'] = {'image': 'iot-exchange-app:local',
    'command': ['uvicorn', 'exchange.predictor:app', '--host', '0.0.0.0', '--port', '8070', '--no-access-log'],
    'environment': {'PROMETHEUS_URL': 'http://prometheus:9090', 'PROA_APPLY': 'false', 'PROA_CAPACITY_RPS': '20', 'PROA_MAX_REPLICAS': '4'},
    'ports': ['127.0.0.1:8170:8070'], 'networks': ['lab']}
services['tests'] = {'image': 'iot-exchange-app:local', 'build': '.', 'profiles': ['test'],
    'user': '0', 'environment': {**base_env, 'DATABASE_URL': services['exchange']['environment']['DATABASE_URL'], 'ADMIN_PASSWORD': '${ADMIN_PASSWORD}'},
    'command': ['pytest', '-q', 'tests'], 'volumes': ['./reports:/reports'], 'networks': ['lab']}
services['fixtures'] = {'image': 'iot-exchange-app:local', 'profiles': ['test'],
    'command': ['python', 'scripts/fixtures.py'], 'networks': ['lab']}
config = {'name': 'iot-exchange', 'services': services,
          'networks': {'lab': {}}, 'volumes': {name: {} for name in ('postgres-data', 'mqtt-data', 'rabbit-data')}}
(ROOT / 'compose.yaml').write_text(yaml.safe_dump(config, sort_keys=False))
