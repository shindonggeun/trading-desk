#!/usr/bin/env bash
# Shared by Jenkins and the current manual deploy entry point. The directory is
# provisioned on the Docker host and mounted at the same path on its deploy node.

backend_fence_configured() {
  [ "${TD_DEPLOY_FENCE_REQUIRED:-0}" = 1 ] || [ -e /var/lib/trading-desk/deploy-fence ] \
    || [ -L /var/lib/trading-desk/deploy-fence ]
}

backend_fence_inherited() {
  [ /proc/self/fd/9 -ef /var/lib/trading-desk/deploy-fence/mutation.lock ]
}

backend_fence_lock() {
  TD_FENCE=/var/lib/trading-desk/deploy-fence
  local lock="$TD_FENCE/mutation.lock" old_umask
  if [[ $TD_FENCE != /* || ! -d $TD_FENCE || $(realpath -e -- "$TD_FENCE") != "$TD_FENCE" \
        || $(stat -c '%a:%u' -- "$TD_FENCE") != "700:$(id -u)" ]]; then
    echo "백엔드 배포 잠금 디렉터리가 없거나 권한이 올바르지 않다" >&2
    return 1
  fi
  if [ -L "$lock" ] || { [ -e "$lock" ] && { [ ! -f "$lock" ] || [ ! -O "$lock" ]; }; }; then
    echo "백엔드 배포 잠금 파일이 올바르지 않다" >&2
    return 1
  fi
  # The manual wrapper and the lease runner can pass the same open descriptor.
  if ! backend_fence_inherited; then
    old_umask=$(umask)
    umask 077
    exec 9<>"$lock"
    umask "$old_umask"
  fi
  flock -w 600 9 || return
  unset DOCKER_CONTEXT DOCKER_TLS_VERIFY DOCKER_CERT_PATH
  export DOCKER_HOST=unix:///var/run/docker.sock
}

backend_fence_idle() {
  if [ -e "$TD_FENCE/active" ] || [ -L "$TD_FENCE/active" ]; then
    echo "활성 백엔드 배포 인계가 있다 — 하위 잡 종료를 확인하기 전에는 새 배포를 시작하지 않는다" >&2
    return 1
  fi
}

backend_fence_route() {
  local split state existing
  split=$(sed -n 's/^DESK_BACKEND_SPLIT=//p' "$ENV_FILE" | tail -n 1) || return 1
  split=${split#\'}; split=${split%\'}; split=${split#\"}; split=${split%\"}
  case "$split" in
    ''|0|false) return 10 ;;
    1|true) ;;
    *) echo "DESK_BACKEND_SPLIT 값이 올바르지 않다" >&2; return 1 ;;
  esac
  if ! state=$(docker inspect -f '{{index .Config.Labels "desk.backend-split"}} {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' \
    trading-desk-backend-prod 2>/dev/null); then
    existing=$(docker ps -aq --filter 'name=^/trading-desk-backend-prod$') || return 1
    if [ -z "$existing" ]; then return 10; fi
    echo "기존 API의 배포 상태를 확인할 수 없다" >&2
    return 1
  fi
  case "$state" in
    '1 running healthy') return 0 ;;
    '1 '*) echo "정상 상태의 split API가 필요하다" >&2; return 1 ;;
    '0 '*) return 10 ;;
    *) echo "기존 API의 split 라벨을 확인할 수 없다" >&2; return 1 ;;
  esac
}
