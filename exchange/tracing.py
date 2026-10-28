"""以有界背景佇列輸出 Zipkin v2 span；追蹤故障不阻塞資料交易。"""
import os
import queue
import threading
import uuid

import httpx

QUEUE = queue.Queue(maxsize=1000)
STARTED = False


def emit(trace_id, name, timestamp, duration, parent=None):
    span = {'traceId': trace_id, 'id': uuid.uuid4().hex[:16], 'name': name,
            'timestamp': int(timestamp * 1000000), 'duration': max(1, int(duration * 1000000)),
            'localEndpoint': {'serviceName': os.getenv('SERVICE_NAME', 'exchange')}}
    if parent:
        span['parentId'] = parent
    try:
        QUEUE.put_nowait(span)
    except queue.Full:
        pass


def start():
    global STARTED
    if STARTED:
        return
    STARTED = True
    def loop():
        with httpx.Client(timeout=2) as client:
            while True:
                first = QUEUE.get()
                batch = [first]
                while len(batch) < 50:
                    try:
                        batch.append(QUEUE.get_nowait())
                    except queue.Empty:
                        break
                try:
                    client.post(os.getenv('ZIPKIN_URL', 'http://jaeger:9411/api/v2/spans'), json=batch).raise_for_status()
                except Exception:
                    pass
    threading.Thread(target=loop, daemon=True).start()
