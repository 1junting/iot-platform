"""在獨立目的端收件後強制結束，供租約重取與去重驗證。"""
import json
import os
import sys
import uuid

from exchange import db
from exchange.transport import deliver

key = sys.argv[1]
delivery_id = str(uuid.uuid4())
document = {'message_id': key, 'source_id': key, 'device_id': key,
            'source': 'http', 'delivery_id': delivery_id, 'values': {'temperature': {'value': 35, 'unit': 'C'}}}
item = {'id': delivery_id, 'route_id': 'crash-probe', 'route_version': 1,
        'snapshot': {'document': document, 'destination': {'protocol': 'http', 'url': 'http://collector:8050/receipts'}}}
db.save(document, 'crash-probe', [item])
row = db.claim()
assert str(row['id']) == delivery_id
assert deliver(row['snapshot']) is False
os._exit(9)
