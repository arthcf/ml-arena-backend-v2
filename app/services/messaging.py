"""Mensageria com o RabbitMQ: avisa o worker que um CSV novo está no MinIO.

O aviso é só um bilhete (run_id, s3_key, type, timestamp). O conteúdo do CSV fica no MinIO.
Chamado por `run_service.py` depois do upload; o worker (`worker/consumer.py`) consome.

Topologia (equivalente ao SNS → SQS → DLQ do T1):

    exchange ml-arena.events (direct) --csv.uploaded--> fila csv-processing (quorum)
                                                             | rejeitada ou estourou o limite
                                                             v
    exchange ml-arena.dlx (fanout) -------------------> fila csv-processing.dlq

A API e o worker declaram a topologia pela mesma função (`declare_topology`): quem subir
primeiro cria, o outro só confirma. Se os dois declarassem a mesma fila com argumentos
diferentes, o RabbitMQ recusaria com PRECONDITION_FAILED.
"""

import asyncio
import json
from datetime import UTC, datetime

import aio_pika
from aio_pika.abc import (
    AbstractChannel,
    AbstractExchange,
    AbstractQueue,
    AbstractRobustConnection,
)

from app.core.config import settings

EXCHANGE = "ml-arena.events"
ROUTING_KEY = "csv.uploaded"
QUEUE = "csv-processing"
DEAD_LETTER_EXCHANGE = "ml-arena.dlx"
DEAD_LETTER_QUEUE = "csv-processing.dlq"
# Máximo de entregas de uma mensagem. O worker decide o fim na última (marca failed e dá ack);
# o limite da fila é a rede de segurança para quando o worker morre no meio do processamento
# várias vezes seguidas: a mensagem vai para a DLQ em vez de voltar para sempre.
MAX_DELIVERIES = 3
# O x-delivery-limit conta *devoluções*, e a mensagem só vai para a DLQ quando as devoluções
# passam do limite: com limite N há N + 1 entregas (medido no RabbitMQ 4.2: limite 3 deu 4
# entregas). Por isso o limite é MAX_DELIVERIES - 1.
DELIVERY_LIMIT = MAX_DELIVERIES - 1
PUBLISH_TIMEOUT_S = 10


class MessagingError(Exception):
    """Falha ao publicar no RabbitMQ (broker fora do ar, timeout, mensagem sem rota)."""


async def declare_topology(
    channel: AbstractChannel,
) -> tuple[AbstractExchange, AbstractQueue]:
    """Cria (ou confirma) exchanges, filas e ligações. Devolve o exchange e a fila principais."""
    dead_letter_exchange = await channel.declare_exchange(
        DEAD_LETTER_EXCHANGE, aio_pika.ExchangeType.FANOUT, durable=True
    )
    dead_letter_queue = await channel.declare_queue(
        DEAD_LETTER_QUEUE, durable=True, arguments={"x-queue-type": "quorum"}
    )
    await dead_letter_queue.bind(dead_letter_exchange)

    exchange = await channel.declare_exchange(
        EXCHANGE, aio_pika.ExchangeType.DIRECT, durable=True
    )
    queue = await channel.declare_queue(
        QUEUE,
        durable=True,
        arguments={
            # Quorum: replicada e com contador de entregas (x-delivery-count), que o
            # worker usa para contar as tentativas.
            "x-queue-type": "quorum",
            "x-delivery-limit": DELIVERY_LIMIT,
            "x-dead-letter-exchange": DEAD_LETTER_EXCHANGE,
        },
    )
    await queue.bind(exchange, ROUTING_KEY)
    return exchange, queue


# Uma conexão por processo da API, aberta na 1ª publicação e reaproveitada. `connect_robust`
# reconecta sozinha se o broker reiniciar. O lock evita duas conexões em requisições simultâneas.
_connection: AbstractRobustConnection | None = None
_exchange: AbstractExchange | None = None
_lock = asyncio.Lock()


async def _get_exchange() -> AbstractExchange:
    global _connection, _exchange
    async with _lock:
        if _exchange is None:
            if _connection is None:
                _connection = await aio_pika.connect_robust(
                    settings.rabbitmq_url, timeout=PUBLISH_TIMEOUT_S
                )
            # Canal com publisher confirms (padrão do aio-pika): o publish só retorna depois
            # que o broker confirma que recebeu e guardou a mensagem.
            channel = await _connection.channel()
            _exchange, _ = await declare_topology(channel)
        return _exchange


async def publish_csv_uploaded(run_id: int, s3_key: str, type_: str) -> None:
    """Avisa o worker que um CSV novo está no MinIO e precisa virar plots."""
    body = json.dumps(
        {
            "run_id": run_id,
            "s3_key": s3_key,
            "type": type_,
            "timestamp": datetime.now(UTC).isoformat(),
        }
    ).encode()
    message = aio_pika.Message(
        body,
        content_type="application/json",
        # Persistente: sobrevive a um restart do broker (a fila também é durável).
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
    )
    global _exchange
    try:
        exchange = await _get_exchange()
        # mandatory (padrão): sem fila ligada, o broker devolve a mensagem e isto falha.
        await exchange.publish(message, ROUTING_KEY, timeout=PUBLISH_TIMEOUT_S)
    except Exception as exc:
        # Descarta o canal: a próxima publicação abre outro (a conexão se recupera sozinha).
        _exchange = None
        raise MessagingError("Falha ao publicar no RabbitMQ.") from exc


async def close() -> None:
    """Fecha a conexão (chamado quando a API desliga)."""
    global _connection, _exchange
    if _connection is not None:
        await _connection.close()
    _connection = None
    _exchange = None
