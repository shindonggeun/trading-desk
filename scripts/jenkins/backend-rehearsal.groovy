// Jenkins 어댑터. Docker에는 전용 scratch 이미지 외의 리소스를 만들지 않는다.

def runRehearsal(def handoff, String revision) {
    String job = env.JOB_NAME
    String scenario = params.TD_CASE ?: 'success'
    if (!(scenario in ['success', 'api-failure', 'api-rollback', 'analyst-drain', 'cancel-api', 'cancel-queued-api'])) {
        error '알 수 없는 리허설 시나리오'
    }
    List causes = currentBuild.getBuildCauses()
    if (job == 'trading-desk-rehearsal/coordinator') {
        if (causes.size() != 1 || causes[0]._class != 'hudson.model.Cause$UserIdCause') {
            error '리허설 부모는 수동으로 실행해야 한다'
        }
        Map result = handoff.runHandoff(revision, coordinatorAdapter(handoff, scenario, env.BUILD_NUMBER))
        echo "TD_PARENT_RESULT ${result}"
        if (result.result == 'SKIPPED') {
            currentBuild.result = 'NOT_BUILT'
        } else if (result.result != 'SUCCESS') {
            error "리허설 종료: ${result.role} ${result.result}"
        }
        return
    }

    String role = job.tokenize('/').last()
    if (!(role in ['api', 'collector', 'analyst']) || !trustedParent(causes, params.TD_PARENT_BUILD)) {
        error '전용 부모 잡에서 시작한 역할 잡만 실행할 수 있다'
    }
    Map release = [revision: params.TD_SHA, imageId: params.TD_IMAGE_ID,
                   agent: params.TD_AGENT, daemon: params.TD_DAEMON]
    handoff.validateRelease(release)
    if (release.agent != 'trading-desk-rehearsal') {
        error '전용 리허설 노드가 필요하다'
    }
    onFixtureNode {
        checkout scm
        withEnv(["TD_SELECTED_SHA=${release.revision}"]) {
            sh 'git merge-base --is-ancestor "$TD_SELECTED_SHA" HEAD && git checkout --detach "$TD_SELECTED_SHA"'
        }
        Map actual = imageIdentity(release.revision) + [trustedParent: true]
        try {
            int code = handoff.consume(role, release, actual) { String service, String sha, String id ->
                return exerciseFixture(service, scenario, id)
            }
            currentBuild.description = resultDescription(params.TD_PARENT_BUILD, role, release, code)
            if (code != 0) {
                error "리허설 역할 결과: ${role} ${code}"
            }
        } finally {
            echo "TD_CHILD_BODY_EXIT ${env.BUILD_TAG}"
        }
    }
}

def coordinatorAdapter(def handoff, String scenario, String parentBuild) {
    String selectedRole
    Map selectedRelease
    return [
        withLock: { String resource, Closure action ->
            lock(resource: resource) {
                echo "TD_LOCK_ENTER ${env.BUILD_TAG}"
                try {
                    action()
                } finally {
                    echo "TD_LOCK_BODY_EXIT ${env.BUILD_TAG}"
                }
            }
        },
        eligible: { String revision ->
            onFixtureNode {
                checkout scm
                sh(returnStdout: true, script: 'git rev-parse HEAD').trim() == revision
            }
        },
        prepare: { String revision ->
            onFixtureNode {
                checkout scm
                withEnv(["TD_SELECTED_SHA=${revision}"]) {
                    sh '''#!/bin/sh
set -eu
test "$(git rev-parse HEAD)" = "$TD_SELECTED_SHA"
tag="trading-desk-rehearsal:$TD_SELECTED_SHA"
if ! docker image inspect "$tag" >/dev/null 2>&1; then
    printf 'FROM scratch\nLABEL org.opencontainers.image.revision=%s trading-desk.rehearsal=true\n' "$TD_SELECTED_SHA" | docker build -t "$tag" -
fi
'''
                }
                Map identity = imageIdentity(revision)
                handoff.validateRelease(identity)
                echo "TD_PREPARED ${identity.revision} ${identity.imageId}"
                identity
            }
        },
        start: { String canonical, Map release ->
            selectedRole = canonical.tokenize('-').last()
            if (canonical != "/trading-desk/trading-desk-backend-${selectedRole}" ||
                !(selectedRole in ['api', 'collector', 'analyst'])) {
                error '알 수 없는 역할 잡'
            }
            selectedRelease = release
            echo "TD_CHILD_REQUEST ${selectedRole} ${parentBuild}"
            // 큐 등록부터 빌드 종료까지 Pipeline Build Step이 직접 관리한다.
            int quiet = scenario == 'cancel-queued-api' && selectedRole == 'api' ? 60 : 0
            build(job: "/trading-desk-rehearsal/${selectedRole}", wait: true, propagate: false, quietPeriod: quiet,
                  parameters: [string(name: 'TD_SHA', value: release.revision),
                               string(name: 'TD_IMAGE_ID', value: release.imageId),
                               string(name: 'TD_AGENT', value: release.agent),
                               string(name: 'TD_DAEMON', value: release.daemon),
                               string(name: 'TD_PARENT_BUILD', value: parentBuild),
                               string(name: 'TD_CASE', value: scenario)])
        },
        await: { child ->
            echo "TD_CHILD_TERMINAL ${child.externalizableId} ${child.result}"
            readResult(child, parentBuild, selectedRole, selectedRelease)
        },
        cancelAndAwait: { child ->
            // start가 반환한 핸들은 이미 종료한 빌드다. 이 경로에서도 종료 관측을 기록한다.
            echo "TD_CHILD_TERMINAL ${child.externalizableId} ${child.result}"
        },
    ]
}

def onFixtureNode(Closure action) {
    return node('trading-desk-rehearsal') {
        if (env.NODE_NAME != 'trading-desk-rehearsal') {
            error '전용 리허설 노드가 필요하다'
        }
        action()
    }
}

Map imageIdentity(String revision) {
    String image
    withEnv(["TD_SELECTED_SHA=${revision}"]) {
        image = sh(returnStdout: true, script: '''#!/bin/sh
set -eu
tag="trading-desk-rehearsal:$TD_SELECTED_SHA"
test "$(docker image inspect -f '{{index .Config.Labels "trading-desk.rehearsal"}}' "$tag")" = true
test "$(docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$tag")" = "$TD_SELECTED_SHA"
docker image inspect -f '{{.Id}}' "$tag"
''').trim()
    }
    return [revision: sh(returnStdout: true, script: 'git rev-parse HEAD').trim(),
            imageId: image, agent: env.NODE_NAME,
            daemon: sh(returnStdout: true, script: "docker info --format '{{.ID}}'").trim()]
}

boolean trustedParent(List causes, Object expectedBuild) {
    if (causes.size() != 1 || !(expectedBuild instanceof String) || !(expectedBuild ==~ /[1-9][0-9]*/)) {
        return false
    }
    def cause = causes[0]
    return cause._class in ['hudson.model.Cause$UpstreamCause',
                            'org.jenkinsci.plugins.workflow.support.steps.build.BuildUpstreamCause'] &&
           cause.upstreamProject == 'trading-desk-rehearsal/coordinator' &&
           cause.upstreamBuild?.toString() == expectedBuild
}

String resultDescription(String parentBuild, String role, Map release, int code) {
    return "TD_RESULT ${parentBuild} ${role} ${release.revision} ${release.imageId} ${code}"
}

int readResult(def child, String parentBuild, String role, Map release) {
    for (int code in [0, 1, 2, 3]) {
        if (child.result == (code == 0 ? 'SUCCESS' : 'FAILURE') &&
            child.description == resultDescription(parentBuild, role, release, code)) {
            return code
        }
    }
    return 1
}

int exerciseFixture(String role, String scenario, String id) {
    echo "TD_CHILD_ENTER ${env.BUILD_TAG} ${role} ${id}"
    if (role == 'api' && scenario == 'cancel-api') {
        echo "TD_CANCEL_POINT ${env.BUILD_TAG}"
        sleep(time: 120, unit: 'SECONDS')
    }
    if (role == 'api' && scenario == 'api-failure') {
        return 1
    }
    if (role == 'analyst' && scenario == 'analyst-drain') {
        return 3
    }
    writeFile(file: 'rehearsal-state.txt', text: 'previous')
    writeFile(file: 'rehearsal-state.txt', text: id)
    if (role == 'api' && scenario == 'api-rollback') {
        writeFile(file: 'rehearsal-state.txt', text: 'previous')
        if (readFile('rehearsal-state.txt') != 'previous') {
            error '리허설 복구 상태가 일치하지 않는다'
        }
        echo "TD_FIXTURE_RESTORED ${env.BUILD_TAG}"
        return 2
    }
    return 0
}

return this
