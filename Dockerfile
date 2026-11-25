# Imagem única do backend: a mesma serve a API, o worker e o migrate. Os três importam o
# pacote `app` e usam as mesmas dependências; o que muda é o comando, definido no compose
# (`fastapi run ...`, `python -m worker.consumer`, `alembic upgrade head`).
FROM python:3.14-slim

# uv copiado da imagem oficial, com versão fixa (antes: `pip install uv`, sempre a mais nova).
COPY --from=ghcr.io/astral-sh/uv:0.9.17 /uv /usr/local/bin/uv

# Compila os .pyc na instalação (o container sobe mais rápido); copia em vez de hardlink
# (o cache do uv não está no mesmo disco); usa o Python da imagem, sem baixar outro.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

# Usuário sem privilégios: se alguém explorar a aplicação, não é root no container.
# Com home, porque o matplotlib do worker grava o cache de fontes em ~/.config.
RUN useradd --create-home --uid 1000 app

WORKDIR /workspace

# Dependências primeiro, só com os arquivos que as definem: enquanto pyproject.toml e
# uv.lock não mudam, esta camada vem do cache e mudar o código não reinstala nada.
# --frozen: instala exatamente o que está no lock (build reproduzível), sem atualizá-lo.
COPY pyproject.toml uv.lock ./
# Cache mount: o cache de downloads do uv fica fora da imagem (antes ele entrava na camada
# e quase dobrava o tamanho), mas continua acelerando os próximos builds.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
ENV PATH="/workspace/.venv/bin:$PATH"

COPY . .

USER app

EXPOSE 8000

# Padrão: a API. O compose troca o comando para o worker e o migrate.
CMD ["fastapi", "run", "app/main.py", "--host", "0.0.0.0", "--port", "8000"]
