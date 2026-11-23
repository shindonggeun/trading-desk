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

감독 세션이 등록된 뒤 실행한다. `peers`의 JSON 목록은 현재 공용 서버에서 로드된 같은 저장소의 독립 사용자 thread를 보여준다. `id`는 바인딩에 사용하는 수신 thread UUID, `source`는 서버가 기록한 출처(예: `cli`, `vscode`), `status`는 서버의 실행 상태다. 서버가 생략한 출처나 상태는 `null`로 표시한다. 목록을 읽은 뒤 확인한 상태가 `notLoaded`라면 후보에서 제외한다. 수신 창은 사용자가 직접 대조한다.

`effectivePolicy`는 항상 `확인 불가`로 표시한다. 이 조회에는 대상 turn의 유효 승인·샌드박스 정책 필드가 없다. CLI 실행 인자, 디스크 설정이나 발신자의 권한으로 수신 정책을 추정하지 않는다. 대상이 없으면 빈 JSON 목록을 반환하고, 연결 실패나 조회 API 미지원은 종료 코드 1로 알린다. 두 경우 모두 표준 오류에 기존 `inbox <역할>` 확인 경로를 표시한다. `bind`와 `send --live`가 성공하면 같은 형식의 수신 정보를 `수신 thread:` 줄에 표시한다. 발송 정보는 RPC 전송 직전에 다시 확인한 수신 정보를 기준으로 한다.

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

- `codex_team.py launch`는 단독 터미널에서 `codex --no-daemon`을 실행한다. 이 세션은 공용 서버 경로의 대상이 아닐 수 있다. 현재 열린 창 통신에는 공용 서버 모드를 우선한다.
- `peers`가 0개거나 proxy 연결/프로토콜이 실패하면 새 서버를 띄우지 않는다. 설치된 CLI·공용 서버 버전과 연결 상태를 확인한다.
- 로컬 Python의 AF_UNIX 지원에 의존하지 않고 설치된 Codex exe의 `app-server proxy`를 사용한다. proxy만 종료하며 공용 서버와 작업자 프로세스를 종료하지 않는다.
- SQLite 메시지 원문과 ack는 기존 팀 도구가 관리한다. MCP 서버·자동 시작 hook·다른 프로그램 설치는 이 변경 범위가 아니다.
- 전송 중 proxy 호출자가 강제 종료되어 `dispatching`이 남으면 해당 역할 인수·재실행은 보류된다. 복구는 관리자가 (1) 원 호출자/proxy 종료 확인, (2) 외부 팀 SQLite 백업, (3) 수신 inbox·ack·서버 turn 접수 결과 대조, (4) 해당 메시지의 transport만 `dispatching`에서 `unknown`으로 변경, (5) 기존 메시지를 inbox로 처리하는 순서로 한다. 원문·소유자·ack를 지우거나 메시지를 자동 재전송하지 않는다. 이는 프로젝트 소유 팀 DB의 수동 복구이며 Codex 내부 DB를 수정하지 않는다. 현재 전용 복구 CLI는 제공하지 않는다.
- 현재 창이 정확히 어느 thread인지 모호하면 자동 선택하지 않는다. 표시 이름·프로젝트·사용자의 명시적 선택을 대조한다.
- 바인딩·idle 전달은 메타데이터만 읽는다. 실행 중 turn은 `thread/turns/list`에서 최신 1개를 `itemsView: notLoaded`로 조회하여 전체 대화를 읽지 않는다. 이 API를 지원하지 않는 구버전 서버에서는 실패 처리하고 inbox로 확인한다. 응답 전체는 4MiB 제한이며 제한 없는 대화 조회나 재시작으로 우회하지 않는다.

## Windows 전달 검증

`peers`·`bind`·`send --live`가 보여 주는 정보의 출처를 구분한다.

| 확인하려는 항목 | 기록할 근거 | 확인 불가인 경우 |
|---|---|---|
| 공용 서버의 수신 후보 | `peers`의 UUID·`source`·`status`·`cwd` | 후보가 없으면 해당 공용 서버를 통한 전달을 검증할 수 없다. 조회 실패나 API 미지원과 빈 목록을 구분한다. |
| 사용자가 보는 Windows 창 | 선택한 IDE/CLI 창의 실제 thread UUID와 후보 UUID 대조 및 해당 창에서 배정이 보였는지 확인 | 창과 UUID를 대조할 수 없으면 전송을 보류한다. `source`는 서버가 기록한 출처이며 같은 이름·작업 경로의 창은 UUID로 구분한다. |
| 시작 설정과 권한 출처 | 사용자가 확인한 시작 옵션·IDE 설정, 수신 창이 직접 표시한 정책과 그 출처 | `effectivePolicy`는 `확인 불가`로 기록한다. 발신 설정, CLI 인자 또는 `source`만으로 수신 정책을 판정할 수 없다. |
| 실제 승인과 실행 | 수신 창에 나타난 승인 요청·차단·완료와 대상 파일의 결과 | 승인 요청 없이 파일이 만들어졌다면 해당 파일 작업에서 승인 요청을 관측하지 않았다고 기록한다. 다른 경로·명령에 적용되는 권한은 확인할 수 없다. |

공용 서버 조회에는 수신 turn의 유효 승인·샌드박스 정책을 알려 주는 응답 필드가 없다. App Server 프로토콜의 `turn/start` 요청에 정책 필드가 있는 버전에서도 이 저장소 도구는 해당 필드를 보내지 않는다. 설치 버전의 응답 구조는 서버를 시작하지 않고 `codex --version`과 `codex app-server generate-json-schema --out <새-임시-폴더>`로 확인할 수 있다. 생성된 `ThreadReadResponse.json`, `TurnStartResponse.json`, `TurnSteerResponse.json`은 설치 CLI의 스키마이며, 연결된 공용 서버 버전이나 수신 창의 정책을 확인하는 자료는 아니다.

### 승인된 테스트 창에서 재현

1. 사용자가 지정한 폐기용 IDE·CLI 수신 창과 감독 테스트 세션을 사용한다. 모두 같은 Git 저장소의 테스트 작업 경로를 열고, 각 수신자가 실행해도 되는 전용 파일 경로를 미리 정한다. 테스트 중 기존 실행 옵션과 승인·샌드박스 정책을 그대로 사용한다.
2. 기존 팀 DB와 분리된 새 절대 경로를 정한다. 모든 참여 세션에서 `codex_team.py`를 호출할 때마다 같은 `CODEX_TEAM_STATE_DIR`을 해당 명령의 환경에 지정한다. 이 변수로 격리되는 것은 팀 SQLite DB다. `peers`는 공용 App Server에 로드된 스레드의 메타데이터를 조회하므로 해당 서버와 메타데이터 조회 범위가 승인된 경우에 실행한다. 공유 메타데이터 조회가 승인 범위에 포함되지 않으면 실전달을 **미검증**으로 남긴다. 아래는 테스트 감독 세션의 PowerShell 예시다. 수신 창의 `inbox`와 `ack`에도 같은 경로를 지정한다. 참여 세션에서 `CODEX_THREAD_ID`를 자동으로 제공하지 않으면 그 창의 시험은 보류한다.

   ```powershell
   $env:CODEX_TEAM_STATE_DIR = '<새-테스트-상태-디렉터리의-절대-경로>'
   python -B -X utf8 scripts/codex_team.py join supervisor
   python -B -X utf8 scripts/codex_team.py peers
   ```

3. 수신 Codex가 실행한 `Write-Output $env:CODEX_THREAD_ID`의 결과처럼 해당 창에서 직접 확인한 UUID를 후보 UUID와 대조한다. 감독이 `bind S1 --thread <IDE-UUID>`, `bind S2 --thread <CLI-UUID>` 중 대조된 대상만 실행한다. 공용 서버 목록에 없는 창에서는 공용 서버 전달 결과를 **미검증**으로 남긴다. `launch`가 사용하는 `codex --no-daemon`의 [전경 실행](codex-team.md#작업자-터미널-연결)은 별도 연결 방식이다.
4. 각 수신자의 유휴 상태가 확인되면 감독에서 아래처럼 한 건을 보낸다. UTF-8 배정 파일에 수신 역할, 사전 합의한 새 파일의 절대 경로와 시험 식별자를 적는다. 수신자에게 모든 `inbox`·`ack` 명령에서 시험용 `CODEX_TEAM_STATE_DIR` 경로를 사용하고 수신 확인은 `ack`로 남기도록 명시한다. 수신 작업은 기존 파일을 덮어쓰지 않는 `New-Item -ItemType File -Path '<합의한-파일>' -Value '<시험-식별자>' -ErrorAction Stop` 정도로 한정한다.

   ```powershell
   python -B -X utf8 scripts/codex_team.py send S1 --from-role supervisor --live --task-key 734/windows-ide-idle --message-file '<시험-배정-파일>'
   ```

5. 수신자는 같은 테스트 상태 경로의 `inbox S1`에서 출력된 메시지 ID를 대조하고, `ack S1 <ID> received`, `ack S1 <ID> started`를 순서대로 기록한다. 사전 합의한 파일 작업에서 수신 창에 표시된 승인·차단과 파일 결과를 기록하고, 결과를 확인한 뒤 `ack S1 <ID> completed`를 남긴다. `live_accepted`는 공용 서버 수락 상태이며 수신 창 표시와 수신자 ack는 각각 확인한다.
6. 실행 중 시험은 해당 창에서 무해한 짧은 읽기 작업이나 대기를 시작해 `peers`의 `active`와 실제 창의 실행 상태를 대조한 뒤 한 번 전송한다. IDE/CLI 및 유휴/실행 중 조합마다 다른 작업 키와 파일명을 사용한다. 같은 조합에서는 저장된 메시지 ID를 기준으로 따라가며 다시 보내지 않는다. 성공 출력의 `수신 thread` 상태와 `공용 서버 수락`의 turn ID를 기록하고, 실제 창에서 기존 turn에 입력이 보였는지 확인한다. 전송 전에 turn이 끝나 유휴 경로로 전송됐다면 실행 중 전달 결과는 미검증으로 기록한다.

배정 저장 뒤 대상 확인이나 proxy 연결에 실패하면 `pending`이 남을 수 있고, 실행 중 조회 API가 미지원이거나 공용 서버 수락 응답을 확인할 수 없으면 `unknown`이 남을 수 있다. 결과와 실패 원인을 기록하고 기존 메시지 ID로 수신 창의 inbox를 확인한다. 다른 창을 선택하거나 메시지를 다시 보내거나 기존 세션을 재시작해 확인을 대체하지 않는다.

실행 기록에는 각 조합의 CLI/IDE 버전과 시작 설정의 출처, 후보 UUID 대조 여부, 전송 전 상태, 메시지 ID, 선택한 창 표시, 승인·파일 결과, 최종 ack를 남긴다. 정책 출처나 화면 증거가 없는 조합은 **미검증**으로 기록한다.

### 자동 회귀의 범위

`scripts`에서 `python -B -X utf8 -m unittest test_codex_live test_codex_team -q`를 실행한다. 가짜 App Server와 격리 DB를 사용해 같은 이름·경로의 CLI와 IDE 중 바인딩한 UUID로 전달하는지, 유휴 `turn/start`·실행 중 `turn/steer` 요청, 미지원 API의 원인과 inbox 상태, 수신 ack 및 중복 차단을 확인한다. Windows에서 실행해도 실제 IDE·CLI 창의 표시, 유효 정책, 승인 요청, 수신 작업의 파일 쓰기는 수동 시험에서 확인한다.
