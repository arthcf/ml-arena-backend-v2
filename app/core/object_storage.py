"""Cliente S3 apontado para o MinIO (no T1 era o S3 da AWS).

O MinIO fala a mesma API do S3, então o `aioboto3` continua servindo: só muda o endereço
(`endpoint_url`) e as credenciais, que são as do MinIO, não as da AWS. Por isso esta sessão
é separada da `aws_session` (`app/core/aws.py`), que agora serve só o DynamoDB.
"""

import aioboto3
from botocore.config import Config

from app.core.config import settings

_session = aioboto3.Session(
    aws_access_key_id=settings.minio_root_user,
    aws_secret_access_key=settings.minio_root_password,
)

_config = Config(
    signature_version="s3v4",
    # Path-style (http://host/bucket/chave): o MinIO não tem DNS por bucket como a AWS
    # (virtual-hosted: http://bucket.host/chave).
    s3={"addressing_style": "path"},
    # O boto3 >= 1.36 manda checksums novos por padrão; servidores S3 compatíveis podem
    # recusá-los. "when_required" volta ao comportamento antigo.
    request_checksum_calculation="when_required",
    response_checksum_validation="when_required",
)


def s3_client(public: bool = False):
    """Cliente S3 do MinIO, para usar com `async with`.

    `public=True` usa a URL que o navegador alcança (ex.: http://localhost:9000). Só serve
    para assinar links: a assinatura inclui o host, então um link assinado para `minio:9000`
    (nome na rede do compose) não abre no navegador, e trocar o host depois invalida a
    assinatura.
    """
    return _session.client(
        "s3",
        endpoint_url=settings.minio_public_url if public else settings.minio_endpoint,
        # Valor qualquer: o MinIO ignora a região, mas a assinatura SigV4 exige uma.
        region_name="us-east-1",
        config=_config,
    )
