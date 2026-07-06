pipeline {
    agent any

    environment {
        REMOTE_USER = 'roconnor'
        REMOTE_HOST = '192.168.2.1'
        REMOTE_DIR  = '/home/pi/networkswitcher'
        SSH_CRED_ID = 'rpi3wifi-deploy-key'
    }

    stages {
        stage('Prepare') {
            steps {
                // Populate known_hosts to avoid interactive host-key prompts
                sh 'mkdir -p ~/.ssh && chmod 700 ~/.ssh'
                sh 'ssh-keyscan -H ${REMOTE_HOST} >> ~/.ssh/known_hosts'
            }
        }

        stage('Sync') {
            steps {
                sshagent(credentials: [SSH_CRED_ID]) {
                    sh 'chmod +x scripts/deploy.sh'
                    sh 'scripts/deploy.sh sync'
                }
            }
        }

        stage('Install') {
            steps {
                sshagent(credentials: [SSH_CRED_ID]) {
                    sh 'scripts/deploy.sh install'
                }
            }
        }
    }

    post {
        success { echo 'Deploy complete. networkswitcher service restarted on the Pi.' }
        failure { echo 'Pipeline failed. Check stage logs above.' }
    }
}
