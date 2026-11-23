# Compose 백엔드 split 전환·복귀 리허설

[`Compose split roundtrip rehearsal`](../.github/workflows/compose-split-roundtrip.yml)은 GitHub-hosted Linux의 Docker에서 실제 백엔드 이미지와 [운영 Compose 정의](../backend/docker-compose-backend.yml)를 사용한다. 기본 Compose에 시험용 오버레이를 추가해 이미지 태그, 컨테이너와 볼륨 이름, 내부 네트워크, 전용 PostgreSQL을 격리한다. 운영 Compose에 정의된 역할, split 프로필, 워커의 API health 의존성, 앱 기동 명령은 원본에서 받는다.

| 순서 | 검증 |
|---|---|
| all 기준선 | 빈 전용 DB에서 두 워커가 기동을 거부하고 스키마를 쓰지 않는지 확인한다. 이후 `all`이 스키마를 준비하고 합성 분석을 처리하며 워커 컨테이너가 없는지 확인한다. |
| split 전환 | API가 healthy이고 DB가 Alembic head에 도달한 뒤 collector·analyst를 시작한다. 각 컨테이너의 역할을 확인한다. |
| 분석과 SSE | analyst 기동 전 API에서 대기 이벤트를 받은 뒤 analyst를 띄운다. API의 열린 SSE에서 실행·계획·종료를 받고 DB에 저장된 이벤트와 대조한다. |
| API 단독 교체 | 같은 앱 이미지로 API 컨테이너만 다시 만들고 두 워커의 ID·이미지·시작 시각·재시작 횟수를 비교한다. 새 API에서 `Last-Event-ID` 이후 이벤트를 재생한다. |
| all 복귀 | 워커를 제거한 뒤 API 자리의 백엔드를 `all`로 기동한다. 기존 DB의 분석 결과가 남아 있고 새로운 합성 분석도 처리한다. |

테스트는 합성 브로커와 대본 분석을 사용한다. 런타임의 두 네트워크는 내부 전용이며 호스트에 서비스 포트를 게시하지 않는다. PostgreSQL과 TradingAgents 홈은 이 프로젝트의 전용 볼륨이다. [Compose 병합](https://docs.docker.com/compose/how-tos/multiple-compose-files/merge/) · [내부 네트워크](https://docs.docker.com/reference/compose-file/networks/#internal)

`python3 -B scripts/tests/compose-split-roundtrip.py`는 Compose 렌더링과 격리 설정만 검사한다. 전체 실행의 `--run`은 폐기 가능한 호스티드 래너에서 사용한다. 전환 순서는 리허설 드라이버가 전용 프로젝트에만 적용한다.

[#840](https://github.com/shindonggeun/trading-desk/pull/840)은 별도 프로세스와 실제 PostgreSQL로 스키마·분석 큐·SSE를 검증했고, [#842](https://github.com/shindonggeun/trading-desk/pull/842)는 합성 HTTP 컨테이너로 API 교체와 워커 유지를 검증했다. [#849](https://github.com/shindonggeun/trading-desk/pull/849), [#855](https://github.com/shindonggeun/trading-desk/pull/855), [#877](https://github.com/shindonggeun/trading-desk/pull/877)의 Jenkins 인계·권한·잠금·취소 시험은 별도다.

운영 전환은 [#776](https://github.com/shindonggeun/trading-desk/issues/776)에서 추적한다. 운영 Jenkins·Vault의 구성, 운영 네트워크와 DB, 실제 브로커 발급자 수, 운영에서 API만 바꿨을 때 워커가 유지되는지는 별도 확인 항목이다.
