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
# Quando o config.py ganhar campos novos (MinIO na Etapa 3, RabbitMQ na Etapa 4), eles entram aqui.
TEST_ENV: dict[str, str] = {
    # Banco: o engine do SQLAlchemy é criado no import, mas só conecta na 1ª query
    "DB_HOST": "localhost",
    "DB_PORT": "5432",  # string numérica: o Pydantic converte para int
    "DB_USER": "test",
    "DB_PASSWORD": "test",
    "DB_NAME": "test",
    "SECRET_KEY": "test-secret-key-nao-use-em-producao",
    # AWS: só nomes; nenhum teste chama a AWS (o worker usa fakes). Conta 000000000000 = exemplo
    "AWS_REGION": "us-east-1",
    "S3_BUCKET_NAME": "test-bucket",
    "SNS_TOPIC_ARN": "arn:aws:sns:us-east-1:000000000000:test-topic",
    "SQS_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/000000000000/test-queue",
    "DYNAMODB_AUDIT_TABLE": "test-audit",  # opcional hoje, obrigatório na Etapa 5
    # Porta 1: nada escuta nela; um teste que use o Redis de verdade falha na hora
    "REDIS_URL": "redis://localhost:1/0",
}

for name, value in TEST_ENV.items():
    os.environ.setdefault(name, value)
