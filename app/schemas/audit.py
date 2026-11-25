"""Formato JSON de um log de auditoria na resposta da API (`GET /api/v1/audit`)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AuditLogRead(BaseModel):
    """Uma ação registrada no DynamoDB."""

    action: str
    timestamp: datetime
    resource_id: str | None = None
    ip_address: str | None = None
    details: dict[str, Any] | None = None
