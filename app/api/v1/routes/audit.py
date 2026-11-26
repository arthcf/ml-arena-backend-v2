"""Rota de auditoria: `GET /api/v1/audit`, as últimas ações do usuário logado.

É a parte de *consulta* do DynamoDB (a gravação acontece em background nas outras rotas e no
worker). Cada usuário só vê os próprios logs: o `user_id` vem do token, não da URL. Ler o log
não gera um log novo.
"""

import logging

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_current_user
from app.db.models import User
from app.schemas.audit import AuditLogRead
from app.services import audit_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("", response_model=list[AuditLogRead])
async def list_my_actions(
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
):
    """Últimas `limit` ações do usuário, da mais nova para a mais antiga. 503 se o DynamoDB falhar."""
    try:
        return await audit_service.list_user_actions(current_user.id, limit)
    except (BotoCoreError, ClientError):
        # Causa mais comum: credenciais da AWS ausentes no .env ou região errada.
        logger.exception("Falha ao consultar a auditoria no DynamoDB.")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Auditoria indisponível no momento (DynamoDB inacessível).",
        ) from None
