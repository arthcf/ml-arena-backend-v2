"""Testa a consulta da auditoria (`GET /api/v1/audit`) sem AWS.

A tabela do DynamoDB é trocada por uma falsa que guarda os parâmetros da Query, e o usuário
logado vem de um override da dependência `get_current_user` (sem banco nem JWT).
"""

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest
from botocore.exceptions import NoCredentialsError
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.main import app
from app.repositories import audit_repository
from app.services import audit_service


class FakeTable:
    def __init__(self, items):
        self.items = items
        self.query_kwargs = None

    async def query(self, **kwargs):
        self.query_kwargs = kwargs
        return {"Items": self.items}


class FakeDynamo:
    def __init__(self, table):
        self.table = table

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def Table(self, name):  # noqa: N802 (nome do boto3)
        return self.table


@pytest.fixture
def table(monkeypatch):
    table = FakeTable(
        [
            {
                "user_id": "7",
                "timestamp": "2026-11-25T12:00:00+00:00#abcd1234",
                "action": "PROCESS_RUN_DONE",
                "resource_id": "3",
                "details": {"processing_duration_s": Decimal("1.5"), "n": Decimal("2")},
            }
        ]
    )
    session = SimpleNamespace(resource=lambda name: FakeDynamo(table))
    monkeypatch.setattr(audit_repository, "aws_session", session)
    return table


@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=7)
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_list_logs_consulta_so_o_usuario_do_mais_novo_para_o_mais_antigo(table):
    items = asyncio.run(audit_repository.list_logs(7, limit=5))
    kwargs = table.query_kwargs
    assert kwargs["ScanIndexForward"] is False
    assert kwargs["Limit"] == 5
    # A condição é Key("user_id").eq("7"): confere campo e valor.
    assert kwargs["KeyConditionExpression"].get_expression()["values"][1] == "7"
    # Decimal vira número (int quando é inteiro), senão o JSON quebra.
    assert items[0]["details"] == {"processing_duration_s": 1.5, "n": 2}


def test_rota_devolve_as_acoes_sem_o_sufixo_da_chave(table, client):
    response = client.get("/api/v1/audit?limit=5")
    assert response.status_code == 200
    [log] = response.json()
    assert log["action"] == "PROCESS_RUN_DONE"
    assert log["timestamp"] == "2026-11-25T12:00:00Z"
    assert log["resource_id"] == "3"


def test_rota_responde_503_se_o_dynamodb_falhar(monkeypatch, client):
    async def broken(user_id, limit):
        raise NoCredentialsError()

    monkeypatch.setattr(audit_repository, "list_logs", broken)
    response = client.get("/api/v1/audit")
    assert response.status_code == 503


def test_rota_exige_login():
    response = TestClient(app).get("/api/v1/audit")
    assert response.status_code == 401


def test_gravar_sem_dynamodb_nao_levanta(monkeypatch, caplog):
    async def broken(**kwargs):
        raise NoCredentialsError()

    monkeypatch.setattr(audit_repository, "insert_log", broken)
    asyncio.run(audit_service.log_user_action(user_id=1, action="LOGIN"))
    assert "Auditoria não gravada (LOGIN)" in caplog.text
