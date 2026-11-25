"""Auditoria: grava as ações dos usuários e as devolve ao próprio usuário.

Gravar roda em background (BackgroundTasks nas rotas, ou no worker): se o DynamoDB falhar
(ex.: credenciais do Learner Lab vencidas), a requisição não é afetada; só o log não grava.
"""

import logging
from datetime import datetime
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from app.repositories import audit_repository

logger = logging.getLogger(__name__)


async def log_user_action(
    user_id: int,
    action: str,
    resource_id: int | str | None = None,
    ip_address: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """
    Serviço central de auditoria.
    Prepara e higieniza os dados antes de enviá-los para armazenamento.
    """
    if details:
        safe_details = details.copy()

        sensitive_keys = ["password", "password_hash", "token", "secret"]

        for key in sensitive_keys:
            if key in safe_details:
                safe_details[key] = "*** MASCARADO ***"

        details = safe_details

    try:
        await audit_repository.insert_log(
            user_id=user_id,
            action=action,
            resource_id=resource_id,
            ip_address=ip_address,
            details=details,
        )
    except (BotoCoreError, ClientError) as exc:
        # Roda em background: sem isto, cada falha vira um traceback "Exception in ASGI
        # application" no log da API, que parece queda mas não é. Uma linha basta; a causa
        # comum é credencial do Learner Lab vencida ou ausente.
        logger.warning("Auditoria não gravada (%s): %s", action, exc)


async def list_user_actions(user_id: int, limit: int) -> list[dict[str, Any]]:
    """Últimas ações do usuário, no formato da resposta da API (`AuditLogRead`)."""
    items = await audit_repository.list_logs(user_id, limit)
    return [
        {
            "action": item["action"],
            # A sort key é "<ISO>#<sufixo>"; o sufixo só existe para a chave não colidir.
            "timestamp": datetime.fromisoformat(item["timestamp"].split("#", 1)[0]),
            "resource_id": item.get("resource_id"),
            "ip_address": item.get("ip_address"),
            "details": item.get("details"),
        }
        for item in items
    ]
