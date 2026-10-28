"""服務各自持有資料庫；以交易、排他領取與租約保存投遞工作。"""
import os
import uuid
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

SCHEMA = '''
CREATE TABLE IF NOT EXISTS profiles(
 id text PRIMARY KEY, version integer NOT NULL, rules jsonb NOT NULL);
INSERT INTO profiles VALUES ('identity',1,'[]') ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS routes(
 id text PRIMARY KEY, version integer NOT NULL, config jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS sources(
 id text PRIMARY KEY, version integer NOT NULL, config jsonb NOT NULL,
 last_error text, last_polled timestamptz);
CREATE TABLE IF NOT EXISTS messages(
 source_id text NOT NULL, id text NOT NULL, fingerprint text NOT NULL,
 protocol text NOT NULL, document jsonb NOT NULL,
 received_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(source_id,id));
CREATE TABLE IF NOT EXISTS deliveries(
 id uuid PRIMARY KEY, source_id text NOT NULL, message_id text NOT NULL,
 route_id text NOT NULL, route_version integer NOT NULL, snapshot jsonb NOT NULL,
 state text NOT NULL DEFAULT 'PENDING', attempts integer NOT NULL DEFAULT 0,
 available_at timestamptz NOT NULL DEFAULT now(), lease_until timestamptz,
 lease_token uuid, error text, acknowledged_at timestamptz,
 FOREIGN KEY(source_id,message_id) REFERENCES messages(source_id,id));
CREATE INDEX IF NOT EXISTS delivery_due ON deliveries(state,available_at);
CREATE TABLE IF NOT EXISTS quarantine(
 id bigserial PRIMARY KEY, protocol text NOT NULL, reason text NOT NULL,
 raw_base64 text NOT NULL, received_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS audit(
 id bigserial PRIMARY KEY, action text NOT NULL, subject text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS receipts(
 delivery_id text PRIMARY KEY, document jsonb NOT NULL, transport text NOT NULL,
 fingerprint text NOT NULL, received_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS devices(id text PRIMARY KEY, document jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS alerts(id uuid PRIMARY KEY, document jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS feedback(id uuid PRIMARY KEY, document jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS registrations(
 id text PRIMARY KEY, endpoint text UNIQUE NOT NULL, address text NOT NULL,
 links text NOT NULL, expires_at timestamptz NOT NULL);
'''


@contextmanager
def connect():
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row,
                         connect_timeout=4) as conn:
        yield conn


def init():
    with connect() as conn:
        conn.execute(SCHEMA)


def config(kind, key, document, expected_version):
    if kind not in {'profiles', 'routes', 'sources'}:
        raise ValueError('Unknown configuration kind')
    column = 'rules' if kind == 'profiles' else 'config'
    with connect() as conn:
        # advisory lock 讓不存在的設定也能安全進行版本比較。
        conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                     (kind + ':' + key,))
        old = conn.execute(f'SELECT version FROM {kind} WHERE id=%s', (key,)).fetchone()
        actual = old['version'] if old else 0
        if actual != expected_version:
            raise ValueError('Configuration version conflict')
        version = actual + 1
        conn.execute(f'INSERT INTO {kind}(id,version,{column}) VALUES (%s,%s,%s) '
                     f'ON CONFLICT(id) DO UPDATE SET version=excluded.version,{column}=excluded.{column}',
                     (key, version, Jsonb(document)))
        conn.execute('INSERT INTO audit(action,subject) VALUES (%s,%s)',
                     ('configure_' + kind, key))
    return version


def save(document, digest, jobs):
    with connect() as conn:
        # 容量限制與新收件在同一交易；重送已收件不受容量限制。
        conn.execute('SELECT pg_advisory_xact_lock(824512)')
        previous = conn.execute('SELECT fingerprint FROM messages WHERE source_id=%s AND id=%s',
                                (document['source_id'], document['message_id'])).fetchone()
        if previous:
            if previous['fingerprint'] != digest:
                raise ValueError('message_id conflict with different content')
            return False
        count = conn.execute("SELECT count(*) AS n FROM deliveries WHERE state IN ('PENDING','RETRY','SENDING')").fetchone()['n']
        if count + len(jobs) > int(os.getenv('MAX_PENDING', '100000')):
            raise OverflowError('Pending capacity exhausted; retry later')
        conn.execute('INSERT INTO messages(source_id,id,fingerprint,protocol,document) VALUES (%s,%s,%s,%s,%s)',
                     (document['source_id'], document['message_id'], digest,
                      document['source'], Jsonb(document)))
        for job in jobs:
            conn.execute('INSERT INTO deliveries(id,source_id,message_id,route_id,route_version,snapshot) VALUES (%s,%s,%s,%s,%s,%s)',
                         (job['id'], document['source_id'], document['message_id'],
                          job['route_id'], job['route_version'], Jsonb(job['snapshot'])))
        return True


def claim():
    token = str(uuid.uuid4())
    with connect() as conn:
        conn.execute("UPDATE deliveries SET state='DEAD',lease_until=NULL,lease_token=NULL,error='Attempt limit after interruption' "
                     "WHERE attempts>=6 AND (state IN ('PENDING','RETRY') OR (state='SENDING' AND lease_until<now()))")
        return conn.execute(
            "WITH candidate AS (SELECT id FROM deliveries WHERE attempts<6 AND "
            "((state IN ('PENDING','RETRY') AND available_at<=now()) OR "
            "(state='SENDING' AND lease_until<now())) ORDER BY available_at,id "
            "FOR UPDATE SKIP LOCKED LIMIT 1) UPDATE deliveries d SET state='SENDING',"
            "attempts=attempts+1,lease_until=now()+make_interval(secs=>%s),lease_token=%s "
            "FROM candidate c WHERE d.id=c.id RETURNING d.*",
            (int(os.getenv('LEASE_SECONDS', '30')), token)).fetchone()


def finish(row, success, broker=False, error=None):
    state = ('BROKER_ACCEPTED' if broker else 'DESTINATION_RECEIVED') if success else ('DEAD' if row['attempts'] >= 6 else 'RETRY')
    with connect() as conn:
        return conn.execute(
            "UPDATE deliveries SET state=%s,error=%s,lease_until=NULL,lease_token=NULL,"
            "available_at=now()+make_interval(secs=>%s),acknowledged_at=CASE WHEN %s THEN now() ELSE NULL END "
            "WHERE id=%s AND lease_token=%s AND state='SENDING' AND lease_until>now() RETURNING id",
            (state, error, min(2 ** row['attempts'], 60), success,
             row['id'], row['lease_token'])).fetchone()
