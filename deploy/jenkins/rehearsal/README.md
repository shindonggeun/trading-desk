# #776/#785 로컬 Jenkins 리허설 템플릿

[비운영 검증 절차](../README.md#비운영-jenkins-어댑터-검증-절차)를 WSL의 로컬 Docker에서 재현하기 위한 Compose 템플릿이다. 현재 검증 범위는 Compose 해석과 정적 가드다. 실제 Jenkins 컨트롤러의 취소·잠금 순서 및 운영 split 전환은 미실측이다. [운영 전환 절차](../../../docs/deploy.md#12-백엔드-나누기--api--collector--analyst-688)

## 격리 경계

| 항목 | 설정 |
| --- | --- |
| 실행 범위 | 프로젝트 `td-jenkins-rehearsal`, 별도 Compose 파일, 모든 서비스에 `rehearsal` 프로필. 기본 선택에는 서비스가 없다. |
| 호스트 포트 | 컨트롤러 UI만 `127.0.0.1:18080`에서 연결한다. 50000이나 Docker 데몬 포트는 게시하지 않는다. |
| 내부망 | `control`에는 controller·agent·scm, `scratch`에는 agent·scratch-docker만 연결한다. agent는 WebSocket으로 controller에, TLS로 scratch-docker의 2376에 연결한다. |
| 볼륨 | Jenkins Home, agent 작업공간, agent 연결 비밀, DinD 데이터, CA·서버 인증서, 클라이언트 인증서, SCM을 프로젝트 전용 볼륨에 둔다. agent는 연결 비밀과 클라이언트 인증서를 읽기 전용으로 받는다. |
| 이미지 | 사전에 승인한 로컬 이미지 ID만 지정한다. `pull_policy: never`이고 Compose build 정의는 없다. 예시 ID는 검증 전용이다. |

DinD에는 **`privileged`가 필요하며 이는 WSL 호스트에 대한 강한 권한**이다. 이 템플릿에서 기본값은 `false`다. 이후 승인된 실행 구성에서만 scratch-docker에 명시적으로 허용한다. 내부망은 네트워크 경계다. privileged가 부여한 호스트 권한까지 격리한다고 판단하지 않는다. [Jenkins Docker 가이드](https://www.jenkins.io/doc/book/installing/docker/)

## 데몬 연결 없이 검증

저장소 루트의 WSL 셸에서 실행한다. 예시 env에는 가짜 이미지 ID만 있다. 존재하지 않는 소켓을 지정해도 `config`는 해석할 수 있다.

```bash
set -o pipefail
dir=deploy/jenkins/rehearsal
docker --host unix:///tmp/td-jenkins-config-only.sock compose \
  -p td-jenkins-rehearsal --env-file "$dir/rehearsal.env.example" -f "$dir/compose.rehearsal.yml" \
  --profile rehearsal config --format json |
  python3 scripts/jenkins/check_rehearsal_config.py
python3 -B scripts/tests/test_rehearsal_compose.py
```

프로필을 빼고 `config --services`를 실행하면 서비스 목록이 비어야 한다. 테스트는 기본 구성과 opt-in 구성을 해석하고, 포트·내부망·볼륨·호스트 소켓·pull·외부 시크릿이 바뀐 해석 결과를 거부하는지 확인한다. 승인된 설정에 한해 `TD_REHEARSAL_PRIVILEGED=true`로 해석하고 가드에는 `--allow-privileged`를 넘긴다. 가드는 해석된 구성만 확인하며 Docker를 호출하거나 실행을 승인하지 않는다.

## 실행을 별도로 승인받은 뒤 준비할 입력

1. 컨트롤러에는 Jenkins LTS와 [플러그인 목록](plugins.txt)의 검토된 버전 및 의존성을 넣은 이미지가 필요하다. agent는 비밀 파일 인수(`@경로`)를 지원하는 Jenkins inbound-agent와 Git·Docker CLI를 제공해야 한다. scratch-docker는 TLS를 쓰는 DinD, scm은 Python 3 이미지를 사용한다. 이미지의 출처·digest·로컬 ID, 플러그인 버전과 의존성을 기록하고 실제 이미지 ID를 저장소 밖 전용 env 파일에 넣는다. [Jenkins Remoting 인바운드 에이전트](https://github.com/jenkinsci/remoting/blob/master/docs/inbound-agent.md#the--argument-annotation)
2. 승인된 SHA를 `develop`로 노출하는 로컬 bare Git 사본을 만든다. 사본에서 `git update-server-info`를 실행하고 `scm-data` 볼륨에 `trading-desk.git`로 넣는다. SCM 주소는 `http://scm:8081/trading-desk.git`다. 운영 자격 증명, 운영 데이터와 호스트 Docker 소켓은 준비 입력에 포함하지 않는다.
3. 컨트롤러와 scm을 먼저 준비하고 [#818의 네 잡과 권한](../README.md#비운영-설정)을 구성한다. 내장 노드 executor를 0으로 두고 `trading-desk-rehearsal` 노드와 같은 이름의 라벨에 executor 1개를 둔다. 새 컨트롤러가 발급한 에이전트 연결 비밀은 `agent-auth` 볼륨의 `secret` 파일에만 저장하고 UID 1000만 읽을 수 있게 한다. 값은 명령 인자·env 파일·Compose 출력에 넣지 않는다.
4. 나머지 서비스에 앞서 승인된 로컬 Docker 소켓과 전용 프로젝트를 다시 확인한다. 전용 이미지 ID가 로컬에 있고, 두 망과 모든 볼륨이 이 프로젝트에 속하고, UI가 loopback에서만 접근되는지 확인한다. Docker Engine 28.0.0 이전 버전은 같은 L2 망에서 loopback 게시 포트에 접근할 수 있어 포트 설정만으로 격리를 판정할 수 없다. 이름이 기존 리소스와 겹치거나 격리·가드 확인이 실패하면 실행을 보류한다. [Docker 포트 게시](https://docs.docker.com/engine/network/port-publishing/)

## 증거 보존과 정리

- [#818의 검증 표](../README.md#남겨야-할-증거)에 따라 정상·복구·큐 대기 취소·실행 중 취소를 기록한다. 부모 요청과 자식 종료 또는 큐 제거, 잠금 해제와 경쟁 부모 진입은 별도 사건이다. 실제 시각·빌드/큐 ID와 이미지·플러그인 목록의 필요한 부분만 저장소 밖에 보존한다.
- 종료 전에 자식 및 경쟁 잡의 종료를 확인한다. Compose 파일·env·프로필·프로젝트가 동일한지 확인하고, 컨테이너와 망의 `com.docker.compose.project`가 `td-jenkins-rehearsal`인지 대조한 뒤 해당 프로젝트에만 `down`을 적용한다. 볼륨은 증거 보존 후 별도 확인을 받고 전용 볼륨만 제거한다.
- 재실측은 같은 버전의 이미지와 플러그인으로 전용 볼륨을 재연결하거나, 빈 볼륨과 승인 SHA로 초기화한다. 다시 만든 컨트롤러의 agent 연결 비밀은 새로 발급한다.
