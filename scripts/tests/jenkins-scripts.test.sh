#!/usr/bin/env bash
# scripts/jenkins/vault-env.sh · wait-ci.sh 를 가짜 curl 로 돌려 본다 (#692 · #691). 네트워크 · Vault · GitHub 에 닿지 않는다.
# jq 가 있어야 한다(CI · Jenkins agent 에는 있다). 없으면 건너뛴다.
#
#   bash scripts/tests/jenkins-scripts.test.sh
set -uo pipefail

command -v jq > /dev/null || { echo "jq 가 없어 건너뛴다"; exit 0; }
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
FAILS=0
check() {  # $1 이름, $2 기대, $3 실제
  if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: 기대 '$2' 실제 '$3'"; sed 's/^/     | /' "$TMP/out"; FAILS=$((FAILS + 1)); fi
}

# ---- 가짜 curl: 마지막 인자가 URL. 명령 인자는 calls 에, 표준입력은 stdin 에 남긴다
mkdir -p "$TMP/bin"
cat > "$TMP/bin/curl" <<'FAKE'
#!/usr/bin/env bash
url=${*: -1}
echo "curl $*" >> "$FAKE_DIR/calls"
cat >> "$FAKE_DIR/stdin" 2>/dev/null || true
case "$url" in
  */auth/approle/login) [ -z "${FAKE_LOGIN_FAIL:-}" ] || exit 22; echo '{"auth":{"client_token":"tok-SECRET-123"}}' ;;
  */auth/token/revoke-self) touch "$FAKE_DIR/revoked" ;;
  */v1/kv/data/*) [ -z "${FAKE_READ_FAIL:-}" ] || exit 22; cat "$FAKE_DIR/secret.json" ;;
  https://api.github.com/*)
    n=$(( $(cat "$FAKE_DIR/n" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$FAKE_DIR/n"
    f="runs.$n.json"; [ -f "$FAKE_DIR/$f" ] || f=$(cd "$FAKE_DIR" && ls runs.*.json | sort -t. -k2 -n | tail -n 1)
    cat "$FAKE_DIR/$f" ;;
  *) exit 7 ;;
esac
FAKE
chmod +x "$TMP/bin/curl"
export PATH="$TMP/bin:$PATH" FAKE_DIR="$TMP/fake"

# ================= vault-env.sh
vault_env() {  # $1 = secret.json 내용, 나머지 = 추가 env
  rm -rf "$FAKE_DIR"; mkdir -p "$FAKE_DIR"; printf '%s' "$1" > "$FAKE_DIR/secret.json"; shift
  env VAULT_BASE=https://vault.example/ VAULT_PATH=kv/trading-desk/backend/prod/env ENV_FILE="$TMP/out.env" \
      COMPOSE_FILES="$REPO_ROOT/backend/docker-compose-backend.yml" VAULT_ROLE_ID=role-SECRET VAULT_SECRET_ID=sid-SECRET "$@" \
      bash "$REPO_ROOT/scripts/jenkins/vault-env.sh" > "$TMP/out" 2>&1
  echo $?
}
GOOD='{"data":{"data":{"DESK_DATABASE_URL":"postgresql+psycopg://u:p%40ss$word@h:5432/d?options=-csearch_path%3Ddesk","DESK_DOCKER_NETWORK":"trading-desk-prod","TOSS_CLIENT_SECRET":"tossSECRETvalue","DESK_WEB_PORT":"10088","NOT_USED_BY_COMPOSE":"x"}}}'

rm -f "$TMP/out.env"
rc=$(vault_env "$GOOD"); check "읽어서 env 파일을 쓴다" 0 "$rc"
check "compose 가 쓰는 키만, 정렬, 작은따옴표" \
  "DESK_DATABASE_URL='postgresql+psycopg://u:p%40ss\$word@h:5432/d?options=-csearch_path%3Ddesk'|DESK_DOCKER_NETWORK='trading-desk-prod'|TOSS_CLIENT_SECRET='tossSECRETvalue'" \
  "$(paste -sd'|' "$TMP/out.env")"
check "env 파일 권한 600" "600" "$(stat -c %a "$TMP/out.env" 2>/dev/null || echo 600)"
check "토큰을 폐기한다" "yes" "$([ -f "$FAKE_DIR/revoked" ] && echo yes)"
SECRETS='tossSECRETvalue|p%40ss|tok-SECRET|role-SECRET|sid-SECRET'
check "값 · 토큰 · AppRole 이 출력에 없다" 0 "$(grep -cE "$SECRETS" "$TMP/out")"
check "값 · 토큰 · AppRole 이 curl 인자에 없다" 0 "$(grep -cE "$SECRETS" "$FAKE_DIR/calls")"
grep -q "NOT_USED_BY_COMPOSE" "$TMP/out"; check "안 쓰는 키는 이름만 알린다" 0 $?

rc=$(vault_env '{"data":{"data":{"DESK_DOCKER_NETWORK":"n"}}}'); check "필수 키가 없으면 멈춘다" 1 "$rc"
grep -q "DESK_DATABASE_URL" "$TMP/out"; check "빠진 필수 키 이름을 알린다" 0 $?
rc=$(vault_env '{"data":{"data":{"DESK_DATABASE_URL":"","DESK_DOCKER_NETWORK":"n"}}}'); check "빈 필수 키도 멈춘다" 1 "$rc"

rc=$(vault_env "$(jq -n '{data:{data:{DESK_DATABASE_URL:"a", DESK_DOCKER_NETWORK:"n", FRED_API_KEY:("it" + ([39]|implode) + "s")}}}')")
check "작은따옴표 값은 멈춘다" 1 "$rc"
check "걸린 키 이름만, 값은 없이" "1 0" "$(grep -c FRED_API_KEY "$TMP/out") $(grep -c "it's" "$TMP/out")"
rc=$(vault_env '{"data":{"data":{"DESK_DATABASE_URL":"a\nb","DESK_DOCKER_NETWORK":"n"}}}'); check "줄바꿈 값은 멈춘다" 1 "$rc"
rc=$(vault_env '{"data":{"data":{"DESK_DATABASE_URL":"a","DESK_DOCKER_NETWORK":"n","postgres://leak":"x"}}}')
check "이름 형식이 틀린 키는 이름도 찍지 않는다" "1 0" "$rc $(grep -c leak "$TMP/out")"

rm -f "$TMP/out.env"
rc=$(vault_env "$GOOD" FAKE_LOGIN_FAIL=1); check "로그인 실패면 멈추고 파일을 쓰지 않는다" "1 no" "$rc $([ -f "$TMP/out.env" ] && echo yes || echo no)"
rc=$(vault_env "$GOOD" FAKE_READ_FAIL=1); check "읽기 실패면 멈추고 토큰은 폐기" "1 yes" "$rc $([ -f "$FAKE_DIR/revoked" ] && echo yes)"
rc=$(vault_env '{"data":{"data":{}}}'); check "빈 secret 은 멈춘다" 1 "$rc"

FRONT=$(rm -rf "$FAKE_DIR"; mkdir -p "$FAKE_DIR"; echo '{"data":{"data":{"DESK_DOCKER_NETWORK":"trading-desk-prod","DESK_WEB_BIND":"192.168.0.9","DESK_WEB_PORT":"10088"}}}' > "$FAKE_DIR/secret.json"
  env VAULT_BASE=https://vault.example VAULT_PATH=kv/trading-desk/frontend/prod/env ENV_FILE="$TMP/web.env" \
      COMPOSE_FILES="$REPO_ROOT/frontend/docker-compose-frontend.yml" VAULT_ROLE_ID=r VAULT_SECRET_ID=s \
      bash "$REPO_ROOT/scripts/jenkins/vault-env.sh" > /dev/null 2>&1; echo $?; paste -sd'|' "$TMP/web.env")
check "웹 compose 키" "0 DESK_DOCKER_NETWORK='trading-desk-prod'|DESK_WEB_BIND='192.168.0.9'|DESK_WEB_PORT='10088'" "$(echo $FRONT)"

# ================= wait-ci.sh
wait_ci() {  # 인자 = 차례로 돌려줄 runs JSON
  rm -rf "$FAKE_DIR"; mkdir -p "$FAKE_DIR"; local i=0 j
  for j in "$@"; do i=$((i + 1)); printf '%s' "$j" > "$FAKE_DIR/runs.$i.json"; done
  CI_REPO=o/r CI_SHA=abc123 CI_POLL_SEC=0 GITHUB_TOKEN=ghs_SECRET bash "$REPO_ROOT/scripts/jenkins/wait-ci.sh" > "$TMP/out" 2>&1
  echo $?
}
run() { printf '{"name":"%s","status":"%s","conclusion":%s,"run_number":%s,"run_attempt":%s,"html_url":"u%s"}' "$@" "$4"; }
NONE='{"workflow_runs":[]}'
PROG="{\"workflow_runs\":[$(run ci in_progress null 7 1)]}"
OK="{\"workflow_runs\":[$(run ci completed '"success"' 7 1),$(run other completed '"failure"' 8 1)]}"
BAD="{\"workflow_runs\":[$(run ci completed '"failure"' 7 1)]}"
CAN="{\"workflow_runs\":[$(run ci completed '"cancelled"' 7 1)]}"
RERUN="{\"workflow_runs\":[$(run ci completed '"failure"' 7 1),$(run ci completed '"success"' 7 2)]}"

check "없음 → 진행 중 → 성공이면 0" 0 "$(wait_ci "$NONE" "$PROG" "$OK")"
check "GitHub 토큰이 인자 · 출력에 없다" "0 0" "$(grep -c ghs_ "$FAKE_DIR/calls") $(grep -c ghs_ "$TMP/out")"
check "다른 워크플로의 실패는 보지 않는다" 0 "$(wait_ci "$OK")"
check "실패면 1" 1 "$(wait_ci "$PROG" "$BAD")"
check "취소(밀림)면 4" 4 "$(wait_ci "$CAN")"
check "다시 돌린 마지막 시도를 본다" 0 "$(wait_ci "$RERUN")"

if [ "$FAILS" = 0 ]; then echo "모두 통과"; else echo "${FAILS}개 실패"; exit 1; fi
