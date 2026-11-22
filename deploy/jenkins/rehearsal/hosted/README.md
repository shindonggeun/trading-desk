# #776 호스티드 Jenkins 컨트롤러 리허설

`Isolated Jenkins controller rehearsal` 워크플로는 GitHub-hosted runner의 일회용 컨트롤러에서 Jenkins ACL, 큐, Pipeline Build Step과 Lockable Resources 동작을 확인한다. [#818 리허설](../../README.md#비운영-jenkins-어댑터-검증-절차)의 인계·하위 잡 검사를 재사용한다.

- 이미지 준비 단계에서 공개 Jenkins 이미지와 플러그인을 받는다. 빌드 컨텍스트는 컨트롤러 fixture와 기존 인계 코드만 담는다.
- 런타임은 비루트 UID, `--network none`, 전체 capability 제거 및 `no-new-privileges`로 고정한다. 시작 전 Docker 메타데이터에서 호스트 바인드·소켓·게시 포트·credential 환경 변수가 없는지 확인한다. 테스트 요청에는 컨트롤러 안에 저장된 임시 쿠키와 로그에 남기지 않는 CSRF crumb을 쓴다. 컨트롤러와 이미지가 만든 전용 Jenkins Home 볼륨은 시험 뒤 제거한다.
- 익명 호출자는 부모·경쟁 잡을 시작하고 취소할 수 있다. 같은 CSRF 세션에서 자식 직접 시작·취소가 명시적인 권한 오류로 거부되고, 실제 부모 Build Step은 자식 잡을 실행하는지 확인한다. 내부 실행은 기본 Jenkins SYSTEM 주체를 사용한다. 운영의 부모 실행 계정은 별도 확인이 필요하다.
- 큐 대기 중 및 실행 중 취소에서 별도 경쟁 잡이 같은 락에 대기한다. 경쟁 잡의 락 본문에서 처음 관측한 Controller 상태가 해당 부모의 취소된 Queue.LeftItem 또는 종료된 ABORTED Run인지 확인한다. 정확한 unlock 순간은 이 관측으로 입증할 수 없다. 멈춘 부모의 합성 기록이 다음 배포를 막고, 종료를 확인한 경쟁 잡이 해당 기록을 정리한 뒤 재실행을 확인한다.

이 워크플로의 이미지 ID·daemon·스키마 및 릴리스 기록은 합성 입력이다. 실제 `flock` + 가짜 Docker의 #849 펜스 시험은 별도 단계에서 실행한다. 실제 Docker의 API 교체·복구와 워커 유지 증거는 [#842 드릴](../../../../scripts/tests/compose-api-isolation.py)에 있다. 실제 운영 Jenkins 권한, 동일 Docker daemon·공유 펜스, Compose split 전환·복귀는 이 리허설 범위 밖이다.
