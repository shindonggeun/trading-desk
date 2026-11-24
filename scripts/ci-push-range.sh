#!/usr/bin/env bash
# Resolve the exact comparison range for a develop push.
set -euo pipefail

run_full_ci() {
  printf '::notice::Running full CI because %s\n' "$1"
  printf 'force_all=true\n' >> "$GITHUB_OUTPUT"
  exit 0
}

before=${PUSH_BEFORE:-}
head=${PUSH_HEAD:-}
zero_sha=0000000000000000000000000000000000000000
if [[ ${PUSH_FORCED:-} != false ]]; then
  run_full_ci 'the push is forced or its force status is unknown'
fi
if [[ ! $before =~ ^[[:xdigit:]]{40}$ || ! $head =~ ^[[:xdigit:]]{40}$ ||
      $before == "$zero_sha" || $head == "$zero_sha" ]]; then
  run_full_ci 'the push does not provide a usable SHA range'
fi

before=${before,,}
head=${head,,}
if [[ $(git cat-file -t "$before" 2>/dev/null) != commit ||
      $(git cat-file -t "$head" 2>/dev/null) != commit ]]; then
  run_full_ci 'a push SHA does not resolve to a local commit'
fi
if [[ $(git rev-parse --verify HEAD) != "$head" ]]; then
  run_full_ci 'the checkout does not match the push head'
fi
if ! git merge-base --is-ancestor "$before" "$head"; then
  run_full_ci 'the before commit is not a verified ancestor of the push head'
fi

# A failed diff must never look like an empty list. Count both sides of renames.
if ! paths=$(mktemp); then
  run_full_ci 'a temporary diff file could not be created'
fi
trap 'rm -f -- "$paths"' EXIT
if ! git diff --no-renames --name-only -z "$before" "$head" -- > "$paths"; then
  run_full_ci 'the push diff could not be read'
fi
while IFS= read -r -d '' path; do
  case "$path" in
    docs/*) ;;
    backend/*|frontend/*)
      case "${path##*/}" in
        Dockerfile|Dockerfile.*|*.Dockerfile|*.Dockerfile.*|*.dockerignore|\
        compose*.yml|compose*.yaml|docker-compose*.yml|docker-compose*.yaml|\
        nginx*.conf|nginx*.conf.*)
          run_full_ci 'the push changes deployment configuration'
          ;;
      esac
      ;;
    *) run_full_ci 'the push changes shared files' ;;
  esac
done < "$paths"

printf 'force_all=false\nbase=%s\nref=%s\n' "$before" "$head" >> "$GITHUB_OUTPUT"
