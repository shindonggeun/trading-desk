#!/usr/bin/env bash
set -euo pipefail

[[ ${VAULT_BASE:-} == http://127.0.0.1:9 && ${VAULT_PATH:-} == kv/trading-desk/frontend/prod/env ]]
[[ -n ${VAULT_ROLE_ID:-} && -n ${VAULT_SECRET_ID:-} ]]
[[ ${ENV_FILE:-} == "$PWD/.deploy-secrets/env" ]]
[[ ${COMPOSE_FILES:-} == .deploy-src/frontend/docker-compose-frontend.yml && -r $COMPOSE_FILES ]]
umask 077
mkdir -- "$PWD/.deploy-secrets"
printf "DESK_DOCKER_NETWORK='td-web-fixture'\n" > "$ENV_FILE"
