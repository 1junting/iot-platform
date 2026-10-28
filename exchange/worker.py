"""獨立投遞程序，可與 API 分別啟停及水平擴展。"""
import signal
from . import db, runtime, tracing

if __name__ == '__main__':
    db.init()
    tracing.start()
    from prometheus_client import start_http_server
    start_http_server(8071)
    signal.signal(signal.SIGTERM, lambda *_: runtime.STOP.set())
    runtime.worker()
