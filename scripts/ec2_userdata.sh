#!/bin/bash
set -ex

# Log everything
exec > /var/log/cloudops-setup.log 2>&1

echo "=== CloudOps EC2 Setup Starting ==="
date

# System updates
yum update -y
yum install -y python3 python3-pip python3-devel git unzip gcc

# Install AWS CLI v2
if ! command -v aws &> /dev/null; then
    curl -s "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
    unzip -q awscliv2.zip
    ./aws/install
    rm -rf awscliv2.zip aws/
fi

# Create project directory
PROJECT_DIR="/opt/cloudops"
mkdir -p $PROJECT_DIR

# Download project from S3
BUCKET=$(aws s3 ls | grep cloudops-kb | awk '{print $3}' | head -1)
if [ -n "$BUCKET" ]; then
    echo "Downloading from s3://$BUCKET/cloudops-project.zip"
    aws s3 cp "s3://$BUCKET/cloudops-project.zip" "$PROJECT_DIR/cloudops-project.zip"
    cd $PROJECT_DIR
    unzip -qo cloudops-project.zip
    rm -f cloudops-project.zip
else
    echo "ERROR: No cloudops S3 bucket found!"
    exit 1
fi

cd $PROJECT_DIR

# Python virtual environment
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
pip install bedrock-agentcore-starter-toolkit bedrock-agentcore strands-agents --quiet 2>/dev/null || true

# Create systemd service for Command Center (port 8501)
cat > /etc/systemd/system/cloudops-command-center.service <<EOF
[Unit]
Description=CloudOps Command Center (Streamlit)
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=$PROJECT_DIR
Environment="PATH=$PROJECT_DIR/venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="AWS_DEFAULT_REGION=us-east-1"
ExecStart=$PROJECT_DIR/venv/bin/streamlit run app.py --server.port 8501 --server.address 0.0.0.0 --server.headless true
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Create systemd service for SRE Portal (port 8502)
cat > /etc/systemd/system/cloudops-sre-portal.service <<EOF
[Unit]
Description=CloudOps SRE Portal (Streamlit)
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=$PROJECT_DIR
Environment="PATH=$PROJECT_DIR/venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="AWS_DEFAULT_REGION=us-east-1"
ExecStart=$PROJECT_DIR/venv/bin/streamlit run sre_portal.py --server.port 8502 --server.address 0.0.0.0 --server.headless true
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Fix ownership
chown -R ec2-user:ec2-user $PROJECT_DIR

# Enable and start services
systemctl daemon-reload
systemctl enable cloudops-command-center cloudops-sre-portal
systemctl start cloudops-command-center
systemctl start cloudops-sre-portal

echo "=== CloudOps EC2 Setup Complete ==="
date
echo "Command Center: port 8501"
echo "SRE Portal: port 8502"
