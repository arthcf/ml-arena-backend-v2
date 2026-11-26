"""Testa as decisões do worker sobre cada mensagem (ack, nack para tentar de novo, reject para a DLQ).

Sem banco nem RabbitMQ: `process_run` e `_mark_failed` são trocados por funções falsas, e a
mensagem falsa só anota a resposta que recebeu.
"""

import asyncio
import json

import pytest

from worker import consumer


class FakeMessage:
    """Imita a mensagem do aio-pika: corpo, cabeçalhos e as três respostas possíveis."""

    def __init__(self, body, delivery_count=None):
        self.body = body.encode() if isinstance(body, str) else body
        # Como o RabbitMQ: na 1ª entrega o cabeçalho x-delivery-count não vem.
        self.headers = (
            {} if delivery_count is None else {"x-delivery-count": delivery_count}
        )
        self.responses = []

    async def ack(self):
        self.responses.append("ack")

    async def nack(self, requeue=True):
        self.responses.append(f"nack(requeue={requeue})")

    async def reject(self, requeue=False):
        self.responses.append(f"reject(requeue={requeue})")


def _run_message(run_id=1, attempt=1):
    """Mensagem válida na tentativa `attempt` (1 = primeira entrega)."""
    count = None if attempt == 1 else attempt - 1
    return FakeMessage(json.dumps({"run_id": run_id}), delivery_count=count)


@pytest.fixture
def calls(monkeypatch):
    """Registra quem foi marcado como failed; process_run levanta o erro configurado."""
    state = {"failed": [], "error": None}

    async def fake_process_run(run_id):
        if state["error"]:
            raise state["error"]

    async def fake_mark_failed(run_id, message):
        state["failed"].append(run_id)

    monkeypatch.setattr(consumer, "process_run", fake_process_run)
    monkeypatch.setattr(consumer, "_mark_failed", fake_mark_failed)
    monkeypatch.setattr(consumer, "RETRY_DELAY_S", 0)  # sem espera real nos testes
    return state


@pytest.mark.parametrize(
    "body",
    [
        "não é json",
        json.dumps({"Type": "Notification", "Message": "{}"}),  # sem run_id
        json.dumps({"run_id": "abc"}),
    ],
)
def test_mensagem_invalida_vai_para_a_dlq(calls, body):
    message = FakeMessage(body)
    asyncio.run(consumer.handle_message(message))
    assert message.responses == ["reject(requeue=False)"]
    assert calls["failed"] == []


def test_tentativa_vem_do_x_delivery_count():
    assert consumer._attempt(FakeMessage("{}")) == 1  # 1ª entrega: sem cabeçalho
    assert consumer._attempt(FakeMessage("{}", delivery_count=2)) == 3


def test_sucesso_da_ack(calls):
    message = _run_message()
    asyncio.run(consumer.handle_message(message))
    assert message.responses == ["ack"]


def test_erro_de_dado_marca_failed_na_hora(calls):
    calls["error"] = ValueError("CSV ruim")
    message = _run_message()
    asyncio.run(consumer.handle_message(message))
    assert calls["failed"] == [1]
    assert message.responses == ["ack"]


def test_erro_inesperado_volta_para_a_fila(calls):
    calls["error"] = ConnectionError("Redis fora do ar")
    message = _run_message(attempt=1)
    asyncio.run(consumer.handle_message(message))
    assert message.responses == ["nack(requeue=True)"]
    assert calls["failed"] == []


def test_erro_inesperado_na_ultima_tentativa_marca_failed(calls):
    calls["error"] = ConnectionError("Redis fora do ar")
    message = _run_message(attempt=consumer.MAX_ATTEMPTS)
    asyncio.run(consumer.handle_message(message))
    assert calls["failed"] == [1]
    assert message.responses == ["ack"]


def test_failed_e_regravado_ate_o_banco_voltar(monkeypatch):
    """Banco fora: o worker insiste em vez de deixar a mensagem ir para a DLQ."""
    tries = []

    async def flaky_mark_failed(run_id, message):
        tries.append(run_id)
        if len(tries) < 3:
            raise ConnectionError("banco fora")

    monkeypatch.setattr(consumer, "_mark_failed", flaky_mark_failed)
    monkeypatch.setattr(consumer, "RETRY_DELAY_S", 0)
    assert asyncio.run(consumer._mark_failed_until_saved(5, "falhou")) is True
    assert tries == [5, 5, 5]


def _failing_mark_failed(monkeypatch, exc):
    """_mark_failed que sempre falha com `exc`; devolve a lista de chamadas."""
    tries = []

    async def broken(run_id, message):
        tries.append(run_id)
        raise exc

    monkeypatch.setattr(consumer, "_mark_failed", broken)
    monkeypatch.setattr(consumer, "RETRY_DELAY_S", 0)
    return tries


def test_erro_que_nao_passa_sozinho_desiste_na_hora(monkeypatch):
    """Bug ou constraint: insistir prenderia o worker para sempre."""
    tries = _failing_mark_failed(monkeypatch, RuntimeError("bug"))
    assert asyncio.run(consumer._mark_failed_until_saved(5, "falhou")) is False
    assert tries == [5]


def test_banco_fora_desiste_depois_do_prazo(monkeypatch):
    tries = _failing_mark_failed(monkeypatch, ConnectionRefusedError())
    monkeypatch.setattr(consumer, "MARK_FAILED_TIMEOUT_S", 0)
    assert asyncio.run(consumer._mark_failed_until_saved(5, "falhou")) is False
    assert tries == [5]


@pytest.mark.parametrize("attempt", [1, consumer.MAX_ATTEMPTS])
def test_sem_gravar_o_failed_a_mensagem_vai_para_a_dlq(monkeypatch, attempt):
    """Erro de dado (1ª tentativa) ou esgotou as tentativas: sem o failed, reject → DLQ."""
    _failing_mark_failed(monkeypatch, RuntimeError("bug"))

    async def process(run_id):
        raise ValueError("csv ruim") if attempt == 1 else ConnectionError("minio")

    monkeypatch.setattr(consumer, "process_run", process)
    message = _run_message(attempt=attempt)
    asyncio.run(consumer.handle_message(message))
    assert message.responses == ["reject(requeue=False)"]
