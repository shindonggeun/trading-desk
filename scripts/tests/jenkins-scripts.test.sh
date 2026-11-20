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
  */auth/approle/login) [ -z "${FAKE_LOGIN_FAIL:-}" ] || exit 22; echo '{"auth":{"client_token":"tok-SECRET-123"}}' ;;
  */auth/token/revoke-self) touch "$FAKE_DIR/revoked" ;;
  */v1/kv/data/*) [ -z "${FAKE_READ_FAIL:-}" ] || exit 22; cat "$FAKE_DIR/secret.json" ;;
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

FRONT=$(rm -rf "$FAKE_DIR"; mkdir -p "$FAKE_DIR"; echo '{"data":{"data":{"DESK_DOCKER_NETWORK":"trading-desk-prod","DESK_WEB_BIND":"192.168.0.9","DESK_WEB_PORT":"10088"}}}' > "$FAKE_DIR/secret.json"
  env VAULT_BASE=https://vault.example VAULT_PATH=kv/trading-desk/frontend/prod/env ENV_FILE="$TMP/web.env" \
      COMPOSE_FILES="$REPO_ROOT/frontend/docker-compose-frontend.yml" VAULT_ROLE_ID=r VAULT_SECRET_ID=s \
      bash "$REPO_ROOT/scripts/jenkins/vault-env.sh" > /dev/null 2>&1; echo $?; paste -sd'|' "$TMP/web.env")
check "웹 compose 키" "0 DESK_DOCKER_NETWORK='trading-desk-prod'|DESK_WEB_BIND='192.168.0.9'|DESK_WEB_PORT='10088'" "$(echo $FRONT)"

# ================= wait-ci.sh
wait_ci() {  # 인자 = 차례로 돌려줄 runs JSON
  rm -rf "$FAKE_DIR"; mkdir -p "$FAKE_DIR"; local i=0 j
  for j in "$@"; do i=$((i + 1)); printf '%s' "$j" > "$FAKE_DIR/runs.$i.json"; done
  CI_REPO=o/r CI_SHA=abc123 CI_POLL_SEC=0 GITHUB_TOKEN=ghs_SECRET bash "$REPO_ROOT/scripts/jenkins/wait-ci.sh" > "$TMP/out" 2>&1
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

if [ "$FAILS" = 0 ]; then echo "모두 통과"; else echo "${FAILS}개 실패"; exit 1; fi
