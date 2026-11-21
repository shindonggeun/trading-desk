#!/usr/bin/env bash
# develop 커밋의 GitHub Actions 워크플로(.github/workflows/ci.yml, 이름 ci)가 끝나기를 기다린다 (docs/deploy.md 2절).
# Jenkins 가 배포 전에 부른다. 시간 상한은 부르는 쪽(Jenkins timeout)이 둔다.
#
#   CI_REPO=<owner/repo> CI_SHA=<40자 SHA> [CI_WORKFLOW=ci] [CI_BRANCH=develop] [CI_POLL_SEC=10] [CI_SUPERSEDE=1] \
#     [GITHUB_TOKEN=<...>] scripts/jenkins/wait-ci.sh [--superseded-only | --require-head]
#
# 종료 코드: 0 성공 · 1 실패(실패 · 시간 초과 · 건너뜀 등) · 4 밀림 — 이 커밋의 빌드는 배포하지 않고 뒤 커밋의 빌드가 배포한다(#752).
#   밀림은 둘이다.
#   - CI 가 취소됐다(ci.yml 의 concurrency 가 뒤 커밋에 밀려 취소).
#   - CI_SUPERSEDE=1 이고, 기다리는 동안 develop 머리가 더 새 커밋이 됐고, 그 커밋의 CI 가 아직 돌거나(없음 · 대기 · 진행 중) 성공했다.
#   더 새 커밋의 CI 가 실패 · 취소로 끝났거나 읽지 못했으면 밀림이 아니다. 실패한 커밋이나 일시 오류 때문에 배포가 막히지 않게 이 커밋을 계속 본다.
#   develop 은 rebase 머지만 하므로 "머리가 이 커밋과 다르다" 를 "더 새 커밋" 으로 읽는다.
# --superseded-only: CI 를 기다리지 않고 밀림 검사만 한 번 한다(배포 직전, lock 안 — Jenkinsfile.deploy-common.groovy).
#   0 배포 계속 · 4 밀림. GitHub 를 읽지 못하면 0 이다(검사 실패로 배포를 막지 않는다).
# --require-head: 자동 분리 배포는 정확한 develop 머리와 성공 CI를 다시 확인한다. 알 수 없으면 멈춘다.
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
NEWER='' SEEN=unread

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

# develop 머리가 이 커밋이 아니고 그 커밋의 CI 가 아직 돌고 있거나(없음 · 대기 · 진행 중) 성공했으면 0(밀림).
# 그 머리를 NEWER 에, 무엇을 봤는지를 SEEN(unread · same · newer)에 둔다. 밀림으로 볼 상태만 나열한다 — 못 읽은 응답
# (API 오류 · JSON 아님)이나 실패 · 취소로 끝난 머리는 밀림이 아니다(일시 오류 한 번이 초록 커밋의 배포를 막지 않게).
# 머리 커밋의 취소는 사람이 취소했거나 아주 짧은 경쟁뿐이다 — 뒤 커밋 push 로 취소됐다면 머리는 이미 그 뒤 커밋이다.
superseded() {
  local body state
  NEWER='' SEEN=unread
  body="$(api "$API/git/ref/heads/$BRANCH")" || return 1     # SHA 만 (commits/<branch> 는 파일 목록 · patch 까지 준다)
  NEWER="$(printf '%s' "$body" | jq -r '.object.sha // empty' 2>/dev/null)" || return 1
  [ -n "$NEWER" ] || return 1
  if [ "$NEWER" = "$CI_SHA" ]; then
    SEEN=same
    return 1
  fi
  SEEN=newer
  state="$(run_state "$NEWER")"
  case "$state" in
    none | "queued "* | "in_progress "* | "waiting "* | "requested "* | "pending "* | "completed success "*) return 0 ;;
    completed\ *)
      echo "develop 머리 ${NEWER:0:12} 의 CI 가 성공하지 않고 끝났다($state) — 밀림이 아니다, ${CI_SHA:0:12} 를 본다" ;;
    *)
      echo "develop 머리 ${NEWER:0:12} 의 CI 를 읽지 못했다 — 밀림으로 보지 않는다" ;;
  esac
  return 1
}

pushed_back() {
  echo "밀림: ${CI_SHA:0:12} → 더 새 ${NEWER:0:12}"
  exit 4
}

if [ "${1:-}" = "--superseded-only" ]; then
  if superseded; then pushed_back; fi
  case "$SEEN" in
    unread) echo "배포 직전 확인: develop 머리를 읽지 못했다 — ${CI_SHA:0:12} 배포를 계속한다" ;;
    same) echo "배포 직전 확인: develop 머리가 이 커밋이다 — ${CI_SHA:0:12} 를 배포한다" ;;
    *) echo "배포 직전 확인: 더 새 커밋은 배포 후보가 아니다 — ${CI_SHA:0:12} 를 배포한다" ;;
  esac
  exit 0
fi

if [ "${1:-}" = "--require-head" ]; then
  if ! body=$(api "$API/git/ref/heads/$BRANCH") ||
     ! head=$(printf '%s' "$body" | jq -er '.object.sha | select(type == "string")') ||
     [[ ! $head =~ ^[0-9a-f]{40}$ ]]; then
    echo "분리 배포: develop 머리를 확인할 수 없다" >&2
    exit 1
  fi
  if [ "$head" != "$CI_SHA" ]; then
    NEWER=$head
    pushed_back
  fi
  if ! state=$(run_state "$CI_SHA") || [[ $state != "completed success "* ]]; then
    echo "분리 배포: 현재 머리의 CI 성공을 다시 확인할 수 없다" >&2
    exit 1
  fi
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
