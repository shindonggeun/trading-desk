#!/usr/bin/env bash
set -euo pipefail

fixture=/opt/td-web-fixture
mkdir -p "$fixture"/{bin,http}
install -m 755 "$fixture/fake-docker.py" "$fixture/bin/docker"
repo=$(mktemp -d)
trap 'rm -rf -- "$repo"' EXIT
cd "$repo"
git init --quiet --initial-branch=develop
git config user.name 'Web rollback fixture'
git config user.email 'fixture@example.test'
mkdir -p scripts/jenkins frontend
cp "$fixture"/Jenkinsfile-web "$fixture"/Jenkinsfile.deploy-common.groovy .
cp "$fixture"/deploy-compose.sh scripts/
cp "$fixture"/wait-ci.sh "$fixture"/vault-env.sh scripts/jenkins/
printf 'FROM scratch\n' > frontend/frontend.Dockerfile
cat > frontend/docker-compose-frontend.yml <<'EOF'
services:
  web-prod:
    image: trading-desk-web:prod
EOF
printf 'baseline\n' > frontend/rehearsal.txt
git add .
git commit --quiet -m 'Fixture baseline'
git rev-parse HEAD > "$fixture/baseline-sha"
printf 'probe failure\n' > frontend/rehearsal.txt
git add frontend/rehearsal.txt
git commit --quiet -m 'Fixture web probe failure'
git rev-parse HEAD > "$fixture/rejected-sha"
git clone --quiet --bare . "$fixture/http/scm.git"
git --git-dir="$fixture/http/scm.git" update-server-info
