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
  shift; while [ "$#" -gt 0 ]; do case "$1" in -p|--env-file|-f) shift 2 ;; *) break ;; esac; done
  sub=$1; shift
  case "$sub" in
    config)
      case "${1:-}" in
        -q) exit 0 ;;
        --images) echo "$FAKE_IMAGE"; echo "$FAKE_IMAGE" ;;
        --services) printf '%s\n' $FAKE_SERVICES ;;
      esac ;;
    ps) svc=${*: -1}; [ -f "$S/ctr/$svc" ] && echo "cid-$svc"; exit 0 ;;
    up)
      svcs=()
      for a in "$@"; do case "$a" in -*|[0-9]*) ;; *) svcs+=("$a") ;; esac; done
      id=$(resolve "$FAKE_IMAGE"); rc=0
      for svc in "${svcs[@]}"; do
        h=healthy; [ "$(label "$id")" != "${FAKE_BAD_REV:-}" ] || { h=unhealthy; rc=1; }
        echo "$id $h" > "$S/ctr/$svc"
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
  "DESK_KIS_APP_KEY='k1'" "DESK_MAX_CONCURRENT_RUNS='1'" > "$ENVF"

export PATH="$TMP/bin:$PATH" STATE="$TMP/state" STABLE_SEC=0 WAIT_MIN=0 ENV_FILE="$ENVF"
export FAKE_IMAGE=trading-desk-backend:prod FAKE_SERVICES=backend-prod

prod_rev() { local id; id=$(cat "$STATE/tags/trading-desk-backend_prod"); cat "$STATE/img/$id.label"; }
ctr_rev() { local id h; read -r id h < "$STATE/ctr/$1"; cat "$STATE/img/$id.label"; }
deploy() { REVISION="$1" bash "$SCRIPT" backend "$SRC" > "$TMP/out" 2>&1; echo $?; }
check() {  # $1 이름, $2 기대, $3 실제
  if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: 기대 '$2' 실제 '$3'"; sed 's/^/     | /' "$TMP/out"; FAILS=$((FAILS + 1)); fi
}

rc=$(deploy rev1); check "첫 배포" "0 rev1" "$rc $(prod_rev)"
check "첫 배포는 rollback 태그가 없다" "" "$(cat "$STATE/tags/trading-desk-backend_rollback" 2>/dev/null)"

rc=$(deploy rev2); check "두 번째 배포" "0 rev2 rev2" "$rc $(prod_rev) $(ctr_rev backend-prod)"
check "직전 이미지를 rollback 으로 남긴다" "rev1" "$(cat "$STATE/img/$(cat "$STATE/tags/trading-desk-backend_rollback").label")"
check "옛 커밋 태그를 지운다" "" "$(ls "$STATE/tags" | grep -F 'rev1' || true)"

rc=$(ROLLBACK_DRILL=1 deploy rev3); check "드릴은 되돌린다" "2 rev2 rev2" "$rc $(prod_rev) $(ctr_rev backend-prod)"
grep -q "ROLLBACK_DRILL" "$TMP/out"; check "드릴을 알린다" 0 $?

rc=$(FAKE_BAD_REV=rev4 deploy rev4); check "health 실패면 되돌린다" "2 rev2 rev2" "$rc $(prod_rev) $(ctr_rev backend-prod)"

FAKE_LOG_SECRET='postgresql://u:s3cr3t-pass@db/x' FAKE_BAD_REV=rev4b deploy rev4b > /dev/null
check "실패 로그에서 env 값을 가린다" "0 1" "$(grep -c 's3cr3t' "$TMP/out") $(grep -c 'db=\*\*\*\*' "$TMP/out")"

rc=$(FAKE_UNFINISHED='2\nAAPL running 10:00 r1\nNVDA queued 10:01 r2' deploy rev5)
check "진행 중인 분석이 있으면 멈추고 운영은 그대로" "3 rev2" "$rc $(prod_rev)"
grep -q "AAPL running" "$TMP/out"; check "진행 중인 분석을 보인다" 0 $?

rc=$(FORCE=1 FAKE_UNFINISHED='1\nAAPL running 10:00 r1' deploy rev5); check "FORCE 면 기다리지 않는다" "0 rev5" "$rc $(prod_rev)"

touch "$SRC/backend/desk/migrations/versions/0099_new.py"
rc=$(ROLLBACK_DRILL=1 deploy rev6); check "새 마이그레이션이면 되돌리지 않는다" "1 rev6" "$rc $(prod_rev)"
grep -q "0099_new.py" "$TMP/out"; check "새 마이그레이션 이름을 보인다" 0 $?

rc=$(FAKE_SERVICES="backend-prod collector-prod analyst-prod" deploy rev7)
check "#688 서비스 3개를 같은 이미지로" "0 rev7 rev7 rev7" "$rc $(ctr_rev backend-prod) $(ctr_rev collector-prod) $(ctr_rev analyst-prod)"
check "api(PRIMARY)를 먼저 올린다" "backend-prod" "$(grep 'compose.* up ' "$STATE/calls" | tail -2 | head -1 | awk '{print $NF}')"

rc=$(FAKE_SERVICES="backend-prod collector-prod analyst-prod" ROLLBACK_DRILL=1 deploy rev8)
check "서비스 3개 모두 되돌린다" "2 rev7 rev7 rev7" "$rc $(ctr_rev backend-prod) $(ctr_rev collector-prod) $(ctr_rev analyst-prod)"

rc=$(PRIMARY_SERVICE=api deploy rev9); check "없는 PRIMARY_SERVICE 는 바꾸기 전에 멈춘다" "1 rev7" "$rc $(prod_rev)"

if [ "$FAILS" = 0 ]; then echo "모두 통과"; else echo "${FAILS}개 실패"; exit 1; fi
