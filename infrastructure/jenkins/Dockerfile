FROM jenkins/jenkins:2.541.3-lts-jdk21
USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv git && rm -rf /var/lib/apt/lists/*
COPY requirements.lock /tmp/requirements.lock
RUN python3 -m venv /opt/iot-venv && /opt/iot-venv/bin/pip install --timeout 120 -r /tmp/requirements.lock
USER jenkins
RUN jenkins-plugin-cli --plugins workflow-aggregator git
USER root
COPY infrastructure/jenkins/init.groovy /usr/share/jenkins/ref/init.groovy.d/iot-exchange.groovy
USER jenkins
