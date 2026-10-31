#!/bin/bash
# =============================================================================
# CloudOps Command Center — EC2 Deployment Script
# Run this script ON the EC2 instance after SSH-ing in.
# =============================================================================

set -e  # Exit on any error

echo "=============================================="
echo "  CloudOps Command Center — EC2 Setup"
echo "=============================================="

# ---------------------------------------------------------------------------
# STEP 1: System updates and dependencies
# ---------------------------------------------------------------------------
echo ""
echo "[1/8] Updating system packages..."
sudo yum update -y 2>/dev/null || sudo apt-get update -y 2>/dev/null
sudo yum install -y python3 python3-pip python3-venv git unzip 2>/dev/null || \
  sudo apt-get install -y python3 python3-pip python3-venv unzip 2>/dev/null

echo "  ✅ System packages updated"

# ---------------------------------------------------------------------------
# STEP 2: Install AWS CLI v2
# ---------------------------------------------------------------------------
echo ""
echo "[2/8] Installing AWS CLI v2..."
if ! command -v aws &> /dev/null; then
    curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
    unzip -q awscliv2.zip
    sudo ./aws/install
    rm -rf awscliv2.zip aws/
    echo "  ✅ AWS CLI installed"
else
    echo "  ✅ AWS CLI already installed: $(aws --version)"
fi

# ---------------------------------------------------------------------------
# STEP 3: Create project directory
# ---------------------------------------------------------------------------
echo ""
echo "[3/8] Setting up project directory..."
PROJECT_DIR="/opt/cloudops"
sudo mkdir -p $PROJECT_DIR
sudo chown $USER:$USER $PROJECT_DIR
cd $PROJECT_DIR
echo "  ✅ Project directory: $PROJECT_DIR"

# ---------------------------------------------------------------------------
# STEP 4: Upload project files (from S3 or local)
# ---------------------------------------------------------------------------
echo ""
echo "[4/8] Downloading project files..."
# NOTE: Replace YOUR_BUCKET_NAME with your actual S3 bucket
# You should have uploaded the project zip to S3 first
# aws s3 cp s3://YOUR_BUCKET_NAME/cloudops-project.zip .
# unzip -q cloudops-project.zip

# If files are already here (uploaded via SCP), skip this step
if [ ! -f "requirements.txt" ]; then
    echo "  ⚠️  Project files not found. Please upload them first."
    echo "  Run from your local machine:"
    echo "    scp -i your-key.pem -r /path/to/project/* ec2-user@EC2_IP:/opt/cloudops/"
    exit 1
fi
echo "  ✅ Project files found"

# ---------------------------------------------------------------------------
# STEP 5: Python virtual environment
# ---------------------------------------------------------------------------
echo ""
echo "[5/8] Creating Python virtual environment..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip --quiet
echo "  ✅ Virtual environment created"

# ---------------------------------------------------------------------------
# STEP 6: Install Python dependencies
# ---------------------------------------------------------------------------
echo ""
echo "[6/8] Installing Python dependencies (this may take a few minutes)..."
pip install -r requirements.txt --quiet
pip install bedrock-agentcore-starter-toolkit bedrock-agentcore strands-agents --quiet
echo "  ✅ Dependencies installed"

# ---------------------------------------------------------------------------
# STEP 7: Create systemd service for Command Center (port 8501)
# ---------------------------------------------------------------------------
echo ""
echo "[7/8] Creating systemd services..."

sudo tee /etc/systemd/system/cloudops-command-center.service > /dev/null <<EOF
[Unit]
Description=CloudOps Command Center (Streamlit)
After=network.target

[Service]
Type=simple
User=$USER
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

# ---------------------------------------------------------------------------
# STEP 8: Create systemd service for SRE Portal (port 8502)
# ---------------------------------------------------------------------------
sudo tee /etc/systemd/system/cloudops-sre-portal.service > /dev/null <<EOF
[Unit]
Description=CloudOps SRE Resolution Portal (Streamlit)
After=network.target

[Service]
Type=simple
User=$USER
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

# Enable and start services
sudo systemctl daemon-reload
sudo systemctl enable cloudops-command-center
sudo systemctl enable cloudops-sre-portal
sudo systemctl start cloudops-command-center
sudo systemctl start cloudops-sre-portal

echo "  ✅ Systemd services created and started"

# ---------------------------------------------------------------------------
# Final status
# ---------------------------------------------------------------------------
echo ""
echo "=============================================="
echo "  ✅ DEPLOYMENT COMPLETE!"
echo "=============================================="
echo ""
echo "Service Status:"
sudo systemctl status cloudops-command-center --no-pager -l | head -5
echo ""
sudo systemctl status cloudops-sre-portal --no-pager -l | head -5
echo ""
echo "Access your apps at:"
EC2_IP=$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || echo "YOUR_EC2_IP")
echo "  Command Center : http://$EC2_IP:8501"
echo "  SRE Portal     : http://$EC2_IP:8502"
echo ""
echo "Useful commands:"
echo "  View logs  : sudo journalctl -u cloudops-command-center -f"
echo "  Restart    : sudo systemctl restart cloudops-command-center"
echo "  Stop       : sudo systemctl stop cloudops-command-center"
echo ""
