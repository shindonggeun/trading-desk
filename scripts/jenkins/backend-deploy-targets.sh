#!/usr/bin/env bash
# Called from the backend lease while holding the host's deployment lock.
# stdout is the canonical selection, stderr contains only validated decision metadata.
set +x
set -uo pipefail

roles=(api collector analyst)
declare -A containers=(
  [api]=trading-desk-backend-prod
  [collector]=trading-desk-collector-prod
  [analyst]=trading-desk-analyst-prod
)
declare -A commit_labels=() selected=() reported_prs=()
evidence=()
start=$SECONDS

finish() {
  printf '백엔드 배포 판정: SERVICES=%s reason=%s PR=%s\n' "$1" "$2" "${evidence[*]:--}" >&2
  printf '%s\n' "$1"
  exit 0
}

baseline() {
  local role=$1 state
  state=$(docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}} {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "${containers[$role]}" 2>/dev/null) || return 1
  [[ $state =~ ^([0-9a-f]{40})\ running\ healthy$ ]] || return 1
  printf '%s\n' "${BASH_REMATCH[1]}"
}

lookup_pr() {
  local commit=$1 response
  # Ignore ~/.curlrc and keep auth out of command arguments. No redirects.
  [[ ${GITHUB_TOKEN:-} =~ ^[A-Za-z0-9_.-]+$ ]] || return 1
  response=$(printf 'Authorization: Bearer %s\n' "$GITHUB_TOKEN" |
    curl -q --silent --fail --proto '=https' --connect-timeout 3 --max-time 5 \
      --max-filesize 1048576 --write-out '\n%{http_code}' --header @- \
      -H 'Accept: application/vnd.github+json' -H 'X-GitHub-Api-Version: 2022-11-28' \
      "https://api.github.com/repos/$CI_REPO/commits/$commit/pulls?per_page=100" 2>/dev/null) || return 1
  [[ ${response##*$'\n'} == 200 ]] || return 1
  jq -ers --arg repo "$CI_REPO" --arg branch "$CI_BRANCH" '
      if length == 1 then .[0] else error("invalid JSON document") end
      | if type != "array" or length >= 100 then error("incomplete PR data") else . end
      | if all(.[]; type == "object" and (.base.ref | type == "string")
          and (.base.repo.full_name | type == "string")
          and (.state == "open" or .state == "closed")
          and (.merged_at == null or (.merged_at | type == "string")))
        then . else error("invalid PR data") end
      | map(select(.base.ref == $branch and (.base.repo.full_name | ascii_downcase) == ($repo | ascii_downcase)
          and .state == "closed" and .merged_at != null))
      | if length != 1 then error("ambiguous PR") else .[0] end
      | if (.number | type == "number" and . > 0 and . == floor and . <= 2147483647)
          and (.merged_at | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"))
          and (.merge_commit_sha | type == "string" and test("^[0-9a-f]{40}$"))
          and (.labels | type == "array" and all(.[]; type == "object" and (.name | type == "string")))
        then . else error("invalid merged PR") end
      | . as $pr
      | ($pr.labels | map(.name | select(startswith("backend-")))) as $labels
      | if all($labels[]; . == "backend-api" or . == "backend-collector" or . == "backend-analyst")
          and ($labels | unique | length) == ($labels | length)
        then [$pr.number, $pr.merge_commit_sha, (if ($labels | length) > 0 then ($labels | sort | join(",")) else "-" end)] | @tsv
        else error("invalid deployment labels") end
    ' <<< "${response%$'\n'*}" 2>/dev/null
}

classify_commit() {
  local commit=$1 sha parent extra path role metadata number merged labels
  local fallback=''
  local -A required=()
  metadata=$(git rev-list --parents -n 1 "$commit" 2>/dev/null) || finish all history-unavailable
  read -r sha parent extra <<< "$metadata"
  [[ $sha == "$commit" && $parent =~ ^[0-9a-f]{40}$ && -z $extra ]] || finish all ambiguous-history
  git diff --no-ext-diff --name-only --no-renames -z "$parent" "$commit" -- > "$paths" 2>/dev/null || finish all history-unavailable
  while IFS= read -r -d '' path; do
    # Keep the exact role-only paths in sync with .github/labeler.yml.
    case "$path" in
      docs/*|frontend/*) ;;
      backend/desk/prewarm.py) required[api]=1 ;;
      backend/desk/nq_fast.py) required[collector]=1 ;;
      backend/desk/events.py) required[analyst]=1 ;;
      backend/desk/migrations/*) fallback=migration ;;
      *) fallback=${fallback:-shared-path} ;;
    esac
  done < "$paths"
  if [ -z "$fallback" ] && [ "${#required[@]}" -eq 0 ]; then
    commit_labels[$commit]=-
    return
  fi
  (( SECONDS - start < 30 )) || finish all "${fallback:-lookup-budget}"
  metadata=$(lookup_pr "$commit") || finish all "${fallback:-github-pr-unverified}"
  read -r number merged labels <<< "$metadata"
  # Git must be able to prove this PR's merge is on the promotion path and
  # includes the queried commit. A PR merged later than the target is stale evidence.
  git merge-base --is-ancestor "$commit" "$merged" 2>/dev/null &&
    git merge-base --is-ancestor "$merged" "$REVISION" 2>/dev/null || finish all "${fallback:-pr-outside-history}"
  if [[ -v 'reported_prs[$number]' ]]; then
    [[ ${reported_prs[$number]} == "$labels" ]] || finish all "${fallback:-pr-labels-changed}"
  else
    reported_prs[$number]=$labels
    evidence+=("#$number:$labels")
  fi
  if [ -n "$fallback" ]; then finish all "$fallback"; fi
  for role in "${!required[@]}"; do
    [[ ,$labels, == *",backend-$role,"* ]] || finish all missing-role-label
  done
  commit_labels[$commit]=$labels
}

[[ ${REVISION:-} =~ ^[0-9a-f]{40}$ && ${CI_REPO:-} =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ &&
   ${CI_BRANCH:-} =~ ^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$ ]] || finish all invalid-input
git cat-file -e "$REVISION^{commit}" 2>/dev/null || finish all history-unavailable
paths=$(mktemp) || finish all history-unavailable
trap 'rm -f -- "$paths"' EXIT

for role in "${roles[@]}"; do
  deployed=$(baseline "$role") || finish all baseline-unverified
  git merge-base --is-ancestor "$deployed" "$REVISION" 2>/dev/null || finish all baseline-unverified
  commits=$(git rev-list --max-count=101 "$deployed..$REVISION" 2>/dev/null) || finish all history-unavailable
  count=0
  while IFS= read -r sha; do
    [ -n "$sha" ] || continue
    (( ++count <= 100 )) || finish all history-budget
    (( SECONDS - start < 30 )) || finish all lookup-budget
    [[ $sha =~ ^[0-9a-f]{40}$ ]] || finish all history-unavailable
    if [[ ! -v 'commit_labels[$sha]' ]]; then classify_commit "$sha"; fi
    if [[ ,${commit_labels[$sha]}, == *",backend-$role,"* ]]; then selected[$role]=1; fi
  done <<< "$commits"
done

if [ "${#selected[@]}" -eq 0 ]; then finish none no-pending-roles; fi
if [ "${#selected[@]}" -eq "${#roles[@]}" ]; then finish all merged-pr-labels; fi
selection=''
for role in "${roles[@]}"; do
  if [[ -v 'selected[$role]' ]]; then selection+=${selection:+,}$role; fi
done
finish "$selection" merged-pr-labels
