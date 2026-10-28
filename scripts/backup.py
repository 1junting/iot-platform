"""建立各服務快照並還原至一次性資料庫驗證，不覆蓋運行資料庫。"""
import json
import subprocess
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIVES = ROOT / 'reports/private/backups'
ARCHIVES.mkdir(parents=True, exist_ok=True)
CONTAINER = 'iot-exchange-postgres-1'
report = {}


def run(*args, data=None):
    return subprocess.run(['docker', 'exec', '-i', CONTAINER, *args],
                          input=data, capture_output=True, check=True).stdout


for service in ('exchange', 'collector', 'auth', 'devices', 'analytics', 'edge1', 'edge2', 'edge3'):
    archive = run('pg_dump', '-U', 'lab_owner', '-Fc', service)
    path = ARCHIVES / (service + '-' + uuid.uuid4().hex[:8] + '.dump')
    path.write_bytes(archive)
    path.chmod(0o600)
    temporary = 'verify_' + uuid.uuid4().hex[:12]
    run('createdb', '-U', 'lab_owner', temporary)
    try:
        run('pg_restore', '-U', 'lab_owner', '--no-owner', '--no-privileges', '-d', temporary, data=archive)
        counts = run('psql', '-U', 'lab_owner', '-d', temporary, '-Atc',
                     "SELECT json_build_object('messages',(SELECT count(*) FROM messages),'receipts',(SELECT count(*) FROM receipts),'deliveries',(SELECT count(*) FROM deliveries));")
        report[service] = {'restored': True, 'archive_bytes': len(archive), **json.loads(counts)}
    finally:
        run('dropdb', '-U', 'lab_owner', temporary)
(ROOT / 'reports/backup-verification.json').write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
