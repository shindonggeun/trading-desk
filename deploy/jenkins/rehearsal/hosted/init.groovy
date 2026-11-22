// Test controller bootstrap only. Runtime isolation is checked by the driver.
import com.cloudbees.hudson.plugins.folder.Folder
import hudson.model.Item
import hudson.model.ParametersDefinitionProperty
import hudson.model.StringParameterDefinition
import hudson.security.ACL
import hudson.security.AuthorizationMatrixProperty
import hudson.security.LegacySecurityRealm
import hudson.security.ProjectMatrixAuthorizationStrategy
import hudson.security.csrf.DefaultCrumbIssuer
import jenkins.model.Jenkins
import org.jenkinsci.plugins.scriptsecurity.scripts.ScriptApproval
import org.jenkinsci.plugins.scriptsecurity.scripts.languages.GroovyLanguage
import org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition
import org.jenkinsci.plugins.workflow.job.WorkflowJob

def jenkins = Jenkins.get()
def fixture = new File('/opt/td-rehearsal')
def folder = jenkins.createProject(Folder, 'trading-desk-rehearsal')
jenkins.setNumExecutors(2)
jenkins.setSlaveAgentPort(-1)
// No fixture users or credentials. Use the standard anonymous request filters.
jenkins.setSecurityRealm(new LegacySecurityRealm())
jenkins.setCrumbIssuer(new DefaultCrumbIssuer(false))
def strategy = new ProjectMatrixAuthorizationStrategy()
strategy.add(Jenkins.READ, 'anonymous')
strategy.add(Item.READ, 'anonymous')
jenkins.setAuthorizationStrategy(strategy)

def create = { String name, List<String> names, String script, boolean operator, boolean sandbox ->
    def job = folder.createProject(WorkflowJob, name)
    job.addProperty(new ParametersDefinitionProperty(names.collect { new StringParameterDefinition(it, '') }))
    if (operator) {
        def acl = new AuthorizationMatrixProperty([
            (Item.BUILD): (['anonymous'] as Set),
            (Item.CANCEL): (['anonymous'] as Set),
        ])
        job.addProperty(acl)
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
def coordinator = create('coordinator', ['TD_CASE'], pipeline, true, true)
def competitor = create('competitor', ['TD_PARENT_BUILD', 'TD_QUEUE_ID', 'TD_CHILD_BUILD', 'TD_RECOVER'],
                        new File(fixture, 'competitor.groovy').getText('UTF-8'), true, false)
['api', 'collector', 'analyst'].each { role ->
    def job = create(role, ['TD_SHA', 'TD_IMAGE_ID', 'TD_AGENT', 'TD_DAEMON', 'TD_PARENT_BUILD', 'TD_CASE'],
                     pipeline, false, true)
    assert job.getACL().hasPermission2(Jenkins.ANONYMOUS2, Item.READ)
    assert !job.getACL().hasPermission2(Jenkins.ANONYMOUS2, Item.BUILD)
    assert !job.getACL().hasPermission2(Jenkins.ANONYMOUS2, Item.CANCEL)
    // The trusted fixture Pipeline runs under Jenkins' default internal identity.
    assert job.getACL().hasPermission2(ACL.SYSTEM2, Item.BUILD)
    assert job.getACL().hasPermission2(ACL.SYSTEM2, Item.CANCEL)
}
assert [coordinator, competitor].every { it.getACL().hasPermission2(Jenkins.ANONYMOUS2, Item.BUILD) &&
                                       it.getACL().hasPermission2(Jenkins.ANONYMOUS2, Item.CANCEL) }
assert !jenkins.getACL().hasPermission2(Jenkins.ANONYMOUS2, Jenkins.ADMINISTER)
jenkins.save()
new File(jenkins.rootDir, 'td-fixture-ready').text = 'ready\n'
