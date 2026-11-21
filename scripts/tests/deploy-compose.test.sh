#!/usr/bin/env bash
# scripts/deploy-compose.sh 를 가짜 docker 로 돌려 본다 — 성공 · 되돌림 · 진행 중 분석 대기 · 마이그레이션 · 로그 가림 (#692).
# docker 가 없어도 돈다(Git Bash · CI). 실제 컨테이너는 만들지 않는다.
#
#   bash scripts/tests/deploy-compose.test.sh
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/../deploy-compose.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
FAILS=0
REAL_DOCKER="$(command -v docker || true)"     # 맨 끝의 compose 파일 검사용(가짜 docker 를 PATH 에 넣기 전)

# ---- 가짜 docker: 상태는 $STATE 아래 파일로 둔다
mkdir -p "$TMP/bin"
cat > "$TMP/bin/docker" <<'FAKE'
#!/usr/bin/env bash
set -u
S="$STATE"; mkdir -p "$S/tags" "$S/img" "$S/ctr" "$S/net"
key() { printf '%s' "$1" | tr ':/' '__'; }
resolve() { case "$1" in img-*) printf '%s' "$1" ;; *) cat "$S/tags/$(key "$1")" 2>/dev/null ;; esac; }
label() { cat "$S/img/$1.label" 2>/dev/null || true; }
echo "docker $*" >> "$S/calls"
if [ "$1" = compose ]; then
  prof=''
  shift; while [ "$#" -gt 0 ]; do case "$1" in -p|--env-file|-f) shift 2 ;; --profile) prof=$2; shift 2 ;; *) break ;; esac; done
  sub=$1; shift
  case "$sub" in
    config)
      case "${1:-}" in
        -q) exit 0 ;;
        --images) echo "$FAKE_IMAGE"; echo "$FAKE_IMAGE" ;;
        --services) printf '%s\n' $FAKE_SERVICES; [ "$prof" != split ] || printf '%s\n' ${FAKE_SPLIT_SERVICES-collector-prod analyst-prod} ;;
      esac ;;
    ps) svc=${*: -1}; [ -f "$S/ctr/$svc" ] && echo "cid-$svc"; exit 0 ;;
    up)
      svcs=()
      for a in "$@"; do case "$a" in -*|[0-9]*) ;; *) svcs+=("$a") ;; esac; done
      id=$(resolve "$FAKE_IMAGE"); rc=0
      for svc in "${svcs[@]}"; do
        h=healthy; [ "$(label "$id")" != "${FAKE_BAD_REV:-}" ] || { h=unhealthy; rc=1; }
        echo "$id $h" > "$S/ctr/$svc"
        mkdir -p "$S/role"; echo "${DESK_API_ROLE:-unset}" > "$S/role/$svc"     # 셸 환경으로 받은 api 역할
      done
      exit $rc ;;
  esac
  exit 0
fi
case "$1" in
  network) case "$2" in create) touch "$S/net/$3" ;; inspect) [ -f "$S/net/$3" ] || exit 1 ;; esac ;;
  inspect)
    fmt=$3; svc=${4#cid-}; read -r id h < "$S/ctr/$svc"
    case "$fmt" in
      *Labels*) label "$id" ;;
      '{{.Image}}') echo "$id" ;;
      '{{.RestartCount}}') echo 0 ;;
      *State.Status*) echo "running $h 0" ;;
    esac ;;
  build)
    shift; while [ "$#" -gt 1 ]; do case "$1" in -t) t=$2; shift 2 ;; --label) l=${2#*=}; shift 2 ;; *) shift ;; esac; done
    id="img-$(( $(ls "$S/img" | wc -l) + 1 ))"
    echo "$l" > "$S/img/$id.label"
    ls "$1/desk/migrations/versions" > "$S/img/$id.migs"
    echo "$id" > "$S/tags/$(key "$t")" ;;
  tag) id=$(resolve "$2"); [ -n "$id" ] || exit 1; echo "$id" > "$S/tags/$(key "$3")" ;;
  run) img=${*: -3:1}; cat "$S/img/$(resolve "$img").migs" ;;
  exec)
    svc=${2#cid-}; read -r id h < "$S/ctr/$svc"
    case "$*" in
      *unfinished*) printf '%b\n' "${FAKE_UNFINISHED:-0}" ;;
      *api/health*) [ "$(label "$id")" != "${FAKE_BAD_REV:-}" ] ;;
      *) exit 0 ;;
    esac ;;
  logs) echo "boot ok, db=${FAKE_LOG_SECRET:-none}" ;;
  ps) case "$*" in *-aq*) svc=${*: -1}; svc=${svc##*=}; [ ! -f "$S/ctr/$svc" ] || echo "cid-$svc" ;; esac ;;
  rm) shift; for c in "$@"; do case "$c" in cid-*) rm -f "$S/ctr/${c#cid-}" ;; esac; done ;;
  images) for f in "$S"/tags/*; do b=$(basename "$f"); echo "${b%_*}:${b##*_}"; done ;;
  rmi) shift; for t in "$@"; do rm -f "$S/tags/$(key "$t")"; done ;;
esac
exit 0
FAKE
chmod +x "$TMP/bin/docker"

# ---- 가짜 소스 루트: compose · Dockerfile 이름만 있으면 된다
SRC="$TMP/src"
mkdir -p "$SRC/backend/desk/migrations/versions" "$SRC/frontend"
touch "$SRC/backend/docker-compose-backend.yml" "$SRC/backend/backend.Dockerfile" \
      "$SRC/backend/desk/migrations/versions/0001_initial.py"
ENVF="$TMP/env"
printf '%s\n' "DESK_DOCKER_NETWORK='trading-desk-prod'" "DESK_DATABASE_URL='postgresql://u:s3cr3t-pass@db/x'" \
  "DESK_KIS_APP_KEY='k1'" "DESK_OTP_KEY='s3cr3t'" "DESK_MAX_CONCURRENT_RUNS='1'" > "$ENVF"

export PATH="$TMP/bin:$PATH" STATE="$TMP/state" STABLE_SEC=0 WAIT_MIN=0 ENV_FILE="$ENVF"
export FAKE_IMAGE=trading-desk-backend:prod FAKE_SERVICES=backend-prod

prod_rev() { local id; id=$(cat "$STATE/tags/trading-desk-backend_prod"); cat "$STATE/img/$id.label"; }
ctr_rev() { local id; read -r id _ < "$STATE/ctr/$1"; cat "$STATE/img/$id.label"; }
deploy() { REVISION="$1" bash "$SCRIPT" backend "$SRC" > "$TMP/out" 2>&1; echo $?; }
check() {  # $1 이름, $2 기대, $3 실제
  if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: 기대 '$2' 실제 '$3'"; sed 's/^/     | /' "$TMP/out"; FAILS=$((FAILS + 1)); fi
}

rc=$(deploy rev1); check "첫 배포" "0 rev1" "$rc $(prod_rev)"
check "첫 배포는 rollback 태그가 없다" "" "$(cat "$STATE/tags/trading-desk-backend_rollback" 2>/dev/null)"

rc=$(deploy rev2); check "두 번째 배포" "0 rev2 rev2" "$rc $(prod_rev) $(ctr_rev backend-prod)"
check "직전 이미지를 rollback 으로 남긴다" "rev1" "$(cat "$STATE/img/$(cat "$STATE/tags/trading-desk-backend_rollback").label")"
check "옛 커밋 태그를 지운다" 0 "$(find "$STATE/tags" -name '*rev1*' | wc -l | tr -d ' ')"

rc=$(ROLLBACK_DRILL=1 deploy rev3); check "드릴은 되돌린다" "2 rev2 rev2" "$rc $(prod_rev) $(ctr_rev backend-prod)"
grep -q "ROLLBACK_DRILL" "$TMP/out"; check "드릴을 알린다" 0 $?

rc=$(FAKE_BAD_REV=rev4 deploy rev4); check "health 실패면 되돌린다" "2 rev2 rev2" "$rc $(prod_rev) $(ctr_rev backend-prod)"

FAKE_LOG_SECRET='postgresql://u:s3cr3t-pass@db/x' FAKE_BAD_REV=rev4b deploy rev4b > /dev/null
check "실패 로그에서 env 값을 가린다(긴 값부터 — 짧은 비밀이 긴 값의 일부여도)" "0 0 1" "$(grep -c 's3cr3t' "$TMP/out") $(grep -c 'pass' "$TMP/out") $(grep -cxF 'boot ok, db=****' "$TMP/out")"

rc=$(FAKE_UNFINISHED='2\nAAPL running 10:00 r1\nNVDA queued 10:01 r2' deploy rev5)
check "진행 중인 분석이 있으면 멈추고 운영은 그대로" "3 rev2" "$rc $(prod_rev)"
grep -q "AAPL running" "$TMP/out"; check "진행 중인 분석을 보인다" 0 $?

rc=$(FORCE=1 FAKE_UNFINISHED='1\nAAPL running 10:00 r1' deploy rev5); check "FORCE 면 기다리지 않는다" "0 rev5" "$rc $(prod_rev)"

touch "$SRC/backend/desk/migrations/versions/0099_new.py"
rc=$(ROLLBACK_DRILL=1 deploy rev6); check "새 마이그레이션이면 되돌리지 않는다" "1 rev6" "$rc $(prod_rev)"
grep -q "0099_new.py" "$TMP/out"; check "새 마이그레이션 이름을 보인다" 0 $?

# ---- #688 백엔드 나누기 스위치(DESK_BACKEND_SPLIT)
SPLITF="$TMP/env.split"; cp "$ENVF" "$SPLITF"; echo "DESK_BACKEND_SPLIT='1'" >> "$SPLITF"
role() { cat "$STATE/role/$1" 2>/dev/null; }
rc=$(ENV_FILE="$SPLITF" deploy rev7)
check "#688 켜면 서비스 3개를 같은 이미지로" "0 rev7 rev7 rev7" "$rc $(ctr_rev backend-prod) $(ctr_rev collector-prod) $(ctr_rev analyst-prod)"
check "켜면 backend-prod 는 api 역할, compose 는 profile split" "api 1" "$(role backend-prod) $(grep -c 'compose.*--profile split.* up .*backend-prod$' "$STATE/calls")"
check "api(PRIMARY)를 먼저 올린다" "backend-prod" "$(grep 'compose.* up ' "$STATE/calls" | tail -2 | head -1 | awk '{print $NF}')"
check "나머지는 api 를 다시 만들지 않게 --no-deps 로" 1 "$(grep 'compose.* up ' "$STATE/calls" | tail -1 | grep -c -- '--no-deps .*collector-prod analyst-prod$')"

rc=$(ENV_FILE="$SPLITF" ROLLBACK_DRILL=1 deploy rev8)
check "서비스 3개 모두 되돌린다" "2 rev7 rev7 rev7" "$rc $(ctr_rev backend-prod) $(ctr_rev collector-prod) $(ctr_rev analyst-prod)"

: > "$STATE/calls"
rc=$(deploy rev9)
check "끄면 all 하나로 돌아간다" "0 rev9 all" "$rc $(ctr_rev backend-prod) $(role backend-prod)"
check "끄면 collector · analyst 를 지운다" "no no" "$([ -f "$STATE/ctr/collector-prod" ] && echo yes || echo no) $([ -f "$STATE/ctr/analyst-prod" ] && echo yes || echo no)"
first_rm=$(grep -n '^docker rm -f' "$STATE/calls" | head -1 | cut -d: -f1)
first_up=$(grep -n 'compose.* up ' "$STATE/calls" | head -1 | cut -d: -f1)
check "api 를 all 로 띄우기 전에 지운다(토큰 발급자가 둘이 되지 않게)" yes "$([ -n "$first_rm" ] && [ "$first_rm" -lt "${first_up:-0}" ] && echo yes)"

BADF="$TMP/env.bad"; cp "$ENVF" "$BADF"; echo "DESK_BACKEND_SPLIT='yes'" >> "$BADF"
rc=$(ENV_FILE="$BADF" deploy rev10); check "스위치 값이 0 | 1 이 아니면 바꾸기 전에 멈춘다" "1 rev9" "$rc $(prod_rev)"
ROLEF="$TMP/env.role"; cp "$ENVF" "$ROLEF"; echo "DESK_API_ROLE='collector'" >> "$ROLEF"
rc=$(ENV_FILE="$ROLEF" deploy rev10); check "env 파일의 DESK_API_ROLE 은 거부한다(역할은 스크립트만 정한다)" "1 rev9" "$rc $(prod_rev)"
rc=$(ENV_FILE="$SPLITF" FAKE_SPLIT_SERVICES="collector-prod" deploy rev10)
check "켰는데 compose 에 analyst 가 없으면 멈춘다" "1 rev9" "$rc $(prod_rev)"
rc=$(FAKE_SERVICES="backend-prod collector-prod" deploy rev10)
check "껐는데 profile 없이 collector 가 있으면 멈춘다" "1 rev9" "$rc $(prod_rev)"

rc=$(PRIMARY_SERVICE=api deploy rev11); check "없는 PRIMARY_SERVICE 는 바꾸기 전에 멈춘다" "1 rev9" "$rc $(prod_rev)"

# ---- 실제 compose 파일(#688): 진짜 docker compose 가 있을 때(CI)만. 스위치에 따라 서비스 · api 역할이 맞게 풀리는지
if [ -n "$REAL_DOCKER" ] && "$REAL_DOCKER" compose version > /dev/null 2>&1; then
  CF="$HERE/../../backend/docker-compose-backend.yml"
  printf '%s\n' "DESK_DATABASE_URL=postgresql://u:p@db/x" "DESK_DOCKER_NETWORK=trading-desk-prod" > "$TMP/compose.env"
  real() { "$REAL_DOCKER" compose -p trading-desk-backend-prod --env-file "$TMP/compose.env" -f "$CF" "$@" 2> "$TMP/out"; }
  check "compose: 꺼짐은 backend-prod 하나" "backend-prod" "$(real config --services | sort | paste -sd' ')"
  check "compose: 켜짐은 서비스 3개" "analyst-prod backend-prod collector-prod" "$(real --profile split config --services | sort | paste -sd' ')"
  check "compose: 꺼짐의 backend-prod 역할은 all" 1 "$(real config backend-prod | grep -c 'DESK_ROLE: all')"
  DESK_API_ROLE=api real --profile split config --format json > "$TMP/split.json"
  if command -v jq > /dev/null; then
    check "compose: 켜짐의 역할 · 네트워크" \
      "backend-prod api desk|collector-prod collector default|analyst-prod analyst default" \
      "$(jq -r '["backend-prod","collector-prod","analyst-prod"][] as $s | .services[$s] | "\($s) \(.environment.DESK_ROLE) \(.networks | keys | join(","))"' "$TMP/split.json" | paste -sd'|')"
    check "compose: collector · analyst 는 api 가 healthy 가 된 뒤" "service_healthy service_healthy" \
      "$(jq -r '.services["collector-prod","analyst-prod"].depends_on["backend-prod"].condition' "$TMP/split.json" | paste -sd' ')"
  fi
else
  echo "docker compose 가 없어 compose 파일 검사는 건너뛴다(CI 에서 돈다)"
fi

if [ "$FAILS" = 0 ]; then echo "모두 통과"; else echo "${FAILS}개 실패"; exit 1; fi
