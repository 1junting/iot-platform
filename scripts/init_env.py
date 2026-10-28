"""建立獨立隨機秘密，不覆蓋既有設定、不輸出秘密。"""
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / '.env'
if not path.exists():
    values = {key: secrets.token_hex(24) for key in [
        'POSTGRES_PASSWORD', 'SERVICE_TOKEN', 'JWT_SECRET', 'ADMIN_PASSWORD',
        'RABBITMQ_PASSWORD', 'GRAFANA_PASSWORD', 'JENKINS_PASSWORD',
        *[name.upper() + '_DB_PASSWORD' for name in
          ('exchange', 'auth', 'devices', 'analytics', 'collector', 'edge1', 'edge2', 'edge3')]]}
    path.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
    path.chmod(0o600)
    print('Created local .env with independent secrets')
else:
    print('Preserved existing .env')
