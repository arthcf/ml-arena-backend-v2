"""Ponto de entrada da aplicação FastAPI.

Rode com `fastapi dev app/main.py` (dev) ou `fastapi run` (produção). Liga o router da API
em /api/v1 e expõe /health, que o healthcheck do compose usa para saber se a API está de pé.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.router import api_router
from app.db.session import engine
from app.services import messaging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Ao desligar a API, fecha a conexão com o RabbitMQ e o pool de conexões com o banco."""
    yield
    await messaging.close()
    await engine.dispose()


app = FastAPI(title="ML Arena", version="0.1.0", lifespan=lifespan)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Health check do compose: só diz que o processo está de pé, sem tocar no banco."""
    return {"status": "ok"}


app.include_router(api_router, prefix="/api/v1")
