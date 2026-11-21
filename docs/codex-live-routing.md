# 열린 독립 세션으로 전달하기

## 조사 근거와 선택

Claude Code 공식 [Agent teams](https://code.claude.com/docs/en/agent-teams)는 팀 리더·별도 teammate·공유 task list·mailbox를 설명한다. 팀/에이전트 이름으로 mailbox를 찾으며, 문서에 제시된 경로는 `~/.claude/teams/{team-name}/inboxes/{agent-name}.json`이다. 이 팀 구조를 임의의 과거 터미널에 자동 부착하는 기능과 동일하게 보지 않는다. 핵심은 MCP 유무보다 등록된 신원·영속 메시지·실행기에 연결된 수신 경로다.

검토한 공개 구현:

| 자료 | 확인한 구조 | 이번 선택 |
|---|---|---|
| [agent-intercom](https://github.com/iambashar/agent-intercom), [live-push](https://github.com/iambashar/agent-intercom/blob/main/docs/live-push.md) | MCP mailbox와 열린 Codex 공용 App Server의 loaded thread 조회, idle start/active steer | 가장 가까운 전달 구조. 공개 구현의 Unix 소켓 경로를 그대로 쓰지 않고 설치된 CLI proxy로 Windows에 연결 |
| [oh-my-codex](https://github.com/Yeachan-Heo/oh-my-codex) | 작업자 신원·mailbox·task lifecycle과 tmux 기반 팀 실행 | 팀 런타임을 새로 운영할 때 후보. README는 native Windows를 보조 경로로, WSL2를 더 적합한 경로로 설명 |
| [MCP Agent Mail Rust](https://github.com/Dicklesworthstone/mcp_agent_mail_rust) | 에이전트 등록·메시지·파일 예약을 제공하는 MCP 서버 | 통신 저장소 후보. mailbox 도입만으로 현재 TUI의 수신 실행이 보장되지는 않음. 기존 Python판은 유지보수 대상 Rust판으로 대체됐다고 명시 |
| [tap 논문](https://arxiv.org/abs/2606.14445) | 파일 원문 보존 + 별도 실시간 알림, 워크트리 격리 | 영속 원문과 실시간 전달을 분리하는 설계 근거. 이 환경의 동작 증명이나 Claude 내부 구현 명세는 아님 |

OpenAI [App Server 문서](https://learn.chatgpt.com/docs/app-server)는 `thread/loaded/list`, `thread/read`, `turn/start`, `turn/steer`와 상태·이벤트를 정의한다. 프로젝트는 이를 사용하되, **로컬 control socket에 붙는 proxy/WebSocket 연결 방식은 버전 호환을 별도 검증**한다. 외부 패키지 설치·자동 MCP 권한 확대·공용 데몬 재시작은 하지 않는다. 코드 복제가 아니라 공개 동작과 프로토콜을 대조하여 구현한다.

## 사용법

감독 세션이 등록된 뒤 실행한다. `peers`의 JSON 목록은 현재 공용 서버에서 로드된 같은 저장소의 독립 사용자 thread를 보여준다. `id`는 바인딩에 사용하는 수신 thread UUID, `source`는 서버가 기록한 출처(예: `cli`, `vscode`), `status`는 서버의 실행 상태다. 서버가 생략한 출처나 상태는 `null`로 표시한다. 수신 창은 사용자가 직접 대조한다.

`effectivePolicy`는 항상 `확인 불가`로 표시한다. 이 조회에는 대상 turn의 유효 승인·샌드박스 정책 필드가 없다. CLI 실행 인자, 디스크 설정이나 발신자의 권한으로 수신 정책을 추정하지 않는다. 대상이 없으면 빈 JSON 목록을 반환하고, 연결 실패나 조회 API 미지원은 종료 코드 1로 알린다. 두 경우 모두 표준 오류에 기존 `inbox <역할>` 확인 경로를 표시한다.

```powershell
python -B -X utf8 scripts/codex_team.py peers
python -B -X utf8 scripts/codex_team.py bind S1 --thread <정확한-UUID>
python -B -X utf8 scripts/codex_team.py bind S2 --thread <정확한-UUID>
python -B -X utf8 scripts/codex_team.py send S1 --from-role supervisor --live --message-file D:/handoff.txt
```

`bind`는 감독만 실행하며, 같은 Git 저장소의 로드된 thread를 명시적으로 연결한다. 이미 다른 역할에 등록됐거나 다른 작업자가 소유한 역할이면 오류를 낸다. 역할 소유자가 달라지는 인수에는 `--takeover`가 필요하다. 현재 역할과 inbox를 보존하므로 기존 작업을 새로 배정하지 않는다. 새 작업자 창에서 수동 등록하거나 전경 launcher로 재시작하는 대신 이 경로를 사용할 수 있다.

`--live`는 선택한 역할에 저장한 메시지를 공용 서버에 직접 전달한다. 미로드 대상은 자동 resume하지 않는다. idle은 `turn/start`, 실행 중인 대상은 현재 turn ID를 확인한 `turn/steer`를 사용한다. turn 시작 시 model/cwd/sandbox/approval 설정을 덮어쓰지 않는다. 응답을 잃거나 상태가 경합하면 메시지를 자동 재전송하지 않고 inbox에 남긴다. `live_accepted`도 수신 확인이 아니며 수신자가 기존 `ack received → started → completed`를 남겨야 한다.

작업자의 회신도 `send supervisor --from-role S1 --live`로 보낼 수 있다. 감독 세션이 이미 일하고 있다면 현재 turn에 추가된다. 단순 상태 조회는 inbox로 처리하고 상호 회신을 무한히 유발하지 않는다. 모든 메시지는 다른 에이전트의 입력으로 표시하며 승인·운영 권한을 부여하지 않는다.

## 연결 모드와 실패 경계

- `launch --no-daemon`은 단독 터미널 실행 모드다. 이 세션은 공용 서버 경로의 대상이 아닐 수 있다. 현재 열린 창 통신에는 공용 서버 모드를 우선한다.
- `peers`가 0개거나 proxy 연결/프로토콜이 실패하면 새 서버를 띄우지 않는다. 설치된 CLI·공용 서버 버전과 연결 상태를 확인한다.
- 로컬 Python의 AF_UNIX 지원에 의존하지 않고 설치된 Codex exe의 `app-server proxy`를 사용한다. proxy만 종료하며 공용 서버와 작업자 프로세스를 종료하지 않는다.
- SQLite 메시지 원문과 ack는 기존 팀 도구가 관리한다. MCP 서버·자동 시작 hook·다른 프로그램 설치는 이 변경 범위가 아니다.
- 전송 중 proxy 호출자가 강제 종료되어 `dispatching`이 남으면 해당 역할 인수·재실행은 보류된다. 복구는 관리자가 (1) 원 호출자/proxy 종료 확인, (2) 외부 팀 SQLite 백업, (3) 수신 inbox·ack·서버 turn 접수 결과 대조, (4) 해당 메시지의 transport만 `dispatching`에서 `unknown`으로 변경, (5) 기존 메시지를 inbox로 처리하는 순서로 한다. 원문·소유자·ack를 지우거나 메시지를 자동 재전송하지 않는다. 이는 프로젝트 소유 팀 DB의 수동 복구이며 Codex 내부 DB를 수정하지 않는다. 현재 전용 복구 CLI는 제공하지 않는다.
- 현재 창이 정확히 어느 thread인지 모호하면 자동 선택하지 않는다. 표시 이름·프로젝트·사용자의 명시적 선택을 대조한다.
- 바인딩·idle 전달은 메타데이터만 읽는다. 실행 중 turn은 `thread/turns/list`에서 최신 1개를 `itemsView: notLoaded`로 조회하여 전체 대화를 읽지 않는다. 이 API를 지원하지 않는 구버전 서버에서는 실패 처리하고 inbox로 확인한다. 응답 전체는 4MiB 제한이며 제한 없는 대화 조회나 재시작으로 우회하지 않는다.

## Windows 전달 검증

`peers`의 `source`는 App Server가 기록한 출처다. `vscode`와 `cli`가 표시되더라도 실제로 열려 있는 창과 수신 turn의 유효 승인·샌드박스 정책은 각 수신 창에서 확인한다. 사용자가 선택한 창의 UUID와 대조할 수 없거나 표시와 기대가 다르면 배정을 보류한다. 공용 서버 목록에 나타나지 않는 전경 창은 [단독 실행 절차](codex-team.md#작업자-터미널-연결)의 연결 상태를 확인한다.

실패하면 출력된 메시지 ID를 기준으로 수신 창의 `inbox <역할>`에서 원문을 찾는다. 수신 대상 확인이나 proxy 연결이 배정 저장 뒤에 실패하면 전송 상태가 `pending`으로 남을 수 있다. 실행 중 turn을 찾는 API가 지원되지 않거나 전달 응답을 잃으면 `unknown`으로 남을 수 있다. 수신자는 찾은 기존 원문에 대해 `ack`를 남긴다.

`scripts`에서 `python -B -X utf8 -m unittest test_codex_live test_codex_team -q`로 가짜 App Server 프레임을 사용하는 회귀를 실행할 수 있다. 이 검사는 유휴 `turn/start`, 실행 중 `turn/steer`, 미지원 API의 원인과 inbox 상태, 수신 ack 및 중복 차단을 확인한다. 실제 창에서의 파일 쓰기와 승인 요청 비교에는 사용자가 지정한 Windows IDE·CLI 테스트 창이 필요하다.
