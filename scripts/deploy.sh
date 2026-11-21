#!/usr/bin/env bash
# backend-server(192.168.0.9)에 커밋 하나를 올리고 컨테이너를 다시 띄운다. PC 의 저장소 루트에서 실행한다 (docs/deploy.md).
# 평소 배포는 Jenkins 가 develop 머지 뒤 자동으로 한다(#692). 이 스크립트는 Jenkins · Vault 를 쓸 수 없을 때의 대체 경로다.
# 빌드 · 교체 · 확인 · 실패 시 되돌림은 Jenkins 와 같은 scripts/deploy-compose.sh 가 서버에서 한다. env 는 서버 .env.runtime 을 쓴다.
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
# 여기(PC)서 소스를 보내기 전에 한 번, 서버의 deploy-compose.sh 가 새 이미지를 빌드한 뒤 컨테이너를 바꾸기 직전에 한 번 더 묻는다.
#
# 서버에는 git 체크아웃 · 저장소 키를 두지 않는다. git archive 를 SSH 로 보내 서버에서 이미지를 빌드한다(x86_64).
# 비밀은 서버 ~/deploy/trading-desk/.env.runtime(권한 600)에 있다. Vault 로 옮긴 뒤에는 Vault 가 정본이라,
# 이 경로를 쓰기 전에 .env.runtime 이 Vault 값과 같은지 먼저 맞춘다(docs/deploy.md 3절).
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
SHA=$(git rev-parse "$REF^{commit}")
echo "배포: ${SHA:0:12} → $HOST:~/$DIR ($TARGET)"

# #752 운영 커밋의 정본은 컨테이너 이미지 라벨이다. 서버 src/REVISION 은 마지막 "손 배포" 소스의 표시이고 Jenkins 배포는
# src/ 를 쓰지 않는다. 둘이 다르면(그 사이 Jenkins 가 올렸으면) 한 줄 알린다 — 손 배포가 Jenkins 가 올린 것보다 옛 커밋일 수 있다.
LABEL_FMT='{{index .Config.Labels "org.opencontainers.image.revision"}}'
REVS=$(ssh "$HOST" "echo \"\$(cat ~/$DIR/src/REVISION 2>/dev/null)\"; docker inspect -f $(printf '%q' "$LABEL_FMT") trading-desk-backend-prod 2>/dev/null" || true)
SRC_REV=$(printf '%s\n' "$REVS" | sed -n 1p | tr -d '\r')
RUN_REV=$(printf '%s\n' "$REVS" | sed -n 2p | tr -d '\r')
if [ -n "$RUN_REV" ] && [ "$SRC_REV" != "$RUN_REV" ]; then
  SRC_SHOWN=${SRC_REV:0:12}
  echo "참고: 운영 백엔드는 ${RUN_REV:0:12}(컨테이너 라벨 — Jenkins 배포 포함), 서버 src/REVISION ${SRC_SHOWN:-없음} 는 마지막 손 배포 소스다"
fi

# #431 진행 중인 분석. 백엔드 컨테이너 안의 `desk.cli unfinished` 는 첫 줄에 개수, 다음 줄부터 종목 · 상태 · 시각 · id 를
# 낸다. 컨테이너가 없거나(첫 배포) 옛 이미지라 명령이 없으면 "?" — 확인할 수 없다고 알리고 그대로 진행한다.
# 여기서는 소스를 보내기 전에 미리 묻는다. 빌드한 뒤 컨테이너를 바꾸기 직전의 확인은 서버의 deploy-compose.sh 가 한다.
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
UNFINISHED='docker exec trading-desk-backend-prod python -m desk.cli unfinished 2>/dev/null || echo "?"'
if [ "$TARGET" != web ]; then
  wait_idle "ssh \"\$HOST\" $(printf '%q' "$UNFINISHED")" "$DRY" || exit 3
fi
if [ "$DRY" = 1 ]; then echo "드라이런: 배포하지 않는다"; exit 0; fi
FENCE_REQUIRED=${TD_DEPLOY_FENCE_REQUIRED:-0}
case "$FENCE_REQUIRED" in 0|1) ;; *) echo "TD_DEPLOY_FENCE_REQUIRED 는 0 | 1 이어야 한다" >&2; exit 1 ;; esac

# 원격 스크립트는 인자로, 소스 tar 는 표준입력으로 보낸다.
# deploy-compose.sh 종료 코드: 0 배포됨 · 1 실패(되돌리지 못함) · 2 실패해서 직전 이미지로 되돌림 · 3 진행 중인 분석 때문에 멈춤
REMOTE=$(
cat "$(dirname "$0")/jenkins/backend-fence.sh" || exit 1
cat <<'REMOTE'
set -euo pipefail
cd ~/"$DIR"
test -f .env.runtime || { echo "~/$DIR/.env.runtime 가 없다 (docs/deploy.md 3절)"; exit 1; }

# src may come from a commit that predates the fence. Lock the host before
# switching sources and keep the descriptor open through deployment.
if [ "$TARGET" != web ] && backend_fence_configured; then
  backend_fence_lock
  backend_fence_idle
fi

rm -rf src.new && mkdir src.new && tar -x -C src.new
echo "$SHA" > src.new/REVISION
rm -rf src.old && { [ ! -d src ] || mv src src.old; } && mv src.new src

targets=()
[ "$TARGET" = web ] || targets+=(backend)
[ "$TARGET" = backend ] || targets+=(web)
for t in "${targets[@]}"; do
  rc=0
  ENV_FILE=.env.runtime REVISION="$SHA" FORCE="$FORCE" WAIT_MIN="$WAIT_MIN" bash src/scripts/deploy-compose.sh "$t" src || rc=$?
  if [ "$rc" = 3 ] && [ "$t" = "${targets[0]}" ]; then
    # 아무것도 바꾸지 않았다 — src 를 직전 배포로 되돌린다
    [ ! -d src.old ] || { rm -rf src && mv src.old src; }
  fi
  [ "$rc" = 0 ] || exit "$rc"
done
docker ps --filter name=trading-desk --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
REMOTE
)
git archive --format=tar "$REF" | ssh "$HOST" "TARGET=$TARGET SHA=$SHA DIR=$DIR FORCE=$FORCE WAIT_MIN=$WAIT_MIN TD_DEPLOY_FENCE_REQUIRED=$FENCE_REQUIRED \
  bash -c $(printf '%q' "$REMOTE")"

echo "배포 완료: ${SHA:0:12}"
