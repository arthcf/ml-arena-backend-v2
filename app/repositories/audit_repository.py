"""Log de auditoria no DynamoDB (o único serviço que continua na AWS no T2).

Tabela com partition key `user_id` (string) e sort key `timestamp` (string ISO + sufixo
aleatório). Gravar: `insert_log`. Ler: `list_logs`, que faz Query na partition key do usuário,
então nunca lê os itens de outra pessoa nem varre a tabela inteira (Scan).
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from boto3.dynamodb.conditions import Key

from app.core.aws import aws_session
from app.core.config import settings


def _from_dynamo(value: Any) -> Any:
    """O contrário de `_to_dynamo`: o DynamoDB devolve todo número como Decimal, que o JSON não aceita."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _from_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_dynamo(v) for v in value]
    return value


def _to_dynamo(value: Any) -> Any:
    """Converte floats para Decimal, porque o DynamoDB não aceita float do Python."""
    if isinstance(value, float):
        # str() evita a imprecisão binária: Decimal(0.1) vira 0.10000000055...
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_dynamo(v) for v in value]
    return value


async def insert_log(
    user_id: int,
    action: str,
    resource_id: int | str | None = None,
    ip_address: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """
    Grava um log de auditoria no DynamoDB de forma assíncrona.
    """

    timestamp = datetime.now(UTC).isoformat()

    item = {
        "user_id": str(user_id),
        "timestamp": f"{timestamp}#{uuid4().hex[:8]}",  # chave composta: timestamp + sufixo aleatório
        "action": action,
    }

    if resource_id is not None:
        item["resource_id"] = str(resource_id)
    if ip_address is not None:
        item["ip_address"] = ip_address
    if details:
        item["details"] = _to_dynamo(details)

    async with aws_session.resource("dynamodb") as dynamodb:
        table = await dynamodb.Table(settings.dynamodb_audit_table)
        await table.put_item(Item=item)


async def list_logs(user_id: int, limit: int) -> list[dict[str, Any]]:
    """Últimos `limit` logs do usuário, do mais novo para o mais antigo.

    Query na partition key com `ScanIndexForward=False`: o DynamoDB ordena pela sort key
    (`timestamp`, ISO 8601 ordena como texto) e devolve de trás para frente.
    """
    async with aws_session.resource("dynamodb") as dynamodb:
        table = await dynamodb.Table(settings.dynamodb_audit_table)
        response = await table.query(
            KeyConditionExpression=Key("user_id").eq(str(user_id)),
            ScanIndexForward=False,
            Limit=limit,
        )
    return [_from_dynamo(item) for item in response.get("Items", [])]
