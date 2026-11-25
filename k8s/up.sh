#!/usr/bin/env bash
# Sobe o ML Arena num cluster Kubernetes local (kind). Roda da raiz do repositório:
#   bash k8s/up.sh
# Pré-requisitos: docker, kind e kubectl; o .env preenchido (o mesmo do compose); o
# ml-arena-frontend-v2 clonado ao lado. Pare o compose antes (as portas são as mesmas).
# Para apagar tudo: kind delete cluster --name ml-arena
set -euo pipefail

CLUSTER=ml-arena
NS=ml-arena
SECRET_KEYS='DB_USER|DB_PASSWORD|SECRET_KEY|FRONTEND_SECRET_KEY|MINIO_ROOT_USER|MINIO_ROOT_PASSWORD|RABBITMQ_USER|RABBITMQ_PASSWORD|AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN'

[ -f .env ] || { echo "Falta o .env (cp .env.example .env e preencha)."; exit 1; }

echo "== 1. Cluster"
kind get clusters | grep -qx "$CLUSTER" || kind create cluster --config k8s/kind-config.yaml

echo "== 2. Imagens: build local e carga no nó do kind (sem registry)"
docker build -q -t ml-arena-backend .
docker build -q -t ml-arena-frontend ../ml-arena-frontend-v2
kind load docker-image ml-arena-backend ml-arena-frontend --name "$CLUSTER"

echo "== 3. Namespace, ConfigMap e Secret (só as chaves secretas do .env; nada vai para o git)"
kubectl apply -f k8s/00-namespace.yaml -f k8s/01-configmap.yaml
secrets=$(mktemp)
trap 'rm -f "$secrets"' EXIT
grep -E "^($SECRET_KEYS)=" .env > "$secrets"
kubectl -n "$NS" create secret generic ml-arena-secrets --from-env-file="$secrets" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "== 4. Infraestrutura e aplicação"
kubectl apply -f k8s/10-infra.yaml
# Job não aceita mudança de spec: apaga os antigos para rodarem de novo com a imagem atual.
kubectl -n "$NS" delete job migrate minio-init --ignore-not-found
first_install=$(kubectl -n "$NS" get deployment api -o name 2>/dev/null || true)
kubectl apply -f k8s/20-app.yaml
# Numa nova execução, os pods pegam a imagem recém-carregada: a tag não muda, então o apply
# sozinho não os reinicia. Na 1ª instalação não precisa.
if [ -n "$first_install" ]; then
  kubectl -n "$NS" rollout restart deployment api worker frontend
fi

echo "== 5. Esperando ficar pronto"
kubectl -n "$NS" wait --for=condition=complete job/migrate job/minio-init --timeout=300s
kubectl -n "$NS" rollout status deployment/api deployment/worker deployment/frontend --timeout=300s
kubectl -n "$NS" get pods,svc

echo
echo "Pronto: aplicação em http://localhost:8001 | API em http://localhost:8000/docs"
echo "MinIO em http://localhost:9001 | RabbitMQ em http://localhost:15672"
