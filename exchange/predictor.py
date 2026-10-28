"""PROA laboratory controller: real load samples -> trained LSTM -> bounded HPA floor."""
import json
from contextlib import asynccontextmanager
import math
import os
import threading
import time
from collections import deque
from pathlib import Path

import httpx
import numpy as np
from fastapi import FastAPI
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest

from .lstm import LSTM

SAMPLES = deque(maxlen=600)
STATE = {"status": "collecting", "samples": 0, "applied": False, "forecast_rps": None, "test_mae": None, "baseline_mae": None}
FORECAST = Gauge('proa_forecast_rps', 'LSTM forecast requests per second')
REPLICAS = Gauge('proa_recommended_replicas', 'Bounded predictive replica floor')
TRAINED = Gauge('proa_model_trained', 'Whether a real LSTM was trained with observed samples')
STOP = threading.Event()


@asynccontextmanager
async def lifespan(app):
    STOP.clear()
    threading.Thread(target=loop, daemon=True).start()
    yield
    STOP.set()


app = FastAPI(title='PROA LSTM laboratory controller', lifespan=lifespan)


def floor_for(forecast, capacity, minimum=1, maximum=4):
    if not math.isfinite(forecast) or not math.isfinite(capacity) or capacity <= 0 or minimum < 1 or maximum < minimum:
        raise ValueError('Invalid capacity or replica bounds')
    return max(minimum, min(maximum, math.ceil(forecast / capacity)))


def apply_floor(replicas):
    token = Path('/var/run/secrets/kubernetes.io/serviceaccount/token').read_text().strip()
    ca = '/var/run/secrets/kubernetes.io/serviceaccount/ca.crt'
    namespace = Path('/var/run/secrets/kubernetes.io/serviceaccount/namespace').read_text().strip()
    server = 'https://' + os.environ['KUBERNETES_SERVICE_HOST'] + ':' + os.environ['KUBERNETES_SERVICE_PORT']
    with httpx.Client(verify=ca, timeout=5) as client:
        response = client.patch(server + f'/apis/autoscaling/v2/namespaces/{namespace}/horizontalpodautoscalers/hdpcm-exchange',
            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/merge-patch+json'}, json={'spec': {'minReplicas': replicas}})
        response.raise_for_status()


def loop():
    model = None
    scale, trained_at = 1.0, 0
    previous_floor, last_down = 1, time.monotonic()
    while not STOP.is_set():
        try:
            reply = httpx.get(os.environ['PROMETHEUS_URL'] + '/api/v1/query',
                params={'query': os.getenv('METRICS_QUERY', 'sum(rate(exchange_received_total{job="exchange"}[30s])) or vector(0)')}, timeout=4)
            reply.raise_for_status()
            value = float(reply.json()['data']['result'][0]['value'][1])
            if not math.isfinite(value) or value < 0:
                raise ValueError('Invalid metric')
            SAMPLES.append(value)
            STATE['samples'] = len(SAMPLES)
            if len(SAMPLES) >= 64 and time.monotonic() - trained_at > 60:
                data = np.array(SAMPLES)
                split = int(len(data) * .75)
                scale = max(float(data[:split].max()), 1)
                normalized = data / scale
                train = [(normalized[i-8:i], normalized[i]) for i in range(8, split)]
                test = [(normalized[i-8:i], normalized[i]) for i in range(split, len(data))]
                candidate = LSTM()
                candidate.train(train)
                predictions = [candidate.predict(x) for x, _ in test]
                STATE['test_mae'] = float(np.mean([abs(p-y) for p, (_, y) in zip(predictions, test)]) * scale)
                STATE['baseline_mae'] = float(np.mean([abs(x[-1]-y) for x, y in test]) * scale)
                STATE['training_samples'] = split
                STATE['test_samples'] = len(test)
                STATE['dataset'] = 'live Prometheus samples, chronological 75/25 split'
                model, trained_at = candidate, time.monotonic()
                TRAINED.set(1)
                STATE['status'] = 'trained'
            if model:
                forecast = max(0.0, model.predict(np.array(list(SAMPLES)[-8:]) / scale) * scale)
                replicas = floor_for(forecast, float(os.getenv('PROA_CAPACITY_RPS', '20')), minimum=int(os.getenv('PROA_MIN_REPLICAS', '1')), maximum=int(os.getenv('PROA_MAX_REPLICAS', '4')))
                if replicas < previous_floor and time.monotonic() - last_down < 120:
                    replicas = previous_floor
                if replicas != previous_floor:
                    last_down = time.monotonic()
                FORECAST.set(forecast)
                REPLICAS.set(replicas)
                STATE.update(forecast_rps=forecast, recommended_replicas=replicas)
                if os.getenv('PROA_APPLY') == 'true':
                    apply_floor(replicas)
                    STATE['applied'] = True
                previous_floor = replicas
            STATE.pop('error', None)
        except Exception:
            STATE['error'] = 'Metrics or Kubernetes API unavailable'
        STOP.wait(float(os.getenv('PROA_SAMPLE_SECONDS', '2')))


@app.get('/status')
def status():
    return {**STATE, 'note': 'Experimental model; forecast quality and scaling benefit require workload comparison.'}


@app.get('/metrics')
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
