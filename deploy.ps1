# =============================================================================
# CloudOps Command Center - Production Deployment Script (v3)
# =============================================================================
# Credentials are read from environment variables (NEVER hardcode them here).
# Set them for the target account first, then run  .\deploy.ps1 :
#
#   $env:AWS_ACCESS_KEY_ID     = "<your access key>"
#   $env:AWS_SECRET_ACCESS_KEY = "<your secret key>"
#   $env:AWS_REGION            = "us-east-1"   # optional, defaults to us-east-1
#
# See MIGRATION_GUIDE.md for the full per-account walkthrough.
# =============================================================================

$AWS_ACCESS_KEY_ID     = $env:AWS_ACCESS_KEY_ID
$AWS_SECRET_ACCESS_KEY = $env:AWS_SECRET_ACCESS_KEY
$AWS_REGION            = if ($env:AWS_REGION) { $env:AWS_REGION } else { "us-east-1" }

# =============================================================================
# DO NOT EDIT BELOW THIS LINE
# =============================================================================

$ErrorActionPreference = "Continue"
$totalSteps = 9

function Write-Step($n, $t) { Write-Host "`n[$n/$totalSteps] $t" -ForegroundColor Cyan; Write-Host ("=" * 60) -ForegroundColor DarkGray }
function Write-Ok($t) { Write-Host "  [OK] $t" -ForegroundColor Green }
function Write-Warn($t) { Write-Host "  [WARN] $t" -ForegroundColor Yellow }

Write-Host "`n==============================================================" -ForegroundColor Cyan
Write-Host "  CloudOps Command Center - Multi-Agent Production Deploy" -ForegroundColor White
Write-Host "==============================================================" -ForegroundColor Cyan

if (-not $AWS_ACCESS_KEY_ID -or -not $AWS_SECRET_ACCESS_KEY) {
    Write-Host "`n  ERROR: Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY env vars first!" -ForegroundColor Red
    Write-Host "  Example:" -ForegroundColor Gray
    Write-Host '    $env:AWS_ACCESS_KEY_ID = "..."; $env:AWS_SECRET_ACCESS_KEY = "..."' -ForegroundColor Gray
    Write-Host "  See MIGRATION_GUIDE.md for details.`n" -ForegroundColor Gray
    exit 1
}

# --- Step 1: AWS CLI ---
Write-Step 1 "Configuring AWS credentials"
$env:AWS_ACCESS_KEY_ID = $AWS_ACCESS_KEY_ID
$env:AWS_SECRET_ACCESS_KEY = $AWS_SECRET_ACCESS_KEY
$env:AWS_DEFAULT_REGION = $AWS_REGION
$awsDir = "$env:USERPROFILE\.aws"
if (-not (Test-Path $awsDir)) { New-Item -ItemType Directory -Path $awsDir -Force | Out-Null }
Set-Content "$awsDir\credentials" "[default]`naws_access_key_id = $AWS_ACCESS_KEY_ID`naws_secret_access_key = $AWS_SECRET_ACCESS_KEY" -Encoding ASCII
Set-Content "$awsDir\config" "[default]`nregion = $AWS_REGION`noutput = json" -Encoding ASCII
$accountId = (aws sts get-caller-identity --query "Account" --output text 2>&1)
if ($LASTEXITCODE -ne 0) { Write-Host "  ERROR: Invalid credentials!" -ForegroundColor Red; exit 1 }
Write-Ok "Account: $accountId"

# --- Step 2: Python venv ---
Write-Step 2 "Python virtual environment"
$venvPy = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
$venvPip = Join-Path $PSScriptRoot "venv\Scripts\pip.exe"
$venvAC = Join-Path $PSScriptRoot "venv\Scripts\agentcore.exe"

# Find a working Python interpreter (skip Microsoft Store stub)
$pyExe = $null
$userProfile = $env:USERPROFILE
foreach ($candidate in @(
    "$userProfile\AppData\Local\Python\bin\python3.14.exe",
    "$userProfile\AppData\Local\Python\bin\python3.exe",
    "$userProfile\AppData\Local\Python\bin\python.exe",
    "C:\Python311\python.exe",
    "C:\Python310\python.exe"
)) {
    if (Test-Path $candidate) { $pyExe = $candidate; break }
}
if (-not $pyExe) {
    # Fallback: try 'python' from PATH and check it's real
    $pyExe = (Get-Command python -ErrorAction SilentlyContinue | Where-Object { $_.Source -notmatch "WindowsApps" }).Source
}
if (-not $pyExe) { Write-Host "  ERROR: Cannot find Python!" -ForegroundColor Red; exit 1 }

if (-not (Test-Path $venvPy)) {
    & $pyExe -m venv venv
}
& $venvPip install --quiet -r requirements.txt 2>&1 | Out-Null
& $venvPip install --quiet bedrock-agentcore-starter-toolkit bedrock-agentcore strands-agents 2>&1 | Out-Null
Write-Ok "venv ready (Python: $pyExe)"

# --- Step 3: Infrastructure ---
Write-Step 3 "AWS infrastructure (DynamoDB, S3, IAM)"
& $venvPy scripts/setup_aws_infrastructure.py 2>&1 | Out-Null
Write-Ok "Infrastructure created"

# --- Step 4: Bedrock models ---
Write-Step 4 "Bedrock model access"
$models = aws bedrock list-foundation-models --region $AWS_REGION --query "modelSummaries[?contains(modelId,'nova')]" --output text 2>&1
if ($models -match "nova-pro" -and $models -match "nova-micro") { Write-Ok "Nova Micro + Pro available" }
else { Write-Warn "Enable Nova models in Bedrock Console -> Model access" }

# --- Step 5: Deploy AgentCore ---
Write-Step 5 "Deploying AgentCore agent (~60s)"
$null = & $venvAC configure --entrypoint src/agentcore_entrypoint.py --non-interactive 2>&1
$deployOut = (& $venvAC deploy 2>&1) -join " "

# Extract ARN - strip box-drawing chars from output then regex match
$agentArn = ""

# Method 1: From deploy output (strip special chars first)
$cleanDeploy = $deployOut -replace '[^a-zA-Z0-9:/_\-\.\s]', ''
$m1 = [regex]::Match($cleanDeploy, 'src_agentcore_entrypoint-[A-Za-z0-9]+')
if ($m1.Success) {
    $agentArn = "arn:aws:bedrock-agentcore:${AWS_REGION}:${accountId}:runtime/$($m1.Value)"
}

# Method 2: From agentcore status
if (-not $agentArn) {
    $statusOut = (& $venvAC status 2>&1) -join " "
    $cleanStatus = $statusOut -replace '[^a-zA-Z0-9:/_\-\.\s]', ''
    $m2 = [regex]::Match($cleanStatus, 'src_agentcore_entrypoint-[A-Za-z0-9]+')
    if ($m2.Success) {
        $agentArn = "arn:aws:bedrock-agentcore:${AWS_REGION}:${accountId}:runtime/$($m2.Value)"
    }
}

# Method 3: From existing config.py
if (-not $agentArn) {
    $cfgMatch = Select-String -Path "config.py" -Pattern 'AGENT_ARN = "(.+)"'
    if ($cfgMatch) {
        $existing = $cfgMatch.Matches.Groups[1].Value
        if ($existing -and $existing -ne "UPDATE_AFTER_AGENT_DEPLOYMENT") { $agentArn = $existing }
    }
}

if ($agentArn) { Write-Ok "Agent: $agentArn" }
else { Write-Warn "ARN detection failed. Run: agentcore status" }

# Attach PowerUserAccess to the AgentCore runtime role (name/suffix is account-specific — discover it)
$runtimeRole = aws iam list-roles --query "Roles[?starts_with(RoleName, 'AmazonBedrockAgentCoreSDKRuntime')].RoleName" --output text 2>&1
if ($runtimeRole -and $LASTEXITCODE -eq 0) {
    foreach ($rr in ($runtimeRole -split '\s+')) {
        if ($rr) { aws iam attach-role-policy --role-name $rr --policy-arn "arn:aws:iam::aws:policy/PowerUserAccess" 2>&1 | Out-Null }
    }
    Write-Ok "PowerUserAccess attached to runtime role(s): $runtimeRole"
} else {
    Write-Warn "Could not find AgentCore runtime role. Attach PowerUserAccess manually (see MIGRATION_GUIDE.md Section 5.2)."
}

# --- Step 6: Update config ---
Write-Step 6 "Updating config.py with Agent ARN"
$cfgContent = Get-Content "config.py" -Raw -Encoding UTF8
$cfgContent = $cfgContent -replace 'AGENT_ARN = ".*?"', "AGENT_ARN = `"$agentArn`""
Set-Content "config.py" -Value $cfgContent -Encoding UTF8
Write-Ok "config.py updated"

# --- Step 7: Test agent ---
Write-Step 7 "Testing agent"
Start-Sleep 5
$testOut = (& $venvAC invoke '{"prompt":"hello","domain":"auto"}' 2>&1) -join "`n"
if ($testOut -match "response|Response") { Write-Ok "Agent responding!" }
else { Write-Warn "Agent warming up. Test manually in 1 min." }

# --- Step 8: Launch EC2 + Elastic IP ---
Write-Step 8 "Launching EC2 with Elastic IP"

# Key pair
$keyName = "cloudops-key"
$keyFile = Join-Path $PSScriptRoot "$keyName.pem"
$existKey = aws ec2 describe-key-pairs --key-names $keyName 2>&1
if ($LASTEXITCODE -ne 0) {
    $keyMat = aws ec2 create-key-pair --key-name $keyName --query "KeyMaterial" --output text 2>&1
    Set-Content $keyFile -Value $keyMat -Encoding ASCII
    icacls $keyFile /inheritance:r /grant:r "${env:USERNAME}:(R)" 2>&1 | Out-Null
    Write-Ok "Key pair: $keyFile"
} else { Write-Host "  Key exists" -ForegroundColor Gray }

# Security group
$sgName = "cloudops-streamlit-sg"
$sgId = aws ec2 describe-security-groups --filters "Name=group-name,Values=$sgName" --query "SecurityGroups[0].GroupId" --output text 2>&1
if ($sgId -eq "None" -or $LASTEXITCODE -ne 0) {
    $vpcId = aws ec2 describe-vpcs --filters "Name=is-default,Values=true" --query "Vpcs[0].VpcId" --output text 2>&1
    $sgId = aws ec2 create-security-group --group-name $sgName --description "CloudOps" --vpc-id $vpcId --query "GroupId" --output text 2>&1
    aws ec2 authorize-security-group-ingress --group-id $sgId --protocol tcp --port 22 --cidr 0.0.0.0/0 2>&1 | Out-Null
    aws ec2 authorize-security-group-ingress --group-id $sgId --protocol tcp --port 8501 --cidr 0.0.0.0/0 2>&1 | Out-Null
    aws ec2 authorize-security-group-ingress --group-id $sgId --protocol tcp --port 8502 --cidr 0.0.0.0/0 2>&1 | Out-Null
}

# IAM instance profile
$ec2Role = "CloudOpsEC2Role"
$profCheck = aws iam get-instance-profile --instance-profile-name $ec2Role 2>&1
if ($LASTEXITCODE -ne 0) {
    $tp = '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
    aws iam create-role --role-name $ec2Role --assume-role-policy-document $tp 2>&1 | Out-Null
    aws iam attach-role-policy --role-name $ec2Role --policy-arn "arn:aws:iam::aws:policy/PowerUserAccess" 2>&1 | Out-Null
    aws iam create-instance-profile --instance-profile-name $ec2Role 2>&1 | Out-Null
    aws iam add-role-to-instance-profile --instance-profile-name $ec2Role --role-name $ec2Role 2>&1 | Out-Null
    Start-Sleep 10
}

# AMI
$amiId = aws ec2 describe-images --owners amazon --filters "Name=name,Values=al2023-ami-2023*-x86_64" "Name=state,Values=available" --query "sort_by(Images, &CreationDate)[-1].ImageId" --output text 2>&1

# Launch
$instanceId = aws ec2 run-instances --image-id $amiId --instance-type t3.medium --key-name $keyName --security-group-ids $sgId --iam-instance-profile "Name=$ec2Role" --metadata-options "HttpTokens=optional,HttpEndpoint=enabled" --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=cloudops-streamlit}]" --query "Instances[0].InstanceId" --output text 2>&1
Write-Ok "Instance: $instanceId"
aws ec2 wait instance-running --instance-ids $instanceId 2>&1 | Out-Null

# Elastic IP
$allocId = aws ec2 allocate-address --domain vpc --query "AllocationId" --output text 2>&1
$elasticIp = aws ec2 describe-addresses --allocation-ids $allocId --query "Addresses[0].PublicIp" --output text 2>&1
aws ec2 associate-address --instance-id $instanceId --allocation-id $allocId 2>&1 | Out-Null
Write-Ok "Elastic IP: $elasticIp (permanent)"

# Wait for SSH
Write-Host "  Waiting 30s for SSH..." -ForegroundColor Gray
Start-Sleep 30

# Fix key permissions
icacls $keyFile /inheritance:r /grant:r "${env:USERNAME}:(R)" 2>&1 | Out-Null

# --- Step 9: Deploy apps to EC2 ---
Write-Step 9 "Deploying apps to EC2 (install + start)"

# Create project zip
if (Test-Path "cloudops-project.zip") { Remove-Item "cloudops-project.zip" -Force }
Compress-Archive -Path @("app.py","sre_portal.py","learning_loop.py","config.py","requirements.txt","src","scripts") -DestinationPath "cloudops-project.zip" -Force

# SCP zip + key setup script to instance
scp -i $keyFile -o StrictHostKeyChecking=no "cloudops-project.zip" "ec2-user@${elasticIp}:/tmp/" 2>&1 | Out-Null
Write-Host "  Project uploaded to EC2" -ForegroundColor Gray

# Run setup remotely via single SSH command with heredoc-style script
$setupScript = @"
#!/bin/bash
set -e
sudo yum install -y python3.11 python3.11-pip unzip > /dev/null 2>&1
sudo mkdir -p /opt/cloudops
cd /opt/cloudops
sudo cp /tmp/cloudops-project.zip .
sudo unzip -qo cloudops-project.zip
sudo rm -f cloudops-project.zip
sudo chown -R ec2-user:ec2-user /opt/cloudops
# AWS credentials are provided by the CloudOpsEC2Role instance profile (PowerUserAccess)
# attached at launch. Do NOT write long-lived keys onto the instance — only set the region.
mkdir -p ~/.aws
echo '[default]' > ~/.aws/config
echo 'region = $AWS_REGION' >> ~/.aws/config
pip3.11 install streamlit boto3 pydantic structlog -q 2>/dev/null
cd /opt/cloudops
nohup python3.11 -m streamlit run app.py --server.port 8501 --server.address 0.0.0.0 --server.headless true > /tmp/app.log 2>&1 &
nohup python3.11 -m streamlit run sre_portal.py --server.port 8502 --server.address 0.0.0.0 --server.headless true > /tmp/sre.log 2>&1 &
echo SETUP_DONE
"@

# Write script to temp file and SCP it
$scriptFile = Join-Path $env:TEMP "cloudops-setup.sh"
Set-Content $scriptFile -Value $setupScript -Encoding ASCII -NoNewline
scp -i $keyFile -o StrictHostKeyChecking=no $scriptFile "ec2-user@${elasticIp}:/tmp/setup.sh" 2>&1 | Out-Null
ssh -i $keyFile -o StrictHostKeyChecking=no "ec2-user@$elasticIp" "chmod +x /tmp/setup.sh && bash /tmp/setup.sh" 2>&1 | Out-Null
Remove-Item $scriptFile -Force -ErrorAction SilentlyContinue

Write-Host "  Waiting for apps to start..." -ForegroundColor Gray

# Poll until HTTP 200
$ready = $false
for ($i = 1; $i -le 20; $i++) {
    Start-Sleep 10
    try {
        $r = Invoke-WebRequest -Uri "http://${elasticIp}:8501" -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch {}
    Write-Host "  [$($i*10)s] Still starting..." -ForegroundColor Gray
}

# =============================================================================
# RESULT
# =============================================================================
Write-Host ""
if ($ready) {
    Write-Host "==============================================================" -ForegroundColor Green
    Write-Host "  DEPLOYMENT COMPLETE - VERIFIED WORKING" -ForegroundColor Green
    Write-Host "==============================================================" -ForegroundColor Green
} else {
    Write-Host "==============================================================" -ForegroundColor Yellow
    Write-Host "  DEPLOYMENT DONE - Apps may need 1-2 more minutes" -ForegroundColor Yellow
    Write-Host "==============================================================" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "  Account:     $accountId" -ForegroundColor White
Write-Host "  Agent ARN:   $agentArn" -ForegroundColor White
Write-Host "  Elastic IP:  $elasticIp (permanent - never changes)" -ForegroundColor White
Write-Host ""
Write-Host "  Command Center: http://${elasticIp}:8501" -ForegroundColor Cyan
Write-Host "  SRE Portal:     http://${elasticIp}:8502" -ForegroundColor Cyan
Write-Host ""
Write-Host "  SSH: ssh -i $keyFile ec2-user@$elasticIp" -ForegroundColor Gray
Write-Host "  Teardown: .\teardown.ps1 --confirm" -ForegroundColor Gray
Write-Host ""
Write-Host "==============================================================" -ForegroundColor Green
