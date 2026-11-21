# trading-desk Vault AppRole 회전 (#691)

Jenkins 폴더 `trading-desk`의 `trading-desk-vault-secret-id`를 교체하는 절차다. 백엔드와 웹 배포 잡은 `jenkins-trading-desk` AppRole을 공유한다. 기존 역할의 `trading-desk-vault-role-id`는 계속 쓴다. 역할 자체를 다시 만들었거나 Role ID가 다르면 이번 회전을 중단하고 역할 설정을 확인한다. [배포 설정](deploy.md#기존-자동-배포-잡-설정) · [Infra Vault 설정](https://github.com/8llow8llowMe/Infra/blob/main/vault/README.md#trading-desk-secret-path)

## 교체 순서

1. 기존 Secret ID에 대응하는 accessor를 이전 회전 기록이나 해당 ID의 권한 있는 Vault lookup으로 확인한다. Lookup 입력이 필요하면 Secret ID를 표준입력처럼 보호된 경로로 전달해 명령 인자와 셸 기록에 남기지 않는다. 다른 운영자와 같은 역할의 발급·폐기를 겹치지 않게 조율하고, 복구에 쓸 기존 값을 승인된 비밀 저장소에 보관한다. 목록에 나오는 순서로 어느 accessor가 현재 Jenkins의 것인지 판단할 수 없다.
2. 관리자가 승인된 비기록 터미널에서 Infra 공용 `vault/scripts/rotate-approle-secret.sh`를 명시적으로 `jenkins-trading-desk` 역할에 실행한다. 스크립트의 인자 생략 기본값은 다른 프로젝트다. AppRole mount는 기존 Jenkins 설정과 같은 `approle`을 쓴다. 발급 스크립트는 새 Secret ID를 화면에 출력하므로 Jenkins 콘솔, 스크린 녹화, 로그에 연결하지 않는다. Vault 관리 토큰은 명령 인자에 넣지 않고 사용 직후 작업 환경에서 제거한다. [공용 스크립트](https://github.com/8llow8llowMe/Infra/blob/main/vault/scripts/rotate-approle-secret.sh)

   ai-host의 보호된 관리 셸에 `VAULT_TOKEN`을 이미 환경으로 넣은 뒤 실행하는 예시다.

   ```bash
   docker exec -it -e VAULT_TOKEN -e AUTH_PATH=approle vault \
     sh /vault/scripts/rotate-approle-secret.sh jenkins-trading-desk
   unset VAULT_TOKEN
   ```

3. 출력의 `role_id`가 기존 역할과 일치하는지 확인하고 `new_secret_id`만 같은 ID의 폴더 범위 Secret text에 넣는다. 새 Secret ID의 accessor도 별도로 확인해 안전한 관리 기록에 남긴다. 새 ID를 조회해 확인하거나, 단독 발급 전후 Vault accessor 목록에서 새로 생긴 항목이 정확히 하나일 때 그 차이를 쓴다. 새·구 accessor가 모호하면 폐기를 보류한다.
4. 새 폴더 자격으로 AppRole 로그인과 `kv/trading-desk/backend/prod/env`, `kv/trading-desk/frontend/prod/env` 두 읽기가 모두 성공하는지 확인한다. 배포 없이 확인할 수 있는 신뢰된 절차에서 값 없이 성공 여부만 남기고 토큰을 폐기한다. 기존 운영 Pipeline의 Vault 다음 단계는 컨테이너 교체다. 읽기 전용 Jenkins 확인 경로가 없다면 다음 승인된 정상 배포에서 두 소비 경로의 성공을 확인할 때까지 구 ID를 유지한다. [공통 배포 코드](../Jenkinsfile.deploy-common.groovy) · [Vault 읽기 스크립트](../scripts/jenkins/vault-env.sh)
5. 새 자격 검증이 끝나고 구 자격을 사용하는 잡이 끝났는지 확인한 뒤, 앞에서 확인한 **구 accessor만** `auth/approle/role/jenkins-trading-desk/secret-id-accessor/destroy`로 폐기한다. 이 역할의 Secret ID는 자동 만료되지 않는다. 구 ID 폐기와 이미 발급된 토큰의 취소는 별개다. [Vault accessor 폐기](https://developer.hashicorp.com/vault/api-docs/auth/approle#destroy-approle-secret-id-accessor)

## 실패와 복구

| 상황 | 조치 |
| --- | --- |
| 발급 실패 또는 발급 결과 유실 | Jenkins 값은 그대로 둔다. 발급 전후 목록과 변경 기록으로 새 accessor의 생성 여부를 확인한다. 새 발급분을 확실히 식별할 수 있을 때만 정리한다. |
| 교체 뒤 새 로그인 실패 | 자격의 폴더 범위, Role ID, AppRole mount부터 확인한다. 구 ID가 여전히 유효하고 복구에 사용할 수 있으면 기존 폴더 자격 값을 복원한다. 노출된 ID는 복원에 사용하지 않는다. |
| 로그인 성공 후 KV 읽기 실패 | Vault 경로와 정책을 확인하고 두 소비 경로가 모두 성공할 때까지 구 ID를 폐기하지 않는다. 배포는 Vault 단계에서 컨테이너 교체 전에 중단된다. |
| 구 ID 폐기 뒤 새 자격 사용 불가 | 같은 역할에서 새 Secret ID를 발급해 같은 폴더 자격을 교체하고 두 경로를 다시 확인한다. 이미 폐기된 ID는 복구에 사용할 수 없다. |

## 로컬 모의 검증

`bash scripts/tests/jenkins-scripts.test.sh`는 가짜 `curl`로 AppRole 로그인, 교체한 자격으로 백엔드·웹 env 생성, 구 자격으로 로그인 실패 시 KV 읽기 전 중단, 읽기 실패 시 `revoke-self` 요청을 검사한다. jq 설치가 필요하다. 공용 발급 스크립트는 Vault CLI 대역으로 명시 role의 요청 경로와 발급 실패를 별도로 검증한다. 실제 Jenkins 자격 교체, Vault 토큰 폐기 성공, 운영 컨테이너 기동 확인은 운영 검증으로 남는다.
