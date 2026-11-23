# Jenkins 웹 롤백 리허설

[`Isolated Jenkins web rollback rehearsal`](../../../../.github/workflows/jenkins-web-rollback-rehearsal.yml)는 GitHub-hosted runner에서 실제 Jenkins 웹 빌드를 실행한다. 테스트 Git 저장소에 현재 커밋의 `Jenkinsfile-web`, `Jenkinsfile.deploy-common.groovy`, `scripts/deploy-compose.sh` 원본을 넣고, 빌드의 `sh(returnStatus: true)`가 받은 종료 코드가 Jenkins 결과와 설명에 어떻게 반영되는지 확인한다.

| 사례 | Jenkins와 실행 상태에서 확인하는 항목 |
|---|---|
| 첫 정상 웹 배포 | 빌드가 `SUCCESS`이고 설명에 배포 SHA와 `배포됨`이 기록된다. 웹 이미지 ID와 해당 리비전의 HTTP 확인 결과를 기준선으로 확보한다. |
| 새 웹 이미지의 HTTP 확인 실패 | 새 이미지로 웹을 교체하고 HTTP 확인을 실패시킨 뒤 원래 이미지 ID로 복구한다. 복구한 이미지의 HTTP 확인은 성공하고 실패한 이미지는 SHA 태그로 남는다. Jenkins 빌드는 `FAILURE`이며 설명에 실패한 배포 SHA와 `되돌림`이 기록된다. |
| 빌드 종료 | 배포 작업공간의 `.deploy-secrets`와 `.deploy-src`가 제거된다. |

컨트롤러는 비루트 UID와 `--network none`으로 구동하며, 포트 게시와 호스트 마운트 없이 루프백에서만 접근한다. SCM은 fixture Git 저장소를 사용한다. CI와 Vault 스크립트의 입력은 합성이며, Docker 명령은 fixture가 기록하고 응답한다. 운영 비밀과 Docker 소켓은 컨트롤러에 전달하지 않는다.

[#834](https://github.com/shindonggeun/trading-desk/pull/834)는 배포 셸에서 웹 HTTP 실패, 이전 이미지 복구와 종료 코드 `2`를 확인했다. [#855](https://github.com/shindonggeun/trading-desk/pull/855)와 [#877](https://github.com/shindonggeun/trading-desk/pull/877)의 백엔드 역할 잡 리허설은 인계·취소·잠금 동작을 다룬다. 이 리허설은 웹 배포 셸을 실제 Jenkins에 연결해 빌드 결과와 정리를 확인한다.

운영 Jenkins의 SCM·CI 게이트·Vault 권한, 실제 Docker 데몬에서의 웹 이미지 교체와 HTTP 확인, 운영 복구 결과는 [#692](https://github.com/shindonggeun/trading-desk/issues/692)의 별도 증거로 남긴다.
