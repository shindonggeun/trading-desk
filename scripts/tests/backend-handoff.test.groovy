// From the repository root: groovy scripts/tests/backend-handoff.test.groovy
def handoff = new GroovyShell().evaluate(new File('scripts/jenkins/backend-handoff.groovy'))
String revision = 'a' * 40
Map release = [revision: revision, imageId: 'sha256:' + 'b' * 64, agent: 'backend-agent', daemon: 'docker-A']
String prefix = '/trading-desk/trading-desk-backend-'

def scenario = { Map options = [:] ->
    List events = []
    List requests = []
    String active = null
    boolean locked = false
    Map adapter = [
        withLock: { String name, Closure work ->
            assert name == 'trading-desk-prod-deploy'
            locked = true
            events << 'lock'
            try {
                work()
            } finally {
                assert active == null: 'lock released before the downstream job stopped'
                locked = false
                events << 'unlock'
            }
        },
        eligible: { String sha ->
            assert locked && sha == revision
            events << 'eligible'
            options.eligible != false
        },
        prepare: { String sha ->
            assert locked && sha == revision
            events << 'prepare'
            release + (options.prepared ?: [:])
        },
        start: { String job, Map parameters ->
            assert locked && active == null
            assert job.startsWith(prefix)
            if (options.interruptBeforeEnqueue) {
                events << 'rejected'
                throw new InterruptedException('parent cancelled before enqueue')
            }
            active = job.substring(prefix.length())
            events << "start:${active}".toString()
            requests << parameters
            active
        },
        await: { String child ->
            assert locked && active == child
            events << "await:${child}".toString()
            if (options.interrupt == child) {
                throw new InterruptedException('parent cancelled')
            }
            active = null
            options.codes?.get(child) ?: 0
        },
        cancelAndAwait: { String child ->
            assert locked && active == child
            events << "cancel:${child}".toString()
            active = null
            events << "terminated:${child}".toString()
        },
    ]
    [adapter: adapter, events: events, requests: requests]
}

def normal = scenario()
assert handoff.runHandoff(revision, normal.adapter) == [result: 'SUCCESS']
assert normal.events == ['lock', 'eligible', 'prepare',
    'start:api', 'await:api', 'start:collector', 'await:collector', 'start:analyst', 'await:analyst', 'unlock']
assert normal.requests == [release, release, release]

def stale = scenario(eligible: false)
assert handoff.runHandoff(revision, stale.adapter) == [result: 'SKIPPED']
assert stale.events == ['lock', 'eligible', 'unlock']

def wrongRevision = scenario(prepared: [revision: 'd' * 40])
try {
    handoff.runHandoff(revision, wrongRevision.adapter)
    assert false: 'accepted a different build revision'
} catch (IllegalArgumentException expected) {
    assert wrongRevision.events == ['lock', 'eligible', 'prepare', 'unlock']
}
def invalidImage = scenario(prepared: [imageId: 'trading-desk-backend:prod'])
try {
    handoff.runHandoff(revision, invalidImage.adapter)
    assert false: 'accepted a mutable tag'
} catch (IllegalArgumentException expected) {
    assert invalidImage.events == ['lock', 'eligible', 'prepare', 'unlock']
}

for (List row in [
    ['api', 1, 'FAILED', ['start:api']],
    ['api', 2, 'RECOVERED', ['start:api']],
    ['collector', 2, 'RECOVERED', ['start:api', 'start:collector']],
    ['analyst', 3, 'DRAIN_TIMEOUT', ['start:api', 'start:collector', 'start:analyst']],
]) {
    def (role, code, result, started) = row
    def failure = scenario(codes: [(role): code])
    assert handoff.runHandoff(revision, failure.adapter) == [result: result, role: role, status: code]
    assert failure.events.findAll { it.startsWith('start:') } == started
    assert failure.events.last() == 'unlock'
}

def cancelled = scenario(interrupt: 'api')
try {
    handoff.runHandoff(revision, cancelled.adapter)
    assert false: 'parent interruption was swallowed'
} catch (InterruptedException expected) {
    assert cancelled.events == ['lock', 'eligible', 'prepare', 'start:api', 'await:api',
        'cancel:api', 'terminated:api', 'unlock']
}
def beforeEnqueue = scenario(interruptBeforeEnqueue: true)
try {
    handoff.runHandoff(revision, beforeEnqueue.adapter)
    assert false: 'queue interruption was swallowed'
} catch (InterruptedException expected) {
    assert beforeEnqueue.events == ['lock', 'eligible', 'prepare', 'rejected', 'unlock']
}

List deployments = []
Closure deploy = { String role, String sha, String id ->
    deployments << [role, sha, id]
    2
}
try {
    handoff.consume('collector', release, release, deploy)
    assert false: 'accepted direct invocation'
} catch (IllegalArgumentException expected) {
    assert deployments.empty
}
Map actual = release + [trustedParent: true]
for (List row in [
    ['revision', 'e' * 40],
    ['imageId', 'sha256:' + 'e' * 64],
    ['agent', 'other-agent'],
    ['daemon', 'other-daemon'],
]) {
    def (field, value) = row
    try {
        handoff.consume('collector', release, actual + [(field): value], deploy)
        assert false: "accepted mismatched ${field}"
    } catch (IllegalArgumentException expected) {
        assert expected.message.endsWith(field)
        assert deployments.empty
    }
}
assert handoff.consume('collector', release, actual, deploy) == 2
assert deployments == [['collector', release.revision, release.imageId]]

println 'Backend handoff scenarios passed'
