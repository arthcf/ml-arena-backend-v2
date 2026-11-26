"""Teste rápido do RabbitMQ usando a topologia e a publicação reais de `app/services/messaging.py`.

Precisa do RabbitMQ no ar (`docker compose up -d rabbitmq`) e o worker parado (senão ele
consome as mensagens antes do script). Do host, com a porta AMQP publicada pelo compose.dev.yaml:

    RABBITMQ_HOST=localhost uv run python -m scripts.smoke_queue

Confere três coisas:
1. publicar e consumir (o JSON chega igual);
2. o que o cabeçalho x-delivery-count vale a cada devolução (nack com requeue);
3. reject(requeue=False) manda a mensagem para a DLQ.
"""

import asyncio
import json

import aio_pika

from app.core.config import settings
from app.services import messaging

SMOKE_RUN_ID = -1  # não existe no banco; se o worker pegar, só descarta


async def _get(queue, timeout=5.0):
    """Pega uma mensagem da fila, esperando até `timeout` segundos."""
    for _ in range(int(timeout * 10)):
        message = await queue.get(no_ack=False, fail=False)
        if message is not None:
            return message
        await asyncio.sleep(0.1)
    raise TimeoutError(f"nenhuma mensagem em {queue.name}")


async def main() -> None:
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        _, queue = await messaging.declare_topology(channel)
        dlq = await channel.declare_queue(
            messaging.DEAD_LETTER_QUEUE,
            durable=True,
            arguments={"x-queue-type": "quorum"},
        )

        # 1 e 2: publica, consome e devolve, olhando o cabeçalho a cada entrega.
        await messaging.publish_csv_uploaded(SMOKE_RUN_ID, "smoke/key.csv", "smoke")
        for delivery in range(1, 4):
            message = await _get(queue)
            body = json.loads(message.body)
            assert body["run_id"] == SMOKE_RUN_ID
            print(
                f"entrega {delivery}: x-delivery-count =",
                (message.headers or {}).get("x-delivery-count", "<ausente>"),
            )
            if delivery < 3:
                await message.nack(requeue=True)
            else:
                await message.ack()
        print("publicar/consumir ok; ack tirou a mensagem da fila")

        # 3: reject sem requeue vai para a DLQ.
        await messaging.publish_csv_uploaded(SMOKE_RUN_ID, "smoke/key.csv", "smoke")
        message = await _get(queue)
        await message.reject(requeue=False)
        dead = await _get(dlq)
        print(
            "reject foi para a DLQ:",
            json.loads(dead.body)["run_id"] == SMOKE_RUN_ID,
            "| motivo:",
            dict(dead.headers or {}).get("x-first-death-reason"),
        )
        await dead.ack()

    await messaging.close()


if __name__ == "__main__":
    asyncio.run(main())
