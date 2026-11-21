// groovy scripts/tests/backend-rehearsal.test.groovy
String sha = 'a' * 40
String id = 'sha256:' + 'b' * 64
String agent = 'trading-desk-rehearsal'
Map release = [revision: sha, imageId: id, agent: agent, daemon: 'daemon-A']
def handoff = new GroovyShell().evaluate(new File('scripts/jenkins/backend-handoff.groovy'))
new GroovyShell().parse(new File('Jenkinsfile.backend-rehearsal'))

def fixture = { String scenario, boolean cancel = false, Map child = [:] ->
    List events = []
    List requests = []
    Map files = [:]
    boolean locked = false
    boolean onNode = false
    Map env = [NODE_NAME: agent, BUILD_TAG: 'fixture',
               JOB_NAME: "trading-desk-rehearsal/${child.role ?: 'coordinator'}".toString()]
    Map run = [getBuildCauses: { child.causes ?: [] }]
    def binding = new Binding([
        env: env,
        params: [TD_CASE: scenario, TD_SHA: sha, TD_IMAGE_ID: id, TD_AGENT: agent,
                 TD_DAEMON: 'daemon-A', TD_PARENT_BUILD: '12'],
        currentBuild: run,
        scm: [:],
        echo: { message -> events << message.toString() },
        error: { message -> throw new IllegalArgumentException(message.toString()) },
        lock: { Map args, Closure body ->
            assert args.resource == 'trading-desk-prod-deploy'
            locked = true
            try {
                body()
            } finally {
                locked = false
                events << 'unlock'
            }
        },
        node: { String label, Closure body ->
            assert (locked || child.role) && label == agent
            onNode = true
            try {
                body()
            } finally {
                onNode = false
            }
        },
        checkout: { Map ignored -> assert onNode },
        withEnv: { List values, Closure body ->
            assert values == ["TD_SELECTED_SHA=${sha}"]
            body()
        },
        sh: { command ->
            assert onNode
            Map args = command instanceof Map ? command : [script: command]
            if (args.script.startsWith('git rev-parse')) { return sha + '\n' }
            if (args.script.startsWith('git merge-base')) { return null }
            if (args.script.startsWith('docker info')) { return 'daemon-A\n' }
            if (args.returnStdout) { return (child.imageId ?: id) + '\n' }
            assert args.script.contains('trading-desk-rehearsal:')
            events << 'prepare'
        },
        string: { Map arg -> arg },
        build: { Map args ->
            assert locked && !onNode
            assert args.wait == true && args.propagate == false
            assert args.quietPeriod == (scenario == 'cancel-queued-api' ? 60 : 0)
            assert args.job.startsWith('/trading-desk-rehearsal/')
            def role = args.job.tokenize('/').last()
            requests << role
            assert args.parameters.collectEntries { [(it.name): it.value] } == [
                TD_SHA: sha, TD_IMAGE_ID: id, TD_AGENT: agent, TD_DAEMON: 'daemon-A',
                TD_PARENT_BUILD: '12', TD_CASE: scenario,
            ]
            if (cancel) {
                events << 'stop-request'
                events << 'child-terminal'
                throw new InterruptedException('Jenkins stopped downstream')
            }
            int code = role == 'api' && scenario == 'api-rollback' ? 2 : 0
            return [externalizableId: "${role}#1", result: code == 0 ? 'SUCCESS' : 'FAILURE',
                    description: "TD_RESULT 12 ${role} ${sha} ${id} ${code}".toString()]
        },
        writeFile: { Map entry -> files[entry.file] = entry.text },
        readFile: { String path -> files[path] },
    ])
    def adapter = new GroovyShell(binding).evaluate(new File('scripts/jenkins/backend-rehearsal.groovy'))
    [adapter: adapter, events: events, requests: requests, run: run, files: files]
}

def good = fixture('success')
assert handoff.runHandoff(sha, good.adapter.coordinatorAdapter(handoff, 'success', '12')).result == 'SUCCESS'
assert good.requests == ['api', 'collector', 'analyst']
assert good.events.count('prepare') == 1
assert good.events.indexOf('TD_CHILD_TERMINAL api#1 SUCCESS') < good.events.indexOf('TD_CHILD_REQUEST collector 12')
assert good.events.last() == 'unlock'

def recover = fixture('api-rollback')
assert handoff.runHandoff(sha, recover.adapter.coordinatorAdapter(handoff, 'api-rollback', '12')).result == 'RECOVERED'
assert recover.requests == ['api']
assert recover.adapter.exerciseFixture('api', 'api-rollback', id) == 2
assert recover.events.contains('TD_FIXTURE_RESTORED fixture')
assert recover.adapter.exerciseFixture('api', 'api-failure', id) == 1
assert recover.adapter.exerciseFixture('analyst', 'analyst-drain', id) == 3
assert recover.adapter.readResult([result: 'SUCCESS', description: 'missing'], '12', 'api', release) == 1
def wrongResult = [result: 'SUCCESS', description: recover.adapter.resultDescription('11', 'api', release, 0)]
assert recover.adapter.readResult(wrongResult, '12', 'api', release) == 1

def cause = [_class: 'org.jenkinsci.plugins.workflow.support.steps.build.BuildUpstreamCause',
             upstreamProject: 'trading-desk-rehearsal/coordinator', upstreamBuild: 12]
assert recover.adapter.trustedParent([cause], '12')
assert !recover.adapter.trustedParent([cause], '11')
assert !recover.adapter.trustedParent([cause, [_class: 'ReplayCause']], '12')
assert !recover.adapter.trustedParent([[_class: 'hudson.model.Cause$UserIdCause']], '12')

def child = fixture('success', false, [role: 'collector', causes: [cause]])
child.adapter.runRehearsal(handoff, sha)
assert child.run.description == child.adapter.resultDescription('12', 'collector', release, 0)
assert child.files['rehearsal-state.txt'] == id

def mismatch = fixture('success', false, [role: 'collector', causes: [cause], imageId: 'sha256:' + 'c' * 64])
try {
    mismatch.adapter.runRehearsal(handoff, sha)
    assert false: 'accepted a different local image'
} catch (IllegalArgumentException expected) {
    assert expected.message.endsWith('imageId')
    assert mismatch.files.isEmpty()
    assert !mismatch.events.any { it.startsWith('TD_CHILD_ENTER') }
}

for (String scenario in ['cancel-queued-api', 'cancel-api']) {
    def stopped = fixture(scenario, true)
    try {
        handoff.runHandoff(sha, stopped.adapter.coordinatorAdapter(handoff, scenario, '12'))
        assert false: 'parent cancellation was swallowed'
    } catch (InterruptedException expected) {
        assert stopped.requests == ['api']
        assert stopped.events[-4..-1] == ['stop-request', 'child-terminal', 'TD_LOCK_BODY_EXIT fixture', 'unlock']
    }
}

println 'Backend rehearsal Jenkins adapter scenarios passed'
