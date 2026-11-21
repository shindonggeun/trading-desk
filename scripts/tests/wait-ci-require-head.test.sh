#!/usr/bin/env bash
# Offline GitHub responses for the strict split-deployment gate. Requires jq.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
TMP=$(mktemp -d)
trap 'rm -rf -- "$TMP"' EXIT

cat > "$TMP/curl" <<'FAKE'
#!/usr/bin/env bash
set -eu
case "${*: -1}" in
  */git/ref/heads/develop)
    case "$SCENARIO" in
      unreadable) exit 22 ;;
      invalid) printf '%s\n' '{"object":{"sha":42}}' ;;
      stale) printf '{"object":{"sha":"%s"}}\n' "$NEW_SHA" ;;
      *) printf '{"object":{"sha":"%s"}}\n' "$CI_SHA" ;;
    esac
    ;;
  */actions/runs\?head_sha=*'&event=push&branch=develop&per_page=50')
    case "$SCENARIO" in
      head-ok) conclusion=success ;;
      bad-ci) conclusion=failure ;;
      ci-unreadable) exit 22 ;;
      *) exit 99 ;;
    esac
    printf '{"workflow_runs":[{"name":"ci","status":"completed","conclusion":"%s","run_number":1,"run_attempt":1,"html_url":"https://example.invalid/ci"}]}\n' "$conclusion"
    ;;
  *) exit 99 ;;
esac
FAKE
chmod +x "$TMP/curl"
export PATH="$TMP:$PATH" CI_REPO=fixture/project CI_BRANCH=develop CI_WORKFLOW=ci
export CI_SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa NEW_SHA=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
unset GITHUB_TOKEN

expect() {
  local expected=$1 scenario=$2 mode=${3:---require-head} output rc
  if output=$(SCENARIO="$scenario" bash "$HERE/../jenkins/wait-ci.sh" "$mode" 2>&1); then
    rc=0
  else
    rc=$?
  fi
  if [ "$rc" != "$expected" ]; then
    printf '%s %s: expected %s, got %s\n%s\n' "$mode" "$scenario" "$expected" "$rc" "$output" >&2
    exit 1
  fi
}

command -v jq >/dev/null
expect 0 head-ok
expect 4 stale
expect 1 unreadable
expect 1 invalid
expect 1 bad-ci
expect 1 ci-unreadable
expect 0 unreadable --superseded-only
echo 'Strict develop-head/CI gate and legacy fallback checks passed'
