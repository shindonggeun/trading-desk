#!/usr/bin/env bash
# develop 커밋의 GitHub Actions 워크플로(.github/workflows/ci.yml, 이름 ci)가 끝나기를 기다린다 (docs/deploy.md 2절).
# Jenkins 가 배포 전에 부른다. 시간 상한은 부르는 쪽(Jenkins timeout)이 둔다.
#
#   CI_REPO=<owner/repo> CI_SHA=<40자 SHA> [CI_WORKFLOW=ci] [CI_BRANCH=develop] [CI_POLL_SEC=10] [CI_SUPERSEDE=1] \
#     [GITHUB_TOKEN=<...>] scripts/jenkins/wait-ci.sh [--superseded-only]
#
# 종료 코드: 0 성공 · 1 실패(실패 · 시간 초과 · 건너뜀 등) · 4 밀림 — 이 커밋의 빌드는 배포하지 않고 뒤 커밋의 빌드가 배포한다(#752).
#   밀림은 둘이다.
#   - CI 가 취소됐다(ci.yml 의 concurrency 가 뒤 커밋에 밀려 취소).
#   - CI_SUPERSEDE=1 이고, 기다리는 동안 develop 머리가 더 새 커밋이 됐고, 그 커밋의 CI 가 실패로 끝나지 않았다(진행 중 · 없음 · 성공).
#   더 새 커밋의 CI 가 이미 실패면 밀림이 아니다. 실패한 커밋 때문에 배포가 영영 막히지 않게 이 커밋을 계속 기다린다.
#   develop 은 rebase 머지만 하므로 "머리가 이 커밋과 다르다" 를 "더 새 커밋" 으로 읽는다.
# --superseded-only: CI 를 기다리지 않고 밀림 검사만 한 번 한다(배포 직전, lock 안 — Jenkinsfile.deploy-common.groovy).
#   0 배포 계속 · 4 밀림. GitHub 를 읽지 못하면 0 이다(검사 실패로 배포를 막지 않는다).
# 수동 되돌리기(DEPLOY_COMMIT)는 develop 머리보다 옛 커밋이라 Jenkins 가 CI_SUPERSEDE 를 켜지 않고 --superseded-only 도 부르지 않는다.
#
# 요청 수: 한 바퀴에 이 커밋의 실행 1회, 아직 안 끝났으면 밀림 검사로 develop 머리 1회와 그 커밋의 실행 1회를 읽는다.
# 10초 간격이면 분당 최대 18회, 최대 대기 45분(Jenkins ciWaitMinutes)이면 810회다. 토큰 한도(시간당 5000회) 안이다.
# 익명(시간당 60회)으로는 이 간격을 버티지 못한다. 그래서 Jenkins 는 늘 토큰을 넘긴다.
# GITHUB_TOKEN 은 표준입력 헤더로 넘긴다(명령 인자 · 로그에 남지 않는다).
set +x
set -euo pipefail

: "${CI_REPO:?CI_REPO}" "${CI_SHA:?CI_SHA}"
WF="${CI_WORKFLOW:-ci}" BRANCH="${CI_BRANCH:-develop}" POLL="${CI_POLL_SEC:-10}" SUPERSEDE="${CI_SUPERSEDE:-0}"
API="https://api.github.com/repos/$CI_REPO"
NEWER=

api() {
  local args=(-sS --fail --connect-timeout 10 --max-time 30 -H "Accept: application/vnd.github+json" -H "X-GitHub-Api-Version: 2022-11-28")
  if [ -n "${GITHUB_TOKEN:-}" ]; then
    printf 'Authorization: Bearer %s\n' "$GITHUB_TOKEN" | curl "${args[@]}" --header @- "$1"
  else
    curl "${args[@]}" "$1"
  fi
}

# 커밋 $1 의 워크플로 상태: "<status> <conclusion 또는 -> <url>" · none · api-error.
# 같은 커밋에서 다시 돌린 실행이 있으면 가장 마지막 것(run_number · run_attempt 최대)을 본다.
run_state() {
  local body
  if body="$(api "$API/actions/runs?head_sha=$1&event=push&branch=$BRANCH&per_page=50")"; then
    printf '%s' "$body" | jq -r --arg wf "$WF" '
      [.workflow_runs[] | select(.name == $wf)] | sort_by(.run_number, .run_attempt) | last
      | if . == null then "none" else "\(.status) \(.conclusion // "-") \(.html_url)" end'
  else
    echo "api-error"
  fi
}

# develop 머리가 이 커밋이 아니고 그 커밋의 CI 가 실패로 끝나지 않았으면 0(밀림). 그 머리를 NEWER 에 둔다.
superseded() {
  local body state
  body="$(api "$API/commits/$BRANCH")" || return 1
  NEWER="$(printf '%s' "$body" | jq -r '.sha // empty' 2>/dev/null)" || return 1
  if [ -z "$NEWER" ] || [ "$NEWER" = "$CI_SHA" ]; then
    return 1
  fi
  state="$(run_state "$NEWER")"
  case "$state" in
    "completed success "* | "completed cancelled "*) return 0 ;;
    completed\ *)
      echo "develop 머리 ${NEWER:0:12} 의 CI 가 실패로 끝났다($state) — 밀림이 아니다, ${CI_SHA:0:12} 를 본다"
      return 1 ;;
  esac
  return 0
}

pushed_back() {
  echo "밀림: ${CI_SHA:0:12} → 더 새 ${NEWER:0:12}"
  exit 4
}

if [ "${1:-}" = "--superseded-only" ]; then
  if superseded; then pushed_back; fi
  echo "배포 직전 확인: develop 머리에 더 새 배포 후보가 없다 — ${CI_SHA:0:12} 를 배포한다"
  exit 0
fi

echo "CI 확인: $CI_REPO $BRANCH ${CI_SHA:0:12} 워크플로 '$WF'"
last=
while :; do
  state="$(run_state "$CI_SHA")"
  case "$state" in
    "completed success "*) echo "CI 성공: ${state##* }"; exit 0 ;;
    "completed cancelled "*) echo "CI 가 취소됐다(뒤 커밋에 밀림): ${state##* }"; exit 4 ;;
    completed\ *) echo "CI 가 성공하지 않았다 — 배포하지 않는다: $state" >&2; exit 1 ;;
  esac
  if [ "$SUPERSEDE" = 1 ] && superseded; then pushed_back; fi
  if [ "$state" != "$last" ]; then
    case "$state" in
      none) echo "아직 '$WF' 실행이 없다 — 기다린다" ;;
      api-error) echo "GitHub API 를 읽지 못했다 — ${POLL}초 뒤 다시" ;;
      *) echo "CI 진행 중: $state" ;;
    esac
    last="$state"
  fi
  sleep "$POLL"
done
