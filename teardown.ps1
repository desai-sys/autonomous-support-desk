# =============================================================================
# CloudOps Command Center - Complete Teardown
# =============================================================================
# Usage:
#   .\teardown.ps1              # Dry run (preview)
#   .\teardown.ps1 --confirm    # Actually delete everything
# =============================================================================

$ErrorActionPreference = "Continue"
$DryRun = $true
if ($args -contains "--confirm") { $DryRun = $false }
$region = "us-east-1"

function Do-It($desc, [scriptblock]$action) {
    if ($DryRun) { Write-Host "  [DRY] Would: $desc" -ForegroundColor Yellow }
    else { try { & $action; Write-Host "  [OK] $desc" -ForegroundColor Green } catch { Write-Host "  [SKIP] $desc" -ForegroundColor Gray } }
}

Write-Host ""
if ($DryRun) {
    Write-Host "=== TEARDOWN DRY RUN (no changes) ===" -ForegroundColor Yellow
} else {
    Write-Host "=== TEARDOWN - LIVE DELETE ===" -ForegroundColor Red
    Write-Host "  Deleting ALL CloudOps resources in 5 seconds... Ctrl+C to abort" -ForegroundColor Red
    Start-Sleep 5
}

# 1. Destroy AgentCore
Write-Host "`n[1/7] AgentCore agent" -ForegroundColor Cyan
$ac = Join-Path $PSScriptRoot "venv\Scripts\agentcore.exe"
if (Test-Path $ac) { Do-It "Destroy AgentCore agent" { & $ac destroy 2>&1 | Out-Null } }
else { Write-Host "  [SKIP] agentcore not found" -ForegroundColor Gray }

# 2. Terminate EC2 + release Elastic IP
Write-Host "`n[2/7] EC2 instances + Elastic IPs" -ForegroundColor Cyan
$instances = aws ec2 describe-instances --filters "Name=tag:Name,Values=cloudops*" "Name=instance-state-name,Values=running,stopped" --query "Reservations[].Instances[].InstanceId" --output text --region $region 2>&1
if ($instances -and $instances -ne "None") {
    foreach ($iid in ($instances -split '\s+')) {
        if ($iid -match "^i-") { Do-It "Terminate $iid" { aws ec2 terminate-instances --instance-ids $iid --region $region 2>&1 | Out-Null } }
    }
}
# Release Elastic IPs
$eips = aws ec2 describe-addresses --query "Addresses[?Tags[?Value=='cloudops-streamlit'] || !Tags].AllocationId" --output text --region $region 2>&1
# Better: release all unassociated EIPs or those tagged cloudops
$allEips = aws ec2 describe-addresses --query "Addresses[].{AllocId:AllocationId,InstId:InstanceId}" --output json --region $region 2>&1
if ($allEips -and $LASTEXITCODE -eq 0) {
    $eipList = $allEips | ConvertFrom-Json
    foreach ($eip in $eipList) {
        if (-not $eip.InstId) {
            Do-It "Release Elastic IP $($eip.AllocId)" { aws ec2 release-address --allocation-id $eip.AllocId --region $region 2>&1 | Out-Null }
        }
    }
}

# 3. DynamoDB tables
Write-Host "`n[3/7] DynamoDB tables" -ForegroundColor Cyan
$tables = @("CloudOpsTickets","CloudOpsUsers","CloudOpsTenants","CloudOpsUsage","CloudOpsAuditLog")
foreach ($t in $tables) {
    $exists = aws dynamodb describe-table --table-name $t --region $region 2>&1
    if ($LASTEXITCODE -eq 0) { Do-It "Delete table $t" { aws dynamodb delete-table --table-name $t --region $region 2>&1 | Out-Null } }
}

# 4. S3 bucket
Write-Host "`n[4/7] S3 bucket" -ForegroundColor Cyan
$bucket = ""
if (Test-Path "config.py") { $m = Select-String -Path "config.py" -Pattern 'S3_BUCKET_NAME = "(.+)"'; if ($m) { $bucket = $m.Matches.Groups[1].Value } }
if ($bucket) {
    Do-It "Empty + delete S3 bucket $bucket" {
        aws s3 rm "s3://$bucket" --recursive --region $region 2>&1 | Out-Null
        aws s3api delete-bucket --bucket $bucket --region $region 2>&1 | Out-Null
    }
}

# 5. IAM roles
Write-Host "`n[5/7] IAM roles + instance profiles" -ForegroundColor Cyan
$roles = aws iam list-roles --query "Roles[?contains(RoleName,'CloudOps')].RoleName" --output text 2>&1
if ($roles -and $LASTEXITCODE -eq 0) {
    foreach ($r in ($roles -split '\s+')) {
        if (-not $r) { continue }
        Do-It "Delete IAM role $r" {
            $pols = aws iam list-attached-role-policies --role-name $r --query "AttachedPolicies[].PolicyArn" --output text 2>&1
            foreach ($p in ($pols -split '\s+')) { if ($p -match "^arn:") { aws iam detach-role-policy --role-name $r --policy-arn $p 2>&1 | Out-Null } }
            $inl = aws iam list-role-policies --role-name $r --query "PolicyNames" --output text 2>&1
            foreach ($ip in ($inl -split '\s+')) { if ($ip) { aws iam delete-role-policy --role-name $r --policy-name $ip 2>&1 | Out-Null } }
            $profs = aws iam list-instance-profiles-for-role --role-name $r --query "InstanceProfiles[].InstanceProfileName" --output text 2>&1
            foreach ($pf in ($profs -split '\s+')) {
                if ($pf) { aws iam remove-role-from-instance-profile --instance-profile-name $pf --role-name $r 2>&1 | Out-Null; aws iam delete-instance-profile --instance-profile-name $pf 2>&1 | Out-Null }
            }
            aws iam delete-role --role-name $r 2>&1 | Out-Null
        }
    }
}

# 6. Security groups
Write-Host "`n[6/7] Security groups" -ForegroundColor Cyan
$sgs = aws ec2 describe-security-groups --filters "Name=group-name,Values=cloudops*,CloudOps*" --query "SecurityGroups[].GroupId" --output text --region $region 2>&1
if ($sgs -and $LASTEXITCODE -eq 0) {
    foreach ($sg in ($sgs -split '\s+')) {
        if ($sg -match "^sg-") { Do-It "Delete SG $sg" { aws ec2 delete-security-group --group-id $sg --region $region 2>&1 | Out-Null } }
    }
}

# 7. Key pairs + local files
Write-Host "`n[7/7] Key pairs" -ForegroundColor Cyan
$keys = aws ec2 describe-key-pairs --filters "Name=key-name,Values=cloudops*" --query "KeyPairs[].KeyName" --output text --region $region 2>&1
if ($keys -and $LASTEXITCODE -eq 0) {
    foreach ($k in ($keys -split '\s+')) {
        if ($k) { Do-It "Delete key $k" { aws ec2 delete-key-pair --key-name $k --region $region 2>&1 | Out-Null } }
    }
}
Get-ChildItem -Path $PSScriptRoot -Filter "cloudops-key*.pem" | ForEach-Object { Do-It "Remove $($_.Name)" { Remove-Item $_.FullName -Force } }

# Summary
Write-Host ""
if ($DryRun) {
    Write-Host "=== DRY RUN COMPLETE - Nothing deleted ===" -ForegroundColor Yellow
    Write-Host "  Run: .\teardown.ps1 --confirm" -ForegroundColor White
} else {
    Write-Host "=== TEARDOWN COMPLETE ===" -ForegroundColor Green
    Write-Host "  Manual cleanup (if needed): Bedrock KB, ECR repos, CodeBuild projects" -ForegroundColor Gray
}
Write-Host ""
