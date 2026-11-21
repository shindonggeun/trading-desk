// Production job wiring. No secret values, workspace paths or user-selected
// node labels are part of the downstream parameters.

String trustedNode() {
    String name = env.TD_SPLIT_DEPLOY_NODE ?: ''
    if (!(name ==~ /[a-zA-Z0-9_.-]{1,128}/)) {
        error '전용 백엔드 배포 노드 설정이 필요하다'
    }
    return name
}

Map prepare(String revision, List roles) {
    String agent = trustedNode()
    if (env.NODE_NAME != agent) { error '설정된 전용 노드에서만 분리 배포를 준비한다' }
    String parent = env.BUILD_NUMBER
    String record
    withEnv(["TD_NODE=${agent}", "TD_PARENT_BUILD=${parent}", "TD_ROLES=${roles.join(',')}",
             "TD_FORCE_RECREATE=${params.FORCE_DEPLOY ? 1 : 0}"]) {
        record = sh(returnStdout: true, script: 'bash scripts/jenkins/backend-deploy-lease.sh begin .deploy-src').trim()
    }
    List values = record.tokenize()
    if (values.size() != 3 || !(values[0] ==~ /[a-f0-9-]{36}/)) {
        error '배포 인계 기록이 유효하지 않다'
    }
    return [revision: revision, imageId: values[1], agent: agent, daemon: values[2], token: values[0], parent: parent]
}

String resultDescription(Map release, String role, int code) {
    return "TD_RESULT ${release.parent} ${release.token} ${role} ${release.revision} ${release.imageId} ${code}"
}

def dispatch(def handoff, Map release, List roles) {
    String role
    return handoff.dispatchRelease(release, roles, [
        start: { String job, Map image ->
            role = job.tokenize('-').last()
            build(job: job, wait: true, propagate: false,
                  parameters: [string(name: 'TD_RELEASE', value: release.token),
                               string(name: 'TD_PARENT_BUILD', value: release.parent),
                               string(name: 'TD_SHA', value: image.revision),
                               string(name: 'TD_IMAGE_ID', value: image.imageId),
                               string(name: 'TD_AGENT', value: image.agent),
                               string(name: 'TD_DAEMON', value: image.daemon)])
        },
        await: { child ->
            for (int code in [0, 1, 2, 3]) {
                if (child.result == (code == 0 ? 'SUCCESS' : 'FAILURE') &&
                    child.description == resultDescription(release, role, code)) {
                    return code
                }
            }
            error '하위 잡 종료 결과를 인계와 대조할 수 없다 — 잠금 기록을 보존한다'
        },
        // A returned build is terminal. An interrupted build() leaves the ticket
        // untouched so an operator can check the child/queue before resuming.
        cancelAndAwait: { child -> echo "하위 잡 결과 미확인: ${child.externalizableId}" },
    ])
}

def finish(Map release) {
    if (env.NODE_NAME != release.agent) { error '인계를 만든 전용 노드에서만 정리한다' }
    withEnv(["TD_NODE=${release.agent}", "TD_PARENT_BUILD=${release.parent}",
             "REVISION=${release.revision}", "TD_RELEASE=${release.token}"]) {
        sh 'bash scripts/jenkins/backend-deploy-lease.sh finish'
    }
}

boolean trustedParent(List causes, String parent) {
    if (!(parent ==~ /[1-9][0-9]*/) || causes.size() != 1) { return false }
    def cause = causes[0]
    return cause._class in ['hudson.model.Cause$UpstreamCause',
                            'org.jenkinsci.plugins.workflow.support.steps.build.BuildUpstreamCause'] &&
           cause.upstreamProject == 'trading-desk/trading-desk-backend/develop' &&
           cause.upstreamBuild?.toString() == parent
}

def runRole(def handoff, String role) {
    Map release = [revision: params.TD_SHA, imageId: params.TD_IMAGE_ID, agent: params.TD_AGENT,
                   daemon: params.TD_DAEMON, parent: params.TD_PARENT_BUILD ?: '', token: params.TD_RELEASE ?: '']
    if (!trustedParent(currentBuild.getBuildCauses(), release.parent) ||
        !(release.token ==~ /[a-f0-9-]{36}/)) {
        error '정확한 상위 잡의 인계만 실행할 수 있다'
    }
    handoff.validateRelease(release)
    if (release.agent != env.NODE_NAME || release.agent != trustedNode()) {
        error '이미지를 준비한 전용 배포 노드와 다르다'
    }
    if (!currentBuild.getUpstreamBuilds().any { upstream ->
        upstream.fullProjectName == 'trading-desk/trading-desk-backend/develop' &&
        upstream.number.toString() == release.parent && upstream.result == null
    }) {
        error '활성 부모 빌드를 확인할 수 없다'
    }
    int code
    withEnv(["TD_NODE=${env.NODE_NAME}", "TD_PARENT_BUILD=${release.parent}", "TD_RELEASE=${release.token}",
             "REVISION=${release.revision}", "TD_IMAGE_ID=${release.imageId}", "TD_DAEMON=${release.daemon}",
             "TD_ROLE=${role}"]) {
        code = sh(returnStatus: true, script: 'bash scripts/jenkins/backend-deploy-lease.sh deploy "$TD_ROLE"')
    }
    currentBuild.description = resultDescription(release, role, code)
    if (code != 0) { error "서비스 배포 종료 코드: ${code}" }
}

return this
