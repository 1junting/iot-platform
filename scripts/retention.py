"""預設只預覽可清理筆數；明確加 --apply 才刪除已完成的歷史。"""
import argparse
from exchange import db

parser = argparse.ArgumentParser()
parser.add_argument('--days', type=int, default=30)
parser.add_argument('--apply', action='store_true')
args = parser.parse_args()
if args.days < 1:
    parser.error('days must be positive')
with db.connect() as conn:
    candidates = conn.execute("SELECT count(*) AS n FROM messages m WHERE received_at < now()-make_interval(days=>%s) AND "
                             "NOT EXISTS(SELECT 1 FROM deliveries d WHERE d.source_id=m.source_id AND d.message_id=m.id AND state IN ('PENDING','RETRY','SENDING','DEAD'))", (args.days,)).fetchone()['n']
    print({'eligible_messages': candidates, 'apply': args.apply})
    if args.apply:
        conn.execute("CREATE TEMP TABLE cleanup ON COMMIT DROP AS SELECT source_id,id FROM messages m WHERE received_at < now()-make_interval(days=>%s) AND "
                     "NOT EXISTS(SELECT 1 FROM deliveries d WHERE d.source_id=m.source_id AND d.message_id=m.id AND state IN ('PENDING','RETRY','SENDING','DEAD')) FOR UPDATE", (args.days,))
        conn.execute('DELETE FROM deliveries d USING cleanup c WHERE d.source_id=c.source_id AND d.message_id=c.id')
        conn.execute('DELETE FROM messages m USING cleanup c WHERE m.source_id=c.source_id AND m.id=c.id')
