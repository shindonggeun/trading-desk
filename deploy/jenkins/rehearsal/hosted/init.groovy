// Test controller bootstrap only. Runtime isolation is checked by the driver.
import com.cloudbees.hudson.plugins.folder.Folder
import hudson.model.Computer
import hudson.model.Item
import hudson.model.Node
import hudson.model.ParametersDefinitionProperty
import hudson.model.StringParameterDefinition
import hudson.security.AuthorizationMatrixProperty
import hudson.security.HudsonPrivateSecurityRealm
import hudson.security.ProjectMatrixAuthorizationStrategy
import hudson.security.csrf.DefaultCrumbIssuer
import hudson.slaves.DumbSlave
import hudson.slaves.JNLPLauncher
import java.nio.file.Files
import java.nio.file.attribute.PosixFilePermissions
import jenkins.model.Jenkins
import jenkins.security.QueueItemAuthenticatorConfiguration
import org.jenkinsci.plugins.authorizeproject.AuthorizeProjectProperty
import org.jenkinsci.plugins.authorizeproject.ProjectQueueItemAuthenticator
import org.jenkinsci.plugins.authorizeproject.strategy.SpecificUsersAuthorizationStrategy
import org.jenkinsci.plugins.scriptsecurity.scripts.ScriptApproval
import org.jenkinsci.plugins.scriptsecurity.scripts.languages.GroovyLanguage
import org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition
import org.jenkinsci.plugins.workflow.job.WorkflowJob

def jenkins = Jenkins.get()
def fixture = new File('/opt/td-rehearsal')
def folder = jenkins.createProject(Folder, 'trading-desk-rehearsal')
jenkins.setNumExecutors(2)
jenkins.setSlaveAgentPort(-1)
def realm = new HudsonPrivateSecurityRealm(false)
def parentId = 'fixture-coordinator'
def childId = 'fixture-child'
def parentUser = realm.createAccount(parentId, UUID.randomUUID().toString())
def childUser = realm.createAccount(childId, UUID.randomUUID().toString())
jenkins.setSecurityRealm(realm)
jenkins.setCrumbIssuer(new DefaultCrumbIssuer(false))
def strategy = new ProjectMatrixAuthorizationStrategy()
strategy.add(Jenkins.READ, 'anonymous')
strategy.add(Item.READ, 'anonymous')
[parentId, childId].each { id ->
    strategy.add(Jenkins.READ, id)
    strategy.add(Item.READ, id)
    strategy.add(Computer.BUILD, id)
}
jenkins.setAuthorizationStrategy(strategy)

def authConfig = QueueItemAuthenticatorConfiguration.get()
authConfig.authenticators.add(new ProjectQueueItemAuthenticator(
    [SpecificUsersAuthorizationStrategy.name] as Set, [] as Set))

// One disposable WebSocket agent connects via the container's loopback interface.
def agent = new DumbSlave('td-loss-agent', '/tmp/td-loss-agent', new JNLPLauncher())
agent.setMode(Node.Mode.EXCLUSIVE)
jenkins.addNode(agent)
def secret = new File(jenkins.rootDir, 'td-loss-agent-connection').toPath()
Files.createFile(secret, PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString('rw-------')))
Files.writeString(secret, agent.toComputer().getJnlpMac())

def create = { String name, List<String> names, String script, String caller, String runAs, boolean sandbox ->
    def job = folder.createProject(WorkflowJob, name)
    job.addProperty(new ParametersDefinitionProperty(names.collect { new StringParameterDefinition(it, '') }))
    job.addProperty(new AuthorizationMatrixProperty([
        (Item.BUILD): ([caller] as Set),
        (Item.CANCEL): ([caller] as Set),
    ]))
    if (runAs) {
        job.addProperty(new AuthorizeProjectProperty(new SpecificUsersAuthorizationStrategy(runAs)))
    }
    if (!sandbox) {
        // Only this vetted fixture source needs Queue.LeftItem/Run inspection.
        ScriptApproval.get().preapprove(script, GroovyLanguage.get())
        ScriptApproval.get().save()
    }
    job.setDefinition(new CpsFlowDefinition(script, sandbox))
    job.save()
    return job
}

def pipeline = new File(fixture, 'pipeline.groovy').getText('UTF-8')
def coordinator = create('coordinator', ['TD_CASE'], pipeline, 'anonymous', parentId, true)
def denied = create('denied-coordinator', ['TD_CASE'], '''
build(job: '/trading-desk-rehearsal/api', wait: true, propagate: false)
''', 'anonymous', childId, true)
def competitor = create('competitor', ['TD_PARENT_BUILD', 'TD_QUEUE_ID', 'TD_CHILD_BUILD', 'TD_RECOVER',
                                       'TD_VERIFY_IDENTITIES'],
                        new File(fixture, 'competitor.groovy').getText('UTF-8'), 'anonymous', null, false)
def parentAuth = parentUser.impersonate2()
def childAuth = childUser.impersonate2()
['api', 'collector', 'analyst'].each { role ->
    def job = create(role, ['TD_SHA', 'TD_IMAGE_ID', 'TD_AGENT', 'TD_DAEMON', 'TD_PARENT_BUILD', 'TD_CASE'],
                     pipeline, parentId, childId, true)
    assert job.getACL().hasPermission2(Jenkins.ANONYMOUS2, Item.READ)
    assert !job.getACL().hasPermission2(Jenkins.ANONYMOUS2, Item.BUILD)
    assert !job.getACL().hasPermission2(Jenkins.ANONYMOUS2, Item.CANCEL)
    assert job.getACL().hasPermission2(parentAuth, Item.BUILD)
    assert job.getACL().hasPermission2(parentAuth, Item.CANCEL)
    [parentAuth, childAuth].each { auth -> assert !job.getACL().hasPermission2(auth, Item.CONFIGURE) }
    assert !job.getACL().hasPermission2(childAuth, Item.BUILD)
    assert !job.getACL().hasPermission2(childAuth, Item.CANCEL)
}
assert [coordinator, denied, competitor].every { it.getACL().hasPermission2(Jenkins.ANONYMOUS2, Item.BUILD) &&
                                                it.getACL().hasPermission2(Jenkins.ANONYMOUS2, Item.CANCEL) }
assert [Jenkins.ANONYMOUS2, parentAuth, childAuth].every { !jenkins.getACL().hasPermission2(it, Jenkins.ADMINISTER) }
jenkins.save()
new File(jenkins.rootDir, 'td-fixture-ready').text = 'ready\n'
