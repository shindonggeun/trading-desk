# 백엔드 역할별 Jenkins 잡 등록 명세 (#785)

`trading-desk` 폴더의 세 Pipeline 잡에 대한 비활성 `config.xml` 명세다. 디렉터리 이름이 Jenkins의 잡 이름이다.

| 잡 | 역할 |
| --- | --- |
| `trading-desk/trading-desk-backend-api` | API |
| `trading-desk/trading-desk-backend-collector` | 수집기 |
| `trading-desk/trading-desk-backend-analyst` | 분석기 |

각 명세는 `disabled=true`이고 트리거가 없다. 고정된 저장소와 `refs/heads/develop`의 `Jenkinsfile-backend-role`만 SCM으로 불러온다. 하위 잡 파라미터에는 릴리스 ID·부모 빌드 번호·커밋·이미지 ID·노드·데몬만 들어간다. 자동 SCM 트리거는 기존 부모 잡만 소유한다. 역할 잡을 직접 시작하거나 Replay한 실행은 상위 잡 원인 검사에서 거부된다.

XML 구조는 Jenkins [Pipeline Job](https://javadoc.jenkins.io/plugin/workflow-job/org/jenkinsci/plugins/workflow/job/WorkflowJob.html)과 [Pipeline Groovy](https://javadoc.jenkins.io/plugin/workflow-cps/org/jenkinsci/plugins/workflow/cps/CpsFlowDefinition.html)를 기준으로 작성했다.

## 로컬 검증

저장소 루트에서 `python -B -X utf8 scripts/tests/test_jenkins_disabled_jobs.py`, `bash scripts/tests/backend-deploy-lease.test.sh`, `groovy scripts/tests/backend-production.test.groovy`를 실행한다. XML·등록 계약, 담당 역할·중복 소비·실패 및 고아 릴리스 차단, 상위 잡의 종합 경로·역할 호출 순서를 대역으로 확인한다. Jenkins 또는 Docker 연결은 필요 없다.

[`backend-deploy-targets.test.sh`](../../scripts/tests/backend-deploy-targets.test.sh)는 가짜 GitHub
응답과 Docker 조회로 역할별 커밋 범위, 라벨 누락과 공용 변경의 전체 배포 복귀를 확인한다.

## 운영 연결 전 검증과 등록

- 실제 Jenkins에서 폴더·잡·트리거·플러그인을 읽기 전용으로 대조하고 아래 비운영 리허설로 정상·취소·실행기 단절을 검증한다. #842 드릴은 격리 Docker에서 API만 교체·복구하고 양쪽 워커의 ID와 시작 시간이 유지됐음을 확인한다. 컨트롤러 큐·권한·호스트 경계에는 별도 증거가 필요하다.
- [등록 도구](../../scripts/jenkins/register-disabled-backend-jobs.py)는 누락된 잡만 비활성 상태로 생성한다. 기존 자리표시자 잡은 그대로 둔다. 검증과 승인 후 운영자가 기존 정의·비활성 상태를 백업하고 비활성인 동안 위 SCM·트리거·파라미터 계약으로 교체한 뒤 읽기 전용으로 확인한다. 폴더 권한 및 빌드 실행 주체를 설정하고 부모만 하위 잡 읽기·빌드·취소가 가능하게 제한한다.
- 백엔드 배포 노드와 수동 배포 SSH 실행 주체가 같은 Docker 호스트·같은 UID에서 **같은 파일시스템 디렉터리** `/var/lib/trading-desk/deploy-fence`를 사용해야 한다. 호스트 디렉터리는 배포 계정 소유의 mode 700이며 애플리케이션 컨테이너에는 연결하지 않는다. 해당 경로를 같은 이름으로 전용 에이전트에 연결하고 `flock`·Docker CLI를 사용할 수 있는지 확인한다. 펜스가 설치된 배포는 `unix:///var/run/docker.sock`으로 고정된다. 이 소켓이 전용 에이전트와 수동 호스트에서 동일한 데몬을 가리키는지 확인한다.
- 검증 후 Jenkins 전역 설정에 `TD_SPLIT_DEPLOY_NODE`를 전용 노드의 실제 이름으로 설정하고 `TD_SPLIT_JOBS_ENABLED=true`를 별도로 적용한다. 기본값은 종합 배포다. 역할 잡을 끄더라도 호스트 펜스와 전용 노드 설정은 남은 하위 작업을 확인하고 종료하기 전까지 유지한다. 펜스를 사용하는 수동 배포는 최신 `scripts/deploy.sh`를 `TD_DEPLOY_FENCE_REQUIRED=1`로 호출한다. 이전 커밋은 배포 SHA로만 지정할 수 있다. 펜스가 없는 예전 수동 스크립트를 역할 잡과 함께 사용하지 않는다.

### #965 자동 선택 배포 활성화 점검

전용 노드에서 배포 계정으로 [`scripts/jenkins/backend-role-preflight.sh`](../../scripts/jenkins/backend-role-preflight.sh)를
읽기 전용으로 실행한다. 기존 환경의 `TD_SPLIT_JOBS_ENABLED=true`, `TD_SPLIT_DEPLOY_NODE`와
`NODE_NAME`의 일치, mode 700인 펜스의 소유권, 활성 인계 유무를 확인한다. 기존
`trading-desk-github` 자격으로 대상 저장소의 머지 PR을 조회할 수 있는지도 확인한다.
선택기에 필요한 권한은 `Pull requests: read`다. GitHub 조회를 확인할 수 없는 경우
판정은 전체 배포로 복귀한다.

develop 머지 뒤 push CI에서 [역할 라벨 워크플로](../../.github/workflows/backend-role-labeler.yml)가
푸시된 커밋과 머지 PR의 관계를 확인하고 세 가지 역할 라벨을 동기화한다. 이 잡은
배포용 CI 결과를 내기 전에 완료된다. PR 확인이나 라벨 동기화가 실패하면
Jenkins는 필수 라벨을 확인할 수 없는 변경을 전체 배포로 처리한다.
대상 PR의 라벨과 Jenkins 판정을 함께 대조한다. 운영 노드·잡 활성화 및 실제 교체 검증은
승인 뒤 진행한다.

## #776 이미지 인계와 복구

1. 부모 잡이 기존 `trading-desk-prod-deploy` 락을 잡는다. 독립 잡을 활성화하고 Vault의 split이 켜져 있으며 기존 split API가 healthy일 때 API → collector → analyst의 역할별 순서를 사용한다. 이 상태에서 기본 `SERVICES=auto`는 역할별 운영 커밋과 머지 PR 라벨을 대조한다. `all`과 역할 하나를 직접 선택할 수도 있다. 신규 전환이나 split 해제는 종합 경로로 수행한다. [선택 배포 규칙](../../docs/deploy.md#백엔드-역할별-선택-배포-965)
2. 자동 역할별 배포는 CI 성공과 develop 최신 SHA를 이미지 준비 전과 직후에 다시 확인한다. 명시한 `DEPLOY_COMMIT`은 기존 develop ancestry·CI 게이트를 사용한다. 전용 노드에서 이미지 한 번과 단일 Vault 스냅샷을 준비한다. 호스트 펜스 아래에 mode 600 스냅샷, 대상 Compose, 실행 스크립트와 릴리스 기록을 보존한다. 파라미터나 빌드 산출물로 스냅샷을 전달하지 않는다.
3. 부모는 노드 실행기를 반납하고 각 역할 잡이 완료될 때까지 동기 대기한다. 하위 잡은 부모가 아직 활성 상태인지 확인한 뒤 동일 호스트의 `flock`에서 릴리스·부모·SHA·이미지·노드·데몬·순서를 검증하고 해당 역할을 한 번 소비한다. 교체부터 검증과 복구까지 잠금을 유지한다. #808의 단독 배포가 API 스키마 호환, collector 발급 검사, analyst 드레인 및 헬스·이전 이미지 복구를 처리한다. 같은 이미지의 정상 컨테이너는 건너뛴다. `FORCE_DEPLOY`는 재생성을 강제하며 `FORCE_STOP_RUNS`는 analyst 대기를 우회할 때만 사용한다.
4. 확인된 종료 코드 0만 다음 역할을 시작한다. 종료 코드 2는 대상 서비스 복구, 3은 분석 대기 종료로 기록한다. 나머지 실패와 임의 종료도 후속 잡을 차단한다. 부모는 일치하는 자식의 종료 기록을 확인한 경우에만 자기 릴리스를 정리한다.

### 중단된 릴리스

부모가 취소되거나 종료 확인을 잃으면 호스트의 `active` 릴리스가 남는다. 이를 새 배포가 자동으로 덮어쓰지 않는다. 종합 Jenkins 배포와 최신 수동 배포도 동일 호스트 펜스에서 이를 발견하면 배포나 소스 교체 전에 중단한다. 이미 락을 잡아 실행 중인 하위 작업은 락 해제까지 다른 돌연변이와 직렬화된다.

운영자가 부모·자식 빌드 번호와 대기열을 확인하고 실행기 및 Compose 작업의 종료도 확인한 후, 같은 펜스를 확보한 상태에서 해당 릴리스만 폐기한다. `active/env`의 스냅샷 값을 출력하거나 보관하지 않는다. 이후 이미지 라벨·헬스·스키마를 확인하고 기존 종합 경로 또는 동일 SHA의 선택 서비스를 재실행한다. 부모 상태 조회와 티켓 소비 사이의 취소 경쟁, 컨트롤러 재시작, 노드 단절, 권한 및 공유 마운트 동작은 비운영 실측으로 검증한다. 이 항목이 확인될 때까지 운영 활성화는 보류한다.

## 비운영 Jenkins 어댑터 검증 절차

실제 Jenkins의 Pipeline Build Step과 Lockable Resources를 통해 부모와 자식 잡의 수명주기를 확인한다. Jenkinsfile은 네 리허설 잡에서만 실행된다. 각 역할의 교체와 복구는 작업공간 파일로 기록하고, Docker에는 `trading-desk-rehearsal:<SHA>` scratch 이미지만 준비한다. #808의 실제 서비스 교체와 복구는 가짜 Docker 시험에서 확인한다.

### 비운영 설정

GitHub-hosted의 무자격 Controller 동작은 [호스티드 리허설](rehearsal/hosted/README.md)에서 확인한다. 이 경로의 합성 역할 설정과 운영 전환 실측은 별도다.

로컬 Docker의 포트·볼륨·플러그인 준비와 정적 검증은 [격리 Compose 템플릿](rehearsal/README.md)을 참고한다.

1. 격리된 Jenkins 컨트롤러에 별도 Docker 데몬을 쓰는 전용 에이전트 `trading-desk-rehearsal`을 둔다. 노드 이름과 라벨을 모두 이 이름으로 맞춘다.
2. 비운영 컨트롤러의 `trading-desk-rehearsal` 폴더에 `coordinator`, `api`, `collector`, `analyst` Pipeline을 만든다. 네 잡 모두 sandbox Pipeline script from SCM, 동일한 신뢰 저장소의 develop 브랜치, Script Path `Jenkinsfile.backend-rehearsal`로 둔다. 자동 트리거, 폴링과 자동 재실행 설정은 비운영 잡에서도 비워 둔다.
3. 부모 잡에는 문자열 파라미터 `TD_CASE`를 두고 기본값은 `success`로 한다. 세 자식 잡에는 빈 기본값의 문자열 파라미터 `TD_SHA`, `TD_IMAGE_ID`, `TD_AGENT`, `TD_DAEMON`, `TD_PARENT_BUILD`, `TD_CASE`를 둔다. 부모 실행 주체에만 자식 잡 읽기·실행·취소 권한을 부여하고 자식 설정·Replay·설명 수정 권한은 검증 관리자까지로 제한한다. 부모의 동시 실행은 두 번째 빌드가 공통 잠금을 기다리는 장면을 확인할 수 있도록 비운영에서 허용한다.
4. `groovy scripts/tests/backend-rehearsal.test.groovy`는 Jenkins 스텝을 대역으로 실행한다. 실제 Controller·agent·plugin 검증은 다음 절차로 기록한다. [Pipeline Build Step](https://www.jenkins.io/doc/pipeline/steps/pipeline-build-step/) · [Lockable Resources](https://www.jenkins.io/doc/pipeline/steps/lockable-resources/)

### 남겨야 할 증거

| 시나리오 | 확인할 결과 |
| --- | --- |
| `success` | 부모가 잠금 안에서 fixture 이미지를 한 번 준비한다. 세 자식이 같은 커밋·이미지 ID·노드·데몬을 확인하고 API, collector, analyst 순서로 각각 한 번 종료한다. 재실행은 같은 이미지 ID를 재사용한다. |
| `api-failure`, `api-rollback` | API 결과가 각각 1, 2다. 복구 때 `TD_FIXTURE_RESTORED`가 남고 두 경우 모두 worker 요청은 없다. |
| `analyst-drain` | API와 collector 이후 analyst가 3으로 종료하고 부모가 대기 종료로 기록한다. |
| 직접 실행·중복 트리거·값 불일치 | 직접 실행한 자식과 Replay가 역할 실행 전에 거부된다. 수동 부모 요청 한 번당 자식은 역할마다 최대 한 번이다. 별도 비운영 설정에서 잘못된 SHA, 에이전트, 데몬, 이미지 ID의 요청을 주입하고 `TD_CHILD_ENTER` 이전에 실패하는지 확인한다. |
| `cancel-queued-api` | 자식 quiet period가 60초다. API 큐 항목 ID를 확보하고 부모의 Stop을 요청한다. 해당 항목이 취소됐거나 시작됐다면 실제 자식 빌드가 종료된 뒤 다음 부모가 공통 잠금을 얻는지 본다. |
| `cancel-api` | API가 `TD_CANCEL_POINT`에 들어가면 자식 빌드 번호를 기록하고 부모의 Stop을 요청한다. API가 실제로 종료된 뒤 다음 부모가 공통 잠금을 얻고 두 worker가 시작되지 않는지 본다. |

두 취소 시험에서 같은 잠금을 쓰는 두 번째 `success` 부모를 대기시킨다. 기록에는 부모·자식 빌드 또는 큐 ID, 취소 요청 시각, 자식 실제 종료나 큐 제거 시각, 부모 잠금 해제 이벤트, 경쟁 부모의 `TD_LOCK_ENTER` 시각을 포함한다. `TD_CHILD_BODY_EXIT`와 `TD_LOCK_BODY_EXIT`는 각각 본문 종료 표식이다. 실제 빌드 결과와 Lockable Resources 이력으로 종료와 해제를 확인한다. 해당 컨트롤러의 플러그인 버전과 실행 주체의 취소 권한도 기록한다.

자식 ID나 종료 결과를 확인하지 못하거나 잠금 해제 및 경쟁 부모 진입이 자식 종료보다 앞서면 운영 전환을 보류한다. Controller 재시작, 노드 단절, 강제 종료와 손 배포 직렬화도 별도 증거가 필요하다. 현재 저장소 검증의 범위는 대역 스텝과 테스트 절차까지다.
