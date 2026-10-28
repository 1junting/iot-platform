pipeline {
  agent any
  options { disableConcurrentBuilds() }
  stages {
    stage('Contracts and isolated database tests') {
      steps {
        sh '''set +x
          export DATABASE_URL="$CI_DATABASE_URL"
          export PYTHONPATH="$WORKSPACE"
          /opt/iot-venv/bin/python -m pytest -q tests --junitxml=reports/junit.xml
        '''
      }
    }
    stage('Validate deployment files') {
      steps {
        sh '''/opt/iot-venv/bin/python -c "from pathlib import Path; import yaml; [list(yaml.safe_load_all(p.read_text())) for p in Path('deploy/kubernetes').glob('*.yaml')]"'''
      }
    }
  }
  post { always { archiveArtifacts artifacts: 'reports/junit.xml', allowEmptyArchive: true } }
}
