"""所有資料庫測試均使用一次性 schema，避免與運行中的 worker 互相干擾。"""
import os
import uuid
from urllib.parse import quote

import pytest
from psycopg import sql

from exchange import db


@pytest.fixture(scope='session', autouse=True)
def isolated_database():
    original = os.environ['DATABASE_URL']
    name = 'pytest_' + uuid.uuid4().hex
    with db.connect() as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(name)))
    separator = '&' if '?' in original else '?'
    os.environ['DATABASE_URL'] = original + separator + 'options=' + quote('-csearch_path=' + name)
    db.init()
    yield
    os.environ['DATABASE_URL'] = original
    with db.connect() as conn:
        conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(name)))
