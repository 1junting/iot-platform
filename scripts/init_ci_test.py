"""CI 只連接測試專用資料庫，不取得運行服務或 Kubernetes 憑證。"""
import json
import secrets
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
p = root / '.env'
values = dict(line.split('=', 1) for line in p.read_text().splitlines() if '=' in line)
for key in ('CI_DB_PASSWORD', 'CI_SERVICE_TOKEN', 'CI_JWT_SECRET', 'CI_ADMIN_PASSWORD'):
    values.setdefault(key, secrets.token_hex(24))
info = json.loads(subprocess.check_output(['docker','inspect','iot-exchange-postgres-1']))[0]
host = info['NetworkSettings']['Networks']['iot-exchange_lab']['IPAddress']
values['CI_DATABASE_URL'] = 'postgresql://ci_test:' + values['CI_DB_PASSWORD'] + '@' + host + ':5432/ci_test'
password = values['CI_DB_PASSWORD']
commands = f"DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='ci_test') THEN CREATE ROLE ci_test LOGIN PASSWORD '{password}'; END IF; END $$;"
subprocess.run(['docker','exec','-i','iot-exchange-postgres-1','psql','-U','lab_owner','-d','postgres','-v','ON_ERROR_STOP=1'],input=commands.encode(),capture_output=True,check=True)
exists = subprocess.check_output(['docker','exec','iot-exchange-postgres-1','psql','-U','lab_owner','-d','postgres','-Atc',"SELECT 1 FROM pg_database WHERE datname='ci_test'"]).strip()
if not exists:
    subprocess.run(['docker','exec','iot-exchange-postgres-1','createdb','-U','lab_owner','-O','ci_test','ci_test'],check=True,capture_output=True)
subprocess.run(['docker','exec','iot-exchange-postgres-1','psql','-U','lab_owner','-d','postgres','-c','REVOKE ALL ON DATABASE ci_test FROM PUBLIC; GRANT CONNECT ON DATABASE ci_test TO ci_test;'],capture_output=True,check=True)
p.write_text(''.join(key + '=' + value + '\n' for key,value in values.items()))
p.chmod(0o600)
print('Dedicated CI test database ready; no deployment credentials configured')
