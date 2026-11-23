// Runs only in the isolated controller image.
import com.cloudbees.plugins.credentials.CredentialsScope
import com.cloudbees.plugins.credentials.SystemCredentialsProvider
import com.cloudbees.plugins.credentials.impl.UsernamePasswordCredentialsImpl
import hudson.model.BooleanParameterDefinition
import hudson.model.Item
import hudson.model.ParametersDefinitionProperty
import hudson.model.StringParameterDefinition
import hudson.plugins.git.BranchSpec
import hudson.plugins.git.GitSCM
import hudson.plugins.git.UserRemoteConfig
import hudson.security.AuthorizationMatrixProperty
import hudson.security.LegacySecurityRealm
import hudson.security.ProjectMatrixAuthorizationStrategy
import hudson.security.csrf.DefaultCrumbIssuer
import jenkins.model.Jenkins
import org.jenkinsci.plugins.plaincredentials.impl.StringCredentialsImpl
import org.jenkinsci.plugins.workflow.cps.CpsScmFlowDefinition
import org.jenkinsci.plugins.workflow.job.WorkflowJob
import hudson.util.Secret

def jenkins = Jenkins.get()
jenkins.setNumExecutors(1)
jenkins.setLabelString('builder deploy-frontend-prod')
jenkins.setSlaveAgentPort(-1)
jenkins.setQuietPeriod(0)
jenkins.setSecurityRealm(new LegacySecurityRealm())
jenkins.setCrumbIssuer(new DefaultCrumbIssuer(false))
def strategy = new ProjectMatrixAuthorizationStrategy()
strategy.add(Jenkins.READ, 'anonymous')
strategy.add(Item.READ, 'anonymous')
jenkins.setAuthorizationStrategy(strategy)

// Match the unchanged Jenkinsfile IDs with newly generated local fixture values.
def provider = SystemCredentialsProvider.getInstance()
provider.credentials.add(new UsernamePasswordCredentialsImpl(
    CredentialsScope.GLOBAL, 'trading-desk-github', 'Fixture only', 'fixture-user', UUID.randomUUID().toString()))
['trading-desk-vault-role-id', 'trading-desk-vault-secret-id'].each { id ->
    provider.credentials.add(new StringCredentialsImpl(
        CredentialsScope.GLOBAL, id, 'Fixture only', Secret.fromString(UUID.randomUUID().toString())))
}
provider.save()

def job = jenkins.createProject(WorkflowJob, 'td-web-rollback')
job.addProperty(new AuthorizationMatrixProperty([(Item.BUILD): (['anonymous'] as Set)]))
job.addProperty(new ParametersDefinitionProperty([
    new StringParameterDefinition('DEPLOY_COMMIT', ''),
    new BooleanParameterDefinition('FORCE_DEPLOY', false, ''),
    new BooleanParameterDefinition('FORCE_STOP_RUNS', false, ''),
    new BooleanParameterDefinition('ROLLBACK_DRILL', false, ''),
]))
def scm = new GitSCM([new UserRemoteConfig('http://127.0.0.1:8765/scm.git', null, null, null)],
                     [new BranchSpec('*/develop')], false, [], null, null, [])
def flow = new CpsScmFlowDefinition(scm, 'Jenkinsfile-web')
flow.setLightweight(false)
job.setDefinition(flow)
job.save()
jenkins.save()
new File(jenkins.rootDir, 'td-web-fixture-ready').text = 'ready\n'
