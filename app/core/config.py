"""Configuração da aplicação, lida das variáveis de ambiente (ou do `.env`).

Usado por praticamente tudo: `app/db/session.py` (banco), `app/core/security.py`
(JWT), `app/core/object_storage.py` (MinIO), `app/services/messaging.py` e
`worker/consumer.py` (RabbitMQ), `app/core/aws.py` (DynamoDB). Se uma variável obrigatória faltar, o import falha
e a aplicação nem sobe, em vez de quebrar numa requisição qualquer.
"""

from urllib.parse import quote

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configurações lidas das variáveis de ambiente (ou do `.env`, se existir).

    Todas são obrigatórias: faltando alguma, a aplicação não sobe.
    """

    # Banco (RDS na AWS, container `db` no compose)
    db_host: str
    db_port: int
    db_user: str
    db_password: str
    db_name: str
    # JWT: a chave assina os tokens; o algoritmo e a validade têm default
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7
    # MinIO (S3 compatível). Duas URLs: `minio_endpoint` é o endereço na rede do compose,
    # usado por API e worker; `minio_public_url` é o que o navegador alcança e só serve para
    # assinar os links pré-assinados (o host entra na assinatura, não dá para trocar depois).
    minio_endpoint: str
    minio_public_url: str
    minio_root_user: str
    minio_root_password: str
    minio_bucket: str = "ml-arena"
    # RabbitMQ (no T1 eram SNS + SQS). A URL é montada abaixo, como a do banco.
    rabbitmq_host: str
    rabbitmq_port: int = 5672
    rabbitmq_user: str
    rabbitmq_password: str
    # AWS: só o DynamoDB (tabela de auditoria) continua na AWS
    aws_region: str
    dynamodb_audit_table: str | None = None
    redis_url: str
    # Chaves opcionais: se ausentes, o boto3 usa a cadeia padrão (ambiente, IAM role)
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_session_token: str | None = None

    # `extra="ignore"`: variáveis que não estão aqui (ex.: AWS_* usadas pelo boto3) são ignoradas
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    @computed_field
    @property
    def database_url(self) -> str:
        """URL assíncrona do banco, montada a partir das partes acima."""
        return f"postgresql+asyncpg://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"

    @computed_field
    @property
    def rabbitmq_url(self) -> str:
        """URL AMQP do RabbitMQ. Usuário e senha vão escapados (ex.: `@` ou `/` na senha)."""
        user = quote(self.rabbitmq_user, safe="")
        password = quote(self.rabbitmq_password, safe="")
        return f"amqp://{user}:{password}@{self.rabbitmq_host}:{self.rabbitmq_port}/"


# Instância única, criada no import. Os outros módulos fazem `from app.core.config import settings`.
settings = Settings()
