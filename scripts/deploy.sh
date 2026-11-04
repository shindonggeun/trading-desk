#!/usr/bin/env bash
# backend-server(192.168.0.9)에 커밋 하나를 올리고 컨테이너를 다시 띄운다. PC 의 저장소 루트에서 실행한다 (docs/deploy.md).
#
#   scripts/deploy.sh                 # HEAD
#   scripts/deploy.sh <커밋|태그>
#   scripts/deploy.sh HEAD backend    # 백엔드만 (web | backend | all, 기본 all)
#
# 서버에는 git 체크아웃 · 저장소 키를 두지 않는다. git archive 를 SSH 로 보내 서버에서 이미지를 빌드한다(x86_64).
# 비밀은 서버 ~/deploy/trading-desk/.env.runtime(권한 600)에만 있다.
set -euo pipefail

HOST="${DESK_DEPLOY_HOST:-homelab-backend-server}"
DIR="${DESK_DEPLOY_DIR:-deploy/trading-desk}"   # 원격 홈 기준
REF="${1:-HEAD}"
TARGET="${2:-all}"
case "$TARGET" in all|backend|web) ;; *) echo "대상은 all | backend | web" >&2; exit 1 ;; esac

if [ "$REF" = "HEAD" ] && [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "커밋하지 않은 변경은 배포되지 않는다. 먼저 커밋하거나 ref 를 넘긴다" >&2
  exit 1
fi
SHA=$(git rev-parse --short "$REF^{commit}")
echo "배포: $SHA → $HOST:~/$DIR ($TARGET)"

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
  (cd src/backend && docker compose -p trading-desk-backend-prod --env-file ../../.env.runtime \
     -f docker-compose-backend.yml up -d --build --wait --remove-orphans backend-prod)
fi
if [ "$TARGET" != backend ]; then
  (cd src/frontend && docker compose -p trading-desk-web-prod --env-file ../../.env.runtime \
     -f docker-compose-frontend.yml up -d --build --wait --remove-orphans web-prod)
fi
docker image prune -f >/dev/null
docker ps --filter name=trading-desk --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
REMOTE
)
git archive --format=tar "$REF" | ssh "$HOST" "TARGET=$TARGET SHA=$SHA DIR=$DIR bash -c $(printf '%q' "$REMOTE")"

echo "배포 완료: $SHA"
