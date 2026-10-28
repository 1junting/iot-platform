"""增補本機叢集秘密，不修改任何已存在的值。"""
from pathlib import Path
import secrets
p = Path(__file__).resolve().parents[1] / '.env'
if 'K3S_TOKEN=' not in p.read_text():
    with p.open('a') as stream:
        stream.write('K3S_TOKEN=' + secrets.token_hex(32) + '\n')
    p.chmod(0o600)
print('Local cluster secret ready')
