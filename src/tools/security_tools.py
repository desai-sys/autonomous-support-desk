"""
Comprehensive AWS Security Service Tools
Covers IAM, Secrets Manager, KMS, WAF, GuardDuty, Security Hub, and CloudTrail operations.
"""

from src.core.aws_clients import boto3  # account-aware boto3 shim (cross-account support)
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from strands import tool
import structlog

logger = structlog.get_logger()


# ============================================================================
# IAM Tools (Expanded)
# ============================================================================

@tool
def analyze_iam_user(username: str) -> str:
    """Comprehensive IAM user analysis including policies, groups, and access keys."""
    try:
        iam = boto3.client('iam', region_name='us-east-1')
        
        # Get user details
        user_response = iam.get_user(UserName=username)
        user = user_response['User']
        
        # Get attached policies
        attached_policies_response = iam.list_attached_user_policies(UserName=username)
        attached_policies = [policy['PolicyName'] for policy in attached_policies_response['AttachedPolicies']]
        
        # Get inline policies
        inline_policies_response = iam.list_user_policies(UserName=username)
        inline_policies = inline_policies_response['PolicyNames']
        
        # Get groups
        groups_response = iam.get_groups_for_user(UserName=username)
        groups = [group['GroupName'] for group in groups_response['Groups']]
        
        # Get access keys
        access_keys_response = iam.list_access_keys(UserName=username)
        access_keys = []
        for key in access_keys_response['AccessKeyMetadata']:
            access_keys.append({
                'access_key_id': key['AccessKeyId'],
                'status': key['Status'],
                'created_date': key['CreateDate'].isoformat()
            })
        
        # Get MFA devices
        mfa_devices_response = iam.list_mfa_devices(UserName=username)
        mfa_devices = []
        for device in mfa_devices_response['MFADevices']:
            mfa_devices.append({
                'serial_number': device['SerialNumber'],
                'enabled_date': device['EnableDate'].isoformat()
            })
        
        # Get login profile (console access)
        try:
            login_profile_response = iam.get_login_profile(UserName=username)
            console_access = True
            password_last_used = login_profile_response['LoginProfile'].get('PasswordLastUsed')
        except iam.exceptions.NoSuchEntityException:
            console_access = False
            password_last_used = None
        
        analysis = {
            'username': username,
            'user_id': user['UserId'],
            'arn': user['Arn'],
            'created_date': user['CreateDate'].isoformat(),
            'password_last_used': password_last_used.isoformat() if password_last_used else None,
            'console_access': console_access,
            'attached_policies': attached_policies,
            'inline_policies': inline_policies,
            'groups': groups,
            'access_keys': access_keys,
            'mfa_devices': mfa_devices,
            'tags': {tag['Key']: tag['Value'] for tag in user.get('Tags', [])}
        }
        
        return f"IAM User Analysis for {username}:\n" + json.dumps(analysis, indent=2, default=str)
        
    except Exception as e:
        return f"Error analyzing IAM user: {str(e)}"


@tool
def analyze_iam_role(role_name: str) -> str:
    """Comprehensive IAM role analysis including trust policy, permissions, and usage."""
    try:
        iam = boto3.client('iam', region_name='us-east-1')
        
        # Get role details
        role_response = iam.get_role(RoleName=role_name)
        role = role_response['Role']
        
        # Get attached policies
        attached_policies_response = iam.list_attached_role_policies(RoleName=role_name)
        attached_policies = []
        for policy in attached_policies_response['AttachedPolicies']:
            attached_policies.append({
                'policy_name': policy['PolicyName'],
                'policy_arn': policy['PolicyArn']
            })
        
        # Get inline policies
        inline_policies_response = iam.list_role_policies(RoleName=role_name)
        inline_policies = inline_policies_response['PolicyNames']
        
        # Get instance profiles
        instance_profiles_response = iam.list_instance_profiles_for_role(RoleName=role_name)
        instance_profiles = [profile['InstanceProfileName'] for profile in instance_profiles_response['InstanceProfiles']]
        
        # Parse trust policy
        trust_policy = json.loads(role['AssumeRolePolicyDocument'].replace('%20', ' ').replace('%22', '"').replace('%7B', '{').replace('%7D', '}'))
        
        trusted_entities = []
        for statement in trust_policy.get('Statement', []):
            principal = statement.get('Principal', {})
            if isinstance(principal, dict):
                for key, value in principal.items():
                    if isinstance(value, list):
                        trusted_entities.extend([f"{key}: {v}" for v in value])
                    else:
                        trusted_entities.append(f"{key}: {value}")
            elif isinstance(principal, str):
                trusted_entities.append(principal)
        
        analysis = {
            'role_name': role_name,
            'role_id': role['RoleId'],
            'arn': role['Arn'],
            'created_date': role['CreateDate'].isoformat(),
            'last_used': role.get('RoleLastUsed', {}).get('LastUsedDate', '').isoformat() if role.get('RoleLastUsed', {}).get('LastUsedDate') else None,
            'max_session_duration': role['MaxSessionDuration'],
            'path': role['Path'],
            'trusted_entities': trusted_entities,
            'attached_policies': attached_policies,
            'inline_policies': inline_policies,
            'instance_profiles': instance_profiles,
            'tags': {tag['Key']: tag['Value'] for tag in role.get('Tags', [])}
        }
        
        return f"IAM Role Analysis for {role_name}:\n" + json.dumps(analysis, indent=2, default=str)
        
    except Exception as e:
        return f"Error analyzing IAM role: {str(e)}"


@tool
def list_iam_users_without_mfa() -> str:
    """List IAM users who don't have MFA enabled."""
    try:
        iam = boto3.client('iam', region_name='us-east-1')
        
        # Get all users
        users_response = iam.list_users()
        users_without_mfa = []
        
        for user in users_response['Users']:
            username = user['UserName']
            
            # Check if user has console access
            try:
                iam.get_login_profile(UserName=username)
                has_console_access = True
            except iam.exceptions.NoSuchEntityException:
                has_console_access = False
            
            # Check MFA devices
            mfa_response = iam.list_mfa_devices(UserName=username)
            has_mfa = len(mfa_response['MFADevices']) > 0
            
            if has_console_access and not has_mfa:
                users_without_mfa.append({
                    'username': username,
                    'created_date': user['CreateDate'].isoformat(),
                    'password_last_used': user.get('PasswordLastUsed', '').isoformat() if user.get('PasswordLastUsed') else None
                })
        
        result = {
            'users_without_mfa_count': len(users_without_mfa),
            'users': users_without_mfa
        }
        
        return f"IAM Users Without MFA:\n" + json.dumps(result, indent=2, default=str)
        
    except Exception as e:
        return f"Error listing users without MFA: {str(e)}"


@tool
def rotate_iam_access_key(username: str, access_key_id: str) -> str:
    """Rotate an IAM user's access key by creating a new one and marking the old one for deletion."""
    try:
        iam = boto3.client('iam', region_name='us-east-1')
        
        # Create new access key
        new_key_response = iam.create_access_key(UserName=username)
        new_access_key = new_key_response['AccessKey']
        
        # Mark old key as inactive (don't delete immediately for safety)
        iam.update_access_key(
            UserName=username,
            AccessKeyId=access_key_id,
            Status='Inactive'
        )
        
        result = {
            'username': username,
            'old_access_key_id': access_key_id,
            'old_key_status': 'Inactive',
            'new_access_key_id': new_access_key['AccessKeyId'],
            'new_secret_access_key': new_access_key['SecretAccessKey'],
            'created_date': new_access_key['CreateDate'].isoformat(),
            'warning': 'Update applications with new credentials, then delete old key manually'
        }
        
        return f"Access Key Rotation for {username}:\n" + json.dumps(result, indent=2, default=str)
        
    except Exception as e:
        return f"Error rotating access key: {str(e)}"


# ============================================================================
# Secrets Manager Tools
# ============================================================================

@tool
def analyze_secrets_manager_secret(secret_name: str) -> str:
    """Analyze Secrets Manager secret including rotation and access patterns."""
    try:
        secrets = boto3.client('secretsmanager', region_name='us-east-1')
        
        # Get secret details
        response = secrets.describe_secret(SecretId=secret_name)
        
        # Get secret versions
        versions_response = secrets.list_secret_version_ids(SecretId=secret_name)
        
        versions = []
        for version_id, version_info in versions_response['Versions'].items():
            versions.append({
                'version_id': version_id,
                'version_stages': version_info.get('VersionStages', []),
                'created_date': version_info.get('CreatedDate', '').isoformat() if version_info.get('CreatedDate') else None,
                'last_accessed_date': version_info.get('LastAccessedDate', '').isoformat() if version_info.get('LastAccessedDate') else None
            })
        
        analysis = {
            'secret_name': response['Name'],
            'secret_arn': response['ARN'],
            'description': response.get('Description', ''),
            'created_date': response['CreatedDate'].isoformat(),
            'last_changed_date': response.get('LastChangedDate', '').isoformat() if response.get('LastChangedDate') else None,
            'last_accessed_date': response.get('LastAccessedDate', '').isoformat() if response.get('LastAccessedDate') else None,
            'rotation_enabled': response.get('RotationEnabled', False),
            'rotation_lambda_arn': response.get('RotationLambdaARN', 'N/A'),
            'rotation_rules': response.get('RotationRules', {}),
            'kms_key_id': response.get('KmsKeyId', 'Default'),
            'versions': versions,
            'tags': {tag['Key']: tag['Value'] for tag in response.get('Tags', [])}
        }
        
        return f"Secrets Manager Secret Analysis for {secret_name}:\n" + json.dumps(analysis, indent=2, default=str)
        
    except Exception as e:
        return f"Error analyzing secret: {str(e)}"


@tool
def rotate_secrets_manager_secret(secret_name: str, force_rotate: bool = False) -> str:
    """Trigger rotation of a Secrets Manager secret."""
    try:
        secrets = boto3.client('secretsmanager', region_name='us-east-1')
        
        response = secrets.rotate_secret(
            SecretId=secret_name,
            ForceRotateSecretForTest=force_rotate
        )
        
        return f"Initiated rotation for secret {secret_name}. Version ID: {response['VersionId']}"
        
    except Exception as e:
        return f"Error rotating secret: {str(e)}"


# ============================================================================
# KMS Tools
# ============================================================================

@tool
def analyze_kms_key(key_id: str) -> str:
    """Analyze KMS key including usage, grants, and key policy."""
    try:
        kms = boto3.client('kms', region_name='us-east-1')
        
        # Get key details
        key_response = kms.describe_key(KeyId=key_id)
        key = key_response['KeyMetadata']
        
        # Get key policy
        try:
            policy_response = kms.get_key_policy(KeyId=key_id, PolicyName='default')
            key_policy = json.loads(policy_response['Policy'])
        except:
            key_policy = None
        
        # Get grants
        try:
            grants_response = kms.list_grants(KeyId=key_id)
            grants = []
            for grant in grants_response['Grants']:
                grants.append({
                    'grant_id': grant['GrantId'],
                    'grantee_principal': grant['GranteePrincipal'],
                    'operations': grant['Operations'],
                    'creation_date': grant['CreationDate'].isoformat()
                })
        except:
            grants = []
        
        # Get aliases
        aliases_response = kms.list_aliases()
        key_aliases = []
        for alias in aliases_response['Aliases']:
            if alias.get('TargetKeyId') == key['KeyId']:
                key_aliases.append(alias['AliasName'])
        
        analysis = {
            'key_id': key['KeyId'],
            'arn': key['Arn'],
            'description': key['Description'],
            'key_usage': key['KeyUsage'],
            'key_spec': key['KeySpec'],
            'key_state': key['KeyState'],
            'enabled': key['Enabled'],
            'creation_date': key['CreationDate'].isoformat(),
            'deletion_date': key.get('DeletionDate', '').isoformat() if key.get('DeletionDate') else None,
            'origin': key['Origin'],
            'key_manager': key['KeyManager'],
            'customer_master_key_spec': key.get('CustomerMasterKeySpec', 'N/A'),
            'aliases': key_aliases,
            'grants_count': len(grants),
            'grants': grants[:10],  # Limit to first 10 grants
            'has_key_policy': key_policy is not None
        }
        
        return f"KMS Key Analysis for {key_id}:\n" + json.dumps(analysis, indent=2, default=str)
        
    except Exception as e:
        return f"Error analyzing KMS key: {str(e)}"


@tool
def create_kms_key(description: str, key_usage: str = "ENCRYPT_DECRYPT") -> str:
    """Create a new KMS customer managed key."""
    try:
        kms = boto3.client('kms', region_name='us-east-1')
        
        response = kms.create_key(
            Description=description,
            KeyUsage=key_usage,
            Origin='AWS_KMS',
            Tags=[
                {'TagKey': 'CreatedBy', 'TagValue': 'CloudOps-SRE'},
                {'TagKey': 'CreatedAt', 'TagValue': datetime.utcnow().isoformat()}
            ]
        )
        
        key_id = response['KeyMetadata']['KeyId']
        return f"Created KMS key {key_id} with description: {description}"
        
    except Exception as e:
        return f"Error creating KMS key: {str(e)}"


# ============================================================================
# WAF Tools (Expanded)
# ============================================================================

@tool
def analyze_waf_web_acl(web_acl_name: str, scope: str = "REGIONAL") -> str:
    """Comprehensive WAF Web ACL analysis including rules and metrics."""
    try:
        waf = boto3.client('wafv2', region_name='us-east-1')
        cloudwatch = boto3.client('cloudwatch', region_name='us-east-1')
        
        # Get Web ACL details
        acls_response = waf.list_web_acls(Scope=scope)
        web_acl = None
        
        for acl in acls_response['WebACLs']:
            if acl['Name'] == web_acl_name:
                web_acl = acl
                break
        
        if not web_acl:
            return f"WAF Web ACL {web_acl_name} not found"
        
        # Get detailed Web ACL configuration
        acl_response = waf.get_web_acl(Name=web_acl_name, Scope=scope, Id=web_acl['Id'])
        acl_details = acl_response['WebACL']
        
        # Parse rules
        rules = []
        for rule in acl_details.get('Rules', []):
            rule_info = {
                'name': rule['Name'],
                'priority': rule['Priority'],
                'action': rule['Action'],
                'visibility_config': rule['VisibilityConfig']
            }
            
            # Parse statement type
            statement = rule.get('Statement', {})
            if 'ByteMatchStatement' in statement:
                rule_info['type'] = 'ByteMatch'
            elif 'IPSetReferenceStatement' in statement:
                rule_info['type'] = 'IPSetReference'
            elif 'GeoMatchStatement' in statement:
                rule_info['type'] = 'GeoMatch'
            elif 'RateLimitStatement' in statement:
                rule_info['type'] = 'RateLimit'
                rule_info['limit'] = statement['RateLimitStatement']['Limit']
            elif 'ManagedRuleGroupStatement' in statement:
                rule_info['type'] = 'ManagedRuleGroup'
                rule_info['vendor'] = statement['ManagedRuleGroupStatement']['VendorName']
                rule_info['rule_group'] = statement['ManagedRuleGroupStatement']['Name']
            else:
                rule_info['type'] = 'Other'
            
            rules.append(rule_info)
        
        # Get CloudWatch metrics
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=24)
        
        metrics = {}
        metric_names = ['AllowedRequests', 'BlockedRequests', 'CountedRequests']
        
        for metric_name in metric_names:
            try:
                metric_response = cloudwatch.get_metric_statistics(
                    Namespace='AWS/WAFV2',
                    MetricName=metric_name,
                    Dimensions=[
                        {'Name': 'WebACL', 'Value': web_acl_name},
                        {'Name': 'Region', 'Value': 'us-east-1'}
                    ],
                    StartTime=start_time,
                    EndTime=end_time,
                    Period=3600,
                    Statistics=['Sum']
                )
                
                if metric_response['Datapoints']:
                    total = sum(dp['Sum'] for dp in metric_response['Datapoints'])
                    metrics[metric_name] = total
            except:
                metrics[metric_name] = 0
        
        analysis = {
            'web_acl_name': web_acl_name,
            'web_acl_id': web_acl['Id'],
            'arn': web_acl['ARN'],
            'scope': scope,
            'default_action': acl_details['DefaultAction'],
            'rules_count': len(rules),
            'rules': rules,
            'capacity': acl_details.get('Capacity', 'N/A'),
            'managed_by_firewall_manager': acl_details.get('ManagedByFirewallManager', False),
            'recent_metrics_24h': metrics,
            'tags': {tag['Key']: tag['Value'] for tag in acl_details.get('Tags', [])}
        }
        
        return f"WAF Web ACL Analysis for {web_acl_name}:\n" + json.dumps(analysis, indent=2, default=str)
        
    except Exception as e:
        return f"Error analyzing WAF Web ACL: {str(e)}"


@tool
def create_waf_ip_block_rule(web_acl_name: str, scope: str, ip_addresses: List[str], 
                            rule_name: str, priority: int) -> str:
    """Create a WAF rule to block specific IP addresses."""
    try:
        waf = boto3.client('wafv2', region_name='us-east-1')
        
        # First create an IP set
        ip_set_name = f"{rule_name}-ipset"
        
        ip_set_response = waf.create_ip_set(
            Name=ip_set_name,
            Scope=scope,
            IPAddressVersion='IPV4',
            Addresses=ip_addresses,
            Description=f"IP set for blocking IPs in rule {rule_name}",
            Tags=[
                {'Key': 'CreatedBy', 'Value': 'CloudOps-SRE'},
                {'Key': 'Purpose', 'Value': 'IP-Blocking'}
            ]
        )
        
        ip_set_id = ip_set_response['Summary']['Id']
        ip_set_arn = ip_set_response['Summary']['ARN']
        
        # Get current Web ACL
        acls_response = waf.list_web_acls(Scope=scope)
        web_acl = None
        
        for acl in acls_response['WebACLs']:
            if acl['Name'] == web_acl_name:
                web_acl = acl
                break
        
        if not web_acl:
            return f"WAF Web ACL {web_acl_name} not found"
        
        # Get current Web ACL configuration
        acl_response = waf.get_web_acl(Name=web_acl_name, Scope=scope, Id=web_acl['Id'])
        current_acl = acl_response['WebACL']
        
        # Add new rule
        new_rule = {
            'Name': rule_name,
            'Priority': priority,
            'Statement': {
                'IPSetReferenceStatement': {
                    'ARN': ip_set_arn
                }
            },
            'Action': {'Block': {}},
            'VisibilityConfig': {
                'SampledRequestsEnabled': True,
                'CloudWatchMetricsEnabled': True,
                'MetricName': rule_name
            }
        }
        
        # Update Web ACL with new rule
        updated_rules = current_acl['Rules'] + [new_rule]
        
        waf.update_web_acl(
            Name=web_acl_name,
            Scope=scope,
            Id=web_acl['Id'],
            DefaultAction=current_acl['DefaultAction'],
            Rules=updated_rules,
            VisibilityConfig=current_acl['VisibilityConfig'],
            LockToken=acl_response['LockToken']
        )
        
        return f"Created WAF IP blocking rule '{rule_name}' for Web ACL {web_acl_name}. Blocked IPs: {', '.join(ip_addresses)}"
        
    except Exception as e:
        return f"Error creating WAF IP block rule: {str(e)}"


# ============================================================================
# GuardDuty Tools
# ============================================================================

@tool
def analyze_guardduty_findings(detector_id: str = None, severity_filter: str = "HIGH") -> str:
    """Analyze GuardDuty findings with optional severity filtering."""
    try:
        guardduty = boto3.client('guardduty', region_name='us-east-1')
        
        # Get detector ID if not provided
        if not detector_id:
            detectors_response = guardduty.list_detectors()
            if not detectors_response['DetectorIds']:
                return "No GuardDuty detectors found"
            detector_id = detectors_response['DetectorIds'][0]
        
        # Get findings
        findings_response = guardduty.list_findings(
            DetectorId=detector_id,
            FindingCriteria={
                'Criterion': {
                    'severity': {
                        'Gte': 4.0 if severity_filter == "MEDIUM" else 7.0 if severity_filter == "HIGH" else 0.0
                    }
                }
            },
            MaxResults=50
        )
        
        if not findings_response['FindingIds']:
            return f"No GuardDuty findings found with severity {severity_filter} or higher"
        
        # Get finding details
        findings_details = guardduty.get_findings(
            DetectorId=detector_id,
            FindingIds=findings_response['FindingIds']
        )
        
        findings_summary = []
        for finding in findings_details['Findings']:
            finding_summary = {
                'id': finding['Id'],
                'type': finding['Type'],
                'severity': finding['Severity'],
                'title': finding['Title'],
                'description': finding['Description'],
                'created_at': finding['CreatedAt'],
                'updated_at': finding['UpdatedAt'],
                'region': finding['Region'],
                'resource_type': finding.get('Resource', {}).get('ResourceType', 'N/A'),
                'instance_id': finding.get('Resource', {}).get('InstanceDetails', {}).get('InstanceId', 'N/A'),
                'remote_ip': finding.get('Service', {}).get('RemoteIpDetails', {}).get('IpAddressV4', 'N/A'),
                'action': finding.get('Service', {}).get('Action', {}).get('ActionType', 'N/A')
            }
            findings_summary.append(finding_summary)
        
        analysis = {
            'detector_id': detector_id,
            'severity_filter': severity_filter,
            'findings_count': len(findings_summary),
            'findings': findings_summary
        }
        
        return f"GuardDuty Findings Analysis:\n" + json.dumps(analysis, indent=2, default=str)
        
    except Exception as e:
        return f"Error analyzing GuardDuty findings: {str(e)}"


@tool
def archive_guardduty_finding(detector_id: str, finding_id: str, reason: str = "Resolved by CloudOps SRE") -> str:
    """Archive a GuardDuty finding."""
    try:
        guardduty = boto3.client('guardduty', region_name='us-east-1')
        
        guardduty.archive_findings(
            DetectorId=detector_id,
            FindingIds=[finding_id]
        )
        
        # Add a note about the resolution
        guardduty.create_finding_feedback(
            DetectorId=detector_id,
            FindingId=finding_id,
            Feedback='USEFUL',
            Comments=reason
        )
        
        return f"Archived GuardDuty finding {finding_id} with reason: {reason}"
        
    except Exception as e:
        return f"Error archiving GuardDuty finding: {str(e)}"


# ============================================================================
# Security — List tools
# ============================================================================

@tool
def list_iam_users(region: str = 'us-east-1') -> str:
    """List all IAM users in the account with their creation date and last activity."""
    try:
        iam = boto3.client('iam', region_name='us-east-1')
        paginator = iam.get_paginator('list_users')
        users = []
        for page in paginator.paginate():
            for u in page['Users']:
                users.append({
                    'username': u['UserName'],
                    'user_id': u['UserId'],
                    'created': u['CreateDate'].isoformat()[:10],
                    'password_last_used': u.get('PasswordLastUsed', '').isoformat()[:10]
                    if u.get('PasswordLastUsed') else 'Never',
                    'arn': u['Arn'],
                })
        if not users:
            return "No IAM users found."
        lines = [f"Found {len(users)} IAM user(s):\n"]
        for u in users:
            lines.append(
                f"  {u['username']}  created={u['created']}  "
                f"last_login={u['password_last_used']}"
            )
        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing IAM users: {str(e)}"


@tool
def list_iam_roles(region: str = 'us-east-1') -> str:
    """List all IAM roles in the account."""
    try:
        iam = boto3.client('iam', region_name='us-east-1')
        paginator = iam.get_paginator('list_roles')
        roles = []
        for page in paginator.paginate():
            for r in page['Roles']:
                roles.append({
                    'role_name': r['RoleName'],
                    'role_id': r['RoleId'],
                    'created': r['CreateDate'].isoformat()[:10],
                    'path': r['Path'],
                    'description': r.get('Description', '')[:60],
                })
        if not roles:
            return "No IAM roles found."
        lines = [f"Found {len(roles)} IAM role(s):\n"]
        for r in roles:
            lines.append(f"  {r['role_name']}  created={r['created']}  path={r['path']}")
        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing IAM roles: {str(e)}"


@tool
def list_secrets(region: str = 'us-east-1') -> str:
    """List all Secrets Manager secrets (names only, not values)."""
    try:
        sm = boto3.client('secretsmanager', region_name=region)
        paginator = sm.get_paginator('list_secrets')
        secrets = []
        for page in paginator.paginate():
            for s in page['SecretList']:
                secrets.append({
                    'name': s['Name'],
                    'arn': s['ARN'],
                    'last_changed': s.get('LastChangedDate', '').isoformat()[:10]
                    if s.get('LastChangedDate') else 'N/A',
                    'last_accessed': s.get('LastAccessedDate', '').isoformat()[:10]
                    if s.get('LastAccessedDate') else 'N/A',
                    'rotation_enabled': s.get('RotationEnabled', False),
                })
        if not secrets:
            return f"No Secrets Manager secrets found in {region}."
        lines = [f"Found {len(secrets)} secret(s):\n"]
        for s in secrets:
            lines.append(
                f"  {s['name']}  last_changed={s['last_changed']}  "
                f"last_accessed={s['last_accessed']}  rotation={s['rotation_enabled']}"
            )
        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing secrets: {str(e)}"


@tool
def list_kms_keys(region: str = 'us-east-1') -> str:
    """List all KMS customer managed keys (CMKs) in the account."""
    try:
        kms = boto3.client('kms', region_name=region)
        paginator = kms.get_paginator('list_keys')
        keys = []
        for page in paginator.paginate():
            for k in page['Keys']:
                try:
                    meta = kms.describe_key(KeyId=k['KeyId'])['KeyMetadata']
                    # Only show customer managed keys
                    if meta['KeyManager'] == 'CUSTOMER':
                        keys.append({
                            'key_id': meta['KeyId'],
                            'description': meta.get('Description', '')[:60],
                            'state': meta['KeyState'],
                            'usage': meta['KeyUsage'],
                            'created': meta['CreationDate'].isoformat()[:10],
                        })
                except Exception:
                    pass
        if not keys:
            return f"No customer managed KMS keys found in {region}."
        lines = [f"Found {len(keys)} customer managed KMS key(s):\n"]
        for k in keys:
            lines.append(
                f"  {k['key_id']}  [{k['state']}]  usage={k['usage']}  "
                f"created={k['created']}  desc={k['description']}"
            )
        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing KMS keys: {str(e)}"
