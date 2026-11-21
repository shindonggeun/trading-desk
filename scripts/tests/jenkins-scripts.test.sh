#!/usr/bin/env bash
# scripts/jenkins/vault-env.sh · wait-ci.sh 를 가짜 curl 로 돌려 본다 (#692 · #691). 네트워크 · Vault · GitHub 에 닿지 않는다.
# Jenkinsfile 의 sh 블록은 dash 로 검사한다(dash 가 없으면 건너뛴다). vault-env · wait-ci 는 jq 가 있어야 한다(CI · Jenkins agent 에는 있다). 없으면 건너뛴다.
#
#   bash scripts/tests/jenkins-scripts.test.sh
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
FAILS=0
check() {  # $1 이름, $2 기대, $3 실제
  if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: 기대 '$2' 실제 '$3'"; sed 's/^/     | /' "$TMP/out"; FAILS=$((FAILS + 1)); fi
}

# ---- 가짜 curl: 마지막 인자가 URL. 명령 인자는 calls 에, 표준입력은 stdin 에 남긴다
mkdir -p "$TMP/bin"
cat > "$TMP/bin/curl" <<'FAKE'
#!/usr/bin/env bash
url=${*: -1}
echo "curl $*" >> "$FAKE_DIR/calls"
cat >> "$FAKE_DIR/stdin" 2>/dev/null || true
case "$url" in
  */auth/approle/login)
    [ -z "${FAKE_LOGIN_FAIL:-}" ] || exit 22
    if [ -n "${FAKE_EXPECT_SECRET_ID:-}" ]; then
      jq -e '.role_id == "role-SECRET" and .secret_id == env.FAKE_EXPECT_SECRET_ID' "$FAKE_DIR/stdin" >/dev/null || exit 22
    fi
    echo '{"auth":{"client_token":"tok-SECRET-123"}}'
    ;;
  */auth/token/revoke-self) touch "$FAKE_DIR/revoked" ;;
  */v1/kv/data/*) [ -z "${FAKE_READ_FAIL:-}" ] || exit 22; cat "$FAKE_DIR/secret.json" ;;
  https://api.github.com/repos/*/git/ref/heads/*)     # develop 머리(#752): 파일이 없으면 API 오류
    [ -f "$FAKE_DIR/head.json" ] || exit 22; cat "$FAKE_DIR/head.json" ;;
  https://api.github.com/*head_sha=def456*)           # 더 새 커밋의 실행: FAIL 이면 API 오류
    [ "$(cat "$FAKE_DIR/headruns.json")" != FAIL ] || exit 22; cat "$FAKE_DIR/headruns.json" ;;
  https://api.github.com/*)
    n=$(( $(cat "$FAKE_DIR/n" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$FAKE_DIR/n"
    f="runs.$n.json"; [ -f "$FAKE_DIR/$f" ] || f=$(cd "$FAKE_DIR" && ls runs.*.json | sort -t. -k2 -n | tail -n 1)
    cat "$FAKE_DIR/$f" ;;
  *) exit 7 ;;
esac
FAKE
chmod +x "$TMP/bin/curl"
export PATH="$TMP/bin:$PATH" FAKE_DIR="$TMP/fake"

# ================= Jenkinsfile 의 sh 블록은 /bin/sh(dash)로 돈다 (#692: `${running:0:12}` → Bad substitution)
# 블록을 모두 뽑아 dash -n 과 bash 전용 문법 검사를 하고, 배포 대상 확인 블록은 dash 로 실제로 돌린다.
if command -v dash > /dev/null; then
  SHDIR="$TMP/sh"; mkdir -p "$SHDIR"
  for f in "$REPO_ROOT"/Jenkinsfile*; do
    b=$(basename "$f")
    awk -v dir="$SHDIR" -v name="$b" -v q="'''" '
      inblk { if (index($0, q)) { inblk = 0; close(out) } else print > out; next }
      index($0, q) { n++; out = dir "/" name ".multi" n ".sh"; inblk = 1 }
    ' "$f"
    grep -v "'''" "$f" | grep -oE "(sh|script:) '[^']+'" | sed -E "s/^(sh|script:) '//; s/'\$//" \
      | awk -v dir="$SHDIR" -v name="$b" '{ n++; print > (dir "/" name ".line" n ".sh") }'
  done
  check "sh 블록을 찾는다(여러 줄 1개 이상)" yes "$(ls "$SHDIR"/*.multi*.sh > /dev/null 2>&1 && echo yes)"
  BASHISM='\[\[|\$\{[A-Za-z_][A-Za-z0-9_]*:[^-=?+}]|\$\{[A-Za-z_][A-Za-z0-9_]*//?|\$\{#?[A-Za-z_][A-Za-z0-9_]*\[|[A-Za-z_][A-Za-z0-9_]*=\(|<\(|>\(|pipefail|&>|\$'"'"'|^[[:space:]]*function[[:space:]]|\[ [^]]* == '
  bad=""
  for s in "$SHDIR"/*.sh; do
    dash -n "$s" 2> /dev/null || bad="$bad $(basename "$s"):syntax"
    grep -qE "$BASHISM" "$s" && bad="$bad $(basename "$s"):$(grep -nE "$BASHISM" "$s" | head -n 1 | cut -d: -f1)"
  done
  check "sh 블록에 dash 가 못 읽는 문법이 없다" "" "$bad"
  printf '%s\n' 'echo "${x:0:12}"' '[[ a ]]' 'echo "${x:-ok}"' > "$TMP/bashism.sh"
  check "검사가 bash 문법만 잡는다(\${x:-} 는 POSIX)" 2 "$(grep -cE "$BASHISM" "$TMP/bashism.sh")"

  # 배포 대상 확인(needsDeploy): 가짜 docker 가 운영 라벨을 돌려주고, 임시 git 저장소에서 차이를 본다
  ND=$(grep -l 'org.opencontainers.image.revision' "$SHDIR"/*.multi*.sh | head -n 1)
  printf '#!/bin/sh\n[ -n "$FAKE_REV" ] && echo "$FAKE_REV"\nexit 0\n' > "$TMP/bin/docker"; chmod +x "$TMP/bin/docker"
  G="$TMP/repo"; mkdir -p "$G/backend" "$G/frontend"
  git_() { git -C "$G" -c core.autocrlf=false -c user.name=t -c user.email=t@t -c commit.gpgsign=false "$@" > /dev/null; }
  git_ init -q; echo 1 > "$G/backend/a"; git_ add -A; git_ commit -qm c1; C1=$(git -C "$G" rev-parse HEAD)
  echo 1 > "$G/frontend/b"; git_ add -A; git_ commit -qm c2; C2=$(git -C "$G" rev-parse HEAD)
  echo 2 > "$G/backend/a"; git_ add -A; git_ commit -qm c3; C3=$(git -C "$G" rev-parse HEAD)
  needs() {  # $1 운영 라벨, $2 대상 → 종료 코드
    (cd "$G" && env FAKE_REV="$1" CONTAINER=trading-desk-backend SHA="$2" WATCH=backend dash -e "$ND") > "$TMP/out" 2>&1
    echo $?
  }
  check "라벨이 없으면 배포(10)" 10 "$(needs "" "$C3")"
  check "기록에 없는 운영 커밋이면 배포(10)" 10 "$(needs 0123456789abcdef0123456789abcdef01234567 "$C3")"
  check "감시 경로가 그대로면 배포하지 않는다(0)" 0 "$(needs "$C1" "$C2")"
  check "감시 경로가 바뀌면 배포(10)" 10 "$(needs "$C1" "$C3")"
  check "커밋은 12자로 찍는다" 1 "$(grep -c "운영 $(printf %.12s "$C1") → 대상 $(printf %.12s "$C3")\$" "$TMP/out")"
else
  echo "dash 가 없어 Jenkinsfile sh 블록 검사를 건너뛴다"
fi

# ================= scripts/deploy.sh — 운영 컨테이너 라벨과 서버 src/REVISION 이 다르면 한 줄 알린다(#752). ssh 는 가짜
cat > "$TMP/bin/ssh" <<'FAKE'
#!/usr/bin/env bash
case "${*: -1}" in
  *src/REVISION*) printf '%s\n%s\n' "${FAKE_SRC_REV:-}" "${FAKE_RUN_REV:-}" ;;
  *) echo 0 ;;
esac
FAKE
chmod +x "$TMP/bin/ssh"
deploy_dry() {  # $1 src/REVISION, $2 운영 라벨 → 알림 줄 수 (드라이런, web 만 — 아무것도 보내지 않는다)
  (cd "$REPO_ROOT" && FAKE_SRC_REV="$1" FAKE_RUN_REV="$2" bash scripts/deploy.sh --dry-run "$(git rev-parse HEAD)" web) \
    > "$TMP/out" 2>&1
  grep -c '^참고: 운영 백엔드는 2eb087139d9b(컨테이너 라벨' "$TMP/out"
}
check "손 배포 뒤 Jenkins 가 올렸으면 한 줄 알린다" 1 "$(deploy_dry 5d5d404aaaaaaaaa 2eb087139d9bbbbb)"
check "같으면 알리지 않는다" 0 "$(deploy_dry 2eb087139d9bbbbb 2eb087139d9bbbbb)"
check "운영 라벨을 못 읽으면 알리지 않는다" 0 "$(deploy_dry 5d5d404aaaaaaaaa '')"

command -v jq > /dev/null || { echo "jq 가 없어 vault-env · wait-ci 검사를 건너뛴다"; [ "$FAILS" = 0 ] && exit 0; echo "${FAILS}개 실패"; exit 1; }

# ================= vault-env.sh
vault_env() {  # $1 = secret.json 내용, 나머지 = 추가 env
  rm -rf "$FAKE_DIR"; mkdir -p "$FAKE_DIR"; printf '%s' "$1" > "$FAKE_DIR/secret.json"; shift
  env VAULT_BASE=https://vault.example/ VAULT_PATH=kv/trading-desk/backend/prod/env ENV_FILE="$TMP/out.env" \
      COMPOSE_FILES="$REPO_ROOT/backend/docker-compose-backend.yml" VAULT_ROLE_ID=role-SECRET VAULT_SECRET_ID=sid-SECRET "$@" \
      bash "$REPO_ROOT/scripts/jenkins/vault-env.sh" > "$TMP/out" 2>&1
  echo $?
}
GOOD='{"data":{"data":{"DESK_DATABASE_URL":"postgresql+psycopg://u:p%40ss$word@h:5432/d?options=-csearch_path%3Ddesk","DESK_DOCKER_NETWORK":"trading-desk-prod","TOSS_CLIENT_SECRET":"tossSECRETvalue","DESK_WEB_PORT":"10088","NOT_USED_BY_COMPOSE":"x"}}}'

rm -f "$TMP/out.env"
rc=$(vault_env "$GOOD"); check "읽어서 env 파일을 쓴다" 0 "$rc"
check "compose 가 쓰는 키만, 정렬, 작은따옴표" \
  "DESK_DATABASE_URL='postgresql+psycopg://u:p%40ss\$word@h:5432/d?options=-csearch_path%3Ddesk'|DESK_DOCKER_NETWORK='trading-desk-prod'|TOSS_CLIENT_SECRET='tossSECRETvalue'" \
  "$(paste -sd'|' "$TMP/out.env")"
check "env 파일 권한 600" "600" "$(stat -c %a "$TMP/out.env" 2>/dev/null || echo 600)"
check "토큰을 폐기한다" "yes" "$([ -f "$FAKE_DIR/revoked" ] && echo yes)"
SECRETS='tossSECRETvalue|p%40ss|tok-SECRET|role-SECRET|sid-SECRET'
check "값 · 토큰 · AppRole 이 출력에 없다" 0 "$(grep -cE "$SECRETS" "$TMP/out")"
check "값 · 토큰 · AppRole 이 curl 인자에 없다" 0 "$(grep -cE "$SECRETS" "$FAKE_DIR/calls")"
grep -q "NOT_USED_BY_COMPOSE" "$TMP/out"; check "안 쓰는 키는 이름만 알린다" 0 $?

rc=$(vault_env '{"data":{"data":{"DESK_DOCKER_NETWORK":"n"}}}'); check "필수 키가 없으면 멈춘다" 1 "$rc"
grep -q "DESK_DATABASE_URL" "$TMP/out"; check "빠진 필수 키 이름을 알린다" 0 $?
rc=$(vault_env '{"data":{"data":{"DESK_DATABASE_URL":"","DESK_DOCKER_NETWORK":"n"}}}'); check "빈 필수 키도 멈춘다" 1 "$rc"

rc=$(vault_env "$(jq -n '{data:{data:{DESK_DATABASE_URL:"a", DESK_DOCKER_NETWORK:"n", FRED_API_KEY:("it" + ([39]|implode) + "s")}}}')")
check "작은따옴표 값은 멈춘다" 1 "$rc"
check "걸린 키 이름만, 값은 없이" "1 0" "$(grep -c FRED_API_KEY "$TMP/out") $(grep -c "it's" "$TMP/out")"
rc=$(vault_env '{"data":{"data":{"DESK_DATABASE_URL":"a\nb","DESK_DOCKER_NETWORK":"n"}}}'); check "줄바꿈 값은 멈춘다" 1 "$rc"
rc=$(vault_env '{"data":{"data":{"DESK_DATABASE_URL":"pw-ends-with\\","DESK_DOCKER_NETWORK":"n"}}}'); check "역슬래시 값은 멈춘다" 1 "$rc"
rc=$(vault_env "$GOOD" VAULT_BASE=http://vault.example); check "https 가 아니면 로그인하지 않는다" "1 0" "$rc $(grep -c login "$FAKE_DIR/calls" 2>/dev/null || echo 0)"
check "임시 폴더를 남기지 않는다" 0 "$(find "$TMP" -mindepth 1 -maxdepth 1 -name 'tmp.*' | wc -l | tr -d ' ')"
rc=$(vault_env '{"data":{"data":{"DESK_DATABASE_URL":"a","DESK_DOCKER_NETWORK":"n","postgres://leak":"x"}}}')
check "이름 형식이 틀린 키는 이름도 찍지 않는다" "1 0" "$rc $(grep -c leak "$TMP/out")"

rm -f "$TMP/out.env"
rc=$(vault_env "$GOOD" FAKE_LOGIN_FAIL=1); check "로그인 실패면 멈추고 파일을 쓰지 않는다" "1 no" "$rc $([ -f "$TMP/out.env" ] && echo yes || echo no)"
rc=$(vault_env "$GOOD" FAKE_READ_FAIL=1); check "읽기 실패면 멈추고 토큰은 폐기" "1 yes" "$rc $([ -f "$FAKE_DIR/revoked" ] && echo yes)"
rc=$(vault_env '{"data":{"data":{}}}'); check "빈 secret 은 멈춘다" 1 "$rc"

FRONT_GOOD='{"data":{"data":{"DESK_DOCKER_NETWORK":"trading-desk-prod","DESK_WEB_BIND":"192.168.0.9","DESK_WEB_PORT":"10088"}}}'
FRONT=$(rm -rf "$FAKE_DIR"; mkdir -p "$FAKE_DIR"; printf '%s' "$FRONT_GOOD" > "$FAKE_DIR/secret.json"
  env VAULT_BASE=https://vault.example VAULT_PATH=kv/trading-desk/frontend/prod/env ENV_FILE="$TMP/web.env" \
      COMPOSE_FILES="$REPO_ROOT/frontend/docker-compose-frontend.yml" VAULT_ROLE_ID=r VAULT_SECRET_ID=s \
      bash "$REPO_ROOT/scripts/jenkins/vault-env.sh" > /dev/null 2>&1; echo $?; paste -sd'|' "$TMP/web.env")
check "웹 compose 키" "0 DESK_DOCKER_NETWORK='trading-desk-prod'|DESK_WEB_BIND='192.168.0.9'|DESK_WEB_PORT='10088'" "$(echo $FRONT)"

# #691 회전 후에는 같은 role의 새 Secret ID만 허용하는 Vault 대역으로 두 배포 소비 경로를 확인한다.
rm -f "$TMP/out.env"
rc=$(vault_env "$GOOD" FAKE_EXPECT_SECRET_ID=sid-SECRET-new)
check "회전 전 자격 거절 시 KV를 읽거나 env 파일을 쓰지 않는다" "1 0 no" \
  "$rc $(grep -c '/v1/kv/data/' "$FAKE_DIR/calls") $([ -f "$TMP/out.env" ] && echo yes || echo no)"
rc=$(vault_env "$GOOD" VAULT_SECRET_ID=sid-SECRET-new FAKE_EXPECT_SECRET_ID=sid-SECRET-new)
check "새 자격으로 백엔드 KV를 읽고 토큰 폐기를 요청한다" "0 1 yes yes" \
  "$rc $(grep -c '/v1/kv/data/trading-desk/backend/prod/env' "$FAKE_DIR/calls") $([ -f "$FAKE_DIR/revoked" ] && echo yes) $([ -s "$TMP/out.env" ] && echo yes)"
rm -f "$TMP/out.env"
rc=$(vault_env "$FRONT_GOOD" VAULT_SECRET_ID=sid-SECRET-new FAKE_EXPECT_SECRET_ID=sid-SECRET-new \
  VAULT_PATH=kv/trading-desk/frontend/prod/env COMPOSE_FILES="$REPO_ROOT/frontend/docker-compose-frontend.yml")
check "새 자격으로 웹 KV를 읽고 토큰 폐기를 요청한다" "0 1 yes yes" \
  "$rc $(grep -c '/v1/kv/data/trading-desk/frontend/prod/env' "$FAKE_DIR/calls") $([ -f "$FAKE_DIR/revoked" ] && echo yes) $([ -s "$TMP/out.env" ] && echo yes)"
check "새 자격이 curl 인자와 출력에 없다" "0 0" \
  "$(grep -c sid-SECRET-new "$FAKE_DIR/calls") $(grep -c sid-SECRET-new "$TMP/out")"

# ================= wait-ci.sh
wait_ci() {  # 인자 = 차례로 돌려줄 runs JSON. HEAD(develop 머리 SHA) · HEADRUNS(그 실행) · SUP(CI_SUPERSEDE) · MODE(인자)
  rm -rf "$FAKE_DIR"; mkdir -p "$FAKE_DIR"; local i=0 j
  for j in "$@"; do i=$((i + 1)); printf '%s' "$j" > "$FAKE_DIR/runs.$i.json"; done
  [ -z "${HEAD:-}" ] || printf '{"ref":"refs/heads/develop","object":{"sha":"%s","type":"commit"}}' "$HEAD" > "$FAKE_DIR/head.json"
  printf '%s' "${HEADRUNS:-$NONE}" > "$FAKE_DIR/headruns.json"
  CI_REPO=o/r CI_SHA=abc123 CI_POLL_SEC=0 CI_SUPERSEDE="${SUP:-0}" GITHUB_TOKEN=ghs_SECRET     bash "$REPO_ROOT/scripts/jenkins/wait-ci.sh" ${MODE:-} > "$TMP/out" 2>&1
  echo $?
}
run() { printf '{"name":"%s","status":"%s","conclusion":%s,"run_number":%s,"run_attempt":%s,"html_url":"u%s"}' "$@" "$4"; }
NONE='{"workflow_runs":[]}'
PROG="{\"workflow_runs\":[$(run ci in_progress null 7 1)]}"
OK="{\"workflow_runs\":[$(run ci completed '"success"' 7 1),$(run other completed '"failure"' 8 1)]}"
BAD="{\"workflow_runs\":[$(run ci completed '"failure"' 7 1)]}"
CAN="{\"workflow_runs\":[$(run ci completed '"cancelled"' 7 1)]}"
RERUN="{\"workflow_runs\":[$(run ci completed '"failure"' 7 1),$(run ci completed '"success"' 7 2)]}"

check "없음 → 진행 중 → 성공이면 0" 0 "$(wait_ci "$NONE" "$PROG" "$OK")"
check "GitHub 토큰이 인자 · 출력에 없다" "0 0" "$(grep -c ghs_ "$FAKE_DIR/calls") $(grep -c ghs_ "$TMP/out")"
check "다른 워크플로의 실패는 보지 않는다" 0 "$(wait_ci "$OK")"
check "실패면 1" 1 "$(wait_ci "$PROG" "$BAD")"
check "취소(밀림)면 4" 4 "$(wait_ci "$CAN")"
check "다시 돌린 마지막 시도를 본다" 0 "$(wait_ci "$RERUN")"

# #752 밀림: 기다리는 동안 develop 머리가 더 새 커밋(def456)이 되면 4, 그 커밋의 CI 가 이미 실패면 밀림이 아니다
check "밀림 검사를 켜지 않으면 머리를 읽지 않는다" "0 0" "$(HEAD=def456 wait_ci "$PROG" "$OK") $(grep -c /git/ref/ "$FAKE_DIR/calls")"
check "머리가 더 새 커밋이고 그 CI 가 진행 중이면 밀림(4)" 4 "$(SUP=1 HEAD=def456 HEADRUNS="$PROG" wait_ci "$PROG" "$OK")"
check "밀림 줄에 두 커밋" 1 "$(grep -c '^밀림: abc123 → 더 새 def456$' "$TMP/out")"
check "더 새 커밋의 CI 가 성공이어도 밀림(4)" 4 "$(SUP=1 HEAD=def456 HEADRUNS="$OK" wait_ci "$PROG" "$OK")"
check "더 새 커밋의 CI 가 실패면 이 커밋을 배포(0)" 0 "$(SUP=1 HEAD=def456 HEADRUNS="$BAD" wait_ci "$PROG" "$OK")"
check "머리가 이 커밋이면 그대로 기다린다(0)" 0 "$(SUP=1 HEAD=abc123 wait_ci "$PROG" "$OK")"
check "더 새 커밋의 CI 가 취소면 밀림이 아니다(0)" 0 "$(SUP=1 HEAD=def456 HEADRUNS="$CAN" wait_ci "$PROG" "$OK")"
check "더 새 커밋의 실행을 못 읽으면 밀림이 아니다(0)" 0 "$(SUP=1 HEAD=def456 HEADRUNS=FAIL wait_ci "$PROG" "$OK")"
check "머리를 못 읽어도 계속 기다린다(0)" 0 "$(SUP=1 wait_ci "$PROG" "$OK")"
check "이 커밋이 이미 성공이면 머리를 보지 않고 배포(0)" 0 "$(SUP=1 HEAD=def456 HEADRUNS="$PROG" wait_ci "$OK")"
check "밀림 검사 중에도 이 커밋의 실패는 1" 1 "$(SUP=1 HEAD=abc123 wait_ci "$BAD")"

# 배포 직전(lock 안) 한 번만: --superseded-only
check "배포 직전 밀림(4)" 4 "$(MODE=--superseded-only HEAD=def456 HEADRUNS="$NONE" wait_ci "$OK")"
check "배포 직전 — 실행 목록을 기다리지 않는다" 0 "$(grep -c head_sha=abc123 "$FAKE_DIR/calls")"
check "배포 직전 — 더 새 커밋 CI 실패면 배포(0)" 0 "$(MODE=--superseded-only HEAD=def456 HEADRUNS="$BAD" wait_ci "$OK")"
check "배포 직전 — 머리가 이 커밋이면 배포(0)" 0 "$(MODE=--superseded-only HEAD=abc123 wait_ci "$OK")"
check "배포 직전 — GitHub 를 못 읽으면 배포(0)" 0 "$(MODE=--superseded-only wait_ci "$OK")"
check "배포 직전 — 못 읽었다고 적는다" 1 "$(grep -c '머리를 읽지 못했다' "$TMP/out")"
check "배포 직전 — 더 새 커밋 실행을 못 읽으면 배포(0)" 0 "$(MODE=--superseded-only HEAD=def456 HEADRUNS=FAIL wait_ci "$OK")"
check "배포 직전 — 더 새 커밋 실행이 JSON 이 아니면 배포(0)" 0 "$(MODE=--superseded-only HEAD=def456 HEADRUNS='<html>' wait_ci "$OK")"
check "배포 직전 — 더 새 커밋 CI 취소면 배포(0)" 0 "$(MODE=--superseded-only HEAD=def456 HEADRUNS="$CAN" wait_ci "$OK")"
check "배포 직전 — 토큰이 인자 · 출력에 없다" "0 0" "$(grep -c ghs_ "$FAKE_DIR/calls") $(grep -c ghs_ "$TMP/out")"

if [ "$FAILS" = 0 ]; then echo "모두 통과"; else echo "${FAILS}개 실패"; exit 1; fi
