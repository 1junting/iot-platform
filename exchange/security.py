"""JWT 與服務身份檢查；授權在服務端執行。"""
import os
import secrets
import time

import jwt
from fastapi import Header, HTTPException


def require(scope):
    def authorize(authorization: str = Header(default=''),
                  x_service_token: str = Header(default='')):
        service_credential = x_service_token or authorization.removeprefix('Bearer ')
        if service_credential and secrets.compare_digest(
                service_credential, os.environ['SERVICE_TOKEN']):
            return {'sub': 'service', 'scopes': ['*']}
        try:
            token = authorization.removeprefix('Bearer ')
            claims = jwt.decode(token, os.environ['JWT_SECRET'], algorithms=['HS256'],
                                audience='iot-exchange', issuer='iot-auth',
                                options={'require': ['exp', 'iat', 'sub', 'iss', 'aud']})
            if scope not in claims.get('scopes', []) and '*' not in claims.get('scopes', []):
                raise HTTPException(403, 'Insufficient permission')
            return claims
        except jwt.PyJWTError:
            raise HTTPException(401, 'Authentication required') from None
    return authorize


def issue(subject, scopes):
    return jwt.encode({'sub': subject, 'scopes': scopes, 'iss': 'iot-auth',
                       'aud': 'iot-exchange', 'iat': int(time.time()),
                       'exp': int(time.time()) + 1800},
                      os.environ['JWT_SECRET'], algorithm='HS256')
