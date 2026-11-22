// The driver supplies only numeric IDs; the observed controller items are fixed.
import com.cloudbees.groovy.cps.NonCPS
import hudson.model.Cause
import hudson.model.Queue
import jenkins.model.Jenkins
import org.jenkinsci.plugins.workflow.job.WorkflowJob

lock(resource: 'trading-desk-prod-deploy') {
    def evidence = observe(params.TD_PARENT_BUILD, params.TD_QUEUE_ID, params.TD_CHILD_BUILD,
                           params.TD_RECOVER == 'yes')
    echo evidence
}

@NonCPS
String observe(String parentBuild, String queueId, String childBuild, boolean recover) {
    def jenkins = Jenkins.get()
    def api = jenkins.getItemByFullName('trading-desk-rehearsal/api', WorkflowJob)
    assert parentBuild ==~ /[1-9][0-9]*/
    assert !!queueId != !!childBuild
    String evidence
    if (queueId) {
        assert queueId ==~ /[1-9][0-9]*/
        def item = jenkins.queue.getItem(Long.parseLong(queueId))
        // An item absent from the active queue may have started. Require the
        // controller's actual cancelled LeftItem, not mere absence.
        assert item instanceof Queue.LeftItem && item.task == api && item.cancelled
        assert matchesParent(item.causes, parentBuild)
        evidence = "queue=${queueId} cancelled"
    } else {
        assert childBuild ==~ /[1-9][0-9]*/
        def child = api.getBuildByNumber(Integer.parseInt(childBuild))
        assert child != null && !child.building && child.result?.toString() == 'ABORTED'
        assert matchesParent(child.causes, parentBuild)
        evidence = "api #${childBuild} ABORTED"
    }
    def marker = new File(jenkins.rootDir, 'td-fixture-active')
    assert marker.getText('UTF-8').trim() == parentBuild
    if (recover) {
        assert marker.delete()
    }
    return "TD_COMPETITOR_OBSERVED_TERMINAL parent=${parentBuild} ${evidence} recovered=${recover}"
}

@NonCPS
boolean matchesParent(List causes, String parentBuild) {
    return causes.size() == 1 && causes[0] instanceof Cause.UpstreamCause &&
           causes[0].upstreamProject == 'trading-desk-rehearsal/coordinator' &&
           causes[0].upstreamBuild.toString() == parentBuild
}
