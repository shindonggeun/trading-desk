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
| `backend/docker-compose-backend.yml` | `backend-prod`(+ 나누기를 켜면 `collector-prod` · `analyst-prod`, 12절). 앱이 읽는 키만 `environment` 로 넘기고 필수 키는 `${KEY:?}` 로 막는다 |
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
                  백엔드 역할별 커밋·PR 라벨 또는 기존 backend/ 차이, 웹은 frontend/ 차이
   3. 운영 배포   Vault → 임시 env 파일(600) → scripts/deploy-compose.sh → env 파일 삭제
```

| 단계 | 하는 일 | 멈추면 |
|------|---------|--------|
| CI 확인 | `scripts/jenkins/wait-ci.sh` 가 Actions API 로 그 커밋의 push 실행(워크플로 `ci`)을 10초마다 본다. 다시 돌린 실행이 있으면 마지막 시도 | 실패 → 빌드 실패, 운영 그대로. **밀림** → NOT_BUILT(실패 아님), 뒤 커밋의 빌드가 배포. 밀림 = CI 취소, 또는 기다리는 동안 develop 머리가 실패하지 않은 더 새 커밋(아래) |
| 배포 대상 | 활성화된 백엔드 역할 잡은 각 컨테이너의 커밋과 대상 커밋 사이의 변경 경로·머지 PR 라벨을 대조한다. 종합 경로는 기존 `git diff -- backend`(웹 `frontend`)를 사용한다. | 역할 판정 실패는 전체 배포로 복귀한다. 배포할 변화가 없으면 "변경 없음"을 기록한다. |
| Vault | `scripts/jenkins/vault-env.sh` 가 `kv/trading-desk/{backend,frontend}/prod/env` 를 읽어 compose 가 쓰는 키만 `KEY='value'` 로 쓴다(3절) | 봉인 · 네트워크 · 권한 · 필수 키 없음 → **아무것도 바꾸기 전에** 멈춘다. 운영 그대로 |
| 빌드 | `<이름>:<커밋 12자>` 이미지, 라벨에 커밋 | 빌드 실패 → 운영 그대로 |
| 분석 대기 (#431, 백엔드) | 컨테이너를 바꾸기 직전 `desk.cli unfinished` 로 묻고 최대 30분 기다린다 | 넘기면 멈춘다(종료 코드 3, 운영 그대로) → 끊어도 되면 `FORCE_STOP_RUNS` |
| 교체 | 운영 이미지를 `:rollback` 으로 남기고 새 이미지를 `:prod` 로. **api(`primaryService`, 기동 시 Alembic `upgrade head`)를 먼저** `--wait`, compose 의 나머지 서비스(#688 collector · analyst)를 그다음 | → 되돌림(2-2) |
| 확인 | 교체 대상 컨테이너의 커밋 라벨 = 대상 커밋 · 컨테이너 안에서 `/api/health` 가 `{"ok":true}`(웹은 `/`) · 30초 동안 재시작 없이 running · healthy | → 되돌림(2-2) |

잡 파라미터(Build with Parameters):

| 파라미터 | 뜻 |
|----------|-----|
| `DEPLOY_COMMIT` | 비우면 develop 끝. 이전 커밋으로 되돌릴 때 develop 에 있는 SHA(그 커밋의 CI 가 성공했어야 한다). 소스는 그 커밋, 스크립트는 develop 끝 것을 쓴다 |
| `FORCE_DEPLOY` | 운영과 차이가 없어도 다시 빌드 · 배포 (첫 배포 · Vault 값만 바꿨을 때) |
| `FORCE_STOP_RUNS` | (백엔드) 진행 중인 분석을 기다리지 않는다 — 그 분석은 실패로 끝난다 |
| `ROLLBACK_DRILL` | 새 이미지로 바꾼 뒤 확인을 일부러 실패시켜 되돌림을 시험한다(2-2) |
| `SERVICES` | (백엔드) 기본 `auto`는 머지 PR에 따라 역할을 선택한다. `all`은 전체 배포를 요청한다. 활성화된 역할 잡에서는 역할 하나를 직접 요청할 수 있다. |

- 같은 커밋에서 backend/ · frontend/ 가 함께 바뀌면 두 잡이 lock 을 차례로 잡는다 — 어느 쪽이 먼저 바뀔지 정해져 있지 않으니 API 를 바꾸는 PR 은 이전 · 다음 화면 둘 다와 맞게 둔다.
- Jenkins 는 `~/deploy/trading-desk/src` 를 고치지 않는다. 운영 커밋은 컨테이너 라벨로 본다(2-3).

#### PR 은 몰아서 머지해도 된다 — 마지막 초록 커밋만 배포 (#752)

develop 에 커밋이 연달아 들어오면 앞 빌드는 실패가 아니라 **밀림(NOT_BUILT)** 으로 끝나고, 가장 새 커밋의 빌드 하나가 배포한다.

```
머지 A ─▶ 빌드 A: CI 대기 … develop 머리가 B 로 바뀜, B 의 CI 는 진행 중 → "밀림: A → 더 새 B", NOT_BUILT
머지 B ─▶ 빌드 B: (A 가 끝날 때까지 대기열) CI 대기 … 머리가 C → "밀림: B → 더 새 C", NOT_BUILT
머지 C ─▶ 빌드 C: CI 성공 → lock → 배포 직전 다시 확인(머리 = C) → 배포
```

- 잡은 동시에 두 빌드를 돌리지 않는다(`disableConcurrentBuilds()`). 대기열은 Jenkins 가 하나로 합친다.
- 밀림 검사는 두 번이다.
  - CI 를 기다리는 동안 매 바퀴(`wait-ci.sh`, `CI_SUPERSEDE=1`)
  - lock 을 잡고 배포하기 직전 한 번(`wait-ci.sh --superseded-only`). GitHub 를 못 읽으면 배포를 계속한다.
- **더 새 커밋의 CI 가 실패 · 취소로 끝났거나 그 실행을 읽지 못했으면 밀림이 아니다.** 지금 커밋을 계속 기다려 배포한다. 실패한 커밋이나 GitHub 일시 오류 하나 때문에 초록 커밋의 배포가 막히지 않게 하려는 것이다. 밀림은 머리 CI 가 없음 · 대기 · 진행 중 · 성공일 때뿐이다.
- 더 새 커밋의 CI 가 진행 중일 때 밀린 뒤 그 CI 가 실패하면, 밀린 초록 커밋은 자동으로 다시 배포되지 않는다. develop 을 고치는 커밋이 배포하거나, `DEPLOY_COMMIT` 으로 그 초록 커밋을 올린다.
- 머리 커밋에 push CI 가 영영 생기지 않으면(`[skip ci]` · Actions 장애) 앞 커밋은 밀리고 머리 빌드는 45분 뒤 시간 초과로 실패한다. 이 저장소는 `[skip ci]` 를 쓰지 않는다.
- 확인할 것(운영): 대기열에서 합쳐진 빌드가 **가장 새 커밋**을 checkout 하는지. 앞 커밋을 고정한 채 돌면 그 빌드는 밀리고 머리 커밋의 빌드가 생기지 않는다. PR 3개를 연달아 머지한 뒤 마지막 커밋 SHA 를 배포한 빌드가 있는지 본다(이슈 완료 기준).
- `DEPLOY_COMMIT`(되돌리기)은 일부러 옛 커밋이라 밀림 검사를 하지 않는다.
- `disableConcurrentBuilds(abortPrevious: true)` 는 쓰지 않는다. 앞 빌드를 '운영 배포' 단계(`deploy-compose.sh` 의 이미지 교체 · compose up 사이)에서도 끊어 서비스가 내려간 채 남을 수 있다. 밀림 검사로 같은 결과(밀린 빌드 실패 0, 마지막 초록 커밋만 배포)를 끊김 없이 얻는다.
- 콘솔에는 `밀림: <커밋 12자> → 더 새 <커밋 12자>` 한 줄이 남고, 빌드 설명 끝에 `밀림` 이 붙는다.
- GitHub API 요청은 한 바퀴에 최대 3회(10초 간격이면 45분에 810회)로, 토큰 한도(시간당 5000회) 안이다.

#### 백엔드 역할별 선택 배포 (#965)

develop push CI는 배포용 결과를 내기 전에 머지 PR에 `backend-api`, `backend-collector`,
`backend-analyst`를 [라벨 규칙](../.github/labeler.yml)에 따라 동기화한다.
초기 전용 경로는 각 역할이 실제로 실행하는 `backend/desk/prewarm.py`, `backend/desk/nq_fast.py`,
`backend/desk/events.py`다. `docs/`와 `frontend/`는 백엔드 선택 대상에서 제외한다. 그 밖의
경로는 모두 공용으로 분류한다. `desk/api.py`·`settings.py`·`db.py`·`roles.py`,
Alembic 리비전, 의존성, Dockerfile·Compose·배포 스크립트 변경은 세 역할에 적용한다.
전용 경로를 추가할 때 호출처를 확인하고 라벨 규칙과 배포 선택기를 함께 갱신한다.

1. 상위 잡은 대상 커밋의 CI와 밀림 검사를 통과한 뒤 호스트 펜스 안에서 세 컨테이너의
   OCI 커밋 라벨을 읽는다. 역할별 운영 커밋부터 대상 커밋까지 확인하므로, 밀린 빌드나
   이전 부분 배포의 차이도 다음 판정에 들어간다.
2. 그 범위의 배포 관련 커밋에 대응하는 develop 머지 PR의 역할 라벨과 변경 경로를 대조한다.
   공용 경로 또는 신규 마이그레이션, 라벨 누락·불일치, 확인할 수 없는 범위나 PR 조회
   실패는 전체 배포로 복귀한다. 배포 로그 한 줄에서 선택·이유·확인한 PR 번호와 역할 라벨을
   대조한다.
3. `auto`에서 필요한 역할이 없으면 빌드와 교체를 건너뛴다. 대상이 있으면 이미지 한 개를
   준비한다. 같은 이미지 ID로 정해진 API → collector →
   analyst 순서에서 선택된 잡만 실행한다. analyst의 진행 중 분석 대기, collector의
   토큰 재발급 검사와 역할별 상태 확인·복구는 기존 계약을 사용한다.

수동 실행에서 `SERVICES=all`은 세 역할 전체를 지정한다. 준비된 split 환경에서
`SERVICES=api`, `collector`, `analyst` 중 하나를 요청할 수 있다. 필요한 역할이 더 있으면
판정 결과가 요청 범위를 넓힌다. `auto`에서 `FORCE_DEPLOY`·`ROLLBACK_DRILL`을 사용하거나
`DEPLOY_COMMIT`으로 커밋을 직접 지정할 때는 전체가 대상이다. 최초 split 전환과 종합 경로
복귀는 기존 절차를 따른다.

운영 활성화 전에는 [Jenkins 역할 잡 명세](../deploy/jenkins/README.md#운영-연결-전-검증과-등록)와
[`backend-role-preflight.sh`](../scripts/jenkins/backend-role-preflight.sh)로 기존 전용 노드,
호스트 펜스, 잡·권한 조건을 대조한다. 기존 Jenkins GitHub 조회 자격의 `Pull requests: read`
가능 여부도 확인한다. 활성화와 실측은 별도 승인으로 진행한다.

운영 기록에는 API 전용 PR 머지 전후 세 컨테이너의 ID·시작 시각·이미지 ID·커밋 라벨을
남긴다. API만 바뀌고 두 워커가 유지되는지 확인한다. 공용 파일이나 새 리비전이 들어간
별도의 승인된 배포에서는 세 잡 순서, 이미지 빌드 한 번, API 스키마 및 각 역할의 헬스를
확인한다.

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

**운영 커밋의 정본은 컨테이너 라벨이다**(Jenkins 의 '배포 대상' 비교도 이 라벨을 본다). 서버 `~/deploy/trading-desk/src/REVISION` 은 마지막 **손 배포** 소스의 표시라, 그 뒤 Jenkins 가 올렸으면 라벨과 다른 게 정상이다(#752). 라벨이 비면 이 변경 전 손 배포로 올린 이미지다 — 그때만 `src/REVISION` 을 본다. `scripts/deploy.sh` 는 시작할 때 둘이 다르면 한 줄 알린다.

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
src/           손 배포로 마지막에 올린 소스 (REVISION 에 커밋 — 운영 커밋 표시가 아니다, 2-3). Jenkins 배포는 이 폴더를 쓰지 않는다
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

#### 독립 백엔드 잡 등록 리허설 (#785)

세 역할 잡의 비활성 XML은 `deploy/jenkins/trading-desk/`에 있다. 기존 멀티브랜치 잡은 아래 절차로 관리한다. Infra의 Jenkins 설정에는 Job DSL과 JCasC 등록 절차가 없으므로 역할 잡의 명세는 이 저장소에서 관리한다.

`scripts/jenkins/register-disabled-backend-jobs.py`는 대상 컨트롤러의 `trading-desk` 폴더에서 세 잡의 존재를 읽어 출력한다. 비운영 컨트롤러에 같은 폴더와 Pipeline 플러그인을 준비한 뒤 기본 명령으로 목록을 대조한다. CLI가 읽기와 생성에 사용하는 인증은 Jenkins 환경에서 이미 설정된 방식을 쓴다. [Jenkins CLI 문서](https://www.jenkins.io/doc/book/managing/cli/)

```bash
python -B -X utf8 scripts/jenkins/register-disabled-backend-jobs.py -- java -jar jenkins-cli.jar -s "$JENKINS_REHEARSAL_URL"
python -B -X utf8 scripts/jenkins/register-disabled-backend-jobs.py --apply -- java -jar jenkins-cli.jar -s "$JENKINS_REHEARSAL_URL"
```

`--apply`는 누락된 비활성 잡만 생성한다. 이미 있는 잡은 UI에서 별도로 상태와 트리거를 확인한다. 등록이 중간에 실패하면 원인을 확인한 뒤 다시 실행할 수 있다. 생성된 세 잡은 각각 비활성이며 트리거가 비어 있어야 한다.

#### 기존 자동 배포 잡 설정

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
   | `trading-desk-github` | Username with password (또는 GitHub App) | username `shindonggeun`, password = fine-grained PAT — 저장소 `shindonggeun/trading-desk` 만, 기존 권한은 Contents · Actions · Metadata **읽기**. #965 선택 배포는 Pull requests **읽기**도 필요하며 운영 자격은 별도 승인으로 확인한다. 홈랩 GitHub App 을 이 저장소에 설치할 수 있으면 그 App credential 을 같은 ID 로 |

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

기존 AppRole의 Secret ID 교체는 [Vault AppRole 회전 절차](vault-approle-rotation.md)를 따른다.

```text
kv/trading-desk/backend/prod/env    # 백엔드 compose 의 운영 env(api · collector · analyst 공용) — 키 목록 정본: 루트 .env.example
kv/trading-desk/frontend/prod/env   # 웹 compose 보간 키만: DESK_DOCKER_NETWORK · DESK_WEB_BIND · DESK_WEB_PORT (· DESK_WEB_MEM_LIMIT)
```

- 환경당 secret 하나, **키 하나에 값 하나**(`.env` 통째로 한 키에 넣지 않는다). 값에 작은따옴표 · 줄바꿈을 넣지 않는다 — 파이프라인이 `KEY='value'` 로 쓰고, 걸리면 키 이름만 알리고 멈춘다.
- compose 가 쓰지 않는 키는 env 파일에 넣지 않는다(이름만 알린다). Vault 에 없는 선택 키는 compose 기본값(`${KEY:-기본}`)을 쓴다.
- `DESK_API_ROLE`은 `scripts/deploy-compose.sh`가 `DESK_BACKEND_SPLIT`에 따라 설정하는 내부 셸 변수다. Vault 와 `.env.runtime`에 넣지 않는다. env 파일에 비어 있지 않은 값이 있으면 배포 스크립트가 컨테이너 교체 전에 중단한다.
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
| `DESK_WEB_PORT` | `10088` |

`DESK_WEB_BIND`를 생략하면 compose 는 `127.0.0.1`에 바인딩한다. 홈랩처럼 인그레스가 배포 호스트의 LAN 주소로 접속하는 구성에서는 해당 주소(홈랩은 `192.168.0.9`)를 웹 Vault 경로와 손 배포용 `.env.runtime`에 명시한다.

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

**토큰 갱신을 여러 프로세스가 함께 쓸 때(#766, 백엔드 나누기)**: 액세스 토큰은 1시간이고, 갱신할 때마다 refresh token 이 바뀐다 —
이미 쓴 refresh token 을 다시 보내면 OpenAI 가 로그인을 끝낸다. 라이브러리(`tradingagents.llm_clients.chatgpt_auth.access_token`)가
갱신을 볼륨의 `chatgpt-auth.json.lock` 잠금(`flock`) 안에서만 하고, 잠금을 잡은 뒤 파일을 다시 읽어 다른 프로세스가 이미 갱신했으면
그 토큰을 쓴다. 새 토큰은 임시 파일에 쓴 뒤 원자적으로 바꾼다. 그래서 api(AI 채팅)와 analyst(분석 · 리포트 · 아침 시황)가 같은 볼륨을
써도 갱신은 한 번이다 — `backend/tests/test_chatgpt_refresh.py` 가 독립 프로세스 둘과 가짜 OAuth(회전 · 재사용 거절)로 이를 확인하고,
TradingAgents SHA 를 올려 이 잠금이 빠지면 여기서 깨진다. 전제: 세 컨테이너가 **같은 호스트의 같은 명명 볼륨**
(`trading-desk-tradingagents-home`, local 드라이버)을 같은 사용자(`app`)로 쓴다. NFS 같은 네트워크 볼륨이나 다른 호스트로 나누면 `flock` 이
보장되지 않으니 그때는 갱신을 한 프로세스로 모아야 한다.

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
  발급은 `DESK_ROLE` all · collector 만 한다 — 로그 `toss token issued (all)` 이 발급 한 번이다. legacy의 `python -m desk.cli flow-history`는 저장소의 토큰만 읽는다.
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

## 10. 알림 채널 — 텔레그램 (#580)

값은 서버 `.env.runtime` 에만 넣고, 넣은 뒤 `scripts/deploy.sh HEAD backend`. 하나라도 비면 텔레그램이 꺼진다(보안 규칙: [security.md §5](security.md)).

- 공통: `DESK_PUBLIC_URL=https://trading.8llow8llowme.com` (메시지 링크)
- **텔레그램**
  1. 텔레그램에서 `@BotFather` → `/newbot` → 토큰을 `DESK_TELEGRAM_BOT_TOKEN` 에.
  2. 만든 봇에게 아무 말이나 한 번 보낸 뒤, 서버에서 `curl -s "https://api.telegram.org/bot<토큰>/getUpdates"` 의 `message.chat.id` 를 `DESK_TELEGRAM_CHAT_ID` 에(이 명령은 셸 기록에 토큰이 남으니 끝나면 `history -d`).
  3. 재배포 → 화면 설정 → 알림 채널 → 텔레그램 "켜기" → "시험 보내기".
- 예전 두 번째 채널은 #986 에서 걷어 냈다. 앱은 그 채널의 키 세 개(REST 키 · 클라이언트 시크릿 · 봉인 키 `DESK_NOTIFY_KEY`)를 읽지 않는다 — compose 가 넘기지 않으니 `vault-env.sh` 가 찍는 "compose 가 쓰지 않는 키" 목록에 남아 있으면 Vault · `.env.runtime` 에서 지운다(운영 Vault 는 2026-11-25 에 지움). 그 채널은 운영에서 한 번도 연결되지 않아 봉인된 토큰 행은 없다. 연결된 적이 있는 환경이면 그 채널 콘솔에서 앱 연결을 끊거나 클라이언트 시크릿을 폐기해 토큰을 무효로 만든다. DB `notify_channels` · `nq_weekly_deliveries` 에 남은 그 채널의 행은 읽지 않고 그대로 둔다(스키마 변경 없음).

## 11. KRX OPEN API 일별 이력 (#601)

- openapi.krx.co.kr 에서 인증키를 받고, 쓸 서비스마다 **이용 신청**을 해 승인을 받는다(목록 · 필드는 [krx.md](krx.md)). 승인 안 된 서비스는 401 로 답하고 6시간 건너뛴다.
- 서버 `.env.runtime` 에 `DESK_KRX_API_KEY` 를 넣고 `scripts/deploy.sh HEAD backend`. 비어 있으면 수집이 꺼지고 다른 기능은 그대로다. `DESK_MACRO_INTERVAL_SEC=0` 이어도 꺼진다.
- 그다음부터 KRX 영업일 08:30 KST 뒤 첫 패스(15분 간격)가 전 영업일치를 `krx_daily` 에 쌓는다. 서버가 쉬었으면 최근 2주 중 빠진 날을 채운다.
- 과거치: `ssh -t homelab-backend-server docker exec -it trading-desk-backend-prod python -m desk.cli krx backfill --api kospi_dd_trd --from 2015-01-01`
  (이미 받은 날은 건너뛴다 · 기본 `--max-calls 3000`, 키 한도는 하루 10,000건). 진행은 `python -m desk.cli krx status`.

## 12. 백엔드 나누기 — api · collector · analyst (#688)

같은 이미지(`trading-desk-backend:prod`)를 역할(`DESK_ROLE`, `backend/desk/roles.py`)만 달리해 컨테이너 3개로 띄운다. **기본은 꺼짐**이다 — `backend-prod` 하나가 `all` 로 지금처럼 돈다.

| 서비스 · 컨테이너 | 역할 | 네트워크 | 메모리 상한 |
|---|---|---|---|
| `backend-prod` · `trading-desk-backend-prod` | 꺼짐 `all` / 켜짐 `api`(화면 · SSE · 로그인, run 은 `queued` 만 쓴다) | `desk`(웹이 이름으로 닿는 유일한 백엔드) | `DESK_BACKEND_MEM_LIMIT`(기본 2g, 켜면 1g 권장) |
| `collector-prod` · `trading-desk-collector-prod` | `collector`(시세 · 수급 · 알림 · 정기 작업, **브로커 토큰 발급**) | 프로젝트 `default`(밖으로 나가는 연결만) | `DESK_COLLECTOR_MEM_LIMIT`(기본 1g) |
| `analyst-prod` · `trading-desk-analyst-prod` | `analyst`(LLM run 을 DB 대기열에서 가져감 · 아침 시황 · 리포트 · 채점). **하나만** — 두 번째는 PG advisory lock 에 막힌다 | 프로젝트 `default` | `DESK_ANALYST_MEM_LIMIT`(기본 2g) |

- **스위치**: Vault `kv/trading-desk/backend/prod/env` 의 `DESK_BACKEND_SPLIT` — `0`(기본) | `1`. `scripts/deploy-compose.sh` 가 읽어 `1` 이면 compose profile `split` 을 켜고 `backend-prod` 를 `api` 로 띄운다(셸 환경 `DESK_API_ROLE`). 다른 값이면 바꾸기 전에 멈춘다. compose 를 손으로 부르지 않는다 — 스위치와 역할을 맞추는 것이 이 스크립트다.
- **순서**: api를 먼저 `up --wait`로 띄워 Alembic `upgrade head`를 마친다. API가 healthy가 되면 collector · analyst를 `--no-deps`로 띄운다. 워커는 기동 전에 적용된 Alembic head 집합을 읽어 이미지와 대조한다. DB를 읽을 수 없거나, 리비전이 비어 있거나, 이미지와 다르면 시작을 중단한다. 워커 오류가 나면 API의 DB 연결과 배포 이미지가 같은 스키마를 쓰는지 확인한다.
- **되돌림**: 변경 전후 스키마의 호환성을 확인하고 2-1 · 2-2 절을 따른다. 이미 실행 중인 이전 워커는 다음 기동 때 이 검사를 거친다. API와 워커가 잠시 공존하는 구간의 호환성은 배포 전에 확인한다.
- **진행 중인 분석**: 종합 배포는 `desk.cli unfinished`로 확인하고 분석이 끝나기를 기다린다. split에서 API만 교체하면 분석은 analyst에서 계속된다. analyst를 재시작하면 `running`만 실패로 바뀌고 `queued`는 남는다.
- **일정 · 뉴스 저장 파일**(#996 · #1004): 경제 일정(FOMC · BLS) · 거시 뉴스 · 실적 일정 · 뉴스 다이제스트(헤드라인의 한국어 제목 · 감정 · 시황 읽기, `news_digest.json`) · 섹터 이슈(`sector_news.json`)는 collector의 갱신 작업이 공유 볼륨(`tradingagents_home`)의 `desk-cache/*.json`에 저장하고, api · analyst는 파일이 바뀌면 60초 안에 다시 읽는다(`snapshots` · `snapshots_analyst` 잡). Redis와 상관없다. split collector는 기동 때 경제 일정 · 뉴스 다이제스트 · 섹터 이슈 파일을 먼저 읽는다(재시작해도 같은 헤드라인을 모델에 다시 묻지 않는다). 뉴스 다이제스트 파일은 4 MB(넘으면 오래된 읽기부터 빼고 쓴다), 섹터 이슈 파일은 1 MB(넘으면 쓰지 않는다)가 상한이고, 상한을 넘는 파일은 api · analyst가 읽지 않는다. api가 파일을 읽지 못하거나 내용이 깨졌으면 마지막 값을 계속 쓰고, 그동안 `/api/status`의 `snapshots` 줄이 매 패스 error로 남는다(로그는 처음 한 번, analyst는 로그만). collector가 저장하지 못하면 collector의 `economic_calendar` · `macro_news` · `sector_news` · `earnings` 줄이 error가 된다(collector 로그). 즐겨찾기 실적 요청과 채점 live 기록은 파일이 아니라 DB에서 따라 읽는다(#1005): collector의 실적 패스가 모든 계정의 즐겨찾기(최대 200종목)를 직접 읽고, api의 `calibration_live` 작업이 채점 결과로 플레이북 보정 표를 다시 계산한다(채점 주기, 기본 1시간).

### 켜기 전 선행 조건 (모두 머지 · 배포된 뒤, 사용자 승인으로 켠다)

1. 전환할 develop SHA의 CI 성공 및 운영 반영을 확인한다. 해당 SHA에 Compose 역할과 스위치(#769), analyst 대기열(#771), PostgreSQL 이벤트 알림 및 관련 검증(#750 · #772), API와 analyst 사이의 OAuth 갱신 직렬화(#766), 브로커 단일 발급자 잠금(#776)이 포함돼야 한다.
2. collector가 모은 시세·수급·알림을 API와 화면이 읽을 수 있도록 공유 캐시(#760)와 시장 SSE(#690)의 운영 준비를 확인한다.
3. [#818 비운영 리허설](../deploy/jenkins/README.md#비운영-jenkins-어댑터-검증-절차)의 실측 기록을 확인한다. 큐 대기와 실행 중 취소 모두에서 자식 종료나 큐 제거가 공통 잠금 해제와 경쟁 잡 진입보다 앞서야 한다. 실측 기록이 없거나 순서가 어긋나면 전환을 보류한다.

### 전환과 첫 1시간 관찰 (#776)

1. 현재 백엔드 커밋 라벨과 서비스 상태를 [2-3절](#2-3-운영-커밋--상태-확인)로 기록한다. 전환 전 1시간의 브로커 토큰 발급, 429, 분석 대기열과 컨테이너 메모리도 비교 기준으로 남긴다.
2. 기존 종합 잡 `trading-desk-backend/develop`에서 승인된 SHA 하나로 전환한다. [3절](#3-운영-env--vault-정본--envruntime-손-배포-대체)에 따라 Vault의 `DESK_BACKEND_SPLIT`만 `1`로 바꾸고, Build with Parameters에서 `DEPLOY_COMMIT=<승인된 SHA>`, `FORCE_DEPLOY=true`로 실행한다. `FORCE_STOP_RUNS`는 기본값 `false`로 두고 진행 중인 분석이 끝날 때까지 기다린다. 작업 중에는 백엔드 배포와 손 배포를 겹쳐 실행하지 않는다.
3. 배포 직후 세 컨테이너가 `healthy`이고 모두 승인된 SHA인지 확인한다. `backend-prod`의 `desk.backend-split` 라벨이 `1`이고 `/api/health`가 정상이어야 한다. 이어서 1시간 동안 아래 신호를 전환 전 기록과 비교한다.

토큰 발급과 429는 각 컨테이너 로그에서 시각·역할·건수만 기록한다. 분석 상태는 화면에서, 메모리는 `docker stats --no-stream`에서 확인한다.

| 확인 | 정상 상태 | 장애 판단 |
| --- | --- | --- |
| 브로커 발급 | 발급이 있으면 collector에서만 기록된다. 공유 토큰을 재사용하면 발급 로그가 없을 수 있다. | 다른 역할에서 발급하거나 잠금 충돌·반복 발급과 함께 브로커 호출이 실패한다. |
| 429 · OAuth | 새 429가 대기열이나 화면 지연을 유발하지 않고 기존 ChatGPT 로그인으로 채팅·분석이 동작한다. | 전환 전보다 429가 지속적으로 늘고 분석 또는 수집이 지연되거나, OAuth 갱신 실패로 채팅·분석이 중단된다. |
| run 대기열 · 화면 | 승인된 분석이 `queued`에서 analyst의 `running`으로 넘어가고 결과가 화면에 보인다. `GET /api/status`의 작업과 화면 시세·수급 갱신도 정상이다. | 대기열이 줄지 않고 analyst가 가져가지 않거나, 결과·시세가 화면까지 전달되지 않는다. |
| 메모리 · 재시작 | 컨테이너별 메모리 사용량이 한도 안에서 안정되고 재시작 횟수가 증가하지 않는다. | 메모리 한도에 닿아 OOM이 나거나 재시작·헬스 실패가 반복된다. |

API 헬스 실패, 발급자 중복, OOM 재시작 등 명확한 장애는 관찰 종료를 기다리지 않고 복귀 절차로 간다. 관찰 구간에 분석이나 수집이 없었으면 해당 항목을 미확인으로 남긴다. 전환 결과에 배포 SHA, Jenkins 빌드, 관찰 시간과 각 항목의 상태를 기록한다.

### 기존 `all` 경로로 복귀

1. 새 백엔드 배포 요청을 멈추고 진행 중인 배포의 종료를 확인한다. 실패한 빌드의 자동 이미지 되돌림 결과와 현재 컨테이너 라벨을 확인한다. 자동 이미지 되돌림은 split 스위치를 복원하지 않는다.
2. Vault에서 `DESK_BACKEND_SPLIT=0`으로 되돌리고 기존 종합 잡을 `FORCE_DEPLOY=true`로 실행한다. 역할만 되돌릴 때는 현재 DB 스키마와 호환됨을 확인하고 전환에 사용한 SHA를 `DEPLOY_COMMIT`으로 지정한다. 이전 이미지로도 돌아가야 하는 경우에는 적용된 Alembic 리비전과 이미지 호환성을 먼저 확인하고 [2-2절](#2-2-되돌림)을 따른다. Jenkins·Vault를 쓸 수 없으면 [2-4절](#2-4-손-배포--scriptsdeploysh-대체-경로)에 따라 `.env.runtime`도 `0`으로 맞추고 호환되는 SHA로 백엔드를 배포한다.
3. 빌드 로그에서 collector · analyst를 지운 뒤 `backend-prod`를 올린 순서를 확인한다. `backend-prod` 하나만 남고 `desk.backend-split=0`, 커밋 라벨과 헬스가 정상이어야 한다. 다시 run 대기열, 화면 갱신, 토큰 발급 역할을 확인한다. 복귀 후 발급이 있으면 `toss token issued (all)`로 기록된다.

#808은 이미지 준비와 서비스 단독 배포·되돌림을 제공한다. #814는 독립 잡 인계 계약의 대역 시험이며, #818은 비운영 어댑터와 실측 절차다. 운영 독립 잡 연결과 활성화는 이 종합 잡 전환 절차와 별도로 검증하고 승인받는다.

## 13. 공유 캐시 — 홈랩 Redis (#689, 선택)

api · collector · analyst(#688) 가 따로 뜨면 같은 수급 · 시세를 프로세스마다 다시 부른다. Redis 를 켜면 collector 가 읽은 값을 api 가 먼저 읽는다.
**비워 두면 지금처럼 프로세스 메모리 캐시만** 쓴다(아무 키도 필수가 아니다). 넣는 것 · 넣지 않는 것은 [security.md](security.md) §5.2.
**선행 조건: 백엔드가 api · collector · analyst 로 나뉘어 있을 것**(compose 의 `DESK_ROLE`). `all` 한 프로세스면 나눠 쓸 상대가 없어 이득이 없다.

- 키 이름에 코드 지문(`desk/` 소스의 해시)이 들어간다. 배포하면 새 키로 시작하고 옛 빌드의 값은 TTL 까지 그냥 남았다 사라진다 — 값 모양이 바뀐 배포가 옛 값을 읽어 화면이 깨지는 일이 없다.
  - 예외(#993): `sector_bars` 는 코드 지문 대신 손으로 올리는 버전을 넣어 배포를 넘겨 남는다. 그래서 재기동 뒤 첫 prewarm 패스가 섹터 ETF 이력을 처음부터 다시 읽지 않는다. 이 이득은 프로세스가 하나(`all`)여도 있다.
- 운영 `DESK_REDIS_CACHE_KEY` 는 운영에만 쓴다. 개발 · 가짜 실행(`DESK_FAKE_RUNS`) 프로세스가 같은 Redis 를 가리켜도 키가 다르면 서로의 값을 보지도 덮지도 못한다.
- Redis 가 응답하지 않을 때: 비동기 호출은 0.25초에서 끊고, 워커 스레드의 동기 호출(갭 · 오실레이터 · 섹터 봉)은 Sentinel 3대를 차례로 물어 최대 1초 남짓이다. 그 뒤 30초는 건너뛴다.
  - 예외(#993): 비동기 호출의 타임아웃이 한도의 2배(0.5초)를 넘겨 깼으면 Redis 가 아니라 이 프로세스의 이벤트 루프가 막혔던 것이다(기동 직후 등). 그 읽기 · 쓰기만 miss 로 세고(`late`) 건너뛰지 않는다. 다만 그런 `late` 가 비동기 호출 성공 없이 3번 이어지면 30초 건너뛴다.

| 키 | 값 |
|----|----|
| `DESK_REDIS_SENTINELS` | `192.168.0.11:26379,192.168.0.13:26379,192.168.0.12:26379` (Infra `redis/README.md`) |
| `DESK_REDIS_MASTER` | 비우면 `master-redis` |
| `DESK_REDIS_PASSWORD` | Redis `requirepass` — **Vault 에만** |
| `DESK_REDIS_CACHE_KEY` | 이 앱 전용 무작위 비밀(32바이트, base64). 키 이름을 HMAC 으로 가리고 값을 봉인한다 — **Vault 에만**. 비면 Redis 를 설정해도 꺼진다. 바꾸면 기존 값은 TTL 까지 무시될 뿐이다 |
| `DESK_REDIS_URL` | Sentinel 대신 단일 서버(`redis://host:6379/0`, 시험용). Sentinel 이 있으면 무시 |

| 공유 이름(`td:<이름>:…`) | 쓰는 쪽 → 읽는 쪽 | TTL |
|------|------|-----|
| `market_flow` · `futures_flow` | collector 스냅샷 잡(`fresh`) → api 카드. 실패한 읽기는 넣지 않는다 | 각 캐시 TTL, 최소 90초(1분 스냅샷 사이를 잇는다) |
| `quote` | 먼저 읽은 프로세스 → 나머지. 역할을 나누면 collector 의 수요 수집(`demand`)이 화면이 읽는 종목을 10초마다 받아 쓴다 | 10초(직접), 30초(수집). api 만 10초 넘은 값을 받는다 |
| `futures_quote` | collector 의 수요 수집 → api 의 선물 시세 카드(현재가 · 분봉). 실패한 읽기는 넣지 않는다 | 카드 TTL 이상 |
| `want` | api(`demand_wants`, 2초마다, 바뀌었거나 10초 지났을 때) → collector(`demand`, 5초마다) | 20초 |
| `sr` · `gap_fill` · `oscillator` | 계산한 프로세스 → 나머지(메모리 → Redis 2단) | 각 캐시 TTL |
| `sector_bars` (#993) | api 의 섹터 뷰(`day_sectors`)가 ETF 하나의 그날 전체 일봉 읽기를 마치면 쓴다(봉 시각 · 수정 종가, 하루 한 번). 그날 다음 api 프로세스가 읽고 최근 10봉만 합친다. **빌드 지문 대신 버전으로 키를 만들어 배포를 넘겨 남는다**(docs/security.md 5.2) | 24시간(그 KST 날짜에만 읽는다) |

나머지 메모리 캐시(추정치 종가 · 바이낸스 추정 · 공시 · 실시간 봉 · 롱숏 · NQ 한도 · 종목 프로필 · 예상치)는 한 프로세스만 쓰거나 값이 JSON 으로 돌아오지 않아 그대로 둔다.

### 켜기 (사람이 한 번)

```bash
# 1) Vault 에 키 4개를 더한다 — PATCH(merge) 라 다른 키는 그대로다(POST 는 secret 전체를 덮어쓴다).
#    토큰 · 비밀번호는 read -s 로 받아 파이프로만 넘긴다(argv · 셸 기록 · 파일에 남기지 않는다). 토큰은 3절처럼 ui-trading-desk 계정에서.
#    캐시 키는 여기서 만들어 바로 넣는다(화면에 내지 않는다)
read -rs -p 'vault token: ' VAULT_TOKEN; echo
read -rs -p 'redis password: ' P; echo
K=$(head -c 32 /dev/urandom | base64 | tr -d '\n')
printf '%s\n%s' "$P" "$K" | jq -Rs 'split("\n") as [$p, $k]
  | {data: {DESK_REDIS_SENTINELS: "192.168.0.11:26379,192.168.0.13:26379,192.168.0.12:26379",
            DESK_REDIS_MASTER: "master-redis", DESK_REDIS_PASSWORD: $p, DESK_REDIS_CACHE_KEY: $k}}' \
  | curl -sS --fail -o /dev/null -X PATCH --header 'Content-Type: application/merge-patch+json' \
      --header @<(printf 'X-Vault-Token: %s\n' "$VAULT_TOKEN") --data @- \
      https://vault.8llow8llowme.com/v1/kv/data/trading-desk/backend/prod/env && echo 넣음
curl -sS --fail --header @<(printf 'X-Vault-Token: %s\n' "$VAULT_TOKEN") \
  https://vault.8llow8llowme.com/v1/kv/data/trading-desk/backend/prod/env | jq -r '.data.data | keys[]' | grep REDIS   # 이름만 확인
unset VAULT_TOKEN P K
# 2) Jenkins trading-desk-backend 를 FORCE_DEPLOY 로 (손 배포면 .env.runtime 에 같은 4줄 → scripts/deploy.sh HEAD backend)
```

### 연결 확인 (배포 뒤)

```bash
ssh homelab-backend-server 'docker logs trading-desk-backend-prod 2>&1 | grep -E "shared cache (on|unreachable)" | tail -3'
#   shared cache on (sentinel master-redis via 3 hosts)   ← 켜짐
#   shared cache unreachable (...) - memory only for 30s  ← 닿지 않음: 앱은 메모리 캐시로 계속 돈다
```

- 보호 모드(§15)에서는 아래 `/api/status` 진단 계수와 `/api/perf/timings` 를 **소유자 세션**으로 읽는다. 다른 계정에는 계수가 빠지고(오류 없음) timings 는 403 이다(#995).
- `/api/status` 의 `shared_cache`: `on` · `down_sec`(건너뛰는 남은 초) · `hits` · `misses` · `writes` · `skipped` ·
  `rejected`(봉인 · 검사에 걸린 값 — 0 이 아니면 캐시 키가 바뀌었거나 누가 `td:` 아래에 쓴다) · `errors` ·
  `late`(이벤트 루프가 막혀 늦게 깬 비동기 호출 — miss 로만 센다, #993) — 이 프로세스 것.
- 키 확인(master 호스트 main-server 에서). 키 이름은 `td:<종류>:<HMAC>` 라 종목이 보이지 않고 값은 봉인돼 있다. 비밀번호는 argv 대신 env 로:

```bash
ssh -t homelab-main-server
read -rs -p 'redis password: ' REDISCLI_AUTH; echo; export REDISCLI_AUTH
docker exec -e REDISCLI_AUTH redis-node1 redis-cli --scan --pattern 'td:*' | cut -d: -f2 | sort | uniq -c   # 종류별 개수
docker exec -e REDISCLI_AUTH redis-node1 sh -c 'redis-cli --scan --pattern "td:*" | head -50 | while read k; do redis-cli ttl "$k"; done' | sort -n | head -3   # -1 이 나오면 TTL 없는 키(버그)
unset REDISCLI_AUTH
```

### 전 · 후 측정 (#689 완료 기준)

1. 켜기 **전**, 장중(09:00–15:30 KST)에 `GET /api/perf/timings?reset=1` 로 비우고 1시간 뒤 `GET /api/perf/timings` 와 `GET /api/status` 를 저장한다.
2. 켠 **뒤** 같은 요일 · 같은 시간대에 똑같이 한다.
3. 비교: `/api/market/flow` · `/api/market/futures` · `/api/quote` · `/api/levels` · `/api/gaps` · `/api/oscillator` 의 p95,
   그리고 `/api/status` `outbound` 의 `toss` · `kis`(그 프로세스가 `since` 부터 부른 수). api 프로세스의 수가 0 에 가까우면 완료.
4. 수요 수집(#689): api 의 `/api/status` `demand` 는 `wanted`(최근 30초에 읽힌 키 수) · `flushes`, collector 의 `demand` 는
   `collecting` · `served` · `failed` 다. 화면을 닫으면 1분 안에 둘 다 0 이 된다. collector 의 `shared_cache.misses` 에는 want 조회(쉬는 동안 분당 12번)가 들어간다.
   수집 뒤에도 api 에 남는 직접 호출: 국장 1분봉 채우기 · `/api/live/stream` 1분봉, 포트폴리오의 보유 · 환율(계좌 데이터라 공유하지 않음), 종목마다 처음 한 번(콜드).

### 이벤트 루프 지연 (#859)

요청 · 스트림 · 백그라운드 작업이 이벤트 루프 하나를 같이 쓴다. 루프 위의 동기 호출(파일 쓰기, TLS 컨텍스트 생성, 큰 계산)이나
GIL 을 잡은 워커 스레드가 있으면 모든 요청이 같이 늦어진다(`desk.loop_lag`).

- `/api/status` 의 `loop_lag`: 최근 5분의 `p50_ms` · `p95_ms` · `max_ms`, `stall_ms`(250ms) 넘게 멈춘 횟수 `stalls_recent`,
  그리고 기동 뒤 남겨 둔 멈춤(최근 200개)의 자리 `sites`(`모듈.py:줄 함수` · 횟수 · 최대 ms). 그 자리는 멈춤을 처음 본 순간
  루프 스레드가 서 있던 가장 안쪽 `desk` 프레임이다. 경고 줄 `event loop blocked` 는 자리마다 1분에 한 번(그 사이 횟수를 붙여) 남는다.
  자리가 소켓 읽기처럼 평범한 곳이면 루프가 GIL 을 기다린 것(워커 스레드의 CPU 작업)으로 읽는다.
- slow request 줄 끝의 `loop_max=<ms>`: 그 요청이 도는 동안 가장 길게 멈춘 루프 시간. 요청 시간과 비슷하면 그 요청은 자기 일이
  아니라 루프를 기다린 것이다.

```bash
ssh homelab-backend-server 'docker logs --since 24h trading-desk-backend-prod 2>&1 | grep "event loop blocked" | sed "s/.* at //" | sort | uniq -c | sort -rn | head'
```

## 14. 운영 로그의 알려진 외부 원천 실패 (#861)

운영 서버에서 소셜 · 예측시장 조회가 실패해도 TradingAgents 분석은 이어진다. 분석가에게 전달되는
`<stocktwits unavailable: …>`, `<Reddit unavailable: … this is not an absence of discussion>`, Polymarket
`currently unavailable`은 조회 실패를 뜻한다. 정상 조회에서 게시물이나 시장이 0건인 경우와 구분한다.
시장 심리 보고서에는 해당 소셜 근거가, 뉴스 보고서에는 해당 예측시장 확률이 빠질 수 있다.

| 로그 | 관측(2026-11-23, 서버와 PC에서 같은 요청 한 번씩) | 서버 | PC |
|------|------|------|----|
| `StockTwits fetch failed … 403 Forbidden` | 서버 요청에 즉시 접근 거부 응답(0.03초) | 403 | 200 |
| `Reddit RSS fetch failed for r/a+b+c … 404` | `+`로 묶은 `search.rss`는 404, 개별 서브레딧의 인증 없는 검색은 429 | 404 · 429 | 404 · 429 |
| `Polymarket search failed … Connection reset` | 서버에서 연결 리셋, PC에서 정상 응답 | 리셋 | 200 |

- 당시 관측한 실패는 원천마다 0.2초 미만이었다. Reddit이 429를 받으면 `Retry-After`(상한 60초)를 기다렸다 한 번 더 묻는다.
  헤더가 없으면 약 60초를 기다린다. Polymarket HTTP 요청의 라이브러리 타임아웃은 30초다.
- `Cookie fetch from fc.yahoo.com failed` 는 yfinance 내부 경고다. 바로 뒤 Yahoo 가 빈 일봉을 줄 수 있어 `desk.prices._yahoo_rows` 가
  일봉 이상이 비면 한 번 더 묻는다(로그 `Yahoo returned no 1d bars for …; asking once more`).

### Trading Desk에서 제어할 수 있는 범위

- TradingAgents 핀 `5a74a7a0211932326eaa73f2ac71c95fc91daf22`에서 `sentiment_analyst.py`가 StockTwits와
  Reddit을 직접 조회한다. 두 원천은 vendor 설정으로 선택할 수 없으며, 원시 HTTP 결과와 재시도는 라이브러리 안에서 처리된다.
- `get_prediction_markets`는 `data_vendors.prediction_markets`와 `tool_vendors.get_prediction_markets`로 등록된 vendor를
  고른다. 등록된 vendor는 Polymarket 하나다. 빈 값은 기본 체인으로 돌아가고, 미등록 이름은 `ValueError`가 된다.
  Polymarket은 연결 오류를 먼저 안내 문자열로 변환하므로 호출자가 구조화된 오류 종류를 받지 못한다.
- 개별 실행에서 소셜 조회를 생략하려면 새 분석의 **옵션 → 애널리스트 → 심리**를 해제한다. 두 원천과 함께 심리 분석가의
  뉴스 기반 심리 분석도 빠진다. 뉴스 분석가를 선택한 채 Polymarket만 제외할 공개 설정은 없다.
- 조회 실패 문구는 분석가에게 전달되는 입력이다. 보고서 본문의 최종 표시는 모델 출력에 달려 있다.

위 핀을 로컬에서 추출해 `urlopen`과 `requests.get`에 가짜 응답을 넣고 실제 대기 없이 확인했다.
StockTwits 403은 두 번 연속 호출됐고, Reddit 404는 재시도 없이 조회 불가, 정상 빈 피드는 0건으로 처리됐다.
Reddit 429의 `Retry-After: 75`에는 60초 대기를 한 번 예약했고, Polymarket 연결 리셋은 재시도 없이 조회 불가로 반환됐다.

### 현재 앱의 대체 경계

고정 버전의 공개 생성 인자는 분석가 선택, 설정, 콜백까지만 제공한다. 앱은 선택된 심리·뉴스의 원천 근거를
기존 그래프 초기 상태의 `sentiment_report`·`news_report`에 넣는다. 그 실행에서 라이브러리의 두 노드를
제외한다. 앱의 기존 종목 뉴스와 글로벌 뉴스 도구를 사용하며 초기 보고서에서 원천을 구분한다.
뉴스 분석가가 수행하던 모델 주도 검색과 FRED 질의는 준비 보고서의 범위에 포함되지 않는다. 시장·기초 분석과
뒤의 연구·논쟁은 기존 그래프에서 처리한다. 준비 보고서만 선택한 실행은 그래프의 분석가 최소 한 명 조건을
충족하기 위해 기본 시장 분석을 함께 실행하고 그 사실을 채팅에 표시한다.
이미 재사용 표시가 있는 일반 보고서의 초기 주입은 유지한다. 앱이 준비한 원천 상태 보고서는 다음 실행의
재사용 대상으로 등록하지 않는다. 중단된 분석을 재개하면 해당 체크포인트에 저장된 근거를 사용한다.

- StockTwits는 승인된 자동 조회 계약이 확인되지 않아 제외한다. Reddit은 승인된 OAuth 조회가 필요하므로
  결합 RSS와 개별 서브레딧 조회를 제외한다.
  [Stocktwits 이용 조건](https://stocktwits.com/about/legal/terms/) ·
  [Reddit Data API](https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki)
- 예측시장은 기존 도구 또는 카테고리 vendor 설정의 `polymarket` 또는 `default`에 한해 현재 UTC 날짜의
  분석에서 공개 Gamma `GET https://gamma-api.polymarket.com/public-search`를 사용한다. 다른 이름이나 빈
  설정에는 원천 제외 상태를 표시한다. 대상 종목 코드만 검색어로 전달하며 응답의 리디렉션을 따르지 않는다.
  거래 기능과 과거 날짜의 추정 확률은 제공하지 않는다.
  [공식 공개 검색](https://docs.polymarket.com/api-reference/search/search-markets-events-and-profiles) ·
  [Polymarket 이용 조건](https://polymarket.com/tos)

| 결과 | 동작 |
| --- | --- |
| HTTP 403·404·451 | 한 번의 요청에서 중단하고 같은 공개 검색 경로를 프로세스에서 15분간 쉰다. |
| 연결 오류·타임아웃 | 최대 2.5초 예산 안에서 요청당 최대 1.1초를 적용하고 200ms 후 한 번만 재시도한다. |
| HTTP 429 | 해당 실행은 즉시 끝내고 `Retry-After` 이후의 다음 실행에서 조회를 허용한다. 값이 없으면 60초다. |
| 기타 연속 실패 | 재시도를 포함한 조회 전체를 실패 한 번으로 세고 3회 연속 시 60초간 쉰다. |
| 성공 0건 | 조회 성공과 사용 가능한 시장 0건을 구분해서 전달한다. 유효한 Yes 가격 0은 그대로 남긴다. |

같은 경로는 동시에 한 요청만 허용한다. 대기 시간이 지난 뒤의 첫 요청이 성공하면 회로를 닫는다.
경과 시간은 단조 시계를 사용한다. 원천과 원인 코드, 회로 상태만 로그에 남기며 같은 차단으로 건너뛸 때
경고를 반복하지 않는다. 채팅과 분석 입력에는 `이 원천의 근거 없음(조회 불가)`를 남긴다.
회귀는 HTTP 응답·시계·그래프를 합성해 검증한다. 배포 후 24시간 반복 실패 여부는 별도 운영 완료 조건이다.

## 15. 계좌 데이터 보호 모드 (#871)

기본 배포 팩토리는 시작 시 DB 정책을 읽는다. `protected`에서는 같은 소유자와 직전 격리 세대에서 승인한 고정 Toss 원천을 검증한다. `isolated`, 잘못된 정책 또는 보호 계좌 승인 불일치로는 기동할 수 없다. 승인과 전진 전환 절차의 검증 및 사용자 승인이 끝날 때까지 운영 DB는 `legacy`에 둔다.

기존 `desk.cli flow-history`는 legacy 모드에서 사용한다. 보호 세대의 토큰은 검증된 팩토리의 공유 슬롯에서 읽으며, 단발 CLI에는 별도의 연동이 필요하다.

### 전환 전 점검

1. 전환 대상 SHA에서 PostgreSQL 17 CI와 `tests/test_privacy_transition.py`, `tests/test_account_egress.py`가 성공했는지 확인한다. DB가 `legacy`인 동안 승인된 배포 절차로 동일 이미지를 준비하고, 호환되는 API 또는 `all` 기동을 통해 Alembic head 적용과 헬스를 확인한다. split의 collector·analyst도 동일 이미지로 기동을 확인한다. 소유 계정과 실제 고정 Toss 계좌를 대조한다. 초기 보호 범위는 승인된 Toss 계좌와 기존 공개 알림으로 제한된다.
2. 신뢰할 수 있는 운영 셸에서 실행 중인 백엔드 컨테이너의 설정을 점검 프로세스에 직접 전달한다. 다음은 split 구성의 점검이다. `all` 구성은 `docker inspect`에 `trading-desk-backend-prod` 하나만 전달한다. Docker 설정 원문에는 비밀이 있으므로 파일·로그에 남기지 않는다.

   ```bash
   docker inspect --type container trading-desk-backend-prod trading-desk-collector-prod trading-desk-analyst-prod \
     | docker exec -i trading-desk-backend-prod python -m desk.privacy_admin status --inspect-stdin
   ```

3. 점검 결과의 정책 상태·세대, 역할 구성, 명시 소유자 슬롯과 레거시 파생 기록 건수를 검토한다. 역할 점검은 실행 중인 컨테이너의 기동 명령, 이미지, 환경과 메모리 볼륨을 비교하며 split에는 PostgreSQL이 필요하다. `--inspect-stdin`을 생략한 상태 조회에는 역할 대조 결과가 `unverified`로 표시된다. `python -m desk.privacy_admin hide-legacy`에서 이전 대상 건수를 먼저 읽는다. 롤아웃 승인과 세 역할의 호환 기동이 확인될 때까지 쓰기 명령을 실행하지 않는다.

### 승인 후 전환 순서

1. 배포 잡을 정지하고 DB와 TradingAgents 볼륨을 백업한다. 인그레스에서 유지보수 503을 응답하고, 열려 있는 스트림과 분석의 종료를 확인한다. 기존 API·collector·analyst와 해당 볼륨을 사용하는 작업자를 모두 정지하고 분석 대기열과 브로커 발급자 잠금이 해제됐는지 확인한다. 이 단계는 운영자가 확인한다.
2. 정지한 서비스와 같은 DB·메모리 볼륨 및 고정 Toss 설정을 쓰는 단발 관리 컨테이너를 아래 함수로 실행한다. 로컬의 `trading-desk-backend:prod`가 승인된 이미지인지 확인한 뒤 승인된 커밋의 저장소 루트에서 실행한다. `PRIVACY_ENV_FILE`에는 [3절](#3-운영-env--vault-정본--envruntime-손-배포-대체)의 동일 운영 설정을 전달받은 권한 600 파일 경로를 지정한다. 현재 셸의 환경이 같은 이름의 파일 설정을 덮어쓰므로 별도로 주입한 운영 설정이 없는 관리 셸을 사용한다. 관리가 끝나면 임시 env 파일을 정리한다.

   ```bash
   privacy_admin() {
     docker compose -p trading-desk-backend-prod --env-file "${PRIVACY_ENV_FILE:?}" \
       -f backend/docker-compose-backend.yml run --no-deps --rm --pull never -T backend-prod \
       python -m desk.privacy_admin "$@"
   }
   privacy_admin status
   ```

   명시 소유자 바인딩을 확인한다. 빈 슬롯이면 [명시 소유자 지정 절차](account-owner-binding.md)를 먼저 따른다. 정책 상태가 `legacy`이고 세대가 `0`이면 아래 명령을 실행한 뒤 새 격리 세대를 조회한다. `schema_not_ready`이면 legacy 호환 이미지의 스키마 준비부터 재확인한다. 격리에서 다시 보호 상태로 여는 경우에는 현재 세대를 사용한다.

   ```bash
   privacy_admin isolate --epoch 0 --confirm
   privacy_admin status
   ```

   아래 두 명령의 `N`을 점검에서 확인한 격리 세대로 바꿔 실행한다. 중간에 실패하면 같은 세대에서 남은 대상 건수를 확인하고 다시 실행한다.

   ```bash
   privacy_admin hide-legacy --apply --epoch N --confirm
   privacy_admin hide-legacy --archive-memory --apply --epoch N --confirm
   ```

   메모리 보관은 해당 볼륨을 사용하는 모든 프로세스를 멈춘 상태에서 백엔드와 같은 UID로 실행한다. Linux의 동일 볼륨에서 그룹·다른 사용자에게 쓰기가 닫힌 디렉터리를 써야 한다. 레거시 메모리 두 파일을 비공개로 보관하며 보호 모드는 보관 파일을 읽지 않는다. `hide-legacy`로 조치 대상 run과 원래 위치에 남은 메모리 파일이 모두 0건인지 다시 확인한다. 바인딩 증거가 없는 run과 보유 알림은 숨긴 채 보존하므로 `status`의 비귀속 건수에는 남을 수 있다.

3. 셸 명령 추적을 끈 관리 터미널에서 `privacy_admin toss-approval-check --epoch N`을 실행한다. 권한 있는 운영자가 현재 명시 소유자와 실제 고정 Toss 계좌·자격 구성을 별도로 대조한 뒤, JSON의 `challenge` 값을 아래 입력에 붙여 넣는다. 챌린지는 확인 당시의 바인딩·원천·격리 세대를 묶으며 표준 입력으로 전달한다. 발급 명령의 표준 출력은 관리 컨테이너가 종료되기 전 Docker 로그에 기록될 수 있으므로 접근이 제한된 관리 환경에서 실행하고 그 출력을 운영 기록에 복사하지 않는다.

   ```bash
   set +x
   read -rs PRIVACY_APPROVAL_CHALLENGE
   if printf '%s\n' "${PRIVACY_APPROVAL_CHALLENGE:?}" | privacy_admin approve-toss --epoch N --confirm; then
     privacy_admin activate --epoch N --confirm
   fi
   unset PRIVACY_APPROVAL_CHALLENGE
   ```

   `activate`는 현재 소유자·Toss 설정의 승인과 남은 레거시 run·메모리를 확인한다. `legacy_cleanup_pending`이면 출력된 두 건수를 확인하고 남은 이전을 처리한다. `toss_approval_mismatch`이면 격리 상태에서 바인딩·원천·세대를 다시 대조한다. 같은 소유 세대에 다른 원천을 승인할 수 없으며, 자격이나 계좌 교체에는 별도의 소유 세대 교체 절차가 필요하다.
4. `activate`가 반환한 보호 세대를 배포 기록에 남긴다. 보호 상태로 전환된 뒤 API를 먼저 띄워 헬스를 확인하고 같은 이미지의 collector·analyst를 띄운다. 인그레스를 열기 전에 소유자와 두 번째 계정의 계좌·분석 접근, 분석 claim, 보유 알림 차단, 공개 알림과 공개 조회를 확인한다. 로그에는 역할·상태·세대·건수만 기록한다.

### 장애 시 격리로 복귀

인그레스가 유지보수 503을 응답하도록 하고 추가 배포와 작업 시작을 멈춘다. 위 관리 컨테이너의 `privacy_admin status --policy-only`에서 보호 세대를 확인하고 `privacy_admin rollback --epoch N --confirm`을 실행한다. 정책 전용 조회는 메모리 볼륨이나 승인 자료를 읽지 않는다. 바로 전 활성화가 반환한 보호 세대를 배포 기록에서 대조할 수 있다. 보호 상태와 세대가 일치할 때만 `protected → isolated`가 적용된다. 기존 호환 프로세스의 요청은 503이 되고 새 프로세스는 기동이 거절된다. 기존 요청·작업의 종료와 데이터 보존을 확인한 뒤 유지보수 상태를 유지한다. 이미 외부로 전달된 알림은 회수할 수 없다. [12절의 `all` 복귀](#기존-all-경로로-복귀)는 프로세스 배치 변경에만 사용한다.
