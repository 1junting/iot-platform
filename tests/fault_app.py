"""僅由灰度／熔斷驗收暫時部署的錯誤目的端。"""
from fastapi import FastAPI
from fastapi.responses import JSONResponse
app = FastAPI()


@app.get('/health')
def health():
    return {'status': 'ready'}


@app.get('/fault')
def fault():
    return JSONResponse({'fixture': 'expected failure'}, status_code=500)
