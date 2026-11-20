#!/usr/bin/env bash
# develop 커밋의 GitHub Actions 워크플로(.github/workflows/ci.yml, 이름 ci)가 끝나기를 기다린다 (docs/deploy.md 2절).
# Jenkins 가 배포 전에 부른다. 시간 상한은 부르는 쪽(Jenkins timeout)이 둔다.
#
#   CI_REPO=<owner/repo> CI_SHA=<40자 SHA> [CI_WORKFLOW=ci] [CI_BRANCH=develop] [CI_POLL_SEC=30] [GITHUB_TOKEN=<...>] \
#     scripts/jenkins/wait-ci.sh
#
# 종료 코드: 0 성공 · 1 실패(실패 · 시간 초과 · 건너뜀 등) · 4 취소 — ci.yml 의 concurrency 가 뒤 커밋에 밀려 취소한 것(그 뒤 커밋의 빌드가 배포한다)
# GITHUB_TOKEN 이 있으면 표준입력 헤더로 넘긴다(명령 인자 · 로그에 남지 않는다). 없으면 익명(공개 저장소, 시간당 60회).
set +x
set -euo pipefail

: "${CI_REPO:?CI_REPO}" "${CI_SHA:?CI_SHA}"
WF="${CI_WORKFLOW:-ci}" BRANCH="${CI_BRANCH:-develop}" POLL="${CI_POLL_SEC:-30}"
URL="https://api.github.com/repos/$CI_REPO/actions/runs?head_sha=$CI_SHA&event=push&branch=$BRANCH&per_page=50"

api() {
  local args=(-sS --fail --connect-timeout 10 --max-time 30 -H "Accept: application/vnd.github+json" -H "X-GitHub-Api-Version: 2022-11-28")
  if [ -n "${GITHUB_TOKEN:-}" ]; then
    printf 'Authorization: Bearer %s\n' "$GITHUB_TOKEN" | curl "${args[@]}" --header @- "$1"
  else
    curl "${args[@]}" "$1"
  fi
}

echo "CI 확인: $CI_REPO $BRANCH ${CI_SHA:0:12} 워크플로 '$WF'"
last=
while :; do
  if body="$(api "$URL")"; then
    # 같은 커밋에서 다시 돌린 실행이 있으면 가장 마지막 것(run_number · run_attempt 최대)을 본다
    state="$(printf '%s' "$body" | jq -r --arg wf "$WF" '
      [.workflow_runs[] | select(.name == $wf)] | sort_by(.run_number, .run_attempt) | last
      | if . == null then "none" else "\(.status) \(.conclusion // "-") \(.html_url)" end')"
  else
    state="api-error"
  fi
  case "$state" in
    "completed success "*) echo "CI 성공: ${state##* }"; exit 0 ;;
    "completed cancelled "*) echo "CI 가 취소됐다(뒤 커밋에 밀림): ${state##* }"; exit 4 ;;
    completed\ *) echo "CI 가 성공하지 않았다 — 배포하지 않는다: $state" >&2; exit 1 ;;
  esac
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
