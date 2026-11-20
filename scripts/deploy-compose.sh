#!/usr/bin/env bash
# 받아 둔 소스로 이미지를 빌드하고 compose 서비스를 바꾼 뒤 확인한다. 확인이 실패하면 직전 이미지로 되돌린다 (docs/deploy.md 2절).
# 배포 호스트(backend-server)에서 돈다. Jenkins(Jenkinsfile.deploy-common.groovy)와 손 배포(scripts/deploy.sh)가 같은 이 스크립트를 부른다.
#
#   ENV_FILE=<env 파일> REVISION=<커밋 SHA> scripts/deploy-compose.sh <backend|web> [소스 루트, 기본 이 저장소]
#
#   ENV_FILE         compose --env-file. Jenkins 는 Vault 에서 받은 임시 파일(600), 손 배포는 ~/deploy/trading-desk/.env.runtime
#   REVISION         배포할 커밋(40자 SHA). 이미지 라벨 org.opencontainers.image.revision 에 남기고, 확인 단계가 컨테이너의 이 값을 본다
#   PRIMARY_SERVICE  backend 에서 먼저 올릴 서비스. 기동 시 Alembic upgrade head 를 하는 api 다(기본 backend-prod).
#                    compose 의 나머지 서비스(#688 collector · analyst)는 같은 이미지로 그다음에 올린다
#   FORCE=1          진행 중인 분석을 기다리지 않는다(#431). 그 분석은 실패로 끝난다
#   WAIT_MIN         진행 중인 분석을 기다릴 최대 분(기본 30)
#   STABLE_SEC       기동 뒤 재시작 없이 버텨야 하는 초(기본 30)
#   WAIT_TIMEOUT     compose up --wait 상한 초(기본 300)
#   ROLLBACK_DRILL=1 확인을 일부러 실패시켜 되돌림을 시험한다
#
# 백엔드 나누기(#688, docs/deploy.md 12절): env 파일의 DESK_BACKEND_SPLIT=1 이면 compose profile split(collector · analyst)을 켜고
# backend-prod 를 api 역할로 띄운다. 0(기본)이면 backend-prod 하나가 all 이고, 남아 있는 collector · analyst 는 api 를 바꾸기 전에 지운다.
#
# 종료 코드: 0 배포됨 · 1 실패, 되돌리지 못함(사람이 본다) · 2 실패해서 직전 이미지로 되돌림 · 3 진행 중인 분석 때문에 멈춤(운영은 그대로)
#
# 이미지 태그: <이름>:<커밋 12자> 로 빌드 → 지금 운영 이미지를 <이름>:rollback 으로 남김 → <이름>:prod 로 바꿔 compose 를 다시 띄운다.
# 손으로 되돌릴 때: docker tag <이름>:rollback <이름>:prod 후 compose up -d --no-build --force-recreate.
#
# 값은 출력하지 않는다. 실패 때 찍는 컨테이너 로그는 env 파일의 값을 가린 뒤 찍는다.
set +x
set -euo pipefail

TARGET="${1:?대상: backend | web}"
ROOT="${2:-$(cd "$(dirname "$0")/.." && pwd)}"
: "${ENV_FILE:?ENV_FILE (compose --env-file)}" "${REVISION:?REVISION (배포할 커밋 SHA)}"
[ -r "$ENV_FILE" ] || { echo "ENV_FILE 를 읽을 수 없다: $ENV_FILE" >&2; exit 1; }
ENV_FILE="$(cd "$(dirname "$ENV_FILE")" && pwd)/$(basename "$ENV_FILE")"
ROOT="$(cd "$ROOT" && pwd)"
WAIT_MIN="${WAIT_MIN:-30}" STABLE_SEC="${STABLE_SEC:-30}" WAIT_TIMEOUT="${WAIT_TIMEOUT:-300}"
FORCE="${FORCE:-0}" DRILL="${ROLLBACK_DRILL:-0}"
LABEL=org.opencontainers.image.revision

case "$TARGET" in
  backend) DIR=backend; FILE=docker-compose-backend.yml; DOCKERFILE=backend.Dockerfile
           PROJECT=trading-desk-backend-prod; PRIMARY="${PRIMARY_SERVICE:-backend-prod}" ;;
  web)     DIR=frontend; FILE=docker-compose-frontend.yml; DOCKERFILE=frontend.Dockerfile
           PROJECT=trading-desk-web-prod; PRIMARY=web-prod ;;
  *) echo "대상은 backend | web" >&2; exit 1 ;;
esac

PROFILE=() SPLIT=0 SPLIT_SERVICES=(collector-prod analyst-prod)
# -p 를 빼지 않는다 — 빠지면 디렉터리 이름이 프로젝트가 돼 --remove-orphans 가 남의 컨테이너를 지울 수 있다
compose() { docker compose -p "$PROJECT" --env-file "$ENV_FILE" ${PROFILE[@]+"${PROFILE[@]}"} -f "$ROOT/$DIR/$FILE" "$@"; }
cid() { compose ps -a -q "$1" 2>/dev/null | head -n 1; }
rev_of() { docker inspect -f "{{index .Config.Labels \"$LABEL\"}}" "$1" 2>/dev/null || true; }
env_value() {  # env 파일의 값 하나(따옴표를 벗긴다). 파일을 셸로 읽지 않는다
  local v
  v=$(sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1)
  v=${v#\'}; v=${v%\'}; v=${v#\"}; v=${v%\"}
  printf '%s' "$v"
}
mask() {  # 표준입력의 텍스트에서 env 파일 값을 가린다. 8자 이상이거나 비밀처럼 보이는 키의 값.
  # 긴 값부터 가린다 — 짧은 값이 긴 값의 일부면 짧은 쪽을 먼저 가렸을 때 긴 값의 나머지가 드러난다.
  # 앱이 런타임에 받은 토큰(토스 · KIS · ChatGPT) 같은 파생 비밀은 가리지 못한다 — 앱이 로그에 쓰지 않는 것이 규칙이다(security.md §5)
  local text line k v
  local -a vals=()
  text=$(cat)
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|\#*) continue ;; esac
    k=${line%%=*}
    [ "$k" != "$line" ] || continue
    v=${line#*=}
    v=${v#\'}; v=${v%\'}; v=${v#\"}; v=${v%\"}
    [ -n "$v" ] || continue
    if [ "${#v}" -ge 8 ] || [[ $k =~ (KEY|SECRET|TOKEN|PASSWORD|CHAT_ID|DATABASE_URL) ]]; then
      vals+=("${#v} $v")
    fi
  done < "$ENV_FILE"
  if [ "${#vals[@]}" -gt 0 ]; then
    while IFS= read -r line; do
      v=${line#* }
      text=${text//"$v"/****}
    done < <(printf '%s\n' "${vals[@]}" | sort -rn)
  fi
  printf '%s\n' "$text"
}

echo "== $TARGET: ${REVISION:0:12} ($PROJECT)"
# #688 스위치 하나로 profile 과 api 의 역할을 함께 정한다. 셸 환경의 DESK_API_ROLE 이 env 파일보다 앞선다(compose 보간 규칙)
if [ "$TARGET" = backend ]; then
  case "$(env_value DESK_BACKEND_SPLIT)" in
    ''|0|false) SPLIT=0 ;;
    1|true) SPLIT=1 ;;
    *) echo "DESK_BACKEND_SPLIT 는 0 | 1 이어야 한다" >&2; exit 1 ;;
  esac
  if [ "$SPLIT" = 1 ]; then PROFILE=(--profile split); export DESK_API_ROLE=api; else export DESK_API_ROLE=all; fi
  echo "백엔드 나누기: $([ "$SPLIT" = 1 ] && echo '켜짐 — api · collector · analyst' || echo '꺼짐 — all 하나')"
fi
# 필수 키(\${KEY:?})가 비면 여기서 멈춘다. -q 라 값은 출력하지 않는다
compose config -q

mapfile -t IMAGES < <(compose config --images | sort -u)
[ "${#IMAGES[@]}" = 1 ] || { echo "서비스들이 이미지 하나를 함께 써야 한다: ${IMAGES[*]}" >&2; exit 1; }
IMAGE=${IMAGES[0]}          # trading-desk-backend:prod
REPO=${IMAGE%:*}
SHORT=${REVISION:0:12}
mapfile -t SERVICES < <(compose config --services)
printf '%s\n' "${SERVICES[@]}" | grep -qxF "$PRIMARY" || { echo "compose 에 $PRIMARY 서비스가 없다 (PRIMARY_SERVICE)" >&2; exit 1; }
OTHERS=()
for s in "${SERVICES[@]}"; do [ "$s" = "$PRIMARY" ] || OTHERS+=("$s"); done
echo "서비스: $PRIMARY ${OTHERS[*]}"
if [ "$TARGET" = backend ]; then     # 스위치와 compose 가 어긋나면(서비스 이름이 바뀜 등) 바꾸기 전에 멈춘다
  for s in "${SPLIT_SERVICES[@]}"; do
    has=0; printf '%s\n' "${SERVICES[@]}" | grep -qxF "$s" && has=1
    [ "$has" = "$SPLIT" ] || { echo "DESK_BACKEND_SPLIT=$SPLIT 와 compose 가 어긋난다: $s 서비스 $([ "$has" = 1 ] && echo 있음 || echo 없음)" >&2; exit 1; }
  done
fi

NET=$(env_value DESK_DOCKER_NETWORK)
[ -n "$NET" ] || { echo "env 파일에 DESK_DOCKER_NETWORK 가 없다" >&2; exit 1; }
docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null

PREV_CID=$(cid "$PRIMARY")
PREV_IMAGE='' PREV_REV=''
if [ -n "$PREV_CID" ]; then
  PREV_IMAGE=$(docker inspect -f '{{.Image}}' "$PREV_CID")
  PREV_REV=$(rev_of "$PREV_CID")
  echo "지금 운영: ${PREV_REV:-(커밋 라벨 없음 — 옛 이미지)}"
else
  echo "지금 운영 컨테이너 없음(첫 배포) — 실패해도 되돌릴 이미지가 없다"
fi

echo "== 이미지 빌드 $REPO:$SHORT"
docker build -f "$ROOT/$DIR/$DOCKERFILE" -t "$REPO:$SHORT" --label "$LABEL=$REVISION" "$ROOT/$DIR"

# 새 Alembic 리비전이 들어간 배포는 자동으로 되돌리지 않는다. 옛 이미지는 기동 시 upgrade head 에서 모르는 리비전을 만나 뜨지 못한다.
MIGRATES=0
versions() {
  docker run --rm --network none --entrypoint python "$1" -c \
    'import pathlib, desk.migrations.versions as v; print("\n".join(sorted(p.name for p in pathlib.Path(v.__file__).parent.glob("*.py") if p.name != "__init__.py")))'
}
if [ "$TARGET" = backend ] && [ -n "$PREV_IMAGE" ]; then
  if old=$(versions "$PREV_IMAGE") && new=$(versions "$REPO:$SHORT"); then
    added=$(comm -13 <(printf '%s\n' "$old") <(printf '%s\n' "$new") | grep -v '^$' || true)
    if [ -n "$added" ]; then
      MIGRATES=1
      echo "새 마이그레이션: ${added//$'\n'/ } — 실패하면 자동으로 되돌리지 않는다"
    fi
  else
    MIGRATES=1
    echo "마이그레이션 목록을 비교하지 못했다 — 실패하면 자동으로 되돌리지 않는다"
  fi
fi

# #431 백엔드를 다시 띄우면 진행 중인 분석이 끊긴다. 빌드가 끝난 지금(컨테이너를 바꾸기 직전) 묻고, 있으면 기다린다
wait_idle() {
  [ "$TARGET" = backend ] || return 0
  [ -n "$PREV_CID" ] || return 0
  local deadline out n
  deadline=$(( $(date +%s) + WAIT_MIN * 60 ))
  while :; do
    out=$(docker exec "$PREV_CID" python -m desk.cli unfinished 2>/dev/null) || out="?"
    n=${out%%$'\n'*}; n=${n%$'\r'}
    if [ "$n" = "?" ]; then echo "진행 중인 분석을 확인하지 못했다(컨테이너가 멈춤 · 옛 이미지) — 그대로 진행"; return 0; fi
    if [ "$n" = 0 ]; then echo "진행 중인 분석 없음"; return 0; fi
    echo "진행 중인 분석 ${n}개 — 백엔드를 다시 띄우면 실패로 끝난다:"
    printf '%s\n' "$out" | tail -n +2 | sed 's/^/  /'
    if [ "$FORCE" = 1 ]; then echo "FORCE: 기다리지 않고 진행"; return 0; fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
      echo "${WAIT_MIN}분을 기다려도 끝나지 않았다. 멈춘다 — 운영은 그대로다. 끊어도 되면 FORCE 로 다시" >&2
      return 3
    fi
    echo "끝나기를 기다린다 (30초마다 확인, 최대 ${WAIT_MIN}분)"
    sleep 30
  done
}
wait_idle || exit 3

# #688 스위치를 끈 배포: 남은 collector · analyst 를 api 가 all 로 뜨기 전에 지운다 — all 과 collector 가 함께 떠 브로커 토큰을
# 둘이 발급하지 않게. 꺼진 profile 의 컨테이너는 --remove-orphans 로 지워진다고 기대하지 않는다
drop_split() {
  [ "$TARGET" = backend ] && [ "$SPLIT" = 0 ] || return 0
  local s ids
  for s in "${SPLIT_SERVICES[@]}"; do
    ids=$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT" --filter "label=com.docker.compose.service=$s")
    [ -n "$ids" ] || continue
    echo "백엔드 나누기를 껐다 — $s 를 멈추고 지운다"
    printf '%s\n' "$ids" | xargs docker rm -f >/dev/null
  done
}
drop_split

up() { compose up -d --no-build --force-recreate --wait --wait-timeout "$WAIT_TIMEOUT" "$@"; }
# api 를 먼저(Alembic upgrade head), 나머지는 그다음. --no-deps: depends_on 으로 api 를 한 번 더 다시 만들지 않는다
up_all() { up --remove-orphans "$PRIMARY" && { [ "${#OTHERS[@]}" = 0 ] || up --no-deps "${OTHERS[@]}"; }; }

probe() {  # 컨테이너 안에서 본다 — 백엔드는 호스트 포트를 열지 않는다
  local c
  c=$(cid "$PRIMARY")
  if [ "$TARGET" = backend ]; then
    docker exec "$c" python -c "import json, urllib.request; r = urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=5); assert json.load(r).get('ok') is True"
  else
    docker exec "$c" wget -q -O /dev/null http://127.0.0.1/
  fi
}

verify() {  # $1 = 기대 커밋(비면 보지 않음), $2 = 1 이면 드릴(일부러 실패)
  local want=$1 drill=${2:-0} s c got state
  declare -A before=()
  for s in "${SERVICES[@]}"; do
    c=$(cid "$s")
    [ -n "$c" ] || { echo "확인 실패: $s 컨테이너가 없다"; return 1; }
    got=$(rev_of "$c")
    if [ -n "$want" ] && [ "$got" != "$want" ]; then echo "확인 실패: $s 커밋이 ${got:-(없음)} — 기대 $want"; return 1; fi
    before[$s]=$(docker inspect -f '{{.RestartCount}}' "$c")
  done
  probe || { echo "확인 실패: $PRIMARY 상태 확인(health) 응답이 없다"; return 1; }
  echo "기동 확인 — ${STABLE_SEC}초 동안 재시작 없이 버티는지 본다"
  sleep "$STABLE_SEC"
  for s in "${SERVICES[@]}"; do
    c=$(cid "$s")
    state=$(docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} {{.RestartCount}}' "$c")
    case "$state" in
      "running healthy ${before[$s]}"|"running none ${before[$s]}") ;;
      *) echo "확인 실패: $s 상태 $state (재시작 전 ${before[$s]})"; return 1 ;;
    esac
  done
  probe || { echo "확인 실패: $PRIMARY 상태 확인(health) 응답이 없다"; return 1; }
  if [ "$drill" = 1 ]; then echo "ROLLBACK_DRILL: 확인을 일부러 실패시킨다"; return 1; fi
  return 0
}

show_logs() {
  local s c
  for s in "${SERVICES[@]}"; do
    c=$(cid "$s"); [ -n "$c" ] || continue
    echo "---- $s 로그 (마지막 60줄, env 값은 가림)"
    docker logs --tail 60 "$c" 2>&1 | mask || echo "(로그를 읽지 못했다)"
  done
}

rollback() {
  if [ -z "$PREV_IMAGE" ]; then echo "되돌릴 직전 이미지가 없다(첫 배포)."; return 1; fi
  if [ "$MIGRATES" = 1 ]; then
    echo "새 마이그레이션이 들어간 배포라 자동으로 되돌리지 않는다 — docs/deploy.md 2절 '되돌림'"
    return 1
  fi
  echo "== 되돌림: $REPO:rollback(${PREV_REV:-커밋 라벨 없음}) → $IMAGE"
  docker tag "$REPO:rollback" "$IMAGE"
  up_all && verify "$PREV_REV" 0
}

echo "== 교체: $IMAGE ← $REPO:$SHORT"
[ -z "$PREV_IMAGE" ] || docker tag "$PREV_IMAGE" "$REPO:rollback"
docker tag "$REPO:$SHORT" "$IMAGE"
if up_all && verify "$REVISION" "$DRILL"; then
  # 지금 것 · 직전 것만 남기고 이 이름의 옛 커밋 태그를 지운다. 층은 대부분 공유라 남는 공간은 작다
  docker images --format '{{.Repository}}:{{.Tag}}' "$REPO" \
    | grep -vxF -e "$IMAGE" -e "$REPO:rollback" -e "$REPO:$SHORT" -e "$REPO:<none>" \
    | xargs -r docker rmi >/dev/null 2>&1 || true
  docker image prune -f >/dev/null 2>&1 || true
  docker ps --filter "label=com.docker.compose.project=$PROJECT" --format 'table {{.Names}}\t{{.Status}}'
  echo "== 배포됨: $TARGET ${REVISION:0:12}"
  exit 0
fi

echo "== 배포 확인 실패: $TARGET ${REVISION:0:12}"
show_logs
if rollback; then
  echo "== 직전 이미지로 되돌렸다(${PREV_REV:-커밋 라벨 없음}). 실패한 이미지는 $REPO:$SHORT 로 남아 있다"
  exit 2
fi
echo "== 되돌리지 못했다 — 운영 상태를 사람이 봐야 한다 (docs/deploy.md 2절 '되돌림')" >&2
exit 1
