#!/usr/bin/env bash
# Teste ponta a ponta do ML Arena com o compose no ar (`docker compose up -d --build`).
# Roda da raiz do repositório: `bash scripts/e2e.sh`. Precisa de curl e python3 no host.
# No Kubernetes (k8s/up.sh): `K8S=1 bash scripts/e2e.sh` (usa kubectl em vez do compose).
#
# Percorre o fluxo da aplicação pela API (o mesmo que o frontend usa): cadastro, login,
# dois runs, upload dos CSVs, processamento pelo worker, comparação com os links do MinIO,
# desacoplamento (worker parado), falhas esperadas e remoção. Para no primeiro erro.
set -euo pipefail

API=${API:-http://localhost:8000/api/v1}
EMAIL="e2e-$(date +%s)@example.com"
PASSWORD="senha-e2e-123"

ok() { printf '  \033[32mok\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFALHOU\033[0m %s\n' "$1"; exit 1; }
json() { python3 -c "import sys, json; d = json.load(sys.stdin); print($1)"; }
status_of() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

wait_run_done() { # espera o run $1 ficar done (até $2 segundos)
  for _ in $(seq 1 "${2:-60}"); do
    st=$(curl -s "$API/runs/$1" -H "$AUTH" | json 'd["status"]')
    [ "$st" = "done" ] && return 0
    [ "$st" = "failed" ] && fail "run $1 terminou failed"
    sleep 1
  done
  fail "run $1 não ficou done (status: $st)"
}

new_run() { # cria um run com nome $1 e devolve o id
  curl -s -X POST "$API/runs" -H "$AUTH" -H 'content-type: application/json' \
    -d "{\"name\": \"$1\", \"algorithm\": \"$2\", \"metrics\": {\"test\": {\"rmse\": $3}}}" |
    json 'd["id"]'
}

upload() { # sobe o CSV $2 no run $1 e devolve o código HTTP
  status_of -X POST "$API/runs/$1/measured-predicted" -H "$AUTH" -F "file=@$2"
}

# Parar/religar o worker e olhar a fila: pelo compose ou pelo kubectl (K8S=1).
if [ "${K8S:-}" = 1 ]; then
  stop_worker() {
    kubectl -n ml-arena scale deployment/worker --replicas=0 >/dev/null
    kubectl -n ml-arena wait --for=delete pod -l app=worker --timeout=90s >/dev/null 2>&1 || true
  }
  start_worker() { kubectl -n ml-arena scale deployment/worker --replicas=2 >/dev/null; }
  rabbitmqctl() { kubectl -n ml-arena exec deploy/rabbitmq -- rabbitmqctl "$@"; }
else
  stop_worker() { docker compose stop worker >/dev/null 2>&1; }
  start_worker() { docker compose start worker >/dev/null 2>&1; }
  rabbitmqctl() { docker compose exec -T rabbitmq rabbitmqctl "$@"; }
fi

queue_ready() {
  rabbitmqctl list_queues -q name messages_ready | awk '$1 == "csv-processing" {print $2}'
}

echo "1. Cadastro e login"
[ "$(status_of -X POST "$API/users/" -H 'content-type: application/json' \
  -d "{\"name\": \"E2E\", \"email\": \"$EMAIL\", \"password\": \"$PASSWORD\"}")" = 201 ] || fail "cadastro"
TOKEN=$(curl -s -X POST "$API/auth/login" -d "username=$EMAIL&password=$PASSWORD" | json 'd["access_token"]')
AUTH="Authorization: Bearer $TOKEN"
ok "usuário $EMAIL"

echo "2. Dois runs, upload e processamento pelo worker"
RUN_A=$(new_run "modelo A" "ridge" 1.5)
RUN_B=$(new_run "modelo B" "random-forest" 4.2)
[ "$(upload "$RUN_A" samples/modelo_a.csv)" = 201 ] || fail "upload A"
[ "$(upload "$RUN_B" samples/modelo_b.csv)" = 201 ] || fail "upload B"
wait_run_done "$RUN_A"
wait_run_done "$RUN_B"
ok "runs $RUN_A e $RUN_B: done"

echo "3. Comparação: links pré-assinados do MinIO abrem no navegador"
URLS=$(curl -s "$API/runs/compare?run_a=$RUN_A&run_b=$RUN_B" -H "$AUTH" |
  json '"\n".join(u for side in ("run_a", "run_b") for u in d[side]["plots"].values())')
[ "$(echo "$URLS" | wc -l)" = 4 ] || fail "esperava 4 plots"
for url in $URLS; do
  case "$url" in http://localhost:9000/*X-Amz-Signature=*) ;; *) fail "URL inesperada: $url" ;; esac
  [ "$(status_of "$url")" = 200 ] || fail "plot não abre: $url"
done
ok "4 PNGs em localhost:9000 com assinatura, todos 200"

echo "4. Desacoplamento: com o worker parado a API aceita e a mensagem espera na fila"
stop_worker
RUN_C=$(new_run "modelo C" "ridge" 1.6)
[ "$(upload "$RUN_C" samples/modelo_a.csv)" = 201 ] || fail "upload com worker parado"
sleep 2
[ "$(curl -s "$API/runs/$RUN_C" -H "$AUTH" | json 'd["status"]')" != done ] || fail "processou sem worker?"
# O contador do rabbitmqctl atualiza em intervalos de ~5 s: consulta até aparecer.
for _ in $(seq 1 15); do [ "$(queue_ready)" = 1 ] && break; sleep 1; done
[ "$(queue_ready)" = 1 ] || fail "esperava 1 mensagem Ready na fila"
ok "upload 201, run não processado, 1 mensagem Ready"
start_worker
wait_run_done "$RUN_C" 90
ok "worker religado processou o run $RUN_C"

echo "5. Falhas esperadas"
RUN_D=$(new_run "inválido" "ridge" 0)
[ "$(upload "$RUN_D" samples/invalido.csv)" = 422 ] || fail "CSV inválido deveria dar 422"
ok "CSV inválido: 422"
[ "$(upload "$RUN_A" samples/modelo_a.csv)" = 409 ] || fail "segundo upload deveria dar 409"
ok "segundo CSV no mesmo run: 409"
[ "$(status_of "$API/runs")" = 401 ] || fail "sem token deveria dar 401"
ok "sem token: 401"

echo "6. Auditoria (DynamoDB): 200 com credenciais do Learner Lab, 503 sem"
code=$(status_of "$API/audit" -H "$AUTH")
case "$code" in 200 | 503) ok "GET /audit: $code" ;; *) fail "GET /audit: $code" ;; esac

echo "7. Remover um run apaga os objetos do MinIO"
FIRST_URL=$(echo "$URLS" | head -1)
[ "$(status_of -X DELETE "$API/runs/$RUN_A" -H "$AUTH")" = 204 ] || fail "delete"
[ "$(status_of "$FIRST_URL")" = 404 ] || fail "o PNG deveria ter sumido do MinIO"
ok "run $RUN_A removido; o link do plot agora dá 404"

printf '\n\033[32mTudo certo.\033[0m\n'
