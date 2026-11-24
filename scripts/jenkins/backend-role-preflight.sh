#!/usr/bin/env bash
# Check prerequisites on the approved deploy node without creating a lock or ticket.
set +x
set -euo pipefail

fail() { printf '분리 배포 준비 미충족: %s\n' "$1" >&2; exit 1; }

[ "${TD_SPLIT_JOBS_ENABLED:-}" = true ] || fail TD_SPLIT_JOBS_ENABLED
[[ ${TD_SPLIT_DEPLOY_NODE:-} =~ ^[a-zA-Z0-9_.-]{1,128}$ ]] || fail TD_SPLIT_DEPLOY_NODE
[ "${NODE_NAME:-}" = "$TD_SPLIT_DEPLOY_NODE" ] || fail NODE_NAME

fence=/var/lib/trading-desk/deploy-fence
[ -d "$fence" ] && [ "$(realpath -e -- "$fence")" = "$fence" ] \
  && [ "$(stat -c '%a:%u' -- "$fence")" = "700:$(id -u)" ] || fail fence-ownership
[ -x "$fence" ] && [ -w "$fence" ] && [ -r "$fence" ] || fail fence-access
[ ! -e "$fence/active" ] && [ ! -L "$fence/active" ] || fail active-release
if [ -L "$fence/mutation.lock" ] \
  || { [ -e "$fence/mutation.lock" ] && { [ ! -f "$fence/mutation.lock" ] || [ ! -O "$fence/mutation.lock" ]; }; }; then
  fail fence-lock
fi
for dependency in docker git jq curl flock; do
  command -v "$dependency" >/dev/null || fail "$dependency"
done
echo '분리 배포 준비 점검 통과 (노드·플래그·호스트 펜스·명령)'
