FROM jenkins/jenkins:lts-jdk21

USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/* \
    && install -d -o jenkins -g jenkins /opt/td-web-fixture

USER jenkins
RUN jenkins-plugin-cli --plugins workflow-aggregator credentials-binding git lockable-resources matrix-auth
COPY init.groovy /usr/share/jenkins/ref/init.groovy.d/90-web-rollback-fixture.groovy
COPY --chown=jenkins:jenkins seed.sh fake-docker.py wait-ci.sh vault-env.sh Jenkinsfile-web Jenkinsfile.deploy-common.groovy /opt/td-web-fixture/
COPY --chown=jenkins:jenkins scripts/deploy-compose.sh /opt/td-web-fixture/deploy-compose.sh
RUN bash /opt/td-web-fixture/seed.sh
