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

- #776 서비스 단독 배포·되돌림, collector 단일 발급자와 analyst 드레인 계약을 먼저 확정한다.
- 기존 백엔드 잡만 SCM 트리거를 소유한다. 활성화 설계는 CI를 통과한 full SHA, 한 번 빌드한 불변 이미지, 공통 락, API 스키마·헬스 선행 조건을 같은 릴리스 명세로 전달한다.
- 실제 Jenkins에서 읽기 전용으로 폴더·잡·트리거·플러그인을 대조한 뒤 비운영 컨트롤러에서만 비활성 XML 로드, 세 잡의 독립 결과, 이미지 1회 빌드, API 실패 차단, 서비스 단독 롤백을 리허설한다. 운영 등록과 활성화는 별도 승인 후 진행한다.
