## 📝 작업 내용

[작업 내용을 2~4문장으로 요약]

### 주요 변경 사항

1. [주요 변경 1]
2. [주요 변경 2]

## 타입

- [ ] feat: 새로운 기능 추가
- [ ] fix: 버그 수정
- [ ] chore: 빌드 업무 수정, 패키지 매니저 수정
- [ ] refactor: 코드 리펙토링
- [ ] style: 코드 포맷팅, 코드 변경이 없는 경우
- [ ] docs: 문서 수정
- [ ] test: 테스트 코드 추가

## PR 하기 전에 확인해주세요

- [ ] `ruff check` · `pytest` · `npm run build` 를 돌렸나요?
- [ ] 인증 · 세션 · 브로커 키를 건드렸다면 `security-reviewer` 검토를 받았나요?
- [ ] DB 스키마를 바꿨다면 Alembic 리비전을 추가하고 아래 `DB 마이그레이션` 절을 채웠나요?
- [ ] 계획 규칙(`desk/backtest.py` 가 import 하는 모듈, 가격 · 수급 · 뉴스 자료 모듈은 빼고 — `backtest_reports.DATA_MODULES`)을 바꿨다면 백테스트 스냅샷(`desk/data/backtest/*.json`)을 다시 만들었나요? 안 만들면 백테스트 화면에 "오래된 스냅샷"이 뜹니다(`desk/backtest_reports.py` 머리말의 명령).
- [ ] 토스 키 · 계좌 정보 · 비밀번호 · 세션 토큰 · 서버 `.env` 값이 커밋에 섞이지 않았나요?
- [ ] 새 환경 변수가 생겼다면 `.env.example` 에 이름만 추가하고 참고 사항에 `서버 .env 추가 필요: KEY` 를 적었나요?

## 검증 내역

- [실제로 실행한 명령과 결과. 미실행 · 실패는 숨기지 않고 적습니다]

## DB 마이그레이션 (스키마를 바꾼 경우만)

- 리비전: `backend/desk/migrations/versions/<rev>_*.py`
- 운영 적용: 컨테이너 기동 시 `alembic upgrade head` 가 자동 적용 / 수동 조치 필요 여부

## 참고 사항

- [API 계약 변경, 배포 시 할 일, 남은 위험]

## 연관된 이슈

Issue Number: #
