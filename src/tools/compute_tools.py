"""
Comprehensive AWS Compute Service Tools
Covers EC2, Lambda, ECS, EKS, Batch, and Auto Scaling operations.
"""

from src.core.aws_clients import boto3  # account-aware boto3 shim (cross-account support)
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from strands import tool
import structlog

logger = structlog.get_logger()


# ============================================================================
# EC2 Tools (Expanded)
# ============================================================================

@tool
def list_ec2_instances(region: str = 'us-east-1', state: str = 'all') -> str:
    """List all EC2 instances in the account, optionally filtered by state (running/stopped/all)."""
    try:
        ec2 = boto3.client('ec2', region_name=region)

        filters = []
        if state != 'all':
            filters.append({'Name': 'instance-state-name', 'Values': [state]})

        response = ec2.describe_instances(Filters=filters)

        instances = []
        for reservation in response['Reservations']:
            for inst in reservation['Instances']:
                name = next(
                    (t['Value'] for t in inst.get('Tags', []) if t['Key'] == 'Name'),
                    'Unnamed'
                )
                instances.append({
                    'instance_id': inst['InstanceId'],
                    'name': name,
                    'state': inst['State']['Name'],
                    'type': inst['InstanceType'],
                    'private_ip': inst.get('PrivateIpAddress', 'N/A'),
                    'public_ip': inst.get('PublicIpAddress', 'N/A'),
                    'launch_time': inst['LaunchTime'].isoformat(),
                })

        if not instances:
            return f"No EC2 instances found in {region} (filter: {state})."

        lines = [f"Found {len(instances)} EC2 instance(s) in {region}:\n"]
        for i in instances:
            lines.append(
                f"  {i['instance_id']}  [{i['state'].upper()}]  {i['type']}  "
                f"Name={i['name']}  IP={i['private_ip']}/{i['public_ip']}"
            )
        return '\n'.join(lines)

    except Exception as e:
        return f"Error listing EC2 instances: {str(e)}"


@tool
def analyze_ec2_instance(instance_id: str) -> str:
    """Comprehensive EC2 instance analysis including state, type, security groups, and health."""
    try:
        ec2 = boto3.client('ec2', region_name='us-east-1')
        
        # Get instance details
        response = ec2.describe_instances(InstanceIds=[instance_id])
        instance = response['Reservations'][0]['Instances'][0]
        
        # Get instance status
        status_response = ec2.describe_instance_status(InstanceIds=[instance_id])
        status = status_response['InstanceStatuses'][0] if status_response['InstanceStatuses'] else None
        
        # Analyze security groups
        sg_ids = [sg['GroupId'] for sg in instance['SecurityGroups']]
        sg_response = ec2.describe_security_groups(GroupIds=sg_ids)
        
        analysis = {
            'instance_id': instance_id,
            'state': instance['State']['Name'],
            'instance_type': instance['InstanceType'],
            'launch_time': instance['LaunchTime'].isoformat(),
            'vpc_id': instance.get('VpcId', 'N/A'),
            'subnet_id': instance.get('SubnetId', 'N/A'),
            'private_ip': instance.get('PrivateIpAddress', 'N/A'),
            'public_ip': instance.get('PublicIpAddress', 'N/A'),
            'security_groups': [{'id': sg['GroupId'], 'name': sg['GroupName']} for sg in instance['SecurityGroups']],
            'system_status': status['SystemStatus']['Status'] if status else 'unknown',
            'instance_status': status['InstanceStatus']['Status'] if status else 'unknown',
            'monitoring': instance['Monitoring']['State'],
            'tags': {tag['Key']: tag['Value'] for tag in instance.get('Tags', [])}
        }
        
        return f"EC2 Analysis for {instance_id}:\n" + json.dumps(analysis, indent=2, default=str)
        
    except Exception as e:
        return f"Error analyzing EC2 instance: {str(e)}"


@tool
def start_ec2_instance(instance_id: str) -> str:
    """Start a stopped EC2 instance."""
    try:
        ec2 = boto3.client('ec2', region_name='us-east-1')
        response = ec2.start_instances(InstanceIds=[instance_id])
        
        return f"Started EC2 instance {instance_id}. Current state: {response['StartingInstances'][0]['CurrentState']['Name']}"
        
    except Exception as e:
        return f"Error starting EC2 instance: {str(e)}"


@tool
def stop_ec2_instance(instance_id: str, force: bool = False) -> str:
    """
    Stop a running EC2 instance (NOT terminate — data is preserved on EBS volumes).
    Stopping an instance halts billing for compute but keeps the EBS storage.
    The instance can be restarted at any time.
    Use force=True only for immediate shutdown (may cause data corruption).
    """
    try:
        ec2 = boto3.client('ec2', region_name='us-east-1')
        response = ec2.stop_instances(InstanceIds=[instance_id], Force=force)
        
        return f"Stopped EC2 instance {instance_id}. Current state: {response['StoppingInstances'][0]['CurrentState']['Name']}"
        
    except Exception as e:
        return f"Error stopping EC2 instance: {str(e)}"


@tool
def reboot_ec2_instance(instance_id: str) -> str:
    """Reboot an EC2 instance."""
    try:
        ec2 = boto3.client('ec2', region_name='us-east-1')
        ec2.reboot_instances(InstanceIds=[instance_id])
        
        return f"Reboot initiated for EC2 instance {instance_id}"
        
    except Exception as e:
        return f"Error rebooting EC2 instance: {str(e)}"


@tool
def modify_ec2_instance_type(instance_id: str, new_instance_type: str) -> str:
    """Change EC2 instance type (requires instance to be stopped)."""
    try:
        ec2 = boto3.client('ec2', region_name='us-east-1')
        
        # Check if instance is stopped
        response = ec2.describe_instances(InstanceIds=[instance_id])
        state = response['Reservations'][0]['Instances'][0]['State']['Name']
        
        if state != 'stopped':
            return f"Instance {instance_id} must be stopped before changing type. Current state: {state}"
        
        ec2.modify_instance_attribute(
            InstanceId=instance_id,
            InstanceType={'Value': new_instance_type}
        )
        
        return f"Changed instance type for {instance_id} to {new_instance_type}"
        
    except Exception as e:
        return f"Error modifying instance type: {str(e)}"


@tool
def create_ec2_snapshot(instance_id: str, description: str = None) -> str:
    """Create snapshots of all EBS volumes attached to an EC2 instance."""
    try:
        ec2 = boto3.client('ec2', region_name='us-east-1')
        
        # Get instance volumes
        response = ec2.describe_instances(InstanceIds=[instance_id])
        instance = response['Reservations'][0]['Instances'][0]
        
        snapshots = []
        for bdm in instance.get('BlockDeviceMappings', []):
            if 'Ebs' in bdm:
                volume_id = bdm['Ebs']['VolumeId']
                device_name = bdm['DeviceName']
                
                snapshot_desc = description or f"Snapshot of {volume_id} from {instance_id}"
                
                snapshot_response = ec2.create_snapshot(
                    VolumeId=volume_id,
                    Description=snapshot_desc,
                    TagSpecifications=[{
                        'ResourceType': 'snapshot',
                        'Tags': [
                            {'Key': 'SourceInstance', 'Value': instance_id},
                            {'Key': 'DeviceName', 'Value': device_name},
                            {'Key': 'CreatedBy', 'Value': 'CloudOps-SRE'}
                        ]
                    }]
                )
                
                snapshots.append({
                    'snapshot_id': snapshot_response['SnapshotId'],
                    'volume_id': volume_id,
                    'device_name': device_name
                })
        
        return f"Created {len(snapshots)} snapshots for instance {instance_id}: " + \
               ", ".join([s['snapshot_id'] for s in snapshots])
        
    except Exception as e:
        return f"Error creating snapshots: {str(e)}"


# ============================================================================
# Lambda Tools (Expanded)
# ============================================================================

@tool
def analyze_lambda_function(function_name: str) -> str:
    """Comprehensive Lambda function analysis including configuration, metrics, and logs."""
    try:
        lambda_client = boto3.client('lambda', region_name='us-east-1')
        cloudwatch = boto3.client('cloudwatch', region_name='us-east-1')
        
        # Get function configuration
        config = lambda_client.get_function(FunctionName=function_name)
        
        # Get recent metrics
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=1)
        
        metrics = {}
        metric_names = ['Invocations', 'Errors', 'Duration', 'Throttles']
        
        for metric_name in metric_names:
            response = cloudwatch.get_metric_statistics(
                Namespace='AWS/Lambda',
                MetricName=metric_name,
                Dimensions=[{'Name': 'FunctionName', 'Value': function_name}],
                StartTime=start_time,
                EndTime=end_time,
                Period=3600,
                Statistics=['Sum', 'Average', 'Maximum']
            )
            
            if response['Datapoints']:
                latest = max(response['Datapoints'], key=lambda x: x['Timestamp'])
                metrics[metric_name] = latest
        
        analysis = {
            'function_name': function_name,
            'runtime': config['Configuration']['Runtime'],
            'memory_size': config['Configuration']['MemorySize'],
            'timeout': config['Configuration']['Timeout'],
            'last_modified': config['Configuration']['LastModified'],
            'code_size': config['Configuration']['CodeSize'],
            'reserved_concurrency': config['Configuration'].get('ReservedConcurrentExecutions'),
            'environment_variables': list(config['Configuration'].get('Environment', {}).get('Variables', {}).keys()),
            'recent_metrics': metrics
        }
        
        return f"Lambda Analysis for {function_name}:\n" + json.dumps(analysis, indent=2, default=str)
        
    except Exception as e:
        return f"Error analyzing Lambda function: {str(e)}"


@tool
def update_lambda_memory(function_name: str, memory_size: int) -> str:
    """Update Lambda function memory allocation (128-10240 MB)."""
    try:
        lambda_client = boto3.client('lambda', region_name='us-east-1')
        
        if not (128 <= memory_size <= 10240):
            return "Memory size must be between 128 and 10240 MB"
        
        response = lambda_client.update_function_configuration(
            FunctionName=function_name,
            MemorySize=memory_size
        )
        
        return f"Updated memory for {function_name} to {memory_size} MB"
        
    except Exception as e:
        return f"Error updating Lambda memory: {str(e)}"


@tool
def update_lambda_timeout(function_name: str, timeout_seconds: int) -> str:
    """Update Lambda function timeout (1-900 seconds)."""
    try:
        lambda_client = boto3.client('lambda', region_name='us-east-1')
        
        if not (1 <= timeout_seconds <= 900):
            return "Timeout must be between 1 and 900 seconds"
        
        response = lambda_client.update_function_configuration(
            FunctionName=function_name,
            Timeout=timeout_seconds
        )
        
        return f"Updated timeout for {function_name} to {timeout_seconds} seconds"
        
    except Exception as e:
        return f"Error updating Lambda timeout: {str(e)}"


@tool
def invoke_lambda_function(function_name: str, payload: str = "{}") -> str:
    """Invoke a Lambda function with optional payload."""
    try:
        lambda_client = boto3.client('lambda', region_name='us-east-1')
        
        response = lambda_client.invoke(
            FunctionName=function_name,
            Payload=payload
        )
        
        result = {
            'status_code': response['StatusCode'],
            'execution_result': response.get('Payload', b'').read().decode('utf-8') if 'Payload' in response else None,
            'log_result': response.get('LogResult', ''),
            'function_error': response.get('FunctionError')
        }
        
        return f"Lambda invocation result:\n" + json.dumps(result, indent=2)
        
    except Exception as e:
        return f"Error invoking Lambda function: {str(e)}"


# ============================================================================
# Auto Scaling Tools (Expanded)
# ============================================================================

@tool
def analyze_auto_scaling_group(asg_name: str) -> str:
    """Comprehensive Auto Scaling Group analysis including instances, policies, and metrics."""
    try:
        asg_client = boto3.client('autoscaling', region_name='us-east-1')
        ec2 = boto3.client('ec2', region_name='us-east-1')
        cloudwatch = boto3.client('cloudwatch', region_name='us-east-1')
        
        # Get ASG details
        asg_response = asg_client.describe_auto_scaling_groups(
            AutoScalingGroupNames=[asg_name]
        )
        
        if not asg_response['AutoScalingGroups']:
            return f"Auto Scaling Group {asg_name} not found"
        
        asg = asg_response['AutoScalingGroups'][0]
        
        # Get scaling policies
        policies_response = asg_client.describe_policies(
            AutoScalingGroupName=asg_name
        )
        
        # Get instance health
        instances = []
        if asg['Instances']:
            instance_ids = [i['InstanceId'] for i in asg['Instances']]
            ec2_response = ec2.describe_instances(InstanceIds=instance_ids)
            
            for reservation in ec2_response['Reservations']:
                for instance in reservation['Instances']:
                    asg_instance = next(i for i in asg['Instances'] if i['InstanceId'] == instance['InstanceId'])
                    instances.append({
                        'instance_id': instance['InstanceId'],
                        'state': instance['State']['Name'],
                        'health_status': asg_instance['HealthStatus'],
                        'lifecycle_state': asg_instance['LifecycleState'],
                        'availability_zone': instance['Placement']['AvailabilityZone']
                    })
        
        analysis = {
            'asg_name': asg_name,
            'desired_capacity': asg['DesiredCapacity'],
            'min_size': asg['MinSize'],
            'max_size': asg['MaxSize'],
            'current_instances': len(asg['Instances']),
            'health_check_type': asg['HealthCheckType'],
            'health_check_grace_period': asg['HealthCheckGracePeriod'],
            'availability_zones': asg['AvailabilityZones'],
            'launch_template': asg.get('LaunchTemplate', {}).get('LaunchTemplateName', 'N/A'),
            'instances': instances,
            'scaling_policies': [p['PolicyName'] for p in policies_response['ScalingPolicies']]
        }
        
        return f"Auto Scaling Group Analysis for {asg_name}:\n" + json.dumps(analysis, indent=2, default=str)
        
    except Exception as e:
        return f"Error analyzing Auto Scaling Group: {str(e)}"


@tool
def create_scaling_policy(asg_name: str, policy_name: str, policy_type: str, 
                         target_value: float, metric_name: str) -> str:
    """Create a target tracking scaling policy for an Auto Scaling Group."""
    try:
        asg_client = boto3.client('autoscaling', region_name='us-east-1')
        
        if policy_type.lower() == 'target_tracking':
            policy_config = {
                'TargetValue': target_value,
                'PredefinedMetricSpecification': {
                    'PredefinedMetricType': metric_name
                }
            }
            
            response = asg_client.put_scaling_policy(
                AutoScalingGroupName=asg_name,
                PolicyName=policy_name,
                PolicyType='TargetTrackingScaling',
                TargetTrackingConfiguration=policy_config
            )
            
            return f"Created target tracking scaling policy {policy_name} for {asg_name} targeting {metric_name} at {target_value}"
        
        return f"Unsupported policy type: {policy_type}"
        
    except Exception as e:
        return f"Error creating scaling policy: {str(e)}"


# ============================================================================
# ECS Tools
# ============================================================================

@tool
def analyze_ecs_cluster(cluster_name: str) -> str:
    """Analyze ECS cluster including services, tasks, and capacity."""
    try:
        ecs = boto3.client('ecs', region_name='us-east-1')
        
        # Get cluster details
        cluster_response = ecs.describe_clusters(clusters=[cluster_name])
        if not cluster_response['clusters']:
            return f"ECS cluster {cluster_name} not found"
        
        cluster = cluster_response['clusters'][0]
        
        # Get services
        services_response = ecs.list_services(cluster=cluster_name)
        services = []
        
        if services_response['serviceArns']:
            services_detail = ecs.describe_services(
                cluster=cluster_name,
                services=services_response['serviceArns']
            )
            
            for service in services_detail['services']:
                services.append({
                    'name': service['serviceName'],
                    'status': service['status'],
                    'running_count': service['runningCount'],
                    'desired_count': service['desiredCount'],
                    'pending_count': service['pendingCount'],
                    'task_definition': service['taskDefinition'].split('/')[-1]
                })
        
        # Get container instances
        instances_response = ecs.list_container_instances(cluster=cluster_name)
        instance_count = len(instances_response['containerInstanceArns'])
        
        analysis = {
            'cluster_name': cluster_name,
            'status': cluster['status'],
            'running_tasks_count': cluster['runningTasksCount'],
            'pending_tasks_count': cluster['pendingTasksCount'],
            'active_services_count': cluster['activeServicesCount'],
            'registered_container_instances_count': cluster['registeredContainerInstancesCount'],
            'services': services
        }
        
        return f"ECS Cluster Analysis for {cluster_name}:\n" + json.dumps(analysis, indent=2)
        
    except Exception as e:
        return f"Error analyzing ECS cluster: {str(e)}"


@tool
def scale_ecs_service(cluster_name: str, service_name: str, desired_count: int) -> str:
    """Scale an ECS service to the desired number of tasks."""
    try:
        ecs = boto3.client('ecs', region_name='us-east-1')
        
        response = ecs.update_service(
            cluster=cluster_name,
            service=service_name,
            desiredCount=desired_count
        )
        
        return f"Scaled ECS service {service_name} in cluster {cluster_name} to {desired_count} tasks"
        
    except Exception as e:
        return f"Error scaling ECS service: {str(e)}"


@tool
def restart_ecs_service(cluster_name: str, service_name: str) -> str:
    """Force a new deployment of an ECS service (restarts all tasks)."""
    try:
        ecs = boto3.client('ecs', region_name='us-east-1')
        
        response = ecs.update_service(
            cluster=cluster_name,
            service=service_name,
            forceNewDeployment=True
        )
        
        return f"Initiated rolling restart for ECS service {service_name} in cluster {cluster_name}"
        
    except Exception as e:
        return f"Error restarting ECS service: {str(e)}"


# ============================================================================
# Lambda — List all functions
# ============================================================================

@tool
def list_lambda_functions(region: str = 'us-east-1') -> str:
    """List all Lambda functions in the account with runtime and last modified date."""
    try:
        lc = boto3.client('lambda', region_name=region)
        paginator = lc.get_paginator('list_functions')
        functions = []
        for page in paginator.paginate():
            for f in page['Functions']:
                functions.append({
                    'name': f['FunctionName'],
                    'runtime': f.get('Runtime', 'N/A'),
                    'memory_mb': f['MemorySize'],
                    'timeout_sec': f['Timeout'],
                    'last_modified': f['LastModified'][:10],
                    'state': f.get('State', 'Active'),
                })
        if not functions:
            return f"No Lambda functions found in {region}."
        lines = [f"Found {len(functions)} Lambda function(s) in {region}:\n"]
        for f in functions:
            lines.append(
                f"  {f['name']}  runtime={f['runtime']}  "
                f"memory={f['memory_mb']}MB  timeout={f['timeout_sec']}s  "
                f"modified={f['last_modified']}"
            )
        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing Lambda functions: {str(e)}"


# ============================================================================
# EC2 — Monitoring and metrics helpers
# ============================================================================

@tool
def enable_ec2_monitoring(instance_id: str) -> str:
    """Enable detailed CloudWatch monitoring for an EC2 instance."""
    try:
        ec2 = boto3.client('ec2', region_name='us-east-1')
        ec2.monitor_instances(InstanceIds=[instance_id])
        return f"Enabled detailed monitoring for EC2 instance {instance_id}."
    except Exception as e:
        return f"Error enabling EC2 monitoring: {str(e)}"


@tool
def get_ec2_metrics(instance_id: str, hours: int = 1) -> str:
    """Get key CloudWatch metrics for an EC2 instance (CPU, network, disk)."""
    try:
        cw = boto3.client('cloudwatch', region_name='us-east-1')
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=hours)
        dims = [{'Name': 'InstanceId', 'Value': instance_id}]

        metric_names = ['CPUUtilization', 'NetworkIn', 'NetworkOut',
                        'DiskReadBytes', 'DiskWriteBytes']
        results = {}
        for metric in metric_names:
            resp = cw.get_metric_statistics(
                Namespace='AWS/EC2', MetricName=metric,
                Dimensions=dims, StartTime=start_time, EndTime=end_time,
                Period=3600, Statistics=['Average', 'Maximum']
            )
            if resp['Datapoints']:
                dp = max(resp['Datapoints'], key=lambda x: x['Timestamp'])
                results[metric] = {
                    'average': round(dp.get('Average', 0), 2),
                    'maximum': round(dp.get('Maximum', 0), 2),
                }
            else:
                results[metric] = {'average': 0, 'maximum': 0}

        lines = [f"EC2 Metrics for {instance_id} (last {hours}h):\n"]
        for metric, vals in results.items():
            lines.append(f"  {metric}: avg={vals['average']}, max={vals['maximum']}")
        return '\n'.join(lines)
    except Exception as e:
        return f"Error getting EC2 metrics: {str(e)}"
