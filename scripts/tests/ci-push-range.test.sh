#!/usr/bin/env bash
set -euo pipefail

SCRIPT=$(cd "$(dirname "$0")/.." && pwd)/ci-push-range.sh
TMP_ROOT=$(cd "${TMPDIR:-/tmp}" && pwd -P)
TMP=$(mktemp -d "$TMP_ROOT/ci-push-range.XXXXXXXX")
[[ $TMP == "$TMP_ROOT"/ci-push-range.* ]]
trap 'cd "$TMP_ROOT" && rm -rf -- "$TMP"' EXIT
OUTPUT="$TMP/output"
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
git init -q --initial-branch=main --object-format=sha1 "$TMP/repo"
cd "$TMP/repo"
git config user.name 'CI fixture'
git config user.email 'ci@example.invalid'
git commit -q --allow-empty -m initial
git switch -qc develop

commit_file() {
  mkdir -p "$(dirname "$1")"
  printf '%s\n' "$1" >> "$1"
  git add -- "$1"
  git commit -qm "Change ${1##*/}"
}

expect_range() {
  local label=$1 before=$2 head=$3 forced=$4 expected='force_all=true'
  if [[ ${5:-} == exact ]]; then
    printf -v expected 'force_all=false\nbase=%s\nref=%s' "$before" "$head"
  fi
  : > "$OUTPUT"
  PUSH_BEFORE="$before" PUSH_HEAD="$head" PUSH_FORCED="$forced" GITHUB_OUTPUT="$OUTPUT" \
    bash "$SCRIPT" >/dev/null
  if [[ $(< "$OUTPUT") != "$expected" ]]; then
    printf '%s\nExpected:\n%s\nReceived:\n%s\n' "$label" "$expected" "$(< "$OUTPUT")" >&2
    exit 1
  fi
}

commit_file backend/model.py
before=$(git rev-parse HEAD)
commit_file 'docs/guide page.md'
docs_head=$(git rev-parse HEAD)
commit_file frontend/src/view.ts
commit_file backend/worker.py
multi_head=$(git rev-parse HEAD)

# A later push has moved develop, and the default-branch comparison includes an older backend change.
git switch -q --detach "$docs_head"
[[ $(git diff --name-only main...develop) == *backend/model.py* ]]
[[ -z $(git diff --name-only "$before" "$docs_head" -- backend) ]]
expect_range 'single docs push at its original SHA' "$before" "$docs_head" false exact

git switch -q --detach "$multi_head"
[[ $(git diff --name-only "$docs_head" "$multi_head") == $'backend/worker.py\nfrontend/src/view.ts' ]]
expect_range 'push of two commits' "$docs_head" "$multi_head" false exact
expect_range 'missing before SHA' '' "$multi_head" false
expect_range 'branch creation' 0000000000000000000000000000000000000000 "$multi_head" false
expect_range 'branch deletion' "$before" 0000000000000000000000000000000000000000 false
expect_range 'unavailable before SHA' ffffffffffffffffffffffffffffffffffffffff "$multi_head" false
expect_range 'unavailable head SHA' "$before" ffffffffffffffffffffffffffffffffffffffff false
expect_range 'abbreviated before SHA' "${before:0:12}" "$multi_head" false
expect_range 'non-commit before SHA' "$(git rev-parse "$before:backend/model.py")" "$multi_head" false
expect_range 'forced push' "$before" "$multi_head" true
expect_range 'unknown forced flag' "$before" "$multi_head" ''
expect_range 'checkout at another push SHA' "$before" "$docs_head" false

git switch -q --detach "$before"
commit_file backend/diverged.py
diverged=$(git rev-parse HEAD)
git switch -q --detach "$multi_head"
expect_range 'before SHA is outside the head history' "$diverged" "$multi_head" false

for path in .github/workflows/ci.yml .github/workflows/other.yml scripts/shared.sh \
  backend/backend.Dockerfile backend/backend.Dockerfile.dockerignore \
  backend/docker-compose-backend.yml frontend/nginx.conf.template; do
  shared_base=$(git rev-parse HEAD)
  commit_file "$path"
  expect_range "shared or deployment path $path" "$shared_base" "$(git rev-parse HEAD)" false
done

commit_file README.md
rename_base=$(git rev-parse HEAD)
git config diff.renames true
git mv README.md backend/README.md
git commit -qm 'Move shared file into backend'
expect_range 'rename removes a shared path' "$rename_base" "$(git rev-parse HEAD)" false

export FIXTURE_GIT
FIXTURE_GIT=$(command -v git)
mkdir "$TMP/bin"
cat > "$TMP/bin/git" <<'GIT'
#!/usr/bin/env bash
if [[ $1 == diff ]]; then
  printf 'docs/partial.md\0'
  exit 1
fi
exec "$FIXTURE_GIT" "$@"
GIT
chmod +x "$TMP/bin/git"
git switch -q --detach "$multi_head"
PATH="$TMP/bin:$PATH" expect_range 'diff error with partial output' "$docs_head" "$multi_head" false

echo 'Develop push range and full CI fallback checks passed'
