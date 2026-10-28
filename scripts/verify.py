"""執行可重複的 API、資料庫與真實協定驗收。"""
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
report = root / 'reports'
report.mkdir(exist_ok=True)
dc = ['docker','compose','-f','compose.yaml','-f','compose.ops.yaml','--profile','test']
subprocess.run(dc + ['build','tests'],cwd=root,check=True)
subprocess.run(dc + ['up','-d','fixtures'],cwd=root,check=True)
for output, command in [
    ('unit-tests.txt',['python','-m','pytest','-q','tests']),
    ('protocol-run.txt',['python','scripts/acceptance.py']),
    ('edge-run.txt',['python','scripts/edge_acceptance.py']),
    ('load-run.txt',['python','scripts/load.py'])]:
    with (report / output).open('w') as stream:
        subprocess.run(dc + ['run','--rm','--no-deps','tests'] + command,
                       cwd=root,stdout=stream,stderr=subprocess.STDOUT,check=True)
    print('PASS: ' + output)
