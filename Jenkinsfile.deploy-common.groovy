// trading-desk 자동 배포 공통부 (docs/deploy.md 2절). Jenkinsfile-backend · Jenkinsfile-web 이 load 해 run(config) 을 부른다.
// commerce-foundry 의 Jenkinsfile.*-common.groovy 와 같은 모양(얇은 Jenkinsfile + load)이고, 셸 단계는 scripts/ 의 파일로 뺐다 —
// 손 배포(scripts/deploy.sh)와 Jenkins 가 같은 scripts/deploy-compose.sh 로 빌드 · 교체 · 확인 · 되돌림을 한다.
//
// develop 이 배포본이다(docs/git-workflow.md §8). develop 에 커밋이 들어오면:
//   1. CI 확인   (builder)              GitHub Actions `ci` 가 그 커밋에서 성공할 때까지 기다린다. 실패면 멈추고, 밀림(CI 취소, 또는
//                                       develop 에 실패하지 않은 더 새 커밋)이면 NOT_BUILT 로 건너뛴다. 배포 직전(lock 안)에도 한 번 더 본다(#752)
//   2. 배포 대상 (deploy agent, lock)    운영 컨테이너의 커밋 라벨과 비교해 backend/ (web 은 frontend/) 가 바뀌었을 때만 배포한다
//   3. 운영 배포 (deploy agent, lock)    Vault → 임시 env 파일(600) → scripts/deploy-compose.sh → env 파일 삭제
// 다른 브랜치 · PR 빌드는 아무것도 하지 않는다. 이 저장소는 공개라 잡은 fork PR 을 발견하지 않게 둔다(docs/deploy.md 2절 잡 등록).
//
// 비밀값은 Groovy 변수로 들이지 않는다. Vault 로그인 · 읽기는 scripts/jenkins/vault-env.sh 셸 하나 안에서 하고, 토큰은 그 셸이 끝날 때 폐기한다.
// 여기에는 credential 의 이름만 쓴다(Jenkins 폴더 trading-desk 범위). VAULT_ADDR 는 Jenkins 전역 env 다 — 잡 파라미터로 만들지 않는다.

def run(Map config) {
    properties([
        buildDiscarder(logRotator(numToKeepStr: '30')),
        // 동시 빌드는 막는다(대기열은 Jenkins 가 하나로 합친다). abortPrevious 는 쓰지 않는다 — 앞 빌드를 '운영 배포' 단계
        // (deploy-compose.sh 의 이미지 교체 · compose up 사이)에서도 끊어 서비스가 내려간 채 남을 수 있다(#752).
        // 밀린 빌드는 wait-ci.sh 의 밀림 검사(기다리는 동안 · 배포 직전)가 NOT_BUILT 로 건너뛴다.
        disableConcurrentBuilds(),
        parameters([
            string(name: 'DEPLOY_COMMIT', defaultValue: '', trim: true,
                   description: '비우면 이 빌드의 커밋. 이전 커밋으로 되돌릴 때 develop 에 있는 SHA(그 커밋의 CI 가 성공했어야 한다)'),
            booleanParam(name: 'FORCE_DEPLOY', defaultValue: false,
                         description: '운영 컨테이너와 소스 차이가 없어도 다시 빌드 · 배포한다'),
            booleanParam(name: 'FORCE_STOP_RUNS', defaultValue: false,
                         description: '(backend) 진행 중인 분석을 기다리지 않는다 — 그 분석은 실패로 끝난다(#431)'),
            booleanParam(name: 'ROLLBACK_DRILL', defaultValue: false,
                         description: '배포 뒤 확인을 일부러 실패시켜 직전 이미지로 돌아가는지 시험한다'),
        ] + (config.target == 'backend' ? [
            choice(name: 'SERVICES', choices: ['auto', 'all', 'api', 'collector', 'analyst'],
                   description: 'auto는 머지 PR과 운영 역할별 revision을 대조, all은 전체, 개별 역할은 자동 대상에 추가'),
        ] : [])),
    ])

    if (env.CHANGE_ID) {
        echo 'PR 빌드는 하지 않는다 — PR 검사는 GitHub Actions ci 가 한다'
        currentBuild.result = 'NOT_BUILT'
        return
    }
    if (env.BRANCH_NAME != config.branch) {
        echo "${env.BRANCH_NAME} 은 배포 브랜치(${config.branch})가 아니다 — 아무것도 하지 않는다"
        currentBuild.result = 'NOT_BUILT'
        return
    }
    String selection = config.target == 'backend' ? (params.SERVICES ?: 'auto') : 'all'
    boolean splitEnabled = config.target == 'backend' && env.TD_SPLIT_JOBS_ENABLED == 'true'
    String splitNode = config.target == 'backend' ? (env.TD_SPLIT_DEPLOY_NODE ?: '') : ''
    boolean fenceRequired = splitEnabled || splitNode != ''
    if (fenceRequired && !(splitNode ==~ /[a-zA-Z0-9_.-]{1,128}/)) {
        error '분리 배포 또는 활성 잠금에 전용 백엔드 배포 노드 설정이 필요하다'
    }
    if (!(selection in ['auto', 'all', 'api', 'collector', 'analyst']) || (!splitEnabled && !(selection in ['auto', 'all']))) {
        error '서비스 선택에는 승인된 분리 배포 설정이 필요하다'
    }

    String sha = null
    // 이 빌드의 커밋(develop 끝)을 배포할 때만 밀림 검사를 한다. DEPLOY_COMMIT(되돌리기)은 일부러 옛 커밋이다
    boolean followHead = !(params.DEPLOY_COMMIT ?: '').trim()
    if (!splitEnabled || !followHead || (selection == 'auto' && (params.FORCE_DEPLOY || params.ROLLBACK_DRILL))) {
        selection = 'all'
    }
    String newer = null
    stage('CI 확인') {
        node(config.gateAgentLabel) {
            try {
                checkout scm
                sha = resolveCommit()
                currentBuild.description = "${config.target} ${shortSha(sha)}"
                int rc
                timeout(time: config.ciWaitMinutes, unit: 'MINUTES') {
                    withCredentials([usernamePassword(credentialsId: config.githubCredentialId,
                                                      usernameVariable: 'GITHUB_USER', passwordVariable: 'GITHUB_TOKEN')]) {
                        withEnv(["CI_REPO=${config.githubRepo}", "CI_SHA=${sha}", "CI_WORKFLOW=${config.ciWorkflow}",
                                 "CI_BRANCH=${config.branch}", "CI_SUPERSEDE=${followHead ? '1' : '0'}"]) {
                            rc = sh(returnStatus: true, script: 'bash scripts/jenkins/wait-ci.sh')
                        }
                    }
                }
                if (rc == 4) {
                    sha = null
                } else if (rc != 0) {
                    error "CI 가 성공하지 않아 배포하지 않는다(${shortSha(sha)})"
                }
            } finally {
                deleteDir()
            }
        }
    }
    if (sha == null) {
        echo 'CI 가 취소됐거나 develop 에 더 새 배포 후보가 있다(위 "밀림:" 줄) — 그 커밋의 빌드가 배포한다'
        currentBuild.description += ' 밀림'
        currentBuild.result = 'NOT_BUILT'
        return
    }

    lock(resource: config.lockName) {
        Map release = null
        List roles = null
        def production
        def handoff
        node(fenceRequired ? splitNode : config.deployAgentLabel) {
            try {
                if (fenceRequired && env.NODE_NAME != splitNode) { error '전용 백엔드 배포 노드가 다르다' }
                boolean changed
                stage('배포 대상 확인') {
                    // 앞 빌드가 강제로 끊겨(agent 재시작 등) finally 가 못 돌았으면 비밀 폴더가 남아 있을 수 있다
                    sh 'rm -rf .deploy-secrets .deploy-src'
                    checkout scm
                    // 대상 커밋의 소스만 따로 푼다. 스크립트는 이 빌드(develop 끝)의 것을 쓴다 — 스크립트가 없던 옛 커밋으로도 되돌릴 수 있게
                    withEnv(["SHA=${sha}"]) {
                        sh 'set +x; rm -rf .deploy-src && mkdir .deploy-src && git archive "$SHA" | tar -x -C .deploy-src'
                    }
                    // lock 을 기다리는 동안 더 새 커밋이 들어왔으면 그 빌드가 배포한다(그 커밋의 CI 가 이미 실패면 이 커밋을 배포)
                    newer = followHead ? supersededBy(config, sha) : null
                    changed = newer == null && (splitEnabled || needsDeploy(config, sha))
                }
                if (newer != null) {
                    currentBuild.description += ' 밀림'
                    currentBuild.result = 'NOT_BUILT'
                    return
                }
                if (!changed) {
                    currentBuild.description += ' 변경 없음'
                    return
                }
                stage('운영 배포') {
                    String envFile = "${env.WORKSPACE}/.deploy-secrets/env"
                    withCredentials([string(credentialsId: config.vaultRoleIdCredentialId, variable: 'VAULT_ROLE_ID'),
                                     string(credentialsId: config.vaultSecretIdCredentialId, variable: 'VAULT_SECRET_ID')]) {
                        String vaultAddr = env.VAULT_ADDR?.trim()
                        if (!vaultAddr) {
                            error 'Jenkins 전역 env VAULT_ADDR 가 없다(Manage Jenkins → System → Global properties)'
                        }
                        withEnv(["VAULT_BASE=${vaultAddr}", "VAULT_PATH=${config.vaultPath}", "ENV_FILE=${envFile}",
                                 "COMPOSE_FILES=.deploy-src/${config.composeFile}"]) {
                            sh 'bash scripts/jenkins/vault-env.sh'
                        }
                    }
                    Integer rc = null
                    withEnv(["ENV_FILE=${envFile}", "REVISION=${sha}", "TARGET=${config.target}",
                             "PRIMARY_SERVICE=${config.primaryService ?: ''}",
                             "TD_DEPLOY_FENCE_REQUIRED=${fenceRequired ? 1 : 0}",
                             "FORCE=${params.FORCE_STOP_RUNS ? 1 : 0}", "ROLLBACK_DRILL=${params.ROLLBACK_DRILL ? 1 : 0}"]) {
                        if (splitEnabled) {
                            int route = sh(returnStatus: true, script: 'bash scripts/jenkins/backend-deploy-lease.sh route')
                            if (route == 0) {
                                production = load 'scripts/jenkins/backend-production.groovy'
                                handoff = load 'scripts/jenkins/backend-handoff.groovy'
                                if (followHead && !confirmSplitHead(config, sha)) { return }
                                if (selection == 'all') {
                                    release = production.prepare(sha, selection)
                                } else {
                                    withEnv(["CI_REPO=${config.githubRepo}", "CI_BRANCH=${config.branch}"]) {
                                        withCredentials([usernamePassword(credentialsId: config.githubCredentialId,
                                                           usernameVariable: 'GITHUB_USER', passwordVariable: 'GITHUB_TOKEN')]) {
                                            release = production.prepare(sha, selection)
                                        }
                                    }
                                }
                                if (release == null) {
                                    currentBuild.description += ' 변경 없음'
                                    return
                                }
                                roles = handoff.rolesFor(release.selection)
                                // No child has been queued yet. If the image preparation took
                                // long enough for a new head, retire this unused release.
                                boolean stillLatest
                                try {
                                    stillLatest = !followHead || confirmSplitHead(config, sha)
                                } catch (Exception failure) {
                                    production.finish(release)
                                    throw failure
                                }
                                if (!stillLatest) {
                                    production.finish(release)
                                    release = null
                                }
                                return
                            }
                            if (route != 10 || !(selection in ['auto', 'all'])) {
                                error '현재 상태에서 선택한 백엔드 서비스를 배포할 수 없다'
                            }
                            if (!needsDeploy(config, sha)) {
                                currentBuild.description += ' 변경 없음'
                                return
                            }
                        }
                        withEnv(["TD_LEGACY_SPLIT_FALLBACK=${splitEnabled ? 1 : 0}"]) {
                            rc = sh(returnStatus: true, script: 'bash scripts/deploy-compose.sh "$TARGET" .deploy-src')
                        }
                    }
                    if (release != null || rc == null) { return }
                    sh 'rm -rf .deploy-secrets'
                    if (rc == 0) {
                        currentBuild.description += params.ROLLBACK_DRILL ? ' 배포됨(드릴 무시)' : ' 배포됨'
                    } else if (rc == 2) {
                        currentBuild.description += ' 되돌림'
                        error "배포 확인이 실패해 직전 이미지로 되돌렸다(${shortSha(sha)}) — 로그의 '확인 실패' 줄을 본다"
                    } else if (rc == 3) {
                        currentBuild.description += ' 분석 대기로 멈춤'
                        error '진행 중인 분석이 끝나지 않아 멈췄다. 운영은 그대로다 — 끊어도 되면 FORCE_STOP_RUNS 로 다시'
                    } else {
                        currentBuild.description += ' 실패'
                        error "배포가 실패했고 되돌리지 못했다(${shortSha(sha)}) — docs/deploy.md 2절 '되돌림'"
                    }
                }
            } finally {
                sh 'rm -rf .deploy-secrets .deploy-src || true'
                deleteDir()
            }
        }
        if (release != null) {
            // Synchronous child build, with the parent lock held but without
            // holding the production node's executor.
            Map result = production.dispatch(handoff, release, roles)
            node(release.agent) {
                try {
                    checkout scm
                    production.finish(release)
                } finally {
                    deleteDir()
                }
            }
            currentBuild.description += result.result == 'SUCCESS' ? ' 배포됨' : " ${result.role} ${result.result}"
            if (result.result != 'SUCCESS') { error "서비스 배포 실패: ${result.role} ${result.result}" }
        }
    }
}

boolean confirmSplitHead(Map config, String sha) {
    int rc
    withCredentials([usernamePassword(credentialsId: config.githubCredentialId,
                                      usernameVariable: 'GITHUB_USER', passwordVariable: 'GITHUB_TOKEN')]) {
        withEnv(["CI_REPO=${config.githubRepo}", "CI_SHA=${sha}", "CI_BRANCH=${config.branch}",
                 "CI_WORKFLOW=${config.ciWorkflow}"]) {
            rc = sh(returnStatus: true, script: 'bash scripts/jenkins/wait-ci.sh --require-head')
        }
    }
    if (rc == 4) {
        currentBuild.description += ' 밀림'
        currentBuild.result = 'NOT_BUILT'
        return false
    }
    if (rc != 0) { error '분리 배포 전에 develop 머리를 확인하지 못했다' }
    return true
}

// 배포 직전 밀림 검사(scripts/jenkins/wait-ci.sh --superseded-only): 밀렸으면 더 새 커밋 쪽을 알리는 문자열, 아니면 null.
// GitHub 를 못 읽으면 스크립트가 0 을 돌려 배포를 계속한다
String supersededBy(Map config, String sha) {
    int rc
    withCredentials([usernamePassword(credentialsId: config.githubCredentialId,
                                      usernameVariable: 'GITHUB_USER', passwordVariable: 'GITHUB_TOKEN')]) {
        withEnv(["CI_REPO=${config.githubRepo}", "CI_SHA=${sha}", "CI_WORKFLOW=${config.ciWorkflow}",
                 "CI_BRANCH=${config.branch}"]) {
            rc = sh(returnStatus: true, script: 'bash scripts/jenkins/wait-ci.sh --superseded-only')
        }
    }
    return rc == 4 ? 'develop 머리' : null
}

// DEPLOY_COMMIT 이 비면 HEAD(develop 끝). 있으면 develop 에 있는 커밋인지 본다
String resolveCommit() {
    String head = sh(returnStdout: true, script: 'git rev-parse HEAD').trim()
    String wanted = (params.DEPLOY_COMMIT ?: '').trim()
    if (!wanted) {
        return head
    }
    if (!(wanted ==~ /[0-9a-f]{7,40}/)) {
        error 'DEPLOY_COMMIT 는 커밋 SHA(16진수 7~40자)여야 한다'
    }
    String full
    withEnv(["WANTED=${wanted}"]) {
        full = sh(returnStdout: true, script: 'git rev-parse --verify --quiet "${WANTED}^{commit}" || true').trim()
        if (!full || sh(returnStatus: true, script: 'git merge-base --is-ancestor "${WANTED}" HEAD') != 0) {
            error "DEPLOY_COMMIT ${wanted} 가 develop 에 없다"
        }
    }
    return full
}

// 운영 컨테이너 이미지의 커밋 라벨(org.opencontainers.image.revision)과 대상 커밋 사이에 watchPath 가 바뀌었을 때만 배포한다
// Jenkins `sh` 는 /bin/sh(agent 에서 dash)로 돈다 — bash 문법(${v:0:12} · [[ ]] · 배열 등)은 쓰지 않는다. bash 가 필요하면 scripts/ 파일로 빼 `bash` 로 부른다(#692)
boolean needsDeploy(Map config, String sha) {
    if (params.FORCE_DEPLOY || (params.DEPLOY_COMMIT ?: '').trim()) {
        echo 'FORCE_DEPLOY · DEPLOY_COMMIT — 차이를 보지 않고 배포한다'
        return true
    }
    int rc
    List dockerEnv = config.target == 'backend' && env.TD_SPLIT_DEPLOY_NODE ? [
        'DOCKER_HOST=unix:///var/run/docker.sock', 'DOCKER_CONTEXT=', 'DOCKER_TLS_VERIFY=', 'DOCKER_CERT_PATH=',
    ] : []
    withEnv(["CONTAINER=${config.container}", "SHA=${sha}", "WATCH=${config.watchPath}"] + dockerEnv) {
        rc = sh(returnStatus: true, script: '''
            set +x
            running=$(docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$CONTAINER" 2>/dev/null || true)
            if [ -z "$running" ]; then echo "운영 $CONTAINER 의 커밋을 모른다(첫 배포 · 라벨 없는 옛 이미지) — 배포한다"; exit 10; fi
            echo "운영 $(printf %.12s "$running") → 대상 $(printf %.12s "$SHA")"
            git cat-file -e "$running^{commit}" 2>/dev/null || { echo "운영 커밋이 기록에 없다 — 배포한다"; exit 10; }
            if git diff --quiet "$running" "$SHA" -- "$WATCH"; then echo "$WATCH/ 변경 없음 — 배포하지 않는다"; exit 0; fi
            git diff --stat "$running" "$SHA" -- "$WATCH" | tail -n 1
            exit 10
        ''')
    }
    if (rc != 0 && rc != 10) {
        error "배포 대상을 확인하지 못했다(종료 코드 ${rc})"
    }
    return rc == 10
}

// 샌드박스에서 승인 없이 되는 String 메서드만 쓴다
String shortSha(String sha) {
    return sha.length() > 8 ? sha.substring(0, 8) : sha
}

return this
