"""以真實 PostgreSQL 驗證交易、租約、競爭與快照。"""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from exchange import db


def job():
    key = 'unit-' + uuid.uuid4().hex
    doc = {'source_id': 'unit', 'message_id': key, 'source': 'http'}
    item = {'id': str(uuid.uuid4()), 'route_id': key, 'route_version': 1,
            'snapshot': {'document': doc, 'destination': {'protocol': 'http', 'url': 'http://collector:8050/receipts'}}}
    assert db.save(doc, 'test', [item])
    return doc, item


def claim_specific(item):
    # 避免背景 worker 搶走測試工作；本測試在停止 worker 的測試階段執行。
    rows = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda _: db.claim(), range(8)))
    matches = [r for r in rows if r and str(r['id']) == item['id']]
    assert len(matches) == 1
    return matches[0]


def test_atomic_dedupe_conflict_and_lease():
    doc, item = job()
    assert db.save(doc, 'test', [item]) is False
    with pytest.raises(ValueError, match='conflict'):
        db.save(doc, 'changed', [item])
    row = claim_specific(item)
    with db.connect() as conn:
        conn.execute("UPDATE deliveries SET lease_until=now()-interval '1 second' WHERE id=%s", (item['id'],))
    reclaimed = db.claim()
    assert reclaimed['id'] == row['id']
    assert reclaimed['lease_token'] != row['lease_token']
    assert db.finish(row, True) is None
    assert db.finish(reclaimed, True)


def test_attempt_limit_and_capacity_rollback(monkeypatch):
    doc, item = job()
    with db.connect() as conn:
        conn.execute("UPDATE deliveries SET attempts=6,state='SENDING',lease_until=now()-interval '1 second' WHERE id=%s", (item['id'],))
    db.claim()
    with db.connect() as conn:
        assert conn.execute('SELECT state FROM deliveries WHERE id=%s', (item['id'],)).fetchone()['state'] == 'DEAD'
    monkeypatch.setenv('MAX_PENDING', '0')
    key = uuid.uuid4().hex
    with pytest.raises(OverflowError):
        db.save({**doc, 'message_id': key}, 'new', [item])
    with db.connect() as conn:
        assert conn.execute('SELECT 1 FROM messages WHERE id=%s', (key,)).fetchone() is None


def test_configuration_compare_and_swap():
    key = 'unit-' + uuid.uuid4().hex
    assert db.config('profiles', key, [], 0) == 1
    with pytest.raises(ValueError, match='version conflict'):
        db.config('profiles', key, [], 0)


def test_expired_worker_cannot_finish_without_reclaim():
    _, item = job()
    row = db.claim()
    with db.connect() as conn:
        conn.execute("UPDATE deliveries SET lease_until=now()-interval '1 second' WHERE id=%s", (item['id'],))
    assert db.finish(row, True) is None
    reclaimed = db.claim()
    assert db.finish(reclaimed, True)


def test_receipt_conflict_is_atomic_under_concurrency():
    from exchange.runtime import accept_receipt
    key = str(uuid.uuid4())
    def receive(value):
        try:
            accept_receipt(json.dumps({'delivery_id': key, 'values': {'a': {'value': value, 'unit': ''}}}).encode(), 'http')
            return value
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(receive, [1,2]*8))
    accepted = {value for value in results if value is not None}
    assert len(accepted) == 1
    assert None in results
