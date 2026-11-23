#!/usr/bin/env bash
set -euo pipefail

[[ ${CI_REPO:-} == shindonggeun/trading-desk && ${CI_BRANCH:-} == develop && ${CI_WORKFLOW:-} == ci ]]
[[ ${GITHUB_USER:-} == fixture-user && -n ${GITHUB_TOKEN:-} ]]
[[ ${CI_SHA:-} =~ ^[0-9a-f]{40}$ ]]
git cat-file -e "$CI_SHA^{commit}"
case "$*" in
  ''|--superseded-only) ;;
  *) printf 'Unsupported fixture CI call\n' >&2; exit 1 ;;
esac
