"""Teste rápido do MinIO usando as funções reais de `app/services/storage.py`.

Sobe bytes, baixa e compara, gera um link pré-assinado e apaga. Precisa do MinIO no ar
(`docker compose up -d minio minio-init`). Do host, aponte o endpoint para a porta publicada:

    MINIO_ENDPOINT=http://localhost:9000 uv run python -m scripts.smoke_storage

(variável de ambiente tem precedência sobre o `.env`, onde o endpoint é `http://minio:9000`).
Imprime o link: `curl -I "<link>"` deve dar 200 antes do delete; o script já confere isso.
"""

import asyncio
import urllib.error
import urllib.request

from app.services import storage

KEY = "smoke/hello.txt"
DATA = b"ml-arena smoke test\n"


def _status(url: str) -> int:
    try:
        with urllib.request.urlopen(url) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


async def main() -> None:
    await storage.upload_bytes(KEY, DATA, "text/plain")
    print("upload ok")

    assert await storage.download_object(KEY) == DATA
    print("download ok (bytes iguais)")

    url = await storage.presigned_url(KEY)
    print("link:", url)
    assert _status(url) == 200, "link pré-assinado deveria abrir"
    tampered = url.replace("X-Amz-Signature=", "X-Amz-Signature=0", 1)
    assert _status(tampered) == 403, "assinatura adulterada deveria dar 403"
    print("link ok (200) e assinatura adulterada recusada (403)")

    await storage.delete_objects([KEY])
    assert _status(url) == 404, "objeto deveria ter sido apagado"
    print("delete ok (link agora dá 404)")


if __name__ == "__main__":
    asyncio.run(main())
