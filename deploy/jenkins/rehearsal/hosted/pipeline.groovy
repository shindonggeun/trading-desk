// Exact same release, role/cause, build step and result contract as #814/#818.
// The immutable image and daemon fields are synthetic; no Docker client is present.
def handoff
def rehearsal
String agentName
node('built-in') {
    sh 'cp /opt/td-rehearsal/backend-handoff.groovy /opt/td-rehearsal/backend-rehearsal.groovy .'
    handoff = load 'backend-handoff.groovy'
    rehearsal = load 'backend-rehearsal.groovy'
    agentName = env.NODE_NAME
}
String revision = 'a' * 40
String role = env.JOB_NAME.tokenize('/').last()
String scenario = params.TD_CASE ?: 'success'
String deployNode = scenario == 'node-loss' ? 'td-loss-agent' : agentName
Map identity = [revision: revision, imageId: 'sha256:' + ('b' * 64),
                agent: deployNode, daemon: 'fixture-local']

if (role == 'coordinator') {
    def adapter = rehearsal.coordinatorAdapter(handoff, scenario, env.BUILD_NUMBER)
    adapter.eligible = { String candidate -> candidate == revision }
    adapter.prepare = { String candidate ->
        node('built-in') {
            // Durable synthetic orphan marker survives an interrupted parent.
            sh '''
set -eu
if [ -e /var/jenkins_home/td-fixture-active ]; then
    echo TD_ORPHAN_BLOCKED
    exit 1
fi
printf '%s\n' "$BUILD_NUMBER" > /var/jenkins_home/td-fixture-active
'''
        }
        identity
    }
    def withLock = adapter.withLock
    adapter.withLock = { String resource, Closure action ->
        withLock(resource) {
            def outcome = action()
            node('built-in') {
                sh '''
set -eu
test "$(cat /var/jenkins_home/td-fixture-active)" = "$BUILD_NUMBER"
rm -- /var/jenkins_home/td-fixture-active
'''
            }
            outcome
        }
    }
    Map result = handoff.runHandoff(revision, adapter)
    if (result.result != 'SUCCESS') {
        error "TD_RESULT_${result.result}"
    }
    return
}

if (!rehearsal.trustedParent(currentBuild.getBuildCauses(), params.TD_PARENT_BUILD)) {
    error 'TD_UNTRUSTED_PARENT'
}
Map release = [revision: params.TD_SHA, imageId: params.TD_IMAGE_ID,
               agent: params.TD_AGENT, daemon: params.TD_DAEMON]
node(deployNode) {
    assert env.NODE_NAME == deployNode
    int code = handoff.consume(role, release, identity + [trustedParent: true]) {
        String service, String sha, String image ->
            if (scenario == 'node-loss' && service == 'api') {
                sh 'set +x; echo TD_NODE_LOSS_POINT; sleep 120'
            }
            rehearsal.exerciseFixture(service, scenario, image)
    }
    currentBuild.description = rehearsal.resultDescription(params.TD_PARENT_BUILD, role, release, code)
    if (code != 0) {
        error "TD_ROLE_STATUS ${code}"
    }
}
