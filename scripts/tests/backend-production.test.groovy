// From the repository root: groovy scripts/tests/backend-production.test.groovy
String sha = 'a' * 40
String image = 'sha256:' + 'b' * 64
String ticket = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
Map config = [target: 'backend', branch: 'develop', githubRepo: 'org/repo',
              ciWorkflow: 'ci', ciWaitMinutes: 1, gateAgentLabel: 'builder',
              deployAgentLabel: 'deploy', lockName: 'trading-desk-prod-deploy', watchPath: 'backend']

def scenario = { Map options = [:] ->
    List events = []
    List requests = []
    List headCodes = new ArrayList(options.headCodes ?: [0, 0])
    boolean locked = false
    Map run = [description: '', result: null]
    Map env = [BRANCH_NAME: 'develop', BUILD_NUMBER: '7', VAULT_ADDR: 'https://vault.mock',
               TD_SPLIT_JOBS_ENABLED: options.enabled ? 'true' : '',
               TD_SPLIT_DEPLOY_NODE: options.armed || options.enabled ? 'backend-agent' : '',
               WORKSPACE: '/fake']
    Map params = [SERVICES: options.selection ?: 'all', DEPLOY_COMMIT: options.explicit ?: '',
                  FORCE_DEPLOY: options.force ?: false, FORCE_STOP_RUNS: false, ROLLBACK_DRILL: false]
    Binding binding
    binding = new Binding([
        env: env, params: params, currentBuild: run, scm: [:],
        properties: { List value -> }, buildDiscarder: { arg -> arg }, logRotator: { Map arg -> arg },
        disableConcurrentBuilds: { -> null }, parameters: { List arg -> arg },
        string: { Map arg -> arg }, booleanParam: { Map arg -> arg }, choice: { Map arg -> arg },
        stage: { String name, Closure work -> work() }, timeout: { Map args, Closure work -> work() },
        usernamePassword: { Map arg -> arg }, withCredentials: { List args, Closure work -> work() },
        withEnv: { List args, Closure work ->
            Map previous = new LinkedHashMap(env)
            args.each { String pair -> def fields = pair.split('=', 2); env[fields[0]] = fields[1] }
            try { work() } finally { env.clear(); env.putAll(previous) }
        },
        node: { String name, Closure work ->
            assert !env.NODE_NAME
            env.NODE_NAME = name
            try { work() } finally { env.remove('NODE_NAME') }
        },
        checkout: { arg -> }, deleteDir: { -> }, echo: { msg -> }, error: { msg ->
            throw new IllegalStateException(msg.toString())
        },
        lock: { Map arg, Closure work ->
            assert arg.resource == 'trading-desk-prod-deploy' && !locked
            locked = true
            try { work() } finally { locked = false }
        },
        sh: { Object request ->
            String script = request instanceof Map ? request.script : request
            if (script == 'git rev-parse HEAD' || script.startsWith('git rev-parse --verify')) {
                return sha
            }
            if (script.contains(' --require-head')) {
                assert locked && env.NODE_NAME == 'backend-agent'
                assert env.CI_SHA == sha
                events << 'head'
                return headCodes.remove(0)
            }
            if (script.startsWith('bash scripts/jenkins/wait-ci.sh')) { return 0 }
            if (script.startsWith('bash scripts/jenkins/vault-env.sh')) {
                assert locked && env.NODE_NAME
                events << 'vault'
                return 0
            }
            if (script.endsWith('backend-deploy-lease.sh route')) {
                assert locked && env.NODE_NAME == 'backend-agent'
                return options.route == null ? 0 : options.route
            }
            if (script.endsWith('backend-deploy-lease.sh begin .deploy-src')) {
                assert locked && env.NODE_NAME == 'backend-agent'
                events << "prepare:${env.TD_ROLES}:${env.TD_FORCE_RECREATE}:${env.FORCE}".toString()
                return "${ticket} ${image} daemon-A"
            }
            if (script.endsWith('backend-deploy-lease.sh finish')) {
                assert locked && env.NODE_NAME == 'backend-agent' && env.TD_RELEASE == ticket
                events << 'finish'
                return 0
            }
            if (script.startsWith('bash scripts/deploy-compose.sh')) {
                assert locked && env.NODE_NAME
                events << "legacy:${env.TD_DEPLOY_FENCE_REQUIRED}".toString()
                return 0
            }
            if (script.contains('git diff --quiet')) {
                events << 'legacy-diff'
                return 10
            }
            return 0
        },
        load: { String path -> new GroovyShell(binding).evaluate(new File(path)) },
        build: { Map request ->
            assert locked && !env.NODE_NAME
            assert request.wait == true && request.propagate == false
            String role = request.job.tokenize('-').last()
            Map args = request.parameters.collectEntries { item -> [(item.name): item.value] }
            assert args == [TD_RELEASE: ticket, TD_PARENT_BUILD: '7', TD_SHA: sha, TD_IMAGE_ID: image,
                            TD_AGENT: 'backend-agent', TD_DAEMON: 'daemon-A']
            requests << role
            if (options.interrupt) { throw new IllegalStateException('interrupted build') }
            int code = options.codes?.get(role) ?: 0
            return [result: code == 0 ? 'SUCCESS' : 'FAILURE', externalizableId: 'mock#8',
                    description: "TD_RESULT 7 ${ticket} ${role} ${sha} ${image} ${code}".toString()]
        },
    ])
    def pipeline = new GroovyShell(binding).evaluate(new File('Jenkinsfile.deploy-common.groovy'))
    Exception error = null
    try { pipeline.run(config) } catch (Exception failure) { error = failure }
    return [events: events, requests: requests, state: run, error: error]
}

assert scenario().events == ['legacy-diff', 'vault', 'legacy:0']
assert scenario(armed: true).events == ['legacy-diff', 'vault', 'legacy:1']
assert scenario(enabled: true, route: 10).events == ['vault', 'legacy-diff', 'legacy:1']
assert scenario(enabled: true, route: 10, selection: 'api').error
assert scenario(selection: 'api').events == []
def all = scenario(enabled: true)
assert !all.error
assert all.requests == ['api', 'collector', 'analyst']
assert all.events == ['vault', 'head', 'prepare:api,collector,analyst:0:0', 'head', 'finish']
assert !all.events.contains('legacy-diff')
def single = scenario(enabled: true, selection: 'analyst', force: true)
assert single.requests == ['analyst']
assert single.events.contains('prepare:analyst:1:0')
def recovered = scenario(enabled: true, codes: [api: 2])
assert recovered.requests == ['api'] && recovered.events.last() == 'finish'
assert recovered.error.message.contains('RECOVERED')
def cancelled = scenario(enabled: true, interrupt: true)
assert cancelled.requests == ['api'] && !cancelled.events.contains('finish')
assert cancelled.error.message.contains('interrupted build')
def stale = scenario(enabled: true, headCodes: [0, 4])
assert stale.state.result == 'NOT_BUILT' && stale.requests == [] && stale.events.last() == 'finish'
def unknown = scenario(enabled: true, headCodes: [1])
assert unknown.requests == [] && !unknown.events.any { it.startsWith('prepare') } && unknown.error

def child = { String result ->
    String parentJob = 'trading-desk/trading-desk-backend/develop'
    Map currentBuild = [
        getBuildCauses: { -> [[_class: 'hudson.model.Cause$UpstreamCause', upstreamProject: parentJob, upstreamBuild: 7]] },
        getUpstreamBuilds: { -> [[fullProjectName: parentJob, number: 7, result: result]] },
    ]
    List scripts = []
    Binding binding = new Binding([
        env: [TD_SPLIT_DEPLOY_NODE: 'backend-agent', NODE_NAME: 'backend-agent'],
        params: [TD_PARENT_BUILD: '7', TD_RELEASE: ticket, TD_SHA: sha, TD_IMAGE_ID: image,
                 TD_AGENT: 'backend-agent', TD_DAEMON: 'daemon-A'],
        currentBuild: currentBuild,
        withEnv: { List args, Closure work -> work() },
        sh: { Map request -> scripts << request.script; 0 },
        error: { msg -> throw new IllegalStateException(msg.toString()) },
    ])
    def shell = new GroovyShell(binding)
    def production = shell.evaluate(new File('scripts/jenkins/backend-production.groovy'))
    def handoff = shell.evaluate(new File('scripts/jenkins/backend-handoff.groovy'))
    Exception failure = null
    try { production.runRole(handoff, 'api') } catch (Exception error) { failure = error }
    return [scripts: scripts, failure: failure]
}
assert child(null).scripts == ['bash scripts/jenkins/backend-deploy-lease.sh deploy "$TD_ROLE"']
def orphanChild = child('ABORTED')
assert orphanChild.scripts == [] && orphanChild.failure.message.contains('활성 부모')
println 'Backend production wiring scenarios passed'
