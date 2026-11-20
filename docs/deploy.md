# 배포 — backend-server · trading.8llow8llowme.com

## 1. 배치

```
인터넷 ──443──▶ storage(192.168.0.12)  nginx 인그레스 + certbot + fail2ban      ← Infra 저장소 nginx/conf.d/trading.8llow8llowme.conf
                     │ X-Real-IP 를 붙여 192.168.0.9:10088 로
                     ▼
              backend-server(192.168.0.9, x86_64)
                ├─ trading-desk-web-prod       nginx + React 빌드   192.168.0.9:10088 (사설 IP 에만 바인드)
                │      │ docker 네트워크 trading-desk-prod
                │      ▼
                └─ trading-desk-backend-prod   FastAPI + TradingAgents   호스트 포트 없음
                       │ 볼륨 trading-desk-tradingagents-home (ChatGPT 로그인 · 리포트 · 캐시)
                       ▼
              main-server(192.168.0.11:5432)  PostgreSQL — DB trading_desk · 계정 trading_desk · 스키마 desk
```

| 파일 | 내용 |
|------|------|
| `backend/backend.Dockerfile` (+ `.dockerignore`) | 멀티 스테이지. 의존성 층과 소스 층을 갈라 `desk/` 만 바뀌면 TradingAgents 를 다시 설치하지 않는다. 비루트 `app` |
| `backend/docker-compose-backend.yml` | `backend-prod`. 앱이 읽는 키만 `environment` 로 넘기고 필수 키는 `${KEY:?}` 로 막는다 |
| `frontend/frontend.Dockerfile` (+ `.dockerignore`) | 멀티 스테이지. Vite 빌드 → nginx. `nginx.conf.template` 의 `${BACKEND_UPSTREAM}` 을 기동 시 치환 |
| `frontend/docker-compose-frontend.yml` | `web-prod`. 외부에 여는 유일한 컨테이너 |
| `docker-compose-local.yml` | PC 에서 PostgreSQL 경로를 시험할 때만 쓰는 DB |
| `Jenkinsfile-backend` · `Jenkinsfile-web` · `Jenkinsfile.deploy-common.groovy` | develop 머지 뒤 자동 배포(2-1). 얇은 Jenkinsfile 이 공통부를 `load` 한다 |
| `scripts/deploy-compose.sh` | 배포 호스트에서 빌드 · 진행 중 분석 대기 · 교체 · 확인 · 실패 시 되돌림. Jenkins 와 손 배포가 같이 쓴다 |
| `scripts/jenkins/wait-ci.sh` · `vault-env.sh` | GitHub Actions `ci` 성공 대기 · Vault 에서 env 파일 만들기 |
| `scripts/deploy.sh` | 손 배포(대체 경로, 2-4). 커밋 하나를 `git archive` 로 보내 서버에서 `deploy-compose.sh` 를 부른다 |
| `scripts/tests/*.test.sh` | 위 스크립트를 가짜 docker · curl 로 돌리는 시험(CI `deploy-scripts`) |

compose 프로젝트 이름은 `trading-desk-backend-prod` · `trading-desk-web-prod` 로 고정한다(`-p`). 빠지면 디렉터리 이름이 프로젝트가 돼
`--remove-orphans` 가 남의 컨테이너를 지울 수 있다. 이미지는 `trading-desk-backend:prod` · `trading-desk-web:prod` 이고,
배포할 때마다 `:<커밋 12자>` 로 빌드해 `:prod` 로 바꾸며 직전 것을 `:rollback` 으로 남긴다. 이미지 라벨 `org.opencontainers.image.revision` 에 커밋이 있다.

## 2. 배포

**`develop` 이 배포본이다**(`docs/git-workflow.md` §8 — `main` 은 쓰지 않는다). develop 에 머지하면 Jenkins 가 CI 성공을 기다렸다가 올린다(2-1).
손 배포(2-4)는 Jenkins · Vault 를 쓸 수 없을 때의 대체 경로다. 두 경로 모두 서버에서 같은 `scripts/deploy-compose.sh` 가 빌드 · 교체 · 확인 · 되돌림을 한다.

### 2-1. 자동 배포 — Jenkins (#692)

```
develop push ─▶ Jenkins 폴더 trading-desk
                  ├─ trading-desk-backend (Jenkinsfile-backend) ─┐ 같은 lock trading-desk-prod-deploy 로 차례로
                  └─ trading-desk-web     (Jenkinsfile-web)     ─┘
   1. CI 확인     label builder (ai-host-builder)        GitHub Actions `ci` 가 그 커밋에서 성공할 때까지 (최대 45분)
   2. 배포 대상   label deploy-backend-prod · deploy-frontend-prod (둘 다 backend-server)
                  운영 컨테이너의 커밋 라벨과 비교해 backend/ (웹은 frontend/) 가 바뀌었을 때만
   3. 운영 배포   Vault → 임시 env 파일(600) → scripts/deploy-compose.sh → env 파일 삭제
```

| 단계 | 하는 일 | 멈추면 |
|------|---------|--------|
| CI 확인 | `scripts/jenkins/wait-ci.sh` 가 Actions API 로 그 커밋의 push 실행(워크플로 `ci`)을 30초마다 본다. 다시 돌린 실행이 있으면 마지막 시도 | 실패 → 빌드 실패, 운영 그대로. 취소(`ci.yml` concurrency 가 뒤 커밋에 밀어냄) → NOT_BUILT, 뒤 커밋의 빌드가 배포 |
| 배포 대상 | 운영 컨테이너 이미지 라벨의 커밋과 대상 커밋 사이 `git diff -- backend`(웹 `frontend`). 라벨이 없는 옛 이미지면 배포 | 차이가 없으면 아무것도 하지 않는다("변경 없음") |
| Vault | `scripts/jenkins/vault-env.sh` 가 `kv/trading-desk/{backend,frontend}/prod/env` 를 읽어 compose 가 쓰는 키만 `KEY='value'` 로 쓴다(3절) | 봉인 · 네트워크 · 권한 · 필수 키 없음 → **아무것도 바꾸기 전에** 멈춘다. 운영 그대로 |
| 빌드 | `<이름>:<커밋 12자>` 이미지, 라벨에 커밋 | 빌드 실패 → 운영 그대로 |
| 분석 대기 (#431, 백엔드) | 컨테이너를 바꾸기 직전 `desk.cli unfinished` 로 묻고 최대 30분 기다린다 | 넘기면 멈춘다(종료 코드 3, 운영 그대로) → 끊어도 되면 `FORCE_STOP_RUNS` |
| 교체 | 운영 이미지를 `:rollback` 으로 남기고 새 이미지를 `:prod` 로. **api(`primaryService`, 기동 시 Alembic `upgrade head`)를 먼저** `--wait`, compose 의 나머지 서비스(#688 collector · analyst)를 그다음 | → 되돌림(2-2) |
| 확인 | 모든 서비스 컨테이너의 커밋 라벨 = 대상 커밋 · 컨테이너 안에서 `/api/health` 가 `{"ok":true}`(웹은 `/`) · 30초 동안 재시작 없이 running · healthy | → 되돌림(2-2) |

잡 파라미터(Build with Parameters):

| 파라미터 | 뜻 |
|----------|-----|
| `DEPLOY_COMMIT` | 비우면 develop 끝. 이전 커밋으로 되돌릴 때 develop 에 있는 SHA(그 커밋의 CI 가 성공했어야 한다). 소스는 그 커밋, 스크립트는 develop 끝 것을 쓴다 |
| `FORCE_DEPLOY` | 운영과 차이가 없어도 다시 빌드 · 배포 (첫 배포 · Vault 값만 바꿨을 때) |
| `FORCE_STOP_RUNS` | (백엔드) 진행 중인 분석을 기다리지 않는다 — 그 분석은 실패로 끝난다 |
| `ROLLBACK_DRILL` | 새 이미지로 바꾼 뒤 확인을 일부러 실패시켜 되돌림을 시험한다(2-2) |

- 같은 커밋에서 backend/ · frontend/ 가 함께 바뀌면 두 잡이 lock 을 차례로 잡는다 — 어느 쪽이 먼저 바뀔지 정해져 있지 않으니 API 를 바꾸는 PR 은 이전 · 다음 화면 둘 다와 맞게 둔다.
- Jenkins 는 `~/deploy/trading-desk/src` 를 고치지 않는다. 운영 커밋은 컨테이너 라벨로 본다(2-3).

### 2-2. 되돌림

- **자동**: 교체 뒤 확인이 실패하면 `:rollback` 을 `:prod` 로 되돌려 모든 서비스를 다시 띄우고 같은 확인을 한다(기대 커밋 = 직전 커밋). 빌드는 실패로, 설명에 "되돌림" 이 붙는다.
  실패한 이미지는 `<이름>:<커밋 12자>` 로 남는다(다음 성공 배포가 지운다). 로그에는 컨테이너 로그 60줄을 env 값을 가린 채 찍는다.
- **자동으로 되돌리지 않는 경우** — 빌드는 실패로 끝나고 새 컨테이너가 그대로 남는다. 사람이 본다.
  1. 첫 배포(직전 이미지 없음)
  2. **새 Alembic 리비전이 들어간 배포**: 옛 이미지는 기동 시 `upgrade head` 에서 모르는 리비전을 만나 뜨지 못한다. 기본은 고친 커밋을 develop 에 올리는 것(앞으로 고침)이다.
     꼭 되돌려야 하면 새 컨테이너 안에서 직전 리비전으로 내린 뒤(데이터가 지워질 수 있다 — 리비전의 `downgrade` 를 먼저 읽는다) `DEPLOY_COMMIT=<직전 커밋>` 으로 다시 배포한다.
     ```bash
     ssh homelab-backend-server "docker exec trading-desk-backend-prod python -c \"import os; from alembic import command; from desk.db import alembic_config; command.downgrade(alembic_config(os.environ['DESK_DATABASE_URL']), '<직전 리비전 id>')\""
     ```
- **손으로 이전 커밋**: Jenkins 잡 → Build with Parameters → `DEPLOY_COMMIT=<develop 의 이전 커밋>` (다시 빌드한다, 몇 분).
- **드릴**(#692 완료 기준): 마이그레이션이 없는 커밋에서 `FORCE_DEPLOY=true` · `ROLLBACK_DRILL=true` 로 돌리면 "ROLLBACK_DRILL: 확인을 일부러 실패시킨다" → "직전 이미지로 되돌렸다" 가 찍히고, 운영 라벨이 직전 커밋으로 남아야 한다.

### 2-3. 운영 커밋 · 상태 확인

```bash
ssh homelab-backend-server "docker inspect -f '{{.Name}} {{index .Config.Labels \"org.opencontainers.image.revision\"}}' trading-desk-backend-prod trading-desk-web-prod; docker ps --filter name=trading-desk"
curl -s https://trading.8llow8llowme.com/api/health        # {"ok":true}
```

라벨이 비면 이 변경 전 손 배포로 올린 이미지다 — 그때만 `cat ~/deploy/trading-desk/src/REVISION`.

### 2-4. 손 배포 — `scripts/deploy.sh` (대체 경로)

Jenkins · Vault 를 쓸 수 없을 때만 쓴다. env 는 서버 `.env.runtime` 이라, **먼저 Vault 값과 맞춘다**(3절) — 안 맞추면 옛 값으로 뜬다.

```bash
scripts/deploy.sh                  # HEAD 를 올린다 (커밋 안 된 변경이 있으면 멈춘다)
scripts/deploy.sh <커밋|태그>
scripts/deploy.sh HEAD backend     # 백엔드만 (web | backend | all)
scripts/deploy.sh --dry-run <커밋> backend   # 진행 중인 분석만 확인하고 배포하지 않는다
scripts/deploy.sh --force <커밋> backend     # 진행 중인 분석이 있어도 바로 올린다 (그 분석은 실패로 끝난다)
```

**진행 중인 분석을 기다린다**(#431). 백엔드를 다시 띄우면 진행 중인 분석이 끊긴다("서버가 다시 시작돼 분석이 중단됐습니다").
그래서 `backend` · `all` 배포는 먼저 서버의 백엔드 컨테이너 안에서 `python -m desk.cli unfinished` 로
queued/running run 을 묻는다(SSH 와 `docker exec` 만 쓰고 세션 · 비밀값은 쓰지 않는다).

- 없으면 그대로 진행한다.
- 있으면 종목 · 상태를 보이고 30초마다 다시 묻는다. `DESK_DEPLOY_WAIT_MIN` 분(기본 30)이 지나도 남아 있으면 종료 코드 3 으로 멈춘다 — 끊어도 되면 `--force`.
- 새 백엔드 이미지를 빌드한 뒤, 컨테이너를 바꾸기 직전에 서버의 `deploy-compose.sh` 가 한 번 더 묻는다. 빌드하는 몇 분 사이에 제출된 분석도 같은 규칙으로 기다린다. 거기서 멈추면 `src/` 를 직전 배포로 되돌린다.
- 그 뒤 교체 · 확인 · 실패 시 되돌림은 Jenkins 와 같다(2-1 · 2-2). 종료 코드 0 배포됨 · 1 실패(되돌리지 못함) · 2 되돌림 · 3 분석 대기로 멈춤.
- SSH 로 묻지 못하면(접속 실패) 배포하지 않고 종료 코드 3 으로 멈춘다.
- `--dry-run` 은 확인만 한다. 진행 중인 분석이 있으면 종료 코드 3, 없으면 0.
- `web` 만 올릴 때는 묻지 않는다. 컨테이너가 없거나 이 명령이 없는 옛 이미지면 확인할 수 없다고 알리고 진행한다.

끊긴 분석은 실패 화면의 "다시 분석" 버튼으로 같은 종목 · 같은 설정의 새 run 을 낼 수 있다.

서버 `~/deploy/trading-desk/` 구조:

```
.env.runtime   손 배포용 비밀 · 설정 (권한 600) — 저장소에 없다. Vault 로 옮긴 뒤에는 Vault 와 맞춰 둔 사본
src/           손 배포로 마지막에 올린 소스 (REVISION 에 커밋). Jenkins 배포는 이 폴더를 쓰지 않는다
src.old/       그 직전 손 배포
```

순서:

1. `git fetch origin` 후 올릴 커밋 = `origin/develop` 최신. 그 커밋의 CI 가 통과했는지 본다 — `gh run list --branch develop --limit 1`
2. 지금 운영 커밋(2-3)과 비교해 바뀐 쪽만 올린다 — `git diff --stat <운영 커밋> origin/develop`
3. 커밋을 지정해 올린다. 작업 트리가 다른 브랜치에 있어도 지정한 커밋만 나간다

```bash
scripts/deploy.sh "$(git rev-parse --short origin/develop)" backend   # backend/ 만 바뀌었을 때 (web | all)
```

배포 뒤 확인은 2-3.

### 2-5. Jenkins 잡 · credential 등록 (사람이 한 번)

작업 세션은 자격 증명을 받지 않는다. 값 자리는 `<...>` 이고 값은 채팅 · 로그 · 커밋에 쓰지 않는다. 순서:

1. **Vault 정책 · AppRole** — Infra 저장소의 `vault/scripts/bootstrap-trading-desk.sh` 를 ai-host 에서 root 토큰으로 돌린다(Infra `vault/README.md` trading-desk 절).
   root 토큰은 `read -s` 로 받아 이름만 쓴 `-e VAULT_TOKEN` 으로 넘긴다 — 명령 인자(`ps`) · 셸 history 에 남지 않게.
   ```bash
   read -rs VAULT_TOKEN && export VAULT_TOKEN          # root 토큰을 붙여 넣는다(화면에 안 찍힘)
   docker exec -e VAULT_TOKEN -e VAULT_UI_USERNAME=<UI 계정> vault sh /vault/scripts/bootstrap-trading-desk.sh
   docker exec -e VAULT_TOKEN vault vault read -field=role_id auth/approle/role/jenkins-trading-desk/role-id
   docker exec -e VAULT_TOKEN vault vault write -f -field=secret_id auth/approle/role/jenkins-trading-desk/secret-id
   unset VAULT_TOKEN
   ```
2. **Vault 에 값 넣기** — 3절 "`.env.runtime` → Vault 옮기기".
3. **Jenkins 폴더 `trading-desk`** 를 만들고 폴더 범위 credential 3개를 넣는다(전역에 두지 않는다 — 다른 프로젝트 잡이 쓰지 못하게).

   | ID | 종류 | 값 |
   |----|------|-----|
   | `trading-desk-vault-role-id` | Secret text | 1 의 role_id |
   | `trading-desk-vault-secret-id` | Secret text | 1 의 secret_id |
   | `trading-desk-github` | Username with password (또는 GitHub App) | username `shindonggeun`, password = fine-grained PAT — 저장소 `shindonggeun/trading-desk` 만, 권한 Contents · Actions · Metadata **읽기**만. 홈랩 GitHub App 을 이 저장소에 설치할 수 있으면 그 App credential 을 같은 ID 로 |

   전역 env `VAULT_ADDR`(`https://vault.8llow8llowme.com`)는 commerce-foundry 때 이미 있다. 잡 파라미터로 `VAULT_ADDR` 를 만들지 않는다(전역 값을 가린다).
4. **멀티브랜치 파이프라인 2개** — 폴더 `trading-desk` 안에 `trading-desk-backend`(Script Path `Jenkinsfile-backend`) · `trading-desk-web`(Script Path `Jenkinsfile-web`).
   - Branch Source: GitHub, credential `trading-desk-github`, 저장소 `https://github.com/shindonggeun/trading-desk`
   - Behaviours: Discover branches 만 남긴다. **Discover pull requests from origin · from forks 는 지운다**(공개 저장소 — fork PR 의 Jenkinsfile 이 운영 agent 에서 돌면 안 된다).
     Filter by name (with wildcards) Include `develop`.
   - 트리거: develop push 가 빌드를 띄워야 자동 배포가 된다. GitHub → 저장소 Settings → Webhooks 에 Jenkins 의 `/github-webhook/` (push 이벤트)를 걸 수 있으면 그것을,
     Jenkins 가 GitHub 에서 닿지 않으면 Scan Multibranch Pipeline Triggers → Periodically if not otherwise run 을 `2 minutes` 로.
     "Suppress automatic SCM triggering" 은 켜지 않는다(켜면 인덱싱 · 이벤트로 빌드가 뜨지 않는다 — 켜야 한다면 전략을 인덱싱에만).
   - lock `trading-desk-prod-deploy` 는 첫 빌드가 만든다(Lockable Resources).
5. **첫 배포 · 드릴** — `trading-desk-backend` 의 develop → Build with Parameters → `FORCE_DEPLOY=true`, 이어서 `trading-desk-web` 도. 지금 운영 이미지에는 커밋 라벨이 없어 첫 자동 빌드도 배포한다.
   그다음 마이그레이션이 없는 커밋에서 `FORCE_DEPLOY=true` · `ROLLBACK_DRILL=true` 로 되돌림을 시험하고 결과를 #692 에 적는다(2-2).
6. Infra `jenkins/README.md` 의 trading-desk 잡 절과 노드 표(같은 agent 를 쓴다)를 확인한다.

## 3. 운영 env — Vault (정본) · `.env.runtime` (손 배포 대체)

**정본은 홈랩 Vault(KV v2, ai-host)다(#691).** Jenkins 가 배포 때마다 읽어 임시 파일로 compose 에 넘기고 지운다. 앱은 env 를 그대로 읽는다(Vault SDK 없음).

```text
kv/trading-desk/backend/prod/env    # 백엔드 compose 가 쓰는 키 전부(api · collector · analyst 공용) — 키 목록 정본: 루트 .env.example
kv/trading-desk/frontend/prod/env   # 웹 compose 보간 키만: DESK_DOCKER_NETWORK · DESK_WEB_BIND · DESK_WEB_PORT (· DESK_WEB_MEM_LIMIT)
```

- 환경당 secret 하나, **키 하나에 값 하나**(`.env` 통째로 한 키에 넣지 않는다). 값에 작은따옴표 · 줄바꿈을 넣지 않는다 — 파이프라인이 `KEY='value'` 로 쓰고, 걸리면 키 이름만 알리고 멈춘다.
- compose 가 쓰지 않는 키는 env 파일에 넣지 않는다(이름만 알린다). Vault 에 없는 선택 키는 compose 기본값(`${KEY:-기본}`)을 쓴다.
- 키를 더하면 compose · `.env.example` 을 같은 PR 에서 고치고(`tests/test_env_keys.py`), 값은 Vault 에 `vault kv patch` 로 더한 뒤 `FORCE_DEPLOY` 로 배포한다. `kv put` 은 secret 전체를 덮어쓴다.
- 아래 절들(4 · 5 · 6 · 9 · 10 · 11)과 `security.md` 의 "`.env.runtime` 에 넣고 `scripts/deploy.sh HEAD backend`" 는 옮긴 뒤에는
  "Vault `kv/trading-desk/backend/prod/env` 에 `kv patch` 하고 `trading-desk-backend` 를 `FORCE_DEPLOY` 로" 로 읽는다. `.env.runtime` 사본도 같이 고쳐 둔다.
- **Vault 가 잠깐 안 될 때**(봉인 · 재시작 · 네트워크): 배포는 Vault 단계에서 아무것도 바꾸기 전에 멈춘다(빌드 실패). 실행 중인 컨테이너는 그대로 돈다 —
  env 는 기동 때 컨테이너 설정에 들어가 있어, 컨테이너 재시작 · 서버 재부팅(`restart: unless-stopped`)에도 Vault 없이 같은 값으로 다시 뜬다.
  Vault 가 오래 안 되는데 꼭 배포해야 하면 손 배포(2-4)를 `.env.runtime` 으로.

### `.env.runtime` → Vault 옮기기 (#691, 한 번)

값은 화면 · 채팅 · 로그 · 커밋 · 명령 인자에 내지 않고, 평문 사본 파일도 만들지 않는다(파이프로만 넘긴다). 키 이름만 본다.

```bash
ssh homelab-backend-server
cd ~/deploy/trading-desk
# 1) .env.runtime 을 셸이 읽는 것처럼 풀어 JSON 하나를 표준출력으로 낸다. 인자: backend | frontend | check(키 이름 · 걸리는 키만)
read -r -d '' TO_JSON <<'PY'
import json, pathlib, shlex, sys
env = {}
for line in pathlib.Path(".env.runtime").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, raw = line.split("=", 1)
    words = shlex.split(raw)
    env[key.removeprefix("export ").strip()] = words[0] if words else ""
web = {"DESK_DOCKER_NETWORK", "DESK_WEB_BIND", "DESK_WEB_PORT", "DESK_WEB_MEM_LIMIT"}
part = {"backend": {k: v for k, v in env.items() if k not in web - {"DESK_DOCKER_NETWORK"}},
        "frontend": {k: v for k, v in env.items() if k in web}}
if sys.argv[1] == "check":
    bad = sorted(k for k, v in env.items() if any(c in v for c in "'\\\n\r"))
    print("backend:", " ".join(sorted(part["backend"])), "\nfrontend:", " ".join(sorted(part["frontend"])), "\n걸리는 키(작은따옴표 · 역슬래시 · 줄바꿈):", bad)
else:
    print(json.dumps({"data": part[sys.argv[1]]}))
PY
python3 -c "$TO_JSON" check        # 걸리는 키가 있으면 값을 먼저 바꾼다(그 키의 발급처에서)
# 2) Vault Web UI 에 ui-trading-desk 가 붙은 계정으로 로그인해 토큰을 복사해 둔다(화면에 찍히지 않게 read -s)
read -rs VAULT_TOKEN && export VAULT_TOKEN
for part in backend frontend; do
  python3 -c "$TO_JSON" "$part" | curl -sS --fail -o /dev/null -X POST \
    --header @<(printf 'X-Vault-Token: %s\n' "$VAULT_TOKEN") --data @- \
    "https://vault.8llow8llowme.com/v1/kv/data/trading-desk/$part/prod/env" && echo "$part 넣음"
done
# 3) 이름만 확인
for part in backend frontend; do
  curl -sS --fail --header @<(printf 'X-Vault-Token: %s\n' "$VAULT_TOKEN") \
    "https://vault.8llow8llowme.com/v1/kv/data/trading-desk/$part/prod/env" | jq -r '.data.data | keys | join(" ")'
done
unset VAULT_TOKEN TO_JSON
```

`.env.runtime` 은 손 배포 대체용으로 그대로 둔다(사본 · 백업 파일을 더 만들지 않는다). 그 뒤로 값을 바꾸면 Vault 와 `.env.runtime` 을 같이 고친다.

그다음 Jenkins `trading-desk-backend` · `trading-desk-web` 을 `FORCE_DEPLOY=true` 로 돌려 Vault env 로 뜨는지 본다(2-3).

### 필수 키

키 이름은 루트 `.env.example` 과 같다. 필수(Vault · `.env.runtime` 둘 다):

| 키 | 값 |
|----|-----|
| `DESK_DATABASE_URL` | `postgresql+psycopg://trading_desk:<비밀번호>@192.168.0.11:5432/trading_desk?options=-csearch_path%3Ddesk` |
| `DESK_DOCKER_NETWORK` | `trading-desk-prod` |
| `DESK_WEB_BIND` · `DESK_WEB_PORT` | `192.168.0.9` · `10088` |

DB · 계정은 Infra 저장소의 `PostgreSQL/scripts/create-project-db.sh trading_desk trading_desk desk` 로 만들었다(2026-11-04).
비밀번호를 바꿀 때는 같은 스크립트를 새 값으로 다시 돌리고 `.env.runtime` 을 고친 뒤 `scripts/deploy.sh HEAD backend`.

## 4. 처음 한 번 — 로그인 계정

가입 API 는 없다. 서버에서 직접 만든다(비밀번호를 두 번 묻는다, 12자 이상).

```bash
ssh -t homelab-backend-server docker exec -it trading-desk-backend-prod python -m desk.cli create-user <이메일>
```

비밀번호 변경 · 전체 로그아웃은 같은 방식으로 `set-password` · `revoke-sessions` (`docs/security.md` §1).

## 5. 처음 한 번 — ChatGPT 로그인

`.env.runtime` 의 `DESK_FAKE_RUNS=true` 인 동안은 대본 러너가 돈다. 실제 분석은 ChatGPT 구독으로 로그인한 뒤 켠다.
로그인 콜백은 컨테이너의 `127.0.0.1:1455` 로 돌아오므로 PC → 서버 → 컨테이너로 터널을 잇는다.

```bash
# PC 터미널 1: PC 의 1455 → 서버의 1455
ssh -L 1455:127.0.0.1:1455 homelab-backend-server
# 그 SSH 세션 안: 같은 볼륨으로 1회용 컨테이너를 띄워 로그인 (서버 루프백 1455 만 연다)
docker run --rm -it -p 127.0.0.1:1455:1455 -v trading-desk-tradingagents-home:/home/app/.tradingagents \
  trading-desk-backend:prod tradingagents login chatgpt
```

브라우저가 열라는 주소를 PC 브라우저에서 연다. 끝나면 `chatgpt-auth.json` 이 볼륨에 남는다.
그다음 `.env.runtime` 에서 `DESK_FAKE_RUNS=false`, 모델 ID(`TRADINGAGENTS_QUICK_THINK_LLM` · `TRADINGAGENTS_DEEP_THINK_LLM`)를 채우고 `scripts/deploy.sh HEAD backend`.
속도 옵션 env 4개(`TRADINGAGENTS_DEBATE_PARALLEL_FIRST_ROUND` · `TRADINGAGENTS_RISK_PARALLEL_FIRST_ROUND` · `TRADINGAGENTS_QUICK_THINK_REASONING_EFFORT` · `TRADINGAGENTS_DEEP_THINK_REASONING_EFFORT`)는 비워 두면, 보고서 재사용 `DESK_REUSE_REPORTS_MIN` 은 0 이면 꺼진다 — README "ChatGPT 플랜으로 실제 실행".

## 6. 토스증권 Open API

- `.env.runtime` 에 `DESK_BROKER=toss`, `TOSS_CLIENT_ID` · `TOSS_CLIENT_SECRET`(· `TOSS_ACCOUNT_SEQ`).
- **토스 WTS → 설정 → Open API → 허용 IP** 에 홈랩 공인 IP(지금 `1.241.243.70`)를 등록한다. 등록되지 않은 IP 는 403 이다.
- 공인 IP 는 SK브로드밴드 동적 IP 라 바뀔 수 있다. 바뀌면 대시보드 보유 종목 패널에 403 이 보인다 → WTS 에서 다시 등록.
- **토큰은 키(client)당 1개만 유효하다.** 같은 키로 다른 곳에서 토큰을 받으면 서버 토큰이 즉시 무효화된다(401 `token-revoked`).
  서버는 401 을 받으면 새로 받아 한 번 다시 부르지만(#50), 그 순간 다른 쪽 토큰이 죽는다. 그래서 **운영 점검은 앱 API**(`/api/quote` · `/api/portfolio` · `/api/review` …)로 하고,
  컨테이너 안 스크립트에서 `TossBroker` 를 직접 만들지 않는다. PC 로컬 개발은 `DESK_BROKER=mock`(또는 별도 키)로 띄운다 —
  PC 와 서버가 같은 공인 IP 라 허용 IP(403)로도 막히지 않는다. 다른 앱(semibot 등)에 토스를 붙일 때는 키를 따로 발급받는다.
  두 곳이 같은 키를 계속 쓰면 서로 무효화를 반복한다(핑퐁) — 서버는 재발급을 1분에 한 번으로 묶고 로그에 `toss token revoked` 를 남긴다.
- 토큰은 DB `broker_tokens` 에 봉인해 프로세스 · 재배포를 넘어 함께 쓴다(#688, docs/security.md §5.1). 재배포해도 유효한 토큰을 다시 쓰고,
  발급은 `DESK_ROLE` all · collector 만 한다 — 로그 `toss token issued (all)` 이 발급 한 번이다. `python -m desk.cli flow-history` 는 저장소의 토큰만 읽는다.
- 시세 · 보유 정보를 다른 사람에게 보여주지 않는다(재배포 금지). 로그인이 소유자 한 명으로 막는 이유다.

## 7. 백업

| 대상 | 방법 |
|------|------|
| PostgreSQL `trading_desk` | dev 인스턴스라 정기 백업이 없다. `ssh homelab-main-server 'docker exec postgresql pg_dump -U postgres -Fc trading_desk' > trading_desk.dump` |
| 볼륨 `trading-desk-tradingagents-home` | ChatGPT 로그인 · 메모리 로그 · 리포트. `docker run --rm -v trading-desk-tradingagents-home:/v -v $PWD:/b alpine tar czf /b/ta-home.tgz -C /v .` — **로그인 토큰이 들어 있어 사본도 비밀로 다룬다** |

## 8. 인그레스 (Infra 저장소)

`D:\ProjectWorkSpace\Infra` 는 공개 저장소다. 이 앱에 관한 것은 도메인 · 사설 IP · 포트까지만 적는다.

- `nginx/conf.d/trading.8llow8llowme.conf` — HTTP → HTTPS, `X-Real-IP $remote_addr`, SSE 용 `proxy_buffering off` · `proxy_read_timeout 1h`
- 인증서: `certbot/init-cert-non-www.sh trading.8llow8llowme.com`
- fail2ban: 로그인 401 반복 IP 차단 (`fail2ban/filter.d`, `jail.local`)

## 9. 한국투자증권(KIS) 실시간 시세 (#125)

- 한국투자증권 계좌 → **KIS Developers(apiportal.koreainvestment.com) → API 신청** 으로 실전투자 앱키 · 앱시크릿을 받는다.
- 서버 `.env.runtime` 에 `DESK_KIS_APP_KEY` · `DESK_KIS_APP_SECRET` 을 넣고 `scripts/deploy.sh HEAD backend`. 모의투자 키면 `DESK_KIS_WS_URL` · `DESK_KIS_REST_URL` 도 모의 주소로(`.env.example`).
- 서버에서 `ops.koreainvestment.com:21000`(ws) · `openapi.koreainvestment.com:9443`(https) 로 나가는 연결이 열려 있어야 한다.
- 확인: 장중에 실시간 화면을 열면 시세 출처가 실시간(`kis`)으로 바뀌고 1초 간격으로 갱신된다.
  로그인한 브라우저에서 `GET /api/live/kis`(#143) 로 상태를 본다 — `connected` · 구독 종목(`tickers`) · 시장별 마지막 체결 수신 시각(`last_tick.KR` · `last_tick.US`) ·
  거절된 종목(`refused`) · 접속키가 마지막으로 거절된 시각(`approval_refused_at`) · 마지막 끊김(`last_drop`: 시각 · 예외 타입, 정기 접속키 교체는 빼고). 키 · 접속키는 들어 있지 않다.
  INFO 로그는 출력하지 않는다(상태는 이 API 로 본다). 문제는 백엔드 로그(WARNING)의 `kis stream dropped: <예외 타입>` · `kis stream refused <종목>: <사유>` · `approval key refused` 로도 남는다. 그동안 화면은 토스 폴링으로 계속 돈다.
- 규칙(키 보관 · 회전 · 로그)은 [security.md §5](security.md#5-비밀).

## 10. 알림 채널 — 텔레그램 · 카카오톡 (#580)

값은 서버 `.env.runtime` 에만 넣고, 넣은 뒤 `scripts/deploy.sh HEAD backend`. 하나라도 비면 그 채널만 꺼진다(보안 규칙: [security.md §5](security.md)).

- 공통: `DESK_PUBLIC_URL=https://trading.8llow8llowme.com` (메시지 링크 · 카카오 Redirect URI 의 앞부분)
- **텔레그램**
  1. 텔레그램에서 `@BotFather` → `/newbot` → 토큰을 `DESK_TELEGRAM_BOT_TOKEN` 에.
  2. 만든 봇에게 아무 말이나 한 번 보낸 뒤, 서버에서 `curl -s "https://api.telegram.org/bot<토큰>/getUpdates"` 의 `message.chat.id` 를 `DESK_TELEGRAM_CHAT_ID` 에(이 명령은 셸 기록에 토큰이 남으니 끝나면 `history -d`).
  3. 재배포 → 화면 설정 → 알림 채널 → 텔레그램 "켜기" → "시험 보내기".
- **카카오톡 "나에게 보내기"**
  1. [카카오 디벨로퍼스](https://developers.kakao.com) → 앱 만들기 → 앱 키의 **REST API 키**를 `DESK_KAKAO_REST_KEY` 에.
  2. 카카오 로그인 켜기 → Redirect URI 에 `https://trading.8llow8llowme.com/kakao-callback.html` 등록.
  3. 보안 → Client Secret 발급 · **사용함** → `DESK_KAKAO_CLIENT_SECRET` 에.
  4. 동의 항목 → "카카오톡 메시지 전송(talk_message)" 선택 동의. 플랫폼 → Web 사이트 도메인에 `https://trading.8llow8llowme.com`(메시지 링크).
  5. `docker exec trading-desk-backend-prod python -m desk.cli otp-key` 로 새 키를 만들어 `DESK_NOTIFY_KEY` 에(OTP 키와 다른 값).
  6. 재배포 → 설정 → 카카오톡 "연결" → 카카오 로그인 · 동의 → 돌아오면 "시험 보내기". 리프레시 토큰은 약 2달이라, 화면에 "다시 연결"이 뜨면 같은 순서로 한 번 더.

## 11. KRX OPEN API 일별 이력 (#601)

- openapi.krx.co.kr 에서 인증키를 받고, 쓸 서비스마다 **이용 신청**을 해 승인을 받는다(목록 · 필드는 [krx.md](krx.md)). 승인 안 된 서비스는 401 로 답하고 6시간 건너뛴다.
- 서버 `.env.runtime` 에 `DESK_KRX_API_KEY` 를 넣고 `scripts/deploy.sh HEAD backend`. 비어 있으면 수집이 꺼지고 다른 기능은 그대로다. `DESK_MACRO_INTERVAL_SEC=0` 이어도 꺼진다.
- 그다음부터 KRX 영업일 08:30 KST 뒤 첫 패스(15분 간격)가 전 영업일치를 `krx_daily` 에 쌓는다. 서버가 쉬었으면 최근 2주 중 빠진 날을 채운다.
- 과거치: `ssh -t homelab-backend-server docker exec -it trading-desk-backend-prod python -m desk.cli krx backfill --api kospi_dd_trd --from 2015-01-01`
  (이미 받은 날은 건너뛴다 · 기본 `--max-calls 3000`, 키 한도는 하루 10,000건). 진행은 `python -m desk.cli krx status`.
