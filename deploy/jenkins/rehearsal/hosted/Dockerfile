FROM jenkins/jenkins:lts-jdk21
USER jenkins
RUN jenkins-plugin-cli --plugins workflow-aggregator cloudbees-folder lockable-resources matrix-auth
COPY init.groovy /usr/share/jenkins/ref/init.groovy.d/90-controller-fixture.groovy
COPY pipeline.groovy competitor.groovy backend-handoff.groovy backend-rehearsal.groovy /opt/td-rehearsal/
