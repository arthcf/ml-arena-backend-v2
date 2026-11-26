"""Configuração comum dos testes.

`app.core.config` cria `Settings()` no import e exige várias variáveis de ambiente. Sem elas, o
pytest nem coleta os testes. Aqui definimos valores falsos *antes* de qualquer import do `app`
(o pytest carrega o conftest.py primeiro), para os testes rodarem sem `.env`, sem Docker e sem
AWS.

`setdefault` não sobrescreve: se alguém exportar uma variável real no shell, ela vale. E variável
de ambiente tem precedência sobre o `.env`, então com estes valores o resultado dos testes não
depende do `.env` de quem roda.
"""

import os

# Um valor para cada campo obrigatório de `Settings` (app/core/config.py).
# Quando o config.py ganhar um campo obrigatório novo, ele entra aqui.
TEST_ENV: dict[str, str] = {
    # Banco: o engine do SQLAlchemy é criado no import, mas só conecta na 1ª query
    "DB_HOST": "localhost",
    "DB_PORT": "5432",  # string numérica: o Pydantic converte para int
    "DB_USER": "test",
    "DB_PASSWORD": "test",
    "DB_NAME": "test",
    "SECRET_KEY": "test-secret-key-nao-use-em-producao",
    # MinIO: só endereços e nomes; nenhum teste abre conexão (o cliente conecta na 1ª chamada)
    "MINIO_ENDPOINT": "http://localhost:9000",
    "MINIO_PUBLIC_URL": "http://localhost:9000",
    "MINIO_ROOT_USER": "test",
    "MINIO_ROOT_PASSWORD": "test-password",
    "MINIO_BUCKET": "test-bucket",
    # RabbitMQ: o worker e a API só conectam na 1ª publicação/consumo; os testes usam fakes
    "RABBITMQ_HOST": "localhost",
    "RABBITMQ_USER": "test",
    "RABBITMQ_PASSWORD": "test",
    # AWS: só nomes; nenhum teste chama a AWS (o worker usa fakes). Conta 000000000000 = exemplo
    "AWS_REGION": "us-east-1",
    "DYNAMODB_AUDIT_TABLE": "test-audit",
    # Porta 1: nada escuta nela; um teste que use o Redis de verdade falha na hora
    "REDIS_URL": "redis://localhost:1/0",
}

for name, value in TEST_ENV.items():
    os.environ.setdefault(name, value)
