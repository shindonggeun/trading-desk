# 시장 Redis E2E 검증

백엔드 의존성이 설치된 `backend/.venv`, 프론트엔드 의존성이 설치된 `frontend/node_modules`, 로컬 Docker와 `google-chrome`이 있는 Linux에서 저장소 루트 기준으로 실행한다.

```sh
node frontend/tests/market-redis/run.mjs
```

- **지연 REST 회귀 (#866)**. 저장된 알림이 화면에 표시되면 SSE 연결을 열고, 패널이 처음 live로 전환하며 보낸 맞춤 REST 응답 한 건을 보류한다. 응답 본문에는 저장된 알림만 있어야 한다. 실제 Redis로 발행한 새 알림이 화면에 표시된 뒤 응답을 해제한다. 그 응답이 화면에 반영된 후에도 새 알림과 저장된 알림이 각각 한 건씩 남아야 한다.
- **Redis 단절·복구**. Redis 컨테이너를 중지하면 원격 채널은 REST 폴링으로 전환하고 로컬 시세는 SSE를 계속 사용해야 한다. 컨테이너를 다시 시작하면 같은 SSE 연결에서 원격 데이터가 다시 표시되고 반복 폴링이 멈춰야 한다.
