"""
Comprehensive AWS Storage Service Tools
Covers S3, EBS, EFS, FSx, and Storage Gateway operations.
"""

from src.core.aws_clients import boto3  # account-aware boto3 shim (cross-account support)
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from strands import tool
import structlog

logger = structlog.get_logger()


# ============================================================================
# S3 Tools (Expanded)
# ============================================================================

@tool
def analyze_s3_bucket(bucket_name: str) -> str:
    """Comprehensive S3 bucket analysis including size, objects, policies, and configuration."""
    try:
        s3 = boto3.client('s3', region_name='us-east-1')
        cloudwatch = boto3.client('cloudwatch', region_name='us-east-1')
        
        # Get bucket location
        try:
            location = s3.get_bucket_location(Bucket=bucket_name)['LocationConstraint'] or 'us-east-1'
        except:
            location = 'unknown'
        
        # Get bucket policy
        try:
            policy = s3.get_bucket_policy(Bucket=bucket_name)['Policy']
            has_policy = True
        except:
            policy = None
            has_policy = False
        
        # Get public access block
        try:
            public_access = s3.get_public_access_block(Bucket=bucket_name)['PublicAccessBlockConfiguration']
        except:
            public_access = None
        
        # Get versioning
        try:
            versioning = s3.get_bucket_versioning(Bucket=bucket_name)
        except:
            versioning = {}
        
        # Get encryption
        try:
            encryption = s3.get_bucket_encryption(Bucket=bucket_name)['ServerSideEncryptionConfiguration']
        except:
            encryption = None
        
        # Get lifecycle configuration
        try:
            lifecycle = s3.get_bucket_lifecycle_configuration(Bucket=bucket_name)['Rules']
        except:
            lifecycle = []
        
        # Get CloudWatch metrics for bucket size
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(days=2)
        
        try:
            size_response = cloudwatch.get_metric_statistics(
                Namespace='AWS/S3',
                MetricName='BucketSizeBytes',
                Dimensions=[
                    {'Name': 'BucketName', 'Value': bucket_name},
                    {'Name': 'StorageType', 'Value': 'StandardStorage'}
                ],
                StartTime=start_time,
                EndTime=end_time,
                Period=86400,
                Statistics=['Average']
            )
            
            bucket_size = 0
            if size_response['Datapoints']:
                latest = max(size_response['Datapoints'], key=lambda x: x['Timestamp'])
                bucket_size = latest['Average']
        except:
            bucket_size = 0
        
        # Get object count
        try:
            count_response = cloudwatch.get_metric_statistics(
                Namespace='AWS/S3',
                MetricName='NumberOfObjects',
                Dimensions=[
                    {'Name': 'BucketName', 'Value': bucket_name},
                    {'Name': 'StorageType', 'Value': 'AllStorageTypes'}
                ],
                StartTime=start_time,
                EndTime=end_time,
                Period=86400,
                Statistics=['Average']
            )
            
            object_count = 0
            if count_response['Datapoints']:
                latest = max(count_response['Datapoints'], key=lambda x: x['Timestamp'])
                object_count = int(latest['Average'])
        except:
            object_count = 0
        
        # Same rules as the public-bucket scan (account and bucket Block Public Access, policy status, ACLs).
        exposure = _bucket_exposure(bucket_name, *_account_public_access_block())

        analysis = {
            'bucket_name': bucket_name,
            'region': location,
            'size_bytes': int(bucket_size),
            'size_gb': round(bucket_size / (1024**3), 2),
            'object_count': object_count,
            'versioning_enabled': versioning.get('Status') == 'Enabled',
            'has_bucket_policy': has_policy,
            'encryption_enabled': encryption is not None,
            'lifecycle_rules_count': len(lifecycle),
            'public_access_block': public_access,
            'is_public': exposure['public'],
            'public_exposure_reasons': exposure['reasons'],
        }
        
        return f"S3 Bucket Analysis for {bucket_name}:\n" + json.dumps(analysis, indent=2, default=str)
        
    except Exception as e:
        return f"Error analyzing S3 bucket: {str(e)}"


@tool
def list_s3_bucket_objects(bucket_name: str, prefix: str = "", max_keys: int = 100) -> str:
    """List objects in an S3 bucket with optional prefix filter."""
    try:
        s3 = boto3.client('s3', region_name='us-east-1')
        
        params = {
            'Bucket': bucket_name,
            'MaxKeys': max_keys
        }
        
        if prefix:
            params['Prefix'] = prefix
        
        response = s3.list_objects_v2(**params)
        
        if 'Contents' not in response:
            return f"No objects found in bucket {bucket_name} with prefix '{prefix}'"
        
        objects = []
        total_size = 0
        
        for obj in response['Contents']:
            objects.append({
                'key': obj['Key'],
                'size_bytes': obj['Size'],
                'last_modified': obj['LastModified'].isoformat(),
                'storage_class': obj.get('StorageClass', 'STANDARD')
            })
            total_size += obj['Size']
        
        result = {
            'bucket_name': bucket_name,
            'prefix': prefix,
            'object_count': len(objects),
            'total_size_bytes': total_size,
            'total_size_mb': round(total_size / (1024**2), 2),
            'is_truncated': response.get('IsTruncated', False),
            'objects': objects
        }
        
        return f"S3 Objects in {bucket_name}:\n" + json.dumps(result, indent=2, default=str)
        
    except Exception as e:
        return f"Error listing S3 objects: {str(e)}"


@tool
def enable_s3_versioning(bucket_name: str) -> str:
    """Enable versioning on an S3 bucket."""
    try:
        s3 = boto3.client('s3', region_name='us-east-1')
        
        s3.put_bucket_versioning(
            Bucket=bucket_name,
            VersioningConfiguration={'Status': 'Enabled'}
        )
        
        return f"Enabled versioning for S3 bucket {bucket_name}"
        
    except Exception as e:
        return f"Error enabling S3 versioning: {str(e)}"


@tool
def create_s3_lifecycle_policy(bucket_name: str, rule_name: str, 
                              transition_days: int, storage_class: str) -> str:
    """Create a lifecycle policy to transition objects to different storage classes."""
    try:
        s3 = boto3.client('s3', region_name='us-east-1')
        
        lifecycle_config = {
            'Rules': [{
                'ID': rule_name,
                'Status': 'Enabled',
                'Filter': {'Prefix': ''},
                'Transitions': [{
                    'Days': transition_days,
                    'StorageClass': storage_class
                }]
            }]
        }
        
        s3.put_bucket_lifecycle_configuration(
            Bucket=bucket_name,
            LifecycleConfiguration=lifecycle_config
        )
        
        return f"Created lifecycle policy '{rule_name}' for bucket {bucket_name}: transition to {storage_class} after {transition_days} days"
        
    except Exception as e:
        return f"Error creating S3 lifecycle policy: {str(e)}"


@tool
def enable_s3_server_side_encryption(bucket_name: str, kms_key_id: str = None) -> str:
    """Enable server-side encryption on an S3 bucket."""
    try:
        s3 = boto3.client('s3', region_name='us-east-1')
        
        if kms_key_id:
            encryption_config = {
                'Rules': [{
                    'ApplyServerSideEncryptionByDefault': {
                        'SSEAlgorithm': 'aws:kms',
                        'KMSMasterKeyID': kms_key_id
                    },
                    'BucketKeyEnabled': True
                }]
            }
        else:
            encryption_config = {
                'Rules': [{
                    'ApplyServerSideEncryptionByDefault': {
                        'SSEAlgorithm': 'AES256'
                    }
                }]
            }
        
        s3.put_bucket_encryption(
            Bucket=bucket_name,
            ServerSideEncryptionConfiguration=encryption_config
        )
        
        encryption_type = f"KMS ({kms_key_id})" if kms_key_id else "AES256"
        return f"Enabled {encryption_type} encryption for S3 bucket {bucket_name}"
        
    except Exception as e:
        return f"Error enabling S3 encryption: {str(e)}"


# ============================================================================
# EBS Tools (Expanded)
# ============================================================================

@tool
def analyze_ebs_volume(volume_id: str) -> str:
    """Comprehensive EBS volume analysis including performance, snapshots, and usage."""
    try:
        ec2 = boto3.client('ec2', region_name='us-east-1')
        cloudwatch = boto3.client('cloudwatch', region_name='us-east-1')
        
        # Get volume details
        response = ec2.describe_volumes(VolumeIds=[volume_id])
        volume = response['Volumes'][0]
        
        # Get snapshots for this volume
        snapshots_response = ec2.describe_snapshots(
            Filters=[{'Name': 'volume-id', 'Values': [volume_id]}],
            OwnerIds=['self']
        )
        
        # Get CloudWatch metrics
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=1)
        
        metrics = {}
        metric_names = ['VolumeReadOps', 'VolumeWriteOps', 'VolumeReadBytes', 'VolumeWriteBytes']
        
        for metric_name in metric_names:
            try:
                metric_response = cloudwatch.get_metric_statistics(
                    Namespace='AWS/EBS',
                    MetricName=metric_name,
                    Dimensions=[{'Name': 'VolumeId', 'Value': volume_id}],
                    StartTime=start_time,
                    EndTime=end_time,
                    Period=3600,
                    Statistics=['Sum', 'Average']
                )
                
                if metric_response['Datapoints']:
                    latest = max(metric_response['Datapoints'], key=lambda x: x['Timestamp'])
                    metrics[metric_name] = latest
            except Exception:
                pass
            # An idle volume has no datapoints for the hour.
            metrics.setdefault(metric_name, {'Sum': 0, 'Average': 0})
        
        analysis = {
            'volume_id': volume_id,
            'state': volume['State'],
            'size_gb': volume['Size'],
            'volume_type': volume['VolumeType'],
            'iops': volume.get('Iops', 'N/A'),
            'throughput': volume.get('Throughput', 'N/A'),
            'encrypted': volume['Encrypted'],
            'availability_zone': volume['AvailabilityZone'],
            'attached_to': volume['Attachments'][0]['InstanceId'] if volume['Attachments'] else None,
            'device': volume['Attachments'][0]['Device'] if volume['Attachments'] else None,
            'create_time': volume['CreateTime'].isoformat(),
            'snapshot_count': len(snapshots_response['Snapshots']),
            'recent_metrics': {
                'read_ops_per_hour': metrics['VolumeReadOps']['Sum'],
                'write_ops_per_hour': metrics['VolumeWriteOps']['Sum'],
                'read_bytes_per_hour': metrics['VolumeReadBytes']['Sum'],
                'write_bytes_per_hour': metrics['VolumeWriteBytes']['Sum']
            },
            'tags': {tag['Key']: tag['Value'] for tag in volume.get('Tags', [])}
        }
        
        return f"EBS Volume Analysis for {volume_id}:\n" + json.dumps(analysis, indent=2, default=str)
        
    except Exception as e:
        return f"Error analyzing EBS volume: {str(e)}"


@tool
def modify_ebs_volume(volume_id: str, size: int = None, volume_type: str = None, iops: int = None) -> str:
    """Modify EBS volume size, type, or IOPS."""
    try:
        ec2 = boto3.client('ec2', region_name='us-east-1')
        
        modify_params = {'VolumeId': volume_id}
        changes = []
        
        if size:
            modify_params['Size'] = size
            changes.append(f"size to {size} GB")
        
        if volume_type:
            modify_params['VolumeType'] = volume_type
            changes.append(f"type to {volume_type}")
        
        if iops:
            modify_params['Iops'] = iops
            changes.append(f"IOPS to {iops}")
        
        if not changes:
            return "No modifications specified"
        
        response = ec2.modify_volume(**modify_params)
        
        return f"Modified EBS volume {volume_id}: {', '.join(changes)}. Modification ID: {response['VolumeModification']['ModificationId']}"
        
    except Exception as e:
        return f"Error modifying EBS volume: {str(e)}"


@tool
def create_ebs_snapshot(volume_id: str, description: str = None) -> str:
    """Create a snapshot of an EBS volume."""
    try:
        ec2 = boto3.client('ec2', region_name='us-east-1')
        
        snapshot_desc = description or f"Snapshot of {volume_id} created by CloudOps SRE"
        
        response = ec2.create_snapshot(
            VolumeId=volume_id,
            Description=snapshot_desc,
            TagSpecifications=[{
                'ResourceType': 'snapshot',
                'Tags': [
                    {'Key': 'CreatedBy', 'Value': 'CloudOps-SRE'},
                    {'Key': 'SourceVolume', 'Value': volume_id},
                    {'Key': 'CreatedAt', 'Value': datetime.utcnow().isoformat()}
                ]
            }]
        )
        
        return f"Created snapshot {response['SnapshotId']} for volume {volume_id}"
        
    except Exception as e:
        return f"Error creating EBS snapshot: {str(e)}"


@tool
def list_ebs_snapshots(volume_id: str = None, max_results: int = 50) -> str:
    """List EBS snapshots, optionally filtered by volume ID."""
    try:
        ec2 = boto3.client('ec2', region_name='us-east-1')
        
        params = {
            'OwnerIds': ['self'],
            'MaxResults': max_results
        }
        
        if volume_id:
            params['Filters'] = [{'Name': 'volume-id', 'Values': [volume_id]}]
        
        response = ec2.describe_snapshots(**params)
        
        snapshots = []
        for snapshot in response['Snapshots']:
            snapshots.append({
                'snapshot_id': snapshot['SnapshotId'],
                'volume_id': snapshot['VolumeId'],
                'state': snapshot['State'],
                'progress': snapshot['Progress'],
                'start_time': snapshot['StartTime'].isoformat(),
                'volume_size': snapshot['VolumeSize'],
                'description': snapshot.get('Description', '')
            })
        
        result = {
            'snapshot_count': len(snapshots),
            'volume_filter': volume_id,
            'snapshots': snapshots
        }
        
        return f"EBS Snapshots:\n" + json.dumps(result, indent=2, default=str)
        
    except Exception as e:
        return f"Error listing EBS snapshots: {str(e)}"


# ============================================================================
# EFS Tools
# ============================================================================

@tool
def analyze_efs_filesystem(filesystem_id: str) -> str:
    """Analyze EFS filesystem including performance, mount targets, and metrics."""
    try:
        efs = boto3.client('efs', region_name='us-east-1')
        cloudwatch = boto3.client('cloudwatch', region_name='us-east-1')
        
        # Get filesystem details
        response = efs.describe_file_systems(FileSystemId=filesystem_id)
        if not response['FileSystems']:
            return f"EFS filesystem {filesystem_id} not found"
        
        filesystem = response['FileSystems'][0]
        
        # Get mount targets
        mount_targets_response = efs.describe_mount_targets(FileSystemId=filesystem_id)
        
        # Get access points
        access_points_response = efs.describe_access_points(FileSystemId=filesystem_id)
        
        # Get CloudWatch metrics
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=1)
        
        try:
            size_response = cloudwatch.get_metric_statistics(
                Namespace='AWS/EFS',
                MetricName='TotalIOBytes',
                Dimensions=[{'Name': 'FileSystemId', 'Value': filesystem_id}],
                StartTime=start_time,
                EndTime=end_time,
                Period=3600,
                Statistics=['Sum']
            )
            
            total_io = 0
            if size_response['Datapoints']:
                latest = max(size_response['Datapoints'], key=lambda x: x['Timestamp'])
                total_io = latest['Sum']
        except:
            total_io = 0
        
        analysis = {
            'filesystem_id': filesystem_id,
            'creation_token': filesystem['CreationToken'],
            'lifecycle_state': filesystem['LifeCycleState'],
            'performance_mode': filesystem['PerformanceMode'],
            'throughput_mode': filesystem['ThroughputMode'],
            'encrypted': filesystem['Encrypted'],
            'size_bytes': filesystem['SizeInBytes']['Value'],
            'size_gb': round(filesystem['SizeInBytes']['Value'] / (1024**3), 2),
            'mount_target_count': len(mount_targets_response['MountTargets']),
            'access_point_count': len(access_points_response['AccessPoints']),
            'recent_io_bytes': total_io,
            'tags': {tag['Key']: tag['Value'] for tag in filesystem.get('Tags', [])}
        }
        
        return f"EFS Filesystem Analysis for {filesystem_id}:\n" + json.dumps(analysis, indent=2, default=str)
        
    except Exception as e:
        return f"Error analyzing EFS filesystem: {str(e)}"


@tool
def create_efs_access_point(filesystem_id: str, path: str, uid: int = 1000, gid: int = 1000) -> str:
    """Create an EFS access point for controlled access."""
    try:
        efs = boto3.client('efs', region_name='us-east-1')
        
        response = efs.create_access_point(
            FileSystemId=filesystem_id,
            PosixUser={
                'Uid': uid,
                'Gid': gid
            },
            RootDirectory={
                'Path': path,
                'CreationInfo': {
                    'OwnerUid': uid,
                    'OwnerGid': gid,
                    'Permissions': '755'
                }
            },
            Tags=[
                {'Key': 'CreatedBy', 'Value': 'CloudOps-SRE'},
                {'Key': 'Path', 'Value': path}
            ]
        )
        
        return f"Created EFS access point {response['AccessPointId']} for filesystem {filesystem_id} at path {path}"
        
    except Exception as e:
        return f"Error creating EFS access point: {str(e)}"


# ============================================================================
# S3 — List all buckets
# ============================================================================

@tool
def list_s3_buckets() -> str:
    """List all S3 buckets in the account. Shows name, region, and creation date."""
    try:
        s3 = boto3.client('s3', region_name='us-east-1')
        response = s3.list_buckets()
        all_buckets = response.get('Buckets', [])
        if not all_buckets:
            return "No S3 buckets found in this account."

        # For large accounts, just list names without region lookup (avoids timeout)
        total = len(all_buckets)
        lines = [f"Found {total} S3 bucket(s):\n"]

        # Show first 50, summarize the rest
        show_count = min(total, 50)
        for b in all_buckets[:show_count]:
            lines.append(f"  {b['Name']}  (created: {b['CreationDate'].isoformat()[:10]})")

        if total > show_count:
            lines.append(f"\n  ... and {total - show_count} more buckets.")
            lines.append("  Use 'analyze_s3_bucket <name>' for details on a specific bucket.")

        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing S3 buckets: {str(e)}"


@tool
def list_ebs_volumes(region: str = 'us-east-1', state: str = 'all') -> str:
    """List all EBS volumes, optionally filtered by state (available/in-use/all)."""
    try:
        ec2 = boto3.client('ec2', region_name=region)
        filters = []
        if state != 'all':
            filters.append({'Name': 'status', 'Values': [state]})
        response = ec2.describe_volumes(Filters=filters)
        volumes = []
        for v in response['Volumes']:
            attachment = v['Attachments'][0]['InstanceId'] if v['Attachments'] else 'unattached'
            volumes.append({
                'volume_id': v['VolumeId'],
                'size_gb': v['Size'],
                'type': v['VolumeType'],
                'state': v['State'],
                'attached_to': attachment,
                'az': v['AvailabilityZone'],
                'encrypted': v['Encrypted'],
            })
        if not volumes:
            return f"No EBS volumes found (filter: {state})."
        lines = [f"Found {len(volumes)} EBS volume(s):\n"]
        for v in volumes:
            lines.append(
                f"  {v['volume_id']}  {v['size_gb']}GB  {v['type']}  "
                f"[{v['state']}]  attached={v['attached_to']}  az={v['az']}"
            )
        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing EBS volumes: {str(e)}"


@tool
def list_efs_filesystems(region: str = 'us-east-1') -> str:
    """List all EFS filesystems in the account."""
    try:
        efs = boto3.client('efs', region_name=region)
        response = efs.describe_file_systems()
        filesystems = []
        for fs in response['FileSystems']:
            filesystems.append({
                'filesystem_id': fs['FileSystemId'],
                'name': next((t['Value'] for t in fs.get('Tags', []) if t['Key'] == 'Name'), 'Unnamed'),
                'state': fs['LifeCycleState'],
                'size_gb': round(fs['SizeInBytes']['Value'] / (1024 ** 3), 2),
                'performance_mode': fs['PerformanceMode'],
                'throughput_mode': fs['ThroughputMode'],
                'encrypted': fs['Encrypted'],
            })
        if not filesystems:
            return "No EFS filesystems found."
        lines = [f"Found {len(filesystems)} EFS filesystem(s):\n"]
        for fs in filesystems:
            lines.append(
                f"  {fs['filesystem_id']}  Name={fs['name']}  [{fs['state']}]  "
                f"{fs['size_gb']}GB  {fs['performance_mode']}"
            )
        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing EFS filesystems: {str(e)}"


# ============================================================================
# S3 Security — bulk public access check
# ============================================================================

_BLOCK_FLAGS = ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")
_PUBLIC_GROUPS = ("AllUsers", "AuthenticatedUsers")


def _error_code(e: Exception) -> str:
    return str(getattr(e, "response", {}).get("Error", {}).get("Code", type(e).__name__))


def _account_public_access_block():
    """(config dict, error) for the account-wide S3 Block Public Access setting."""
    try:
        account_id = boto3.client("sts").get_caller_identity()["Account"]
        cfg = boto3.client("s3control", region_name="us-east-1").get_public_access_block(
            AccountId=account_id)["PublicAccessBlockConfiguration"]
        return cfg, None
    except Exception as e:
        if _error_code(e) == "NoSuchPublicAccessBlockConfiguration":
            return {}, None
        return None, f"account public access block unreadable ({_error_code(e)})"


def _bucket_region(s3, name: str) -> str:
    try:
        loc = s3.get_bucket_location(Bucket=name).get("LocationConstraint")
        return {None: "us-east-1", "": "us-east-1", "EU": "eu-west-1"}.get(loc, loc)
    except Exception:
        return "us-east-1"


def _bucket_exposure(name: str, account_block, account_error) -> dict:
    """
    Decide whether one bucket is publicly accessible, following S3's rules:
    a public bucket policy (S3's own IsPublic verdict) counts unless
    RestrictPublicBuckets is in effect; a public ACL grant counts unless
    IgnorePublicAcls is in effect; each flag is in effect if set at the account
    OR the bucket level. Returns public True/False, or None when a needed check failed.
    """
    region = _bucket_region(boto3.client("s3", region_name="us-east-1"), name)
    s3 = boto3.client("s3", region_name=region)
    result = {"bucket": name, "region": region, "public": None, "reasons": [], "errors": []}
    if account_error:
        result["errors"].append(account_error)

    try:
        bucket_block = s3.get_public_access_block(Bucket=name)["PublicAccessBlockConfiguration"]
    except Exception as e:
        if _error_code(e) == "NoSuchPublicAccessBlockConfiguration":
            bucket_block = {}
        else:
            bucket_block = None
            result["errors"].append(f"bucket public access block unreadable ({_error_code(e)})")
    effective = {
        flag: bool((account_block or {}).get(flag)) or bool((bucket_block or {}).get(flag))
        for flag in _BLOCK_FLAGS
    }
    result["block_public_access"] = {
        "account": account_block if account_block is not None else "unknown",
        "bucket": bucket_block if bucket_block is not None else "unknown",
        "effective": effective,
    }

    policy_public = None
    try:
        policy_public = bool(s3.get_bucket_policy_status(Bucket=name)["PolicyStatus"].get("IsPublic"))
    except Exception as e:
        if _error_code(e) == "NoSuchBucketPolicy":
            policy_public = False
        else:
            result["errors"].append(f"bucket policy status unreadable ({_error_code(e)})")

    acl_public = None
    try:
        grants = s3.get_bucket_acl(Bucket=name).get("Grants", [])
        public_grants = [g for g in grants
                         if any(grp in g.get("Grantee", {}).get("URI", "") for grp in _PUBLIC_GROUPS)]
        acl_public = bool(public_grants)
        for g in public_grants:
            result["reasons"].append(
                f"ACL grants {g.get('Permission')} to {g['Grantee']['URI'].rsplit('/', 1)[-1]}"
                + (" (ignored: IgnorePublicAcls is on)" if effective["IgnorePublicAcls"] else ""))
    except Exception as e:
        result["errors"].append(f"bucket ACL unreadable ({_error_code(e)})")

    if policy_public:
        result["reasons"].append("bucket policy is public"
                                 + (" (restricted: RestrictPublicBuckets is on)"
                                    if effective["RestrictPublicBuckets"] else ""))

    exposed_by_policy = policy_public and not effective["RestrictPublicBuckets"]
    exposed_by_acl = acl_public and not effective["IgnorePublicAcls"]
    if exposed_by_policy or exposed_by_acl:
        result["public"] = True
    elif all(effective.values()):
        result["public"] = False      # everything is blocked, whatever the policy or ACL say
    elif policy_public is not None and acl_public is not None:
        result["public"] = False
    return result


@tool
def list_public_s3_buckets() -> str:
    """
    Scan ALL S3 buckets in the account and report which are publicly accessible.

    Uses S3's own rules: account- and bucket-level Block Public Access, S3's
    public-policy verdict (GetBucketPolicyStatus) and public ACL grants. Each bucket
    is reported as public, not public, or could-not-check (with the reason).
    """
    try:
        buckets = boto3.client("s3", region_name="us-east-1").list_buckets().get("Buckets", [])
    except Exception as e:
        return json.dumps({"status": "failed", "error": f"Could not list buckets ({_error_code(e)}): {e}"})
    if not buckets:
        return json.dumps({"status": "success", "total_buckets": 0, "public_buckets": [],
                           "summary": "No S3 buckets in this account."})

    account_block, account_error = _account_public_access_block()
    results = [_bucket_exposure(b["Name"], account_block, account_error) for b in buckets]
    public = [r for r in results if r["public"] is True]
    unknown = [r for r in results if r["public"] is None]
    not_public = len(results) - len(public) - len(unknown)
    account_fully_blocked = bool(account_block) and all(account_block.get(f) for f in _BLOCK_FLAGS)

    if unknown:
        summary = (f"Scan INCOMPLETE: {len(public)} public, {not_public} not public, {len(unknown)} could "
                   f"not be checked. Do not report the unchecked buckets as private; give the reasons.")
    elif public:
        summary = f"{len(public)} of {len(results)} buckets are publicly accessible."
    else:
        summary = f"None of the {len(results)} buckets are publicly accessible."

    return json.dumps({
        "status": "incomplete" if unknown else "success",
        "summary": summary,
        "total_buckets": len(results),
        "account_block_public_access": ("all four settings on" if account_fully_blocked
                                        else account_block if account_block is not None else "unknown"),
        "public_buckets": [{"bucket": r["bucket"], "region": r["region"], "reasons": r["reasons"]}
                           for r in public],
        "unchecked_buckets": [{"bucket": r["bucket"], "errors": r["errors"]} for r in unknown],
        "not_public_count": not_public,
        "remediation": "Ask to 'enable public access block on <bucket>' to block a bucket." if public else None,
    }, default=str)


@tool
def enable_s3_public_access_block(bucket_name: str) -> str:
    """
    Apply full public access block to an S3 bucket.
    Blocks all public ACLs and bucket policies that grant public access.
    """
    try:
        s3 = boto3.client('s3', region_name='us-east-1')
        s3.put_public_access_block(
            Bucket=bucket_name,
            PublicAccessBlockConfiguration={
                'BlockPublicAcls': True,
                'IgnorePublicAcls': True,
                'BlockPublicPolicy': True,
                'RestrictPublicBuckets': True,
            }
        )
        return (f"✅ Applied full public access block to '{bucket_name}'. "
                f"All public ACLs and policies are now blocked.")
    except Exception as e:
        return f"Error enabling public access block on '{bucket_name}': {str(e)}"


@tool
def get_s3_bucket_public_access_status(bucket_name: str) -> str:
    """
    Check whether one S3 bucket is publicly accessible, with the evidence: account- and
    bucket-level Block Public Access, S3's public-policy verdict, and public ACL grants.
    """
    try:
        account_block, account_error = _account_public_access_block()
        result = _bucket_exposure(bucket_name, account_block, account_error)
        result["verdict"] = {True: "PUBLIC", False: "NOT PUBLIC", None: "COULD NOT DETERMINE"}[result["public"]]
        return json.dumps(result, default=str)
    except Exception as e:
        return json.dumps({"status": "failed", "bucket": bucket_name,
                           "error": f"Could not check public access ({_error_code(e)}): {e}"})
