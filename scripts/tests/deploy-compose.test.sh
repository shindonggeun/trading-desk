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
resolve() {
  case "$1" in
    img-*) printf '%s' "$1" ;;
    sha256:*) printf 'img-%d' "0x${1#sha256:}" ;;
    *) cat "$S/tags/$(key "$1")" 2>/dev/null ;;
  esac
}
digest() { printf 'sha256:%064x\n' "${1#img-}"; }
label() { cat "$S/img/$1.label" 2>/dev/null || true; }
echo "docker $*" >> "$S/calls"
if [ "$1" = compose ]; then
  prof='' source='' override=''
  shift
  while [ "$#" -gt 0 ]; do
    case "$1" in
      -p|--env-file) shift 2 ;;
      -f) if [ -z "$source" ]; then source=$2; else override=$2; fi; shift 2 ;;
      --profile) prof=$2; shift 2 ;;
      *) break ;;
    esac
  done
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
      for a in "$@"; do case "$a" in -*|[0-9]*|never) ;; *) svcs+=("$a") ;; esac; done
      image=$FAKE_IMAGE
      if [ -n "$override" ]; then
        [ "${#svcs[@]}" = 1 ] || exit 1
        grep -qx "  ${svcs[0]}:" "$override" || exit 1
        image=$(awk '/image:/ {print $2}' "$override")
      fi
      id=$(resolve "$image"); [ -n "$id" ] || exit 1; rc=0
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
      *desk.backend-split*) if [ "$(cat "$S/role/$svc")" = api ]; then echo "1 running $h"; else echo "0 running $h"; fi ;;
      *Labels*) label "$id" ;;
      '{{.Image}}') digest "$id" ;;
      '{{.Image}} '*State.Health.Status*) printf '%s running %s\n' "$(digest "$id")" "$h" ;;
      '{{.RestartCount}}') echo 0 ;;
      *State.Health.Status*RestartCount*) echo "running $h 0" ;;
      *State.Health.Status*) echo "running $h" ;;
      *State.Status*) echo "running $h 0" ;;
    esac ;;
  image) id=$(resolve "${*: -1}") || exit 1; printf '%s %s\n' "$(digest "$id")" "$(label "$id")" ;;
  build)
    shift; while [ "$#" -gt 1 ]; do case "$1" in -t) t=$2; shift 2 ;; --label) l=${2#*=}; shift 2 ;; *) shift ;; esac; done
    id="img-$(( $(ls "$S/img" | wc -l) + 1 ))"
    echo "$l" > "$S/img/$id.label"
    if [ -d "$1/desk/migrations/versions" ]; then
      ls "$1/desk/migrations/versions" > "$S/img/$id.migs"
    fi
    echo "$id" > "$S/tags/$(key "$t")" ;;
  tag) id=$(resolve "$2"); [ -n "$id" ] || exit 1; echo "$id" > "$S/tags/$(key "$3")" ;;
  run) img=${*: -3:1}; cat "$S/img/$(resolve "$img").migs" ;;
  exec)
    svc=${2#cid-}; read -r id h < "$S/ctr/$svc"
    case "$*" in
      *MigrationContext*) [ "${FAKE_SCHEMA_UNAPPLIED:-}" != "$svc" ] || exit 1 ;;
      *unfinished*) printf '%b\n' "${FAKE_UNFINISHED:-0}" ;;
      *api/health*|*wget*http://127.0.0.1/*) [ "$(label "$id")" != "${FAKE_PROBE_BAD_REV:-}" ] || exit 1 ;;
      *) exit 0 ;;
    esac ;;
  logs)
    echo "boot ok, db=${FAKE_LOG_SECRET:-none}"
    if [ "${FAKE_TOKEN_ISSUE:-}" = 1 ]; then echo 'toss token issued (collector)'; fi ;;
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

# ---- #776 준비된 커밋 이미지와 서비스 단독 교체
one() {
  local revision=$1; shift
  ENV_FILE="$SPLITF" REVISION="$revision" bash "$SCRIPT" backend "$SRC" "$@" > "$TMP/out" 2>&1
  echo $?
}
current_revs() { echo "$(ctr_rev backend-prod) $(ctr_rev collector-prod) $(ctr_rev analyst-prod)"; }
up_services() { awk '/^docker compose .* up / {print $NF}' "$STATE/calls" | paste -sd ' ' -; }

stable_sha=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
next_sha=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
schema_sha=cccccccccccccccccccccccccccccccccccccccc

rc=$(one "$stable_sha" --service collector)
check "대상 이미지가 없으면 서비스에서 빌드하지 않는다" "1 rev9" "$rc $(ctr_rev backend-prod)"
rc=$(ENV_FILE="$SPLITF" deploy rev11)
check "단독 모드 시험의 기존 split 배포" "0 rev11 rev11 rev11" "$rc $(current_revs)"
: > "$STATE/calls"
rc=$(one "$stable_sha" --build-only)
retry=$(one "$stable_sha" --build-only)
check "전체 SHA 이미지는 재실행해도 한 번만 준비한다" "0 0 1" "$rc $retry $(grep -c '^docker build ' "$STATE/calls")"
check "이미지 준비는 컨테이너를 교체하지 않는다" "" "$(up_services)"

: > "$STATE/calls"
stable_id=$(printf 'sha256:%064x' "$(sed 's/^img-//' "$STATE/tags/trading-desk-backend_$stable_sha")")
wrong_id=$(printf 'sha256:%064x' 0)
rc=$(one "$stable_sha" --service api --image-id "$wrong_id")
check "전달한 이미지 ID가 일치하지 않으면 교체·스키마 조회 전에 멈춘다" \
  "1 0 rev11 rev11 rev11" "$rc $(grep -cE '^docker (compose .* up |exec )' "$STATE/calls") $(current_revs)"
rc=$(one "$stable_sha" --service api --image-id)
check "전달할 이미지 ID가 비어 있으면 거부한다" "1 " "$rc $(up_services)"
rc=$(one "$stable_sha" --service api --image-id "$stable_id")
check "API만 같은 스키마로 교체한다" "0 $stable_sha rev11 rev11" "$rc $(current_revs)"
check "API 교체에서 의존 서비스를 올리거나 분석을 기다리지 않는다" "backend-prod 0" "$(up_services) $(grep -c unfinished "$STATE/calls")"
check "대상 이미지는 로컬 ID로 고정하고 pull과 의존성 기동을 막는다" 1 "$(grep -c -- '--no-deps --pull never backend-prod$' "$STATE/calls")"
check "단독 교체는 다시 빌드하거나 공유 태그를 이동하지 않는다" "0 rev11" "$(grep -cE '^docker (build|tag) ' "$STATE/calls") $(prod_rev)"

: > "$STATE/calls"
rc=$(one "$stable_sha" --service api)
check "정상 컨테이너에 같은 이미지가 올라 있으면 건너뛴다" "0 " "$rc $(up_services)"
rc=$(FAKE_SCHEMA_UNAPPLIED=backend-prod one "$stable_sha" --service collector)
check "API 적용 상태를 증명할 수 없으면 worker를 교체하지 않는다" "1 " "$rc $(up_services)"
rc=$(one "$stable_sha" --service collector)
check "같은 스키마의 collector만 교체한다" "0 collector-prod $stable_sha $stable_sha rev11" "$rc $(up_services) $(current_revs)"

: > "$STATE/calls"
rc=$(FAKE_UNFINISHED=1 one "$stable_sha" --service analyst)
check "analyst는 분석을 기다리고 시간이 다 되면 그대로 둔다" "3 rev11" "$rc $(ctr_rev analyst-prod)"
rc=$(one "$stable_sha" --service analyst)
check "분석이 없으면 analyst만 교체한다" "0 analyst-prod $stable_sha" "$rc $(up_services) $(ctr_rev analyst-prod)"

one "$next_sha" --build-only >/dev/null
: > "$STATE/calls"
rc=$(FAKE_PROBE_BAD_REV="$next_sha" one "$next_sha" --service api)
check "같은 스키마의 API 검증 실패 시 API만 복구한다" \
  "2 backend-prod backend-prod $stable_sha $stable_sha $stable_sha" "$rc $(up_services) $(current_revs)"
: > "$STATE/calls"
rc=$(FAKE_PROBE_BAD_REV="$next_sha" one "$next_sha" --service collector)
check "health 실패 시 collector만 이전 이미지로 복구한다" \
  "2 collector-prod collector-prod $stable_sha $stable_sha $stable_sha" "$rc $(up_services) $(current_revs)"
: > "$STATE/calls"
rc=$(FAKE_TOKEN_ISSUE=1 one "$next_sha" --service collector)
check "collector 신규 토큰 발급 시 이전 이미지로 복구한다" \
  "2 collector-prod collector-prod $stable_sha" "$rc $(up_services) $(ctr_rev collector-prod)"

touch "$SRC/backend/desk/migrations/versions/0100_next.py"
one "$schema_sha" --build-only >/dev/null
: > "$STATE/calls"
rc=$(one "$schema_sha" --service analyst)
check "스키마가 다르면 API 선행 전 worker를 교체하지 않는다" "1 " "$rc $(up_services)"
rc=$(one "$schema_sha" --service api)
check "새 스키마는 API에서 먼저 적용한다" "0 backend-prod $schema_sha $stable_sha $stable_sha" \
  "$rc $(up_services) $(current_revs)"
: > "$STATE/calls"
rc=$(ROLLBACK_DRILL=1 one "$schema_sha" --service collector)
check "이전 worker의 스키마가 호환되지 않으면 자동 복구하지 않는다" \
  "1 collector-prod $schema_sha $schema_sha $stable_sha" "$rc $(up_services) $(current_revs)"

touch "$SRC/backend/desk/migrations/versions/0101_later.py"
later_sha=dddddddddddddddddddddddddddddddddddddddd
one "$later_sha" --build-only >/dev/null
: > "$STATE/calls"
rc=$(FAKE_BAD_REV="$later_sha" one "$later_sha" --service api)
check "새 스키마 API가 실패하면 API를 자동 복구하지 않는다" "1 backend-prod $later_sha" "$rc $(up_services) $(ctr_rev backend-prod)"
: > "$STATE/calls"
rc=$(one "$later_sha" --service analyst)
check "API가 실패한 뒤 worker를 교체하지 않는다" "1 " "$rc $(up_services)"
: > "$STATE/calls"
rc=$(REVISION="$schema_sha" bash "$SCRIPT" backend "$SRC" --service api > "$TMP/out" 2>&1; echo $?)
check "split이 꺼져 있으면 단독 전환을 막는다" "1 " "$rc $(up_services)"

rc=$(PRIMARY_SERVICE=api deploy rev11); check "없는 PRIMARY_SERVICE 는 바꾸기 전에 멈춘다" "1 rev11" "$rc $(prod_rev)"

# ---- #692 웹 종합 배포: compose up은 성공하고 새 이미지의 HTTP 확인만 실패한다.
WEB_STATE="$TMP/web-state"
deploy_web() {
  STATE="$WEB_STATE" FAKE_IMAGE=trading-desk-web:prod FAKE_SERVICES=web-prod \
    REVISION="$1" bash "$SCRIPT" web "$SRC" > "$TMP/out" 2>&1
  echo $?
}

rc=$(deploy_web web1)
check "웹 복구 시험의 직전 배포" 0 "$rc"
previous_web_image=$(cat "$WEB_STATE/tags/trading-desk-web_prod")
: > "$WEB_STATE/calls"
rc=$(FAKE_PROBE_BAD_REV=web2 deploy_web web2)
check "웹 HTTP 확인 실패는 종료 2와 직전 이미지 ID를 남긴다" \
  "2 $previous_web_image $previous_web_image healthy" \
  "$rc $(cat "$WEB_STATE/tags/trading-desk-web_prod") $(cat "$WEB_STATE/ctr/web-prod")"
web_steps=$(awk '
  /^docker compose .* up / {print "up:" $NF}
  /^docker exec cid-web-prod wget / {print "probe:web-prod"}
' "$WEB_STATE/calls" | paste -sd ' ' -)
check "웹 교체, HTTP 실패, 같은 서비스 복구와 재확인 순서" \
  "up:web-prod probe:web-prod up:web-prod probe:web-prod probe:web-prod" "$web_steps"
check "웹 HTTP 실패와 직전 커밋 복구를 차례로 기록한다" \
  $'확인 실패: web-prod 상태 확인(health) 응답이 없다\n== 직전 이미지로 되돌렸다(web1). 실패한 이미지는 trading-desk-web:web2 로 남아 있다' \
  "$(grep -E '확인 실패: web-prod|직전 이미지로 되돌렸다' "$TMP/out")"

# ---- 실제 compose 파일(#688): 진짜 docker compose 가 있을 때(CI)만. 스위치에 따라 서비스 · api 역할이 맞게 풀리는지
if [ -n "$REAL_DOCKER" ] && "$REAL_DOCKER" compose version > /dev/null 2>&1; then
  CF="$HERE/../../backend/docker-compose-backend.yml"
  printf '%s\n' "DESK_DATABASE_URL=postgresql://u:p@db/x" "DESK_DOCKER_NETWORK=trading-desk-prod" > "$TMP/compose.env"
  real() { "$REAL_DOCKER" compose -p trading-desk-backend-prod --env-file "$TMP/compose.env" -f "$CF" "$@" 2> "$TMP/out"; }
  check "compose: 꺼짐은 backend-prod 하나" "backend-prod" "$(real config --services | sort | paste -sd' ')"
  check "compose: 켜짐은 서비스 3개" "analyst-prod backend-prod collector-prod" "$(real --profile split config --services | sort | paste -sd' ')"
  check "compose: 꺼짐의 backend-prod 역할은 all" 1 "$(real config backend-prod | grep -c 'DESK_ROLE: all')"
  pinned="sha256:${stable_sha}${stable_sha:0:24}"
  printf 'services:\n  collector-prod:\n    image: %s\n' "$pinned" > "$TMP/service.yml"
  check "compose: overlay는 대상 서비스 이미지 ID만 바꾼다" \
    "$pinned trading-desk-backend:prod" \
    "$(real -f "$TMP/service.yml" --profile split config --images | sort -u | paste -sd' ')"
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
