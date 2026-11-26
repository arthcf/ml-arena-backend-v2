"""Worker: consome a fila do RabbitMQ e gera os plots de cada CSV enviado.

Roda como processo separado (`python -m worker.consumer`), fora da API. Para cada mensagem:
baixa o CSV do MinIO, gera os dois PNGs (`worker/processing.py`), sobe os PNGs, e atualiza
run e artifacts no banco. Erro de dado marca como failed na hora; qualquer outro erro
volta pra fila até a 3ª tentativa, e nenhum erro derruba o processo. Os logs ETAPA 1/2/3
mostram onde cada run está.

Diferença para o SQS do T1: lá, "não apagar" já fazia a mensagem voltar depois do visibility
timeout. Aqui o worker responde explicitamente: `ack` (tira da fila), `nack(requeue=True)`
(devolve na hora) ou `reject(requeue=False)` (manda para a DLQ). Como o `nack` devolve na
hora, a espera entre tentativas é feita pelo próprio worker.
"""

import asyncio
import json
import logging
import time

import aio_pika
from aio_pika.abc import AbstractIncomingMessage

from app.core.cache import invalidate_user_cache
from app.core.config import settings
from app.db.models import Artifact, Run
from app.db.session import session_scope
from app.repositories import artifact_repository, run_repository
from app.services import audit_service, storage
from app.services.messaging import MAX_DELIVERIES, QUEUE, declare_topology
from worker.processing import make_plots, read_pairs

logger = logging.getLogger("worker")

CSV_TYPE = "csv_measured_predicted"
# Caminho de cada plot no bucket, por tipo de artifact (convenção do spec, seção 6).
PLOT_FILES = {
    "plot_measured_predicted": "plots/measured_predicted.png",
    "plot_residuals": "plots/residuals.png",
}
# Na última entrega que a fila permite, o worker decide o fim (marca failed e dá ack), então
# a mensagem nunca precisa cair na DLQ por falha transitória.
MAX_ATTEMPTS = MAX_DELIVERIES
# Espera antes de devolver a mensagem: RETRY_DELAY_S × tentativa (5 s, depois 10 s). Sem ela
# as 3 tentativas acabariam em milissegundos, antes de o MinIO ou o banco voltarem. Com
# prefetch 1, bloquear este worker durante a espera é aceitável.
RETRY_DELAY_S = 5
# Erro de dado: o CSV não serve. Não adianta tentar de novo. Qualquer outro erro
# (MinIO, banco, Redis ou inesperado) volta pra fila até MAX_ATTEMPTS.
PERMANENT_ERRORS = (ValueError, KeyError)


async def _ensure_plot_artifact(
    session, run_id: int, plot_type: str, key: str
) -> Artifact:
    """Garante o artifact de um plot, marcando como processing. Cria se não existe, ou reaproveita a linha de tentativa anterior."""
    artifact = await artifact_repository.get_artifact_by_type(
        session, run_id, plot_type
    )
    if artifact is None:
        return await artifact_repository.create_artifact(
            session, run_id, plot_type, key, status="processing"
        )
    return await artifact_repository.update_artifact(
        session, artifact, status="processing", error_message=None
    )


async def _audit_worker(user_id: int, action: str, run_id: int, details: dict) -> None:
    """Registra uma ação de auditoria do worker, com detalhes do run."""
    try:
        await audit_service.log_user_action(
            user_id=user_id,
            action=action,
            resource_id=run_id,
            details=details,
        )
    except Exception as e:
        logger.exception(
            "Run %s: falha ao gravar auditoria (%s): %s", run_id, action, e
        )


async def process_run(run_id: int) -> None:
    """O trabalho de verdade: CSV do MinIO → plots → MinIO → banco."""
    async with session_scope() as session:
        run = await session.get(Run, run_id)
        if run is None:
            logger.info("Run %s não existe mais; descartando a mensagem.", run_id)
            return
        if run.status == "done":
            return  # mensagem repetida (a fila entrega pelo menos uma vez): já foi feito

        csv_artifact = await artifact_repository.get_artifact_by_type(
            session, run_id, CSV_TYPE
        )
        if csv_artifact is None or csv_artifact.status != "done":
            raise ValueError("CSV ainda não está disponível para este run.")

        started = time.monotonic()
        await run_repository.update_run(
            session, run, {"status": "processing", "error_message": None}
        )

        plot_artifacts = {}
        for plot_type, filename in PLOT_FILES.items():
            key = f"runs/{run_id}/{filename}"
            plot_artifacts[plot_type] = await _ensure_plot_artifact(
                session, run_id, plot_type, key
            )

        logger.info("ETAPA 2 iniciada: run %s, baixando o CSV.", run_id)
        data = await storage.download_object(csv_artifact.s3_key)
        measured, predicted = read_pairs(data)
        images = await asyncio.to_thread(make_plots, measured, predicted)

        for plot_type, filename in PLOT_FILES.items():
            image = images[plot_type]
            await storage.upload_bytes(f"runs/{run_id}/{filename}", image, "image/png")
            await artifact_repository.update_artifact(
                session, plot_artifacts[plot_type], status="done", size_bytes=len(image)
            )

        duration = time.monotonic() - started
        await run_repository.update_run(
            session,
            run,
            {"status": "done", "processing_duration_s": duration},
        )
    await _audit_worker(
        user_id=run.user_id,
        action="PROCESS_RUN_DONE",
        run_id=run_id,
        details={"processing_duration_s": duration, "plots": list(PLOT_FILES.keys())},
    )
    await invalidate_user_cache(user_id=run.user_id)
    logger.info("ETAPA 3 concluída: run %s processado.", run_id)


async def _mark_failed(run_id: int, message: str) -> None:
    """Marca o run e seus artifacts como failed, com o motivo. Usado quando não há mais o que tentar.

    O CSV também vira failed, mesmo já estando no MinIO: é o que libera o reenvio pela API
    (`run_service._start_csv_upload` só aceita um novo CSV se o anterior estiver failed).
    """
    async with session_scope() as session:
        run = await session.get(Run, run_id)
        # done: o processamento terminou e só um passo posterior falhou; não há o que desfazer.
        if run is None or run.status == "done":
            return
        for artifact in await artifact_repository.list_artifacts_for_run(
            session, run_id
        ):
            if artifact.status != "failed":
                await artifact_repository.update_artifact(
                    session, artifact, status="failed", error_message=message
                )
        await run_repository.update_run(
            session, run, {"status": "failed", "error_message": message}
        )
    await _audit_worker(
        user_id=run.user_id,
        action="PROCESS_RUN_FAILED",
        run_id=run_id,
        details={"error_message": message},
    )
    try:
        await invalidate_user_cache(user_id=run.user_id)
    except Exception:
        # O failed já está gravado; sem o Redis, o cache antigo só expira pelo TTL.
        logger.exception("Run %s: não foi possível invalidar o cache.", run_id)


async def _mark_failed_until_saved(run_id: int, message: str) -> None:
    """Insiste até conseguir gravar o failed (ex.: o banco está fora).

    Desistir aqui deixaria o run travado: a mensagem estouraria o limite de entregas, iria
    para a DLQ e ninguém atualizaria o banco. Segurar a mensagem (sem ack) é seguro: se o
    worker morrer durante a espera, ela volta para a fila. Com o banco fora, este worker não
    conseguiria processar outra mensagem de qualquer forma.
    """
    while True:
        try:
            await _mark_failed(run_id, message)
            return
        except Exception:
            logger.exception(
                "Run %s: não foi possível gravar o failed; nova tentativa em %ss.",
                run_id,
                RETRY_DELAY_S,
            )
            await asyncio.sleep(RETRY_DELAY_S)


def _attempt(message: AbstractIncomingMessage) -> int:
    """Número desta tentativa (1, 2, 3...).

    Numa fila quorum, o RabbitMQ conta as devoluções em `x-delivery-count`; na 1ª entrega o
    cabeçalho não vem. Por isso tentativa = devoluções anteriores + 1.
    """
    return int((message.headers or {}).get("x-delivery-count", 0)) + 1


async def handle_message(message: AbstractIncomingMessage) -> None:
    """Trata uma mensagem e responde ao broker: ack, nack (tentar de novo) ou reject (DLQ)."""
    try:
        run_id = int(json.loads(message.body)["run_id"])
    except (ValueError, KeyError, TypeError):
        # Sem run_id não há run para marcar como failed, e tentar de novo não muda nada.
        # Vai para a DLQ, onde dá para inspecionar o que chegou (no SQS ela era apagada).
        logger.error("Mensagem sem run_id válido, enviada à DLQ: %r", message.body)
        await message.reject(requeue=False)
        return
    attempts = _attempt(message)
    logger.info("ETAPA 1 recebida: run %s (tentativa %s).", run_id, attempts)

    try:
        await process_run(run_id)
    except PERMANENT_ERRORS as exc:
        # O detalhe técnico vai pro log; o usuário vê só a mensagem geral.
        logger.warning("Run %s: CSV não pôde ser processado: %r", run_id, exc)
        await _mark_failed_until_saved(
            run_id, "Processamento falhou: o CSV não pôde ser lido."
        )
    except Exception:
        # Infraestrutura (MinIO, banco, Redis) ou erro inesperado: vale tentar de novo.
        if attempts < MAX_ATTEMPTS:
            delay = RETRY_DELAY_S * attempts
            logger.exception(
                "Run %s: erro na tentativa %s; volta à fila em %ss.",
                run_id,
                attempts,
                delay,
            )
            # Espera antes de devolver: o nack põe a mensagem de volta na hora, então esperar
            # depois dele não adiantaria (outro worker a pegaria imediatamente).
            await asyncio.sleep(delay)
            await message.nack(requeue=True)
            return  # sem o return, cairia no ack abaixo e a mensagem se perderia
        logger.exception("Run %s: esgotou as %s tentativas.", run_id, MAX_ATTEMPTS)
        await _mark_failed_until_saved(
            run_id, "Falha no processamento. Tente reenviar o CSV."
        )

    await message.ack()


async def _consume(connection: aio_pika.abc.AbstractRobustConnection) -> None:
    """Consome a fila até a conexão cair. Uma mensagem por vez (prefetch 1)."""
    channel = await connection.channel()
    # Prefetch 1: o broker só entrega a próxima depois do ack/nack desta. Com várias réplicas
    # do worker, quem está livre recebe (equivale ao MaxNumberOfMessages=1 do SQS).
    await channel.set_qos(prefetch_count=1)
    _, queue = await declare_topology(channel)
    async with queue.iterator() as messages:
        async for message in messages:
            try:
                await handle_message(message)
            except Exception:
                # Rede de segurança (ex.: falha no próprio ack). Sem resposta, a mensagem ficaria
                # presa a este worker (e, com prefetch 1, ele pararia). Devolve à fila depois
                # de uma espera; o worker segue vivo para as próximas.
                logger.exception("Erro ao tratar mensagem; ela voltará à fila.")
                if not message.processed:
                    await asyncio.sleep(RETRY_DELAY_S)
                    await message.nack(requeue=True)


async def run_forever() -> None:
    """Loop principal: conecta, consome e, se a conexão falhar, espera 5 s e tenta de novo."""
    logger.info("Worker escutando a fila %s", QUEUE)
    while True:
        try:
            # connect_robust reconecta sozinha se o broker reiniciar; este laço cobre a 1ª
            # conexão (ex.: o worker subiu antes do RabbitMQ).
            connection = await aio_pika.connect_robust(settings.rabbitmq_url)
            async with connection:
                await _consume(connection)
        except Exception:
            logger.exception("Falha na conexão com o RabbitMQ; tentando de novo em 5s.")
            await asyncio.sleep(5)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(run_forever())
