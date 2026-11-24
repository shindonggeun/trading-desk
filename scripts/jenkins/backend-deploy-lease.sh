#!/usr/bin/env bash
# Never print the env snapshot. The active directory is intentionally durable:
# an unconfirmed Jenkins interruption leaves a fence for the operator.
set +x
set -euo pipefail
umask 077
# shellcheck source-path=SCRIPTDIR
source "$(dirname "$0")/backend-fence.sh"

validate_release() {
  [[ ${TD_PARENT_BUILD:-} =~ ^[1-9][0-9]*$ && ${REVISION:-} =~ ^[0-9a-f]{40}$ \
      && ${TD_NODE:-} =~ ^[a-zA-Z0-9_.-]{1,128}$ ]]
}

resolve_roles() {
  local requested=$1 calculated role list=''
  case "$requested" in
    all|api,collector,analyst)
      echo '백엔드 배포 대상: all (explicit-all)' >&2
      echo api,collector,analyst
      return
      ;;
    auto|api|collector|analyst) ;;
    *) return 1 ;;
  esac
  # The running revisions are read while this process still owns the host fence.
  if ! calculated=$(bash scripts/jenkins/backend-deploy-targets.sh); then
    echo '백엔드 배포 대상: all (selector-failed)' >&2
    calculated=all
  fi
  case "$calculated" in
    none|api|collector|analyst|api,collector|api,analyst|collector,analyst) ;;
    all) echo api,collector,analyst; return ;;
    *)
      echo '백엔드 배포 대상: all (invalid-selection)' >&2
      echo api,collector,analyst
      return
      ;;
  esac
  for role in api collector analyst; do
    if [[ $requested == "$role" || ,$calculated, == *,"$role",* ]]; then
      list="${list:+$list,}$role"
    fi
  done
  echo "${list:-none}"
}

backend_fence_lock
ticket="$TD_FENCE/active"
case "${1:-}" in
  route)
    backend_fence_idle
    backend_fence_route
    ;;
  begin)
    backend_fence_idle
    backend_fence_route || { echo "분리 배포 준비 중 API 경로가 바뀌었다" >&2; exit 1; }
    validate_release
    [[ ${TD_FORCE_RECREATE:-} =~ ^[01]$ && ${FORCE:-} =~ ^[01]$ && ${ROLLBACK_DRILL:-} =~ ^[01]$ ]]
    roles=$(resolve_roles "${TD_ROLES:-}") || exit 1
    unset GITHUB_TOKEN GITHUB_USER
    if [ "$roles" = none ]; then
      echo none
      exit 0
    fi
    printf '백엔드 배포 인계: %s\n' "$roles" >&2
    source_dir=$2
    nonce=$(cat /proc/sys/kernel/random/uuid)
    daemon=$(docker info --format '{{.ID}}')
    [[ $nonce =~ ^[a-f0-9-]{36}$ && $daemon =~ ^[a-zA-Z0-9_.:-]{1,128}$ ]]
    # The caller holds the Jenkins lock and its Vault snapshot is still private
    # in the parent workspace. A build failure leaves no active ticket.
    bash scripts/deploy-compose.sh backend "$source_dir" --build-only >&2
    image=$(docker image inspect -f '{{.Id}}' "trading-desk-backend:$REVISION")
    [[ $image =~ ^sha256:[0-9a-f]{64}$ ]]
    mkdir -m 700 "$ticket"
    mkdir -p "$ticket/backend" "$ticket/scripts/jenkins"
    cp -- "$ENV_FILE" "$ticket/env"
    chmod 600 "$ticket/env"
    cp -- "$source_dir/backend/docker-compose-backend.yml" "$ticket/backend/"
    cp -- scripts/deploy-compose.sh "$ticket/scripts/"
    cp -- scripts/jenkins/backend-fence.sh "$ticket/scripts/jenkins/"
    printf '%s\n' "$roles" > "$ticket/remaining"
    printf '%s\n' "$nonce" "$TD_PARENT_BUILD" "$REVISION" "$image" "$TD_NODE" "$daemon" \
      "$TD_FORCE_RECREATE" "$FORCE" "$ROLLBACK_DRILL" > "$ticket/release"
    printf '%s %s %s %s\n' "$nonce" "$image" "$daemon" "$roles"
    ;;
  deploy)
    validate_release
    role=$2
    case "$role" in api|collector|analyst) ;; *) exit 1 ;; esac
    mapfile -t release < "$ticket/release"
    if [ "${#release[@]}" != 9 ] || [ "${release[0]}" != "${TD_RELEASE:-}" ] \
      || [ "${release[1]}" != "$TD_PARENT_BUILD" ] || [ "${release[2]}" != "$REVISION" ] \
      || [ "${release[3]}" != "${TD_IMAGE_ID:-}" ] || [ "${release[4]}" != "$TD_NODE" ] \
      || [ "${release[5]}" != "$(docker info --format '{{.ID}}')" ] \
      || [ "${release[5]}" != "${TD_DAEMON:-}" ]; then
      echo "배포 인계와 실제 실행 정보가 일치하지 않는다" >&2
      exit 1
    fi
    remaining=$(cat "$ticket/remaining")
    if [ "${remaining%%,*}" != "$role" ]; then
      echo "이미 소비했거나 순서에 없는 서비스 배포 요청" >&2
      exit 1
    fi
    # Consume before mutation. An exit or interruption leaves no next role.
    : > "$ticket/remaining"
    ENV_FILE="$ticket/env" TD_FENCED_ROLE=1 TD_FORCE_RECREATE="${release[6]}" \
      FORCE="${release[7]}" ROLLBACK_DRILL="${release[8]}" \
      bash "$ticket/scripts/deploy-compose.sh" backend "$ticket" --service "$role" --image-id "$TD_IMAGE_ID"
    if [ "$remaining" != "$role" ]; then
      printf '%s\n' "${remaining#*,}" > "$ticket/remaining"
    fi
    ;;
  finish)
    validate_release
    mapfile -t release < "$ticket/release"
    if [ "${#release[@]}" != 9 ] || [ "${release[0]}" != "${TD_RELEASE:-}" ] \
      || [ "${release[1]}" != "$TD_PARENT_BUILD" ] || [ "${release[2]}" != "$REVISION" ] \
      || [ "${release[4]}" != "$TD_NODE" ] || [ "${release[5]}" != "$(docker info --format '{{.ID}}')" ]; then
      echo "다른 배포 인계를 정리할 수 없다" >&2
      exit 1
    fi
    rm -rf -- "$ticket"
    ;;
  *) echo "지원하지 않는 배포 인계 명령" >&2; exit 1 ;;
esac
