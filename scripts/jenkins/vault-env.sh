#!/usr/bin/env bash
# Vault(KV v2)의 배포 env 를 읽어 compose --env-file 용 파일을 만든다 (docs/deploy.md 2절 · docs/security.md §5).
# Jenkins deploy agent 에서 Jenkinsfile.deploy-common.groovy 가 부른다.
#
#   VAULT_BASE=<주소> VAULT_PATH=kv/trading-desk/backend/prod/env ENV_FILE=<출력 파일> COMPOSE_FILES="<compose.yml> ..." \
#   VAULT_ROLE_ID=<...> VAULT_SECRET_ID=<...> [VAULT_AUTH_PATH=approle] scripts/jenkins/vault-env.sh
#
# - 값은 표준 출력 · 로그 · 명령 인자에 나오지 않는다. set +x 이고 AppRole 자격과 토큰은 표준입력으로 curl 에 넘긴다. 키 이름 · 개수만 찍는다.
# - compose 파일이 ${KEY...} 로 쓰는 키만 쓴다. ${KEY:?} 필수 키가 없거나 비면 멈춘다.
# - 값에 작은따옴표 · 줄바꿈이 있으면 멈춘다. 파일을 KEY='value' 로 쓰고, compose 는 작은따옴표 안을 글자 그대로 읽는다($ 도 그대로).
# - Vault 에 닿지 못하면(봉인 · 네트워크 · 권한) 0 이 아닌 값으로 끝난다. 부르는 쪽은 아무것도 바꾸기 전에 멈춘다 — 실행 중인 컨테이너는 그대로다.
# - 로그인 토큰은 끝날 때(성공 · 실패 모두) revoke-self 로 폐기한다.
set +x
set -euo pipefail
umask 077

: "${VAULT_BASE:?VAULT_BASE (Jenkins 전역 env VAULT_ADDR)}" "${VAULT_PATH:?VAULT_PATH}" "${ENV_FILE:?ENV_FILE}"
: "${COMPOSE_FILES:?COMPOSE_FILES}" "${VAULT_ROLE_ID:?VAULT_ROLE_ID}" "${VAULT_SECRET_ID:?VAULT_SECRET_ID}"
AUTH_PATH="${VAULT_AUTH_PATH:-approle}"
VAULT_BASE="${VAULT_BASE%/}"
case "$VAULT_PATH" in */*/*) ;; *) echo "VAULT_PATH 는 <mount>/<경로> 여야 한다: $VAULT_PATH" >&2; exit 1 ;; esac
API_PATH="${VAULT_PATH%%/*}/data/${VAULT_PATH#*/}"   # KV v2: kv/a/b → kv/data/a/b

WORK="$(mktemp -d)"
TOKEN=
cleanup() {
  if [ -n "$TOKEN" ]; then
    printf 'X-Vault-Token: %s\n' "$TOKEN" \
      | curl -sS -o /dev/null --connect-timeout 5 --max-time 10 --header @- -X POST "$VAULT_BASE/v1/auth/token/revoke-self" || true
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT

# compose 파일이 쓰는 키(주석 줄은 뺀다)와 그중 필수(${KEY:?})
# shellcheck disable=SC2086  # COMPOSE_FILES 는 공백으로 나눈 목록이다
grep -hvE '^[[:space:]]*#' $COMPOSE_FILES | grep -oE '\$\{[A-Za-z_][A-Za-z0-9_]*' | cut -c3- | sort -u > "$WORK/used"
# shellcheck disable=SC2086
grep -hvE '^[[:space:]]*#' $COMPOSE_FILES | grep -oE '\$\{[A-Za-z_][A-Za-z0-9_]*:\?' | sed 's/^..//; s/:?$//' | sort -u > "$WORK/required"
[ -s "$WORK/used" ] || { echo "compose 파일에서 \${KEY} 를 하나도 찾지 못했다: $COMPOSE_FILES" >&2; exit 1; }

if ! jq -n '{role_id: env.VAULT_ROLE_ID, secret_id: env.VAULT_SECRET_ID}' \
     | curl -sS --fail --connect-timeout 10 --max-time 30 -X POST --data @- \
         "$VAULT_BASE/v1/auth/$AUTH_PATH/login" > "$WORK/login.json"; then
  echo "Vault 로그인 실패($VAULT_BASE, auth/$AUTH_PATH) — 배포하지 않는다. 운영 컨테이너는 그대로다" >&2
  exit 1
fi
TOKEN="$(jq -r '.auth.client_token // empty' "$WORK/login.json")"
rm -f "$WORK/login.json"
[ -n "$TOKEN" ] || { echo "Vault 로그인 응답에 토큰이 없다 — 배포하지 않는다" >&2; exit 1; }

if ! printf 'X-Vault-Token: %s\n' "$TOKEN" \
     | curl -sS --fail --connect-timeout 10 --max-time 30 --header @- "$VAULT_BASE/v1/$API_PATH" > "$WORK/secret.json"; then
  echo "Vault 에서 $VAULT_PATH 를 읽지 못했다(없음 · 권한) — 배포하지 않는다. 운영 컨테이너는 그대로다" >&2
  exit 1
fi
jq -e '.data.data | type == "object" and length > 0' "$WORK/secret.json" > /dev/null \
  || { echo "$VAULT_PATH 가 비었거나 KV v2 형식이 아니다" >&2; exit 1; }

# 형식 검사 — 걸린 키의 이름만 찍는다(이름 형식이 틀린 키는 이름도 찍지 않는다: 값을 키 자리에 붙여 넣은 실수일 수 있다)
bad="$(jq -r '([39] | implode) as $q | .data.data | to_entries[]
  | select((.key | test("^[A-Za-z_][A-Za-z0-9_]*$") | not) or (.value | type != "string")
           or (.value | contains($q) or contains("\n") or contains("\r")))
  | if (.key | test("^[A-Za-z_][A-Za-z0-9_]*$")) then .key else "(이름 형식이 틀린 키)" end' "$WORK/secret.json")"
if [ -n "$bad" ]; then
  echo "Vault $VAULT_PATH 에 쓸 수 없는 값이 있다(문자열이 아님 · 작은따옴표 · 줄바꿈): ${bad//$'\n'/ }" >&2
  exit 1
fi

jq -r '.data.data | to_entries[] | select(.value != "") | .key' "$WORK/secret.json" | sort -u > "$WORK/present"
missing="$(comm -23 "$WORK/required" "$WORK/present" | tr '\n' ' ')"
[ -z "${missing// }" ] || { echo "Vault $VAULT_PATH 에 필수 키가 없거나 비었다: $missing" >&2; exit 1; }

mkdir -p "$(dirname "$ENV_FILE")"
jq -r --rawfile used "$WORK/used" '
  ([39] | implode) as $q
  | ($used | split("\n") | map(select(. != ""))) as $u
  | .data.data | to_entries | map(select(.key | IN($u[]))) | sort_by(.key)[]
  | .key + "=" + $q + .value + $q' "$WORK/secret.json" > "$ENV_FILE"
chmod 600 "$ENV_FILE"

echo "Vault $VAULT_PATH → env 파일 $(wc -l < "$ENV_FILE" | tr -d ' ')개 키"
unused="$(comm -13 "$WORK/used" "$WORK/present" | tr '\n' ' ')"
[ -z "${unused// }" ] || echo "compose 가 쓰지 않아 넣지 않은 키: $unused"
echo "Vault 에 없어 compose 기본값을 쓰는 키: $(comm -23 "$WORK/used" "$WORK/present" | wc -l | tr -d ' ')개"
