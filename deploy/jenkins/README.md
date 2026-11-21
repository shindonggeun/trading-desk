# 백엔드 역할별 Jenkins 잡 등록 명세 (#785)

`trading-desk` 폴더의 세 Pipeline 잡에 대한 비활성 `config.xml` 명세다. 디렉터리 이름이 Jenkins의 잡 이름이다.

| 잡 | 역할 |
| --- | --- |
| `trading-desk/trading-desk-backend-api` | API |
| `trading-desk/trading-desk-backend-collector` | 수집기 |
| `trading-desk/trading-desk-backend-analyst` | 분석기 |

각 명세는 `disabled=true`로 시작한다. 자동 트리거, SCM 연결, 빌드 파라미터와 자격 증명 참조가 없다. 잘못 활성화되어 실행되더라도 sandbox Pipeline은 역할이 적힌 오류에서 즉시 끝난다. 기존 `trading-desk-backend`와 `trading-desk-web`의 잡 정의 및 all 모드에는 영향이 없다.

XML 구조는 Jenkins [Pipeline Job](https://javadoc.jenkins.io/plugin/workflow-job/org/jenkinsci/plugins/workflow/job/WorkflowJob.html)과 [Pipeline Groovy](https://javadoc.jenkins.io/plugin/workflow-cps/org/jenkinsci/plugins/workflow/cps/CpsFlowDefinition.html)를 기준으로 작성했다.

## 로컬 검증

저장소 루트에서 `python -B -X utf8 scripts/tests/test_jenkins_disabled_jobs.py`를 실행한다. 세 잡의 이름, 비활성 상태, 트리거 없음, sandbox와 오류 전용 스크립트를 XML에서 확인한다. Jenkins 또는 Docker 연결은 필요 없다.

## 후속 인계

- #808의 단독 배포는 서비스별 검증·되돌림, collector 발급 검사, analyst 분석 대기를 처리한다.
- 기존 백엔드 잡만 SCM 트리거를 소유한다. 실제 Jenkins에서 폴더·잡·트리거·플러그인을 읽기 전용으로 대조한 뒤 비운영 컨트롤러에서 잡 등록과 결과를 리허설한다.

## #776 이미지 인계 모의 검증

`scripts/jenkins/backend-handoff.groovy`는 Jenkins 어댑터를 주입하는 인계 계약이다. 운영 Jenkinsfile과 XML은 현재 기존 설정으로 실행된다. 저장소 루트에서 `groovy scripts/tests/backend-handoff.test.groovy`로 계약을 시험한다.

1. 부모 잡이 기존 `trading-desk-prod-deploy` 락을 잡고 develop SHA의 CI 성공과 밀림 여부를 확인한다. 통과하면 `deploy-compose.sh backend <소스> --build-only`를 한 번 실행한다. 준비 결과에는 전체 커밋 SHA, 로컬 이미지 ID, Jenkins 노드 이름과 Docker 데몬 ID를 넣는다.
2. API → collector → analyst 잡을 하나씩 요청한다. 하위 잡은 락을 다시 잡지 않는다. 각 잡은 신뢰할 수 있는 부모 요청만 받아 실제 소스 SHA·노드·데몬·이미지 ID를 대조한다. 역할은 잡에서 정하고, 환경 파일이나 자격 증명 값은 인계에 넣지 않는다.
3. 하위 잡은 검증한 값을 `deploy-compose.sh backend <소스> --service <역할> --image-id <ID>`에 넘긴다. 스크립트는 로컬에서 커밋 태그를 다시 확인하고 전달받은 이미지 ID가 다르면 교체 전에 멈춘다. API를 먼저 검증하며 후속 역할의 스키마 호환은 #808의 단독 배포가 확인한다.
4. 종료 코드 0만 다음 역할을 실행한다. 2는 대상 서비스 복구, 3은 분석 대기 종료로 남기고 후속 잡을 차단한다. 확인이 필요한 실패도 후속 잡을 차단한다. 재실행은 공통 락을 다시 잡아 새로 확인하고 이미 올라간 정상 이미지는 #808의 단독 배포가 건너뛴다.

실제 어댑터는 요청한 자식 빌드가 종료된 뒤 그 빌드의 결과에서 역할·커밋 SHA·이미지 ID·스크립트 종료 코드를 확인해야 한다. 결과 누락이나 불일치는 후속 잡을 차단한다.

모의 어댑터에서 부모 취소는 현재 자식에 취소를 전달하고 자식 종료 확인 뒤 락을 해제한다. 실제 어댑터의 `start`는 하위 잡을 만든 경우 그 핸들을 추적해야 한다. `cancelAndAwait`는 하위 실행이 끝나거나 배포 권한이 격리된 뒤에만 반환해야 한다. Jenkins에서 자식 큐 등록 직후 취소, 실행 중 취소, 컨트롤러 재시작과 에이전트 단절 상황에서 이 조건을 검증해야 한다. 종료를 확인할 수 없는 경우까지 현재 모의 시험이 보장하지는 않는다.

비운영 리허설에서는 전용 권한으로 부모 외 직접 실행과 재실행을 막고, 하위 잡의 노드·데몬 고정과 공통 락 유지, 손 배포와의 직렬화를 확인한다. 운영 잡 활성화와 split 전환은 이 검증을 마치고 별도 승인을 받은 뒤 진행한다.

## 비운영 Jenkins 어댑터 검증 절차

실제 Jenkins의 Pipeline Build Step과 Lockable Resources를 통해 부모와 자식 잡의 수명주기를 확인한다. Jenkinsfile은 네 리허설 잡에서만 실행된다. 각 역할의 교체와 복구는 작업공간 파일로 기록하고, Docker에는 `trading-desk-rehearsal:<SHA>` scratch 이미지만 준비한다. #808의 실제 서비스 교체와 복구는 가짜 Docker 시험에서 확인한다.

### 비운영 설정

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
