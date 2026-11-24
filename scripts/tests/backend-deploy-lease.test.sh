#!/usr/bin/env bash
# Fake Docker and separate local flock files. Never contacts Docker or Jenkins.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
mkdir -m 700 "$tmp/fence"
mkdir -p "$tmp/bin" "$tmp/scripts/jenkins" "$tmp/.deploy-src/backend"
for file in scripts/deploy-compose.sh scripts/jenkins/backend-fence.sh scripts/jenkins/backend-deploy-lease.sh scripts/jenkins/backend-role-preflight.sh; do
  sed "s|/var/lib/trading-desk/deploy-fence|$tmp/fence|g" "$root/$file" > "$tmp/$file"
done
mv "$tmp/scripts/deploy-compose.sh" "$tmp/actual-deploy.sh"
cp "$tmp/actual-deploy.sh" "$tmp/scripts/actual-deploy.sh"
touch "$tmp/.deploy-src/backend/docker-compose-backend.yml"
export PATH="$tmp/bin:$PATH" TD_RECORD="$tmp/calls" TD_FAKE_LOCK="$tmp/fence/mutation.lock"
export REVISION=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa TD_PARENT_BUILD=3 TD_NODE=agent
export TD_IMAGE_ID=sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
export TD_DAEMON=daemon-A TD_ROLES=api,collector,analyst TD_FORCE_RECREATE=1 FORCE=0 ROLLBACK_DRILL=0
export ENV_FILE="$tmp/vault.env" TD_DEPLOY_FENCE_REQUIRED=1
export TD_SELECTOR=none GITHUB_TOKEN=fixture-token GITHUB_USER=fixture-user
export DOCKER_CONTEXT=wrong DOCKER_HOST=tcp://wrong:2376 DOCKER_TLS_VERIFY=1 DOCKER_CERT_PATH=/wrong
printf "DESK_BACKEND_SPLIT='1'\n" > "$ENV_FILE"
cat > "$tmp/bin/docker" <<'DOCKER'
#!/usr/bin/env bash
[[ ${DOCKER_HOST:-} == unix:///var/run/docker.sock && ! -v DOCKER_CONTEXT && ! -v DOCKER_TLS_VERIFY && ! -v DOCKER_CERT_PATH ]] || exit 98
case "$*" in
  'info --format {{.ID}}') printf '%s\n' "${TD_FAKE_DAEMON:-daemon-A}" ;;
  info) ;;
  'ps -aq --filter name=^/trading-desk-backend-prod$')
    if [ -n "${TD_FAKE_EXISTING:-}" ]; then echo "$TD_FAKE_EXISTING"; fi ;;
  'image inspect -f {{.Id}} '*) printf '%s\n' "$TD_IMAGE_ID" ;;
  'inspect -f '*)
    test "${TD_FAKE_API:-1 running healthy}" != none || exit 1
    printf '%s\n' "${TD_FAKE_API:-1 running healthy}" ;;
  *) echo "Unexpected fixture Docker command" >&2; exit 1 ;;
esac
DOCKER
chmod +x "$tmp/bin/docker"
cat > "$tmp/scripts/jenkins/backend-deploy-targets.sh" <<'SELECTOR'
#!/usr/bin/env bash
set -eu
# The selector must run within the same host fence that creates the release.
if flock -n "$TD_FAKE_LOCK" true; then exit 99; fi
test "$GITHUB_TOKEN" = fixture-token
echo "$TD_SELECTOR"
exit "${TD_SELECTOR_CODE:-0}"
SELECTOR
cat > "$tmp/scripts/deploy-compose.sh" <<'COMPOSE'
#!/usr/bin/env bash
set -euo pipefail
# A fresh open descriptor cannot acquire the lease runner's flock.
if flock -n "$TD_FAKE_LOCK" true; then exit 99; fi
test -z "${GITHUB_TOKEN:-}" && test -z "${GITHUB_USER:-}"
case "$3" in
  --build-only) echo build >> "$TD_RECORD" ;;
  --service)
    test "$TD_FENCED_ROLE" = 1 && test "$TD_FORCE_RECREATE" = 1 && test "$FORCE" = 0
    test "$(stat -c '%a' "$ENV_FILE")" = 600
    grep -qx "DESK_BACKEND_SPLIT='1'" "$ENV_FILE"
    test "$5" = --image-id && test "$6" = "$TD_IMAGE_ID"
    printf '%s\n' "$4" >> "$TD_RECORD"
    exit "${TD_MOCK_CODE:-0}" ;;
  *) exit 99 ;;
esac
COMPOSE
cd "$tmp"
lease() { bash scripts/jenkins/backend-deploy-lease.sh "$@"; }
expect() {
  local want=$1 actual=0
  shift
  "$@" >/dev/null 2>&1 || actual=$?
  if [ "$actual" != "$want" ]; then
    printf 'expected %s, got %s: %s\n' "$want" "$actual" "$*" >&2
    return 1
  fi
}
begin_release() {
  local data
  data=$(lease begin .deploy-src)
  read -r TD_RELEASE image daemon selected <<< "$data"
  export TD_RELEASE
  test "$image" = "$TD_IMAGE_ID" && test "$daemon" = "$TD_DAEMON"
}
export TD_SPLIT_JOBS_ENABLED=true TD_SPLIT_DEPLOY_NODE=agent NODE_NAME=agent
expect 0 bash scripts/jenkins/backend-role-preflight.sh
test ! -e "$tmp/fence/mutation.lock"
TD_SPLIT_JOBS_ENABLED=false expect 1 bash scripts/jenkins/backend-role-preflight.sh
NODE_NAME=other expect 1 bash scripts/jenkins/backend-role-preflight.sh
chmod 755 "$tmp/fence"
expect 1 bash scripts/jenkins/backend-role-preflight.sh
chmod 700 "$tmp/fence"
expect 0 lease route
TD_FAKE_API='0 running healthy' expect 10 lease route
TD_FAKE_API=none expect 10 lease route
TD_FAKE_API=none TD_FAKE_EXISTING=container expect 1 lease route
TD_FAKE_API='<no value> running healthy' expect 1 lease route
TD_FAKE_API='1 running unhealthy' expect 1 lease route
TD_LEGACY_SPLIT_FALLBACK=1 expect 1 bash scripts/actual-deploy.sh backend .deploy-src
TD_FAKE_API='0 running healthy' expect 1 lease begin .deploy-src
begin_release
expect 1 lease route
expect 1 lease begin .deploy-src
expect 1 bash scripts/actual-deploy.sh backend .deploy-src
# Simulate the transmitted manual body against the same fixture host. The real
# wrapper sources the fence before this body, so an old source isn't unpacked.
{
  cat scripts/jenkins/backend-fence.sh
  awk '/^cat <<.REMOTE./ {body=1; next} /^REMOTE$/ {body=0} body' "$root/scripts/deploy.sh" \
    | sed 's|^cd ~/"$DIR"$|cd "$DIR"|'
} > "$tmp/manual-body.sh"
touch .env.runtime src.new
DIR="$tmp" TARGET=backend expect 1 bash manual-body.sh
test -f src.new
TD_FENCED_ROLE=1 expect 1 bash scripts/actual-deploy.sh backend .deploy-src --service api
expect 1 lease deploy collector
TD_FAKE_DAEMON=daemon-B expect 1 lease deploy api
TD_IMAGE_ID=sha256:wrong expect 1 lease deploy api
TD_NODE=other expect 1 lease deploy api
TD_RELEASE=other expect 1 lease finish
expect 0 lease deploy api
expect 1 lease deploy api
TD_MOCK_CODE=2 expect 2 lease deploy collector
expect 1 lease deploy analyst
test "$(cat "$TD_RECORD")" = $'build\napi\ncollector'
expect 0 lease finish
test ! -e "$tmp/fence/active"
TD_ROLES=analyst begin_release
TD_MOCK_CODE=3 expect 3 lease deploy analyst
expect 0 lease finish

# An unchanged auto choice creates no ticket and does no Docker build.
: > "$TD_RECORD"
test "$(TD_ROLES=auto lease begin .deploy-src)" = none
test ! -e "$tmp/fence/active"
test ! -s "$TD_RECORD"

# Two selected roles share a build and leave the omitted role untouched.
TD_ROLES=auto TD_SELECTOR=api,analyst begin_release
test "$selected" = api,analyst
expect 1 bash scripts/jenkins/backend-role-preflight.sh
expect 1 lease deploy collector
expect 0 lease deploy api
expect 0 lease deploy analyst
test "$(cat "$TD_RECORD")" = $'build\napi\nanalyst'
expect 0 lease finish

# An explicit role adds that role to the validated selection.
: > "$TD_RECORD"
TD_ROLES=analyst TD_SELECTOR=collector begin_release
test "$selected" = collector,analyst
expect 1 lease deploy api
expect 0 lease deploy collector
expect 0 lease deploy analyst
test "$(cat "$TD_RECORD")" = $'build\ncollector\nanalyst'
expect 0 lease finish

# Selector failure cannot bypass work required by another role.
: > "$TD_RECORD"
TD_ROLES=api TD_SELECTOR_CODE=1 begin_release
test "$selected" = api,collector,analyst
expect 0 lease deploy api
expect 0 lease deploy collector
expect 0 lease deploy analyst
test "$(cat "$TD_RECORD")" = $'build\napi\ncollector\nanalyst'
expect 0 lease finish
mv "$tmp/fence" "$tmp/removed"
expect 1 bash scripts/actual-deploy.sh backend .deploy-src
echo "Backend lease checks passed"
