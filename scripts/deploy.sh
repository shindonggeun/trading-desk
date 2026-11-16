#!/usr/bin/env bash
# backend-server(192.168.0.9)에 커밋 하나를 올리고 컨테이너를 다시 띄운다. PC 의 저장소 루트에서 실행한다 (docs/deploy.md).
#
#   scripts/deploy.sh                 # HEAD
#   scripts/deploy.sh <커밋|태그>
#   scripts/deploy.sh HEAD backend    # 백엔드만 (web | backend | all, 기본 all)
#   scripts/deploy.sh --dry-run ...   # 진행 중인 분석만 확인하고 멈춘다 (#431)
#   scripts/deploy.sh --force ...     # 진행 중인 분석이 있어도 기다리지 않는다 (그 분석은 실패로 끝난다)
#
# 백엔드를 다시 띄우면 진행 중인 분석이 끊긴다(#431). 그래서 백엔드를 바꾸기 전에 서버의 백엔드 컨테이너 안에서
# `python -m desk.cli unfinished` 로 queued/running run 수를 묻고, 있으면 끝날 때까지
# DESK_DEPLOY_WAIT_MIN 분(기본 30) 기다린다. 넘기면 멈춘다 — 그때는 --force 로만 진행.
#
# 서버에는 git 체크아웃 · 저장소 키를 두지 않는다. git archive 를 SSH 로 보내 서버에서 이미지를 빌드한다(x86_64).
# 비밀은 서버 ~/deploy/trading-desk/.env.runtime(권한 600)에만 있다.
set -euo pipefail

HOST="${DESK_DEPLOY_HOST:-homelab-backend-server}"
DIR="${DESK_DEPLOY_DIR:-deploy/trading-desk}"   # 원격 홈 기준
FORCE=0 DRY=0 ARGS=()
for a in "$@"; do
  case "$a" in
    --force) FORCE=1 ;;
    --dry-run) DRY=1 ;;
    -*) echo "모르는 옵션: $a (--force | --dry-run)" >&2; exit 1 ;;
    *) ARGS+=("$a") ;;
  esac
done
REF="${ARGS[0]:-HEAD}"
TARGET="${ARGS[1]:-all}"
WAIT_MIN="${DESK_DEPLOY_WAIT_MIN:-30}"
case "$TARGET" in all|backend|web) ;; *) echo "대상은 all | backend | web" >&2; exit 1 ;; esac

if [ "$REF" = "HEAD" ] && [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "커밋하지 않은 변경은 배포되지 않는다. 먼저 커밋하거나 ref 를 넘긴다" >&2
  exit 1
fi
SHA=$(git rev-parse --short "$REF^{commit}")
echo "배포: $SHA → $HOST:~/$DIR ($TARGET)"

# #431 진행 중인 분석. 백엔드 컨테이너 안의 `desk.cli unfinished` 는 첫 줄에 개수, 다음 줄부터 종목 · 상태 · 시각 · id 를
# 낸다. 컨테이너가 없거나(첫 배포) 옛 이미지라 명령이 없으면 "?" — 확인할 수 없다고 알리고 그대로 진행한다.
# 같은 함수를 PC(빌드 전에 미리)와 서버(새 이미지를 빌드한 뒤 컨테이너를 바꾸기 직전에 다시)에서 쓴다 —
# 빌드하는 몇 분 사이에 새로 제출된 분석도 끊지 않게.
WAIT_FN=$(cat <<'FN'
wait_idle() {  # $1 = 개수를 묻는 명령, $2 = 1 이면 확인만(드라이런). 0 = 진행, 3 = 멈춤
  local deadline=$(( $(date +%s) + WAIT_MIN * 60 )) out n
  while :; do
    out=$(eval "$1") || { echo "서버의 진행 중인 분석을 묻지 못했다(SSH 접속 실패?) — 멈춘다" >&2; return 3; }
    n=${out%%$'\n'*}; n=${n%$'\r'}
    if [ "$n" = "?" ]; then echo "진행 중인 분석을 확인하지 못했다(백엔드 컨테이너 없음 · 옛 이미지) — 그대로 진행"; return 0; fi
    if [ "$n" = 0 ]; then echo "진행 중인 분석 없음"; return 0; fi
    echo "진행 중인 분석 ${n}개 — 백엔드를 다시 띄우면 실패로 끝난다:"
    printf '%s\n' "$out" | tail -n +2 | sed 's/^/  /'
    if [ "${2:-0}" = 1 ]; then echo "드라이런: 여기서 멈춘다"; return 3; fi
    if [ "$FORCE" = 1 ]; then echo "--force: 기다리지 않고 진행"; return 0; fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
      echo "${WAIT_MIN}분을 기다려도 끝나지 않았다. 멈춘다 — 끊어도 되면 --force 로 다시" >&2
      return 3
    fi
    echo "끝나기를 기다린다 (30초마다 확인, 최대 ${WAIT_MIN}분)"
    sleep 30
  done
}
FN
)
eval "$WAIT_FN"
UNFINISHED='docker exec trading-desk-backend-prod python -m desk.cli unfinished 2>/dev/null || echo "?"'
if [ "$TARGET" != web ]; then
  wait_idle "ssh \"\$HOST\" $(printf '%q' "$UNFINISHED")" "$DRY" || exit 3
fi
if [ "$DRY" = 1 ]; then echo "드라이런: 배포하지 않는다"; exit 0; fi

# 원격 스크립트는 인자로, 소스 tar 는 표준입력으로 보낸다
REMOTE=$(cat <<'REMOTE'
set -euo pipefail
cd ~/"$DIR"
test -f .env.runtime || { echo "~/$DIR/.env.runtime 가 없다 (docs/deploy.md 3절)"; exit 1; }
set -a; . ./.env.runtime; set +a
: "${DESK_DOCKER_NETWORK:?.env.runtime 에 DESK_DOCKER_NETWORK}"

rm -rf src.new && mkdir src.new && tar -x -C src.new
echo "$SHA" > src.new/REVISION
rm -rf src.old && { [ ! -d src ] || mv src src.old; } && mv src.new src

docker network inspect "$DESK_DOCKER_NETWORK" >/dev/null 2>&1 || docker network create "$DESK_DOCKER_NETWORK" >/dev/null

if [ "$TARGET" != web ]; then
  backend() { (cd src/backend && docker compose -p trading-desk-backend-prod --env-file ../../.env.runtime \
     -f docker-compose-backend.yml "$@"); }
  backend build backend-prod
  # #431 빌드하는 동안 새로 제출된 분석이 있으면 컨테이너를 바꾸기 전에 다시 기다린다. 멈추면 소스를 되돌린다
  eval "$WAIT_FN"
  if ! wait_idle "$UNFINISHED"; then
    [ ! -d src.old ] || { rm -rf src && mv src.old src; }
    exit 3
  fi
  backend up -d --wait --remove-orphans backend-prod
fi
if [ "$TARGET" != backend ]; then
  (cd src/frontend && docker compose -p trading-desk-web-prod --env-file ../../.env.runtime \
     -f docker-compose-frontend.yml up -d --build --wait --remove-orphans web-prod)
fi
docker image prune -f >/dev/null
docker ps --filter name=trading-desk --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
REMOTE
)
git archive --format=tar "$REF" | ssh "$HOST" "TARGET=$TARGET SHA=$SHA DIR=$DIR FORCE=$FORCE WAIT_MIN=$WAIT_MIN \
  WAIT_FN=$(printf '%q' "$WAIT_FN") UNFINISHED=$(printf '%q' "$UNFINISHED") bash -c $(printf '%q' "$REMOTE")"

echo "배포 완료: $SHA"
