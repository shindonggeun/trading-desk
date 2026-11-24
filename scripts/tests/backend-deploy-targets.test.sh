#!/usr/bin/env bash
# Only synthetic Git history and fake Docker/GitHub. No credentials or daemon.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/github" "$tmp/repo"
export PATH="$tmp/bin:$PATH" TD_FIXTURE="$tmp"
export CI_REPO=fixture/desk CI_BRANCH=develop GITHUB_TOKEN=fake-github-token
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null

cat > "$tmp/bin/docker" <<'DOCKER'
#!/usr/bin/env bash
set -euo pipefail
[[ $# == 4 && $1 == inspect && $2 == -f && $3 == *org.opencontainers.image.revision* ]]
case "$4" in
  trading-desk-backend-prod) printf '%s %s\n' "$FAKE_API" "${FAKE_API_STATE:-running healthy}" ;;
  trading-desk-collector-prod) printf '%s %s\n' "$FAKE_COLLECTOR" "${FAKE_COLLECTOR_STATE:-running healthy}" ;;
  trading-desk-analyst-prod) printf '%s %s\n' "$FAKE_ANALYST" "${FAKE_ANALYST_STATE:-running healthy}" ;;
  *) exit 1 ;;
esac
DOCKER
cat > "$tmp/bin/curl" <<'CURL'
#!/usr/bin/env bash
set -euo pipefail
[[ $1 == -q && " $* " != *fake-github-token* ]]
IFS= read -r auth
[[ $auth == 'Authorization: Bearer fake-github-token' ]]
url=${!#}
[[ $url =~ ^https://api\.github\.com/repos/fixture/desk/commits/([0-9a-f]{40})/pulls\?per_page=100$ ]]
sha=${BASH_REMATCH[1]}
printf '%s\n' "$sha" >> "$TD_FIXTURE/requests"
cat -- "$TD_FIXTURE/github/$sha"
printf '\n%s' "${FAKE_GITHUB_STATUS:-200}"
CURL
chmod +x "$tmp/bin/docker" "$tmp/bin/curl"

cd "$tmp/repo"
git init -q -b develop
git config user.email 'fixture@example.invalid'
git config user.name 'Synthetic release history'
git config commit.gpgsign false
mkdir -p backend/desk
printf 'old shared\n' > backend/desk/api.py
git add -- backend/desk/api.py
git commit -qm baseline
base=$(git rev-parse HEAD)
sequence=0

new_case() {
  git switch --detach -q "$base"
  export FAKE_API=$base FAKE_COLLECTOR=$base FAKE_ANALYST=$base
  unset FAKE_API_STATE FAKE_COLLECTOR_STATE FAKE_ANALYST_STATE FAKE_GITHUB_STATUS
  : > "$tmp/requests"
}

change() {
  local path=$1
  mkdir -p "$(dirname "$path")"
  printf '%s\n' "$((++sequence))" > "$path"
  git add -- "$path"
  git commit -qm "fixture $sequence"
  commit=$(git rev-parse HEAD)
}

pr() {
  local commit=$1 merged=$2 number=$3 labels=$4
  jq -n --arg repository "$CI_REPO" --arg branch "$CI_BRANCH" --arg merged "$merged" \
    --argjson number "$number" --argjson labels "$labels" '[
      {number: $number, state: "closed", merged_at: "2026-11-24T00:01:02Z", merge_commit_sha: $merged,
       base: {ref: $branch, repo: {full_name: $repository}}, labels: ($labels | map({name: .}))}
    ]' > "$tmp/github/$commit"
}

expect() {
  local wanted=$1 reason=$2 actual
  REVISION=$(git rev-parse HEAD)
  export REVISION
  actual=$(bash "$root/scripts/jenkins/backend-deploy-targets.sh" 2>"$tmp/stderr")
  if [[ $actual != "$wanted" ]] || ! grep -qF "reason=$reason " "$tmp/stderr"; then
    printf 'expected=%s/%s actual=%s\n' "$wanted" "$reason" "$actual" >&2
    cat "$tmp/stderr" >&2
    return 1
  fi
  test "$(wc -l < "$tmp/stderr")" -eq 1
  ! grep -qE 'fake-github-token|untrusted-|fixture@example' "$tmp/stderr"
}

new_case
expect none no-pending-roles
change docs/notes.md
change frontend/only.ts
expect none no-pending-roles
test ! -s "$tmp/requests"

new_case
change backend/desk/prewarm.py
pr "$commit" "$commit" 11 '["backend-api", "enhancement"]'
expect api merged-pr-labels
grep -qF '#11:backend-api' "$tmp/stderr"
test "$(wc -l < "$tmp/requests")" -eq 1

new_case
change backend/desk/nq_fast.py
pr "$commit" "$commit" 12 '["backend-collector"]'
expect collector merged-pr-labels
change backend/desk/events.py
pr "$commit" "$commit" 13 '["backend-analyst"]'
expect collector,analyst merged-pr-labels
export FAKE_COLLECTOR="$commit"
expect analyst merged-pr-labels

new_case
change backend/desk/prewarm.py
api_commit=$commit
change backend/desk/nq_fast.py
pr "$api_commit" "$commit" 14 '["backend-api","backend-collector"]'
pr "$commit" "$commit" 14 '["backend-collector","backend-api"]'
expect api,collector merged-pr-labels
grep -qF '#14:backend-api,backend-collector' "$tmp/stderr"
test "$(grep -o '#14:' "$tmp/stderr" | wc -l)" -eq 1
export FAKE_API="$commit"
expect collector merged-pr-labels
export FAKE_COLLECTOR="$commit"
expect none no-pending-roles
export FAKE_API=$base FAKE_COLLECTOR=$base
pr "$api_commit" "$commit" 14 '["backend-api"]'
expect all pr-labels-changed

new_case
change backend/desk/migrations/versions/9999_new.py
pr "$commit" "$commit" 15 '["backend-api"]'
expect all migration
test ! -s "$tmp/requests"

new_case
change backend/desk/api.py
pr "$commit" "$commit" 16 '["backend-api"]'
expect all shared-path
test ! -s "$tmp/requests"
rm -- backend/desk/api.py
git add -- backend/desk/api.py
git commit -qm 'intervening deletion'
expect all shared-path

new_case
change backend/desk/req_timing.py
pr "$commit" "$commit" 16 '["backend-api"]'
expect all shared-path
test ! -s "$tmp/requests"

new_case
change backend/desk/prewarm.py
expect all github-pr-unverified
pr "$commit" "$commit" 17 '[]'
expect all missing-role-label
pr "$commit" "$commit" 17 '["backend-collector"]'
expect all missing-role-label
pr "$commit" "$commit" 17 '["backend-api","backend-untrusted-label"]'
expect all github-pr-unverified
pr "$commit" "$commit" 17 '["backend-api"]'
FAKE_GITHUB_STATUS=302 expect all github-pr-unverified
cat "$tmp/github/$commit" "$tmp/github/$commit" > "$tmp/extra"
mv -- "$tmp/extra" "$tmp/github/$commit"
expect all github-pr-unverified
pr "$commit" "$commit" 17 '["backend-api"]'
jq '.[0].base.ref = "another-branch"' "$tmp/github/$commit" > "$tmp/extra"
mv -- "$tmp/extra" "$tmp/github/$commit"
expect all github-pr-unverified
pr "$commit" "$commit" 17 '["backend-api"]'
jq '. + .' "$tmp/github/$commit" > "$tmp/extra"
mv -- "$tmp/extra" "$tmp/github/$commit"
expect all github-pr-unverified
jq '[range(100) | {message:"untrusted-cutoff"}]' > "$tmp/github/$commit" --null-input
expect all github-pr-unverified
printf 'untrusted-bad-github-response' > "$tmp/github/$commit"
expect all github-pr-unverified
pr "$commit" "$base" 18 '["backend-api"]'
expect all pr-outside-history

new_case
git mv -- backend/desk/api.py backend/desk/prewarm.py
git commit -qm 'shared rename into an API path'
expect all shared-path
test ! -s "$tmp/requests"

new_case
change backend/desk/prewarm.py
pr "$commit" "$commit" 19 '["backend-api"]'
export FAKE_ANALYST=bad-revision
expect all baseline-unverified
export FAKE_ANALYST=dddddddddddddddddddddddddddddddddddddddd
expect all baseline-unverified
export FAKE_ANALYST=$base FAKE_COLLECTOR_STATE='running unhealthy'
expect all baseline-unverified
unset FAKE_COLLECTOR_STATE
nonancestor=$commit
new_case
change backend/desk/events.py
pr "$commit" "$commit" 20 '["backend-analyst"]'
export FAKE_API=$nonancestor
expect all baseline-unverified

new_case
change unknown-root-runtime-file
expect all shared-path

echo 'backend-deploy-targets fixture passed'
