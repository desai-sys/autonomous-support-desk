#!/bin/bash
set -e
cd /opt/cloudops
aws s3 cp s3://cloudops-kb-6d55a7d0/cloudops-project.zip .
unzip -qo cloudops-project.zip
rm -f cloudops-project.zip
chown -R ec2-user:ec2-user /opt/cloudops
echo "Files extracted: $(ls app.py sre_portal.py)"

# Kill any existing streamlit
pkill -f streamlit 2>/dev/null || true
sleep 2

# Start fresh
cd /opt/cloudops
sudo -u ec2-user nohup python3.11 -m streamlit run app.py --server.port 8501 --server.address 0.0.0.0 --server.headless true > /tmp/app.log 2>&1 &
sudo -u ec2-user nohup python3.11 -m streamlit run sre_portal.py --server.port 8502 --server.address 0.0.0.0 --server.headless true > /tmp/sre.log 2>&1 &

sleep 5
echo "=== PORT CHECK ==="
ss -tlnp | grep -E '8501|8502' || echo "PORTS NOT LISTENING"
echo "=== DONE ==="
