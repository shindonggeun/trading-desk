# 호스티드 Jenkins 컨트롤러 리허설

`Isolated Jenkins controller rehearsal` 워크플로는 GitHub-hosted runner의 일회용 컨트롤러에서 Jenkins ACL, 큐, Pipeline Build Step과 Lockable Resources 동작을 확인한다. [#818 리허설](../../README.md#비운영-jenkins-어댑터-검증-절차)의 인계·하위 잡 검사를 재사용한다.

- 이미지 준비 단계에서 공개 Jenkins 이미지와 플러그인을 받는다. 빌드 컨텍스트는 컨트롤러 fixture와 기존 인계 코드만 담는다.
- 런타임은 비루트 UID, `--network none`, 전체 capability 제거 및 `no-new-privileges`로 고정한다. 시작 전 Docker 메타데이터에서 호스트 바인드·소켓·게시 포트·credential 환경 변수가 없는지 확인한다. 테스트 요청에는 컨트롤러 안에 저장된 임시 쿠키와 로그에 남기지 않는 CSRF crumb을 쓴다. 컨트롤러와 이미지가 만든 전용 Jenkins Home 볼륨은 시험 뒤 제거한다.
- 부모와 자식 잡은 Authorize Project가 지정한 서로 다른 비관리 계정으로 실행된다. 관찰 잡이 정상 실행 후 부모와 세 자식의 실제 Pipeline 주체 및 upstream 원인을 확인한다. 자식 실행 주체에 자식 잡을 빌드·취소할 권한을 부여하지 않으며, 같은 주체가 실제 Build Step에서 거부되는지 확인한다.
- 익명 호출자는 부모·경쟁 잡을 시작하고 취소할 수 있다. 같은 CSRF 세션에서 자식 직접 시작·취소가 명시적인 권한 오류로 거부되는지 확인한다. 리허설의 HTTP 상태 수집을 위해 익명 잡 읽기는 허용한다. [Jenkins 빌드 권한](https://www.jenkins.io/doc/book/security/build-authorization/)
- 큐 대기 중 및 실행 중 취소에서 별도 경쟁 잡이 공통 잠금 `trading-desk-prod-deploy`에 대기한다. 경쟁 잡의 잠금 본문에서 최초로 관측한 상태가 실제 취소된 자식 Queue.LeftItem 또는 종료된 ABORTED Run인지 확인한다. 멈춘 부모의 합성 기록이 다음 배포를 막고, 종료가 확인된 뒤 기록을 정리하면 재실행된다.
- 노드 단절 시나리오는 같은 격리 컨테이너에서 별도 Remoting JVM을 루프백 WebSocket으로 연결한다. API 자식의 실행 지점과 경쟁 잡의 잠금 대기를 관측한 후 해당 JVM을 종료하고 Jenkins에서 노드가 오프라인으로 바뀌는지 확인한다. API 자식이 아직 실행 중인 상태에서 부모를 명시적으로 취소한 뒤 경쟁 잡이 자식의 `ABORTED`를 관측해야 한다. collector·analyst 호출은 이어지지 않고 합성 기록이 후속 배포를 차단한다. 이 시나리오에서는 합성 기록을 보존한다.

경쟁 잡의 최초 상태 관측으로 잠금 해제의 정확한 순간을 특정할 수 없다. 노드 실험은 Remoting 연결이 끊긴 뒤 부모를 취소하는 경로를 다루며, 에이전트 JVM 단절만으로 배포 명령이나 Compose 작업이 종료되는지는 확인하지 않는다.

## 운영에서 확인할 항목

- 실제 Jenkins 잡 목록·트리거·플러그인·빌드 실행 주체를 읽기 전용으로 대조하고, 실제 폴더의 자식 잡 읽기·빌드·취소 권한과 세 잡의 등록·표시를 확인한다. [#785](https://github.com/shindonggeun/trading-desk/issues/785)
- 배포 노드와 수동 배포 계정이 같은 UID·Docker 데몬·펜스 디렉터리를 쓰는지 확인한다. 호스트 상실과 컨트롤러 재시작 후에는 실행기 및 Compose 작업의 종료 여부를 확인한 뒤 공유 펜스에서 해당 릴리스의 복구를 판단한다.
- 실제 Docker의 API 교체·복구와 워커 유지 근거는 [#842 드릴](../../../../scripts/tests/compose-api-isolation.py)에 있다. 운영 split 전환·복귀 검증은 [#776](https://github.com/shindonggeun/trading-desk/issues/776)에서 추적한다.

이미지 ID·daemon·스키마 및 릴리스 기록은 합성 입력이다. 실제 `flock`과 가짜 Docker를 사용하는 #849 펜스 시험은 같은 워크플로의 별도 단계에서 실행한다.
