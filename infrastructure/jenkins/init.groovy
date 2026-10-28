import jenkins.model.Jenkins
import hudson.security.HudsonPrivateSecurityRealm
import hudson.security.FullControlOnceLoggedInAuthorizationStrategy
import org.jenkinsci.plugins.workflow.job.WorkflowJob
import org.jenkinsci.plugins.workflow.cps.CpsScmFlowDefinition
import hudson.plugins.git.GitSCM
import hudson.plugins.git.BranchSpec
import hudson.plugins.git.UserRemoteConfig

def instance = Jenkins.get()
if (!(instance.securityRealm instanceof HudsonPrivateSecurityRealm)) {
  def realm = new HudsonPrivateSecurityRealm(false)
  realm.createAccount('admin', System.getenv('JENKINS_PASSWORD'))
  instance.setSecurityRealm(realm)
  def strategy = new FullControlOnceLoggedInAuthorizationStrategy()
  strategy.setAllowAnonymousRead(false)
  instance.setAuthorizationStrategy(strategy)
}
instance.setNumExecutors(1)
def job = instance.getItem('iot-exchange')
if (job == null) job = instance.createProject(WorkflowJob, 'iot-exchange')
job.setDefinition(new CpsScmFlowDefinition(new GitSCM([new UserRemoteConfig('https://github.com/1junting/iot-platform.git', null, null, null)], [new BranchSpec('*/feat/complete-exchange-platform')], false, [], null, null, []), 'Jenkinsfile'))
job.save()
instance.save()
