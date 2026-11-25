"""Agrega os routers da API v1 sob os prefixos de cada assunto.

Resultado: /api/v1/runs, /api/v1/auth, /api/v1/users e /api/v1/audit. Incluído em `app/main.py`.
"""

from fastapi import APIRouter

from app.api.v1.routes import audit, auth, runs, users

api_router = APIRouter()
api_router.include_router(runs.router, prefix="/runs", tags=["runs"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(audit.router, prefix="/audit", tags=["audit"])
