# 독립 Codex 세션 팀

이 프로젝트는 `scripts/codex_team.py`로 새·기존 독립 세션에 `supervisor`, `S1`, `S2` 같은 역할을 붙인다. 새 채팅에서도 역할을 인수해 이전 작업·메시지를 읽을 수 있다. 하위 에이전트와는 별개다. Python 3와 설치된 Codex CLI가 필요하다.

**이미 열린 세션끼리 통신하려면 [공용 App Server 전달](codex-live-routing.md)을 우선 사용한다.** 감독의 `peers → bind → send --live`로 작업자에게 별도 수동 join이나 터미널 재시작을 요구하지 않고 연결할 수 있다. 아래 `launch --no-daemon`은 별도 단독 전경 실행 방식이며 공용 서버 수신과 구분한다.

## 새 감독 채팅에서 지시하기

> 이 프로젝트의 감독 역할을 인수해줘. `$codex-team`을 적용하고 기존 역할·인계·미완료 작업을 확인해. 이전 작업을 중복 배정하지 말고 전경 연결된 S1·S2와 협업해. 이슈 선정·등록은 `$issue`, 개발 분류·계획·최종 검증 판단은 `$dev-orchestrator`, 커밋·푸시·PR 생성은 `$pr` 절차에 따라 감독이 맡아. 작업자는 배정된 구현·대상 테스트·검토 결과를 감독에게 넘기고, 머지는 내가 할게.

스킬이 목록에 아직 보이지 않으면 `.agents/skills/codex-team/SKILL.md`를 읽으라고 덧붙인다. 감독 세션 안에서 다음을 실행한다. `CODEX_THREAD_ID`로 현재 채팅을 식별한다. 첫 등록에는 `--takeover`가 필요 없고, 기존 감독에서 새 감독으로 바꿀 때 명시적으로 붙인다.

```powershell
python -B -X utf8 scripts/codex_team.py join supervisor --takeover
python -B -X utf8 scripts/codex_team.py status
python -B -X utf8 scripts/codex_team.py inbox supervisor --all
```

같은 역할을 다른 세션에 조용히 덮어쓰지 않는다. 이전 감독은 인수 후 해당 역할로 보낼 수 없다. 이미 전경 실행 중인 역할은 터미널 종료 후 인수한다. 완료된 보고도 `--all`로 읽을 수 있다.

## 작업자 터미널 연결

빈 Codex 채팅 안에서는 다음처럼 지시한다.

> `$codex-team`으로 이 세션을 S1에 등록하고 기존 inbox·인계를 확인해. 아직 코드를 바꾸지 말고 감독에게 수신 확인을 보내. 감독이 이슈와 계획을 전달하면 배정된 구현·대상 테스트·검토를 한국어로 진행해. 커밋·푸시·PR 전에 변경 경로, 검증 결과, 필요한 DDL과 guard 결과를 감독에게 보고하고 대기해.

`join S1`은 현재 세션의 신원 등록이다. **등록만으로 화면에 연결됐다고 표시하지 않는다.** 전경 실행을 사용하려면 현재 Codex를 정상 종료한 뒤 그 터미널의 PowerShell에서 다음을 실행한다. 이미 등록된 역할이면 해당 세션을 재개하고, 미등록이면 새 세션을 만들어 시작 지시로 등록한다.

```powershell
python -B -X utf8 scripts/codex_team.py launch S1
python -B -X utf8 scripts/codex_team.py launch S2 --cwd ../trading-desk-wt/<번호>-<요약>
```

두 명령은 **서로 다른 사용자가 보는 터미널**에서 실행한다. `Start-Process -WindowStyle Hidden`, 비대화형 exec, native `send_message_to_thread`로 실행하는 대체 방식은 화면 연결 검증이 아니다. 새 역할로 작업자를 교체할 때는 새 세션에서 `join S1 --takeover` 후 종료하고 `launch S1`로 연다. `launch S1 --thread <UUID>`는 등록한 역할과 같은 과거 세션을 명시할 때 쓴다.

실행기는 stdin/stdout을 해당 터미널에 상속하고 `codex --no-daemon`으로 실행한다. 다른 세션의 공유 데몬을 중지하지 않는다. 종료 시 본인이 띄운 프로세스만 정리하며, Windows에서는 그 프로세스 트리를 종료한다. 비정상 종료로 남은 heartbeat는 30초 뒤 만료된다. heartbeat는 실행기 생존 증거이며 화면 픽셀이나 사용자의 열람 증거는 아니다. 프로세스 강제 종료 중 외부 부작용의 취소를 보장하지 않으므로 작업 중에는 먼저 작업자에게 안전 중단을 지시한다.

Windows npm 설치에서는 실제 `codex.exe`를 찾아 쉘 없이 실행한다. 다른 설치 구조에서는 실제 실행파일 절대경로를 `CODEX_TEAM_BIN`에 지정한다. 승인·sandbox·모델 설정은 기존 Codex 설정을 사용하며 실행기가 권한을 확대하지 않는다.

## 배정·보고·명시적 수신 확인

다음은 해당 역할로 등록된 **Codex 채팅 안에서** 실행한다. 긴 한국어 지시는 UTF-8 파일로 저장한다. `--task-key`는 동일 작업을 동시에 배정하지 않기 위한 안정된 식별자다. `#20`의 서로 다른 단계라면 `20/price-source`처럼 범위를 담는다. 작업자 한 명에게 미완료 배정은 한 건만 둔다.

```powershell
python -B -X utf8 scripts/codex_team.py send S1 --from-role supervisor --task-key 582/restoration --message-file D:/handoff.txt
python -B -X utf8 scripts/codex_team.py inbox S1
python -B -X utf8 scripts/codex_team.py ack S1 <메시지-ID> received
python -B -X utf8 scripts/codex_team.py ack S1 <메시지-ID> started
python -B -X utf8 scripts/codex_team.py send supervisor --from-role S1 --message '설계 대조 완료. 구현 전 분리안을 보고합니다.'
python -B -X utf8 scripts/codex_team.py ack S1 <메시지-ID> completed
```

`completed`는 작업자의 보고 상태이며 검증·머지·이슈 종료를 대신하지 않는다. 감독은 실제 근거를 대조하고 `$issue`·`$dev-orchestrator`·`$pr`에 따른 이슈·분류·PR 작업을 직접 처리한다. 미완 작업은 상태를 완료로 바꾸지 않고 역할 인수와 inbox로 이어간다. 작업자끼리도 같은 `send S2 --from-role S1` 형식으로 협의할 수 있다.

상태를 구분한다.

| 항목 | 의미 |
|---|---|
| `foreground: false` | 실행기 연결 없음. 메시지는 저장하지만 세션을 자동 재개하지 않음 |
| `transport: pending` | 외부 inbox에 저장됨. 전경 실행기가 전달할 때까지 대기 |
| `transport: queued` | Codex 큐 등록 성공. 화면 표시·수신·작업 시작은 아직 증명하지 않음 |
| `transport: dispatching/unknown` | 전달 도중 중단 또는 실패. 중복 방지를 위해 자동 재전송하지 않음 |
| `state: received/started/completed` | 해당 역할 소유 세션이 각각 수신·착수·완료를 명시적으로 기록 |

연결이 끊겨도 메시지와 작업은 남는다. `unknown`은 `inbox` 원문으로 처리하고 ack하면 된다. 새 작업을 다시 만들어 중복 실행하지 않는다. 작업자는 인계 직후와 작업 경계마다 inbox를 확인한다. 대기 중 Codex의 큐 소비·화면 갱신은 CLI 버전에 의존하므로 `queued`에서 멈췄다면 터미널에서 inbox 확인을 지시한다. 이 도구는 Codex TUI 자체를 패치하거나 임의의 빈 터미널을 찾아 붙이지 않는다.

## 상태·기존 감독 도구와의 관계

새 팀의 기본 위치는 Git common-dir의 부모인 메인 저장소 안 `.trading-desk-codex-team/team.sqlite`다. 클론 디렉터리 이름과 관계없이 모든 연결 워크트리가 이 경로를 공유하며, `.gitignore`가 SQLite 부속 파일까지 무시한다. `CODEX_TEAM_STATE_DIR`을 지정하면 해당 디렉터리의 `team.sqlite`가 우선한다. 같은 팀의 모든 세션과 실행기는 같은 절대 경로를 사용한다. 메시지에 비밀값(토스 키·계좌·비밀번호·세션 토큰·서버 `.env` 값)을 쓰지 않는다. SQLite 트랜잭션과 미완료 task key unique 인덱스로 동시 배정을 제어한다. 다른 로컬 사용자를 차단하는 인증 서버는 아니다.

### 저장소 밖 기존 DB 인계

옛 기본 위치는 메인 저장소 **옆** `.<클론-이름>-codex-team/team.sqlite`다. 옛 위치에만 파일이 있으면 새 실행기도 그 DB를 계속 사용한다. 두 위치에 파일이 있으면 새 실행기는 명시적 경로를 요구한다. 옛 실행기는 기본값으로 항상 옛 위치를 고른다. 새 위치에 먼저 DB를 만든 뒤 옛 실행기를 기본값으로 시작하면 옛 위치에 별도 DB가 생길 수 있다. 버전을 섞어 쓰는 팀은 실행 전부터 모든 실행기에 같은 `CODEX_TEAM_STATE_DIR`을 지정한다.

1. 기존 DB의 실제 위치를 확인하고 팀 명령과 전경 실행기를 모두 멈춘다. 복사 후 원본에 기록된 메시지는 새 DB에 반영되지 않는다.
2. 새 버전의 메인 체크아웃에서 `python -B -X utf8 scripts/codex_team.py migrate-state`를 실행한다. 기본 원본은 옛 위치, 기본 대상은 저장소 안 새 위치다. `--source <기존-team.sqlite-절대경로>`와 이 명령에만 지정하는 `CODEX_TEAM_STATE_DIR`로 각각 다른 원본과 대상을 지정할 수 있다. 대상은 Git에서 무시되는 경로로 지정한다.
3. 이 명령은 SQLite 백업 API로 WAL의 커밋된 내용까지 복사하고 무결성을 확인한다. 같은 디렉터리의 임시 파일을 하드링크로 게시하므로, 하드링크를 지원하는 파일시스템과 쓰기 권한이 필요하다. 대상이 이미 있거나 게시가 실패하면 원본과 기존 대상을 보존한다.
4. 모두 동일한 새 경로를 가리키도록 설정한 뒤 `status`와 `inbox <역할> --all`에서 기존 메시지 ID와 수신 상태를 대조한다. 재개 전 실패했다면 모든 실행기에 기존 경로를 다시 지정할 수 있다. 양쪽에 새 기록이 생겼다면 둘 다 보존하고 메시지 ID와 수신 상태를 먼저 대조한다.

`codex_session_mail.py` 는 로컬 Codex 세션 목록에 팀 역할(`supervisor` · `S1` …)을 붙여 보여 준다. 그 `send` 의 `--resume` 기반 경로는 백그라운드 재개일 수 있으므로 전경 모드 대체로 쓰지 않는다.

## Claude Code 세션과의 관계

이 저장소는 Claude Code 세션끼리도 감독 · 작업 세션으로 돌린다(Claude 의 [세션 간 메시지](https://code.claude.com/docs/en/cross-session-messaging)). 두 체계는 **직접 이어지지 않는다**.

- **Codex → Claude**: Claude 세션의 수신 파이프(`CLAUDE_CODE_MESSAGING_SOCKET`)는 native Windows 에서 첫 줄에 그 세션의 토큰(`CLAUDE_CODE_MESSAGING_TOKEN`)을 요구한다. 토큰은 그 세션의 자식 프로세스(훅 · Bash)에만 주어지므로 Codex 프로세스는 보낼 수 없다. 토큰을 꺼내 넘기는 것은 비밀값을 옮기는 일이라 하지 않는다.
- **Claude → Codex**: Claude 세션이 Bash 로 `codex_team.py` 를 돌릴 수는 있지만, `send` 는 보낸 역할의 `CODEX_THREAD_ID` 소유를 확인하므로 Codex 세션이 아닌 Claude 는 `supervisor` 로 보낼 수 없다. 신원 확인을 느슨하게 하는 우회는 넣지 않았다.
- 그래서 Claude 감독 ↔ Codex 작업 세션 사이는 **사용자가 잇는다**: Claude 감독이 저장소 밖 인계 파일을 쓰고, 사용자가 Codex 감독(또는 작업자)에게 그 파일로 `send --message-file` 을 시킨다. 보고는 반대로.
- 같은 이슈를 Claude 팀과 Codex 팀에 동시에 배정하지 않는다. 배정 목록의 정본은 감독 한 명이다.

## 직접 확인 절차와 기록

자동 테스트(`python -B -X utf8 -m unittest discover -s scripts -p "test_*.py"`, CI `codex-check`)는 Codex CLI 없이 도는 단위 테스트다. 실제 두 세션 사이의 왕복은 사용자가 아래 순서로 확인한다.

1. Codex 새 세션 A 에서 이 저장소를 열고: "`$codex-team` 으로 이 세션을 supervisor 로 등록하고 status 를 보여 줘" → `join supervisor` · `status`.
2. Codex 새 세션 B 에서: "`$codex-team` 으로 이 세션을 S1 에 등록하고 inbox 를 확인해. 코드는 바꾸지 마" → `join S1` · `inbox S1`.
3. A 에서: `send S1 --from-role supervisor --task-key smoke/1 --message "수신 확인만 회신해"`.
4. B 에서: `inbox S1` → `ack S1 <ID> received` → `send supervisor --from-role S1 --message "받았어요"` → `ack S1 <ID> completed`.
5. A 에서: `inbox supervisor`, `status` 로 B 의 회신과 `state: completed` 를 확인한다.
6. (열린 세션 전달) A 에서 `peers` → `bind S1 --thread <B 의 UUID>` → `send S1 --from-role supervisor --live --message "live 확인"` 이 B 화면에 나타나는지 본다.

| 날짜 | Codex CLI 버전 | 1~5 (큐 메시지 왕복) | 6 (--live) | 확인한 사람 · 메모 |
|------|----------------|----------------------|------------|--------------------|
| (미실시) | | | | |
