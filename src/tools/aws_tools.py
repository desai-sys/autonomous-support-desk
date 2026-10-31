"""
Extended AWS Tools — covers all remaining AWS services.
Complements compute_tools, storage_tools, network_tools, database_tools, security_tools.
"""

from src.core.aws_clients import boto3  # account-aware boto3 shim (cross-account support)
import json
from datetime import datetime, timedelta
from typing import List, Optional
from strands import tool


# ============================================================================
# CloudWatch Tools
# ============================================================================

@tool
def list_cloudwatch_alarms(state: str = 'all', region: str = 'us-east-1') -> str:
    """List CloudWatch alarms, optionally filtered by state (OK/ALARM/INSUFFICIENT_DATA/all)."""
    try:
        cw = boto3.client('cloudwatch', region_name=region)
        kwargs = {}
        if state != 'all':
            kwargs['StateValue'] = state.upper()
        response = cw.describe_alarms(**kwargs)
        alarms = []
        for alarm in response['MetricAlarms']:
            alarms.append({
                'name': alarm['AlarmName'],
                'state': alarm['StateValue'],
                'metric': alarm.get('MetricName', 'N/A'),
                'namespace': alarm.get('Namespace', 'N/A'),
                'threshold': alarm.get('Threshold'),
                'comparison': alarm.get('ComparisonOperator'),
                'updated': alarm['StateUpdatedTimestamp'].isoformat(),
            })
        if not alarms:
            return f"No CloudWatch alarms found (filter: {state})."
        return f"Found {len(alarms)} CloudWatch alarm(s):\n" + json.dumps(alarms, indent=2, default=str)
    except Exception as e:
        return f"Error listing CloudWatch alarms: {str(e)}"


@tool
def create_cloudwatch_alarm(alarm_name: str, metric_name: str, namespace: str,
                            threshold: float, comparison_operator: str,
                            evaluation_periods: int = 2, period: int = 300,
                            statistic: str = 'Average', sns_topic_arn: str = '') -> str:
    """Create a CloudWatch metric alarm with optional SNS notification."""
    try:
        cw = boto3.client('cloudwatch', region_name='us-east-1')
        kwargs = dict(
            AlarmName=alarm_name,
            MetricName=metric_name,
            Namespace=namespace,
            Threshold=threshold,
            ComparisonOperator=comparison_operator,
            EvaluationPeriods=evaluation_periods,
            Period=period,
            Statistic=statistic,
            TreatMissingData='notBreaching',
        )
        if sns_topic_arn:
            kwargs['AlarmActions'] = [sns_topic_arn]
        cw.put_metric_alarm(**kwargs)
        return f"Created CloudWatch alarm '{alarm_name}' on {namespace}/{metric_name} {comparison_operator} {threshold}."
    except Exception as e:
        return f"Error creating CloudWatch alarm: {str(e)}"


@tool
def get_cloudwatch_metrics(namespace: str, metric_name: str, dimensions: str = '[]',
                           hours: int = 1, period: int = 300, statistic: str = 'Average') -> str:
    """Retrieve CloudWatch metric statistics. dimensions is a JSON list of {Name, Value} dicts."""
    try:
        cw = boto3.client('cloudwatch', region_name='us-east-1')
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=hours)
        dims = json.loads(dimensions)
        response = cw.get_metric_statistics(
            Namespace=namespace,
            MetricName=metric_name,
            Dimensions=dims,
            StartTime=start_time,
            EndTime=end_time,
            Period=period,
            Statistics=[statistic],
        )
        datapoints = sorted(response['Datapoints'], key=lambda x: x['Timestamp'])
        result = {
            'namespace': namespace,
            'metric_name': metric_name,
            'statistic': statistic,
            'period_seconds': period,
            'hours': hours,
            'datapoints': [{'timestamp': dp['Timestamp'].isoformat(), 'value': dp[statistic]} for dp in datapoints],
        }
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return f"Error getting CloudWatch metrics: {str(e)}"


@tool
def list_log_groups(prefix: str = '', region: str = 'us-east-1') -> str:
    """List CloudWatch Logs log groups, optionally filtered by name prefix."""
    try:
        logs = boto3.client('logs', region_name=region)
        kwargs = {}
        if prefix:
            kwargs['logGroupNamePrefix'] = prefix
        response = logs.describe_log_groups(**kwargs)
        groups = []
        for g in response['logGroups']:
            groups.append({
                'name': g['logGroupName'],
                'retention_days': g.get('retentionInDays', 'Never expire'),
                'stored_bytes': g.get('storedBytes', 0),
                'created': datetime.fromtimestamp(g['creationTime'] / 1000).isoformat(),
            })
        return f"Found {len(groups)} log group(s):\n" + json.dumps(groups, indent=2, default=str)
    except Exception as e:
        return f"Error listing log groups: {str(e)}"


@tool
def get_log_events(log_group: str, log_stream: str, minutes: int = 30) -> str:
    """Retrieve recent log events from a CloudWatch Logs stream."""
    try:
        logs = boto3.client('logs', region_name='us-east-1')
        start_time = int((datetime.utcnow() - timedelta(minutes=minutes)).timestamp() * 1000)
        response = logs.get_log_events(
            logGroupName=log_group,
            logStreamName=log_stream,
            startTime=start_time,
            limit=100,
        )
        events = [{'timestamp': datetime.fromtimestamp(e['timestamp'] / 1000).isoformat(),
                   'message': e['message'].strip()} for e in response['events']]
        return f"Last {len(events)} log events from {log_group}/{log_stream}:\n" + json.dumps(events, indent=2)
    except Exception as e:
        return f"Error getting log events: {str(e)}"


@tool
def create_log_metric_filter(log_group: str, filter_name: str, filter_pattern: str,
                             metric_namespace: str, metric_name: str, metric_value: str = '1') -> str:
    """Create a CloudWatch Logs metric filter to extract metrics from log data."""
    try:
        logs = boto3.client('logs', region_name='us-east-1')
        logs.put_metric_filter(
            logGroupName=log_group,
            filterName=filter_name,
            filterPattern=filter_pattern,
            metricTransformations=[{
                'metricName': metric_name,
                'metricNamespace': metric_namespace,
                'metricValue': metric_value,
            }],
        )
        return f"Created metric filter '{filter_name}' on log group '{log_group}' -> {metric_namespace}/{metric_name}."
    except Exception as e:
        return f"Error creating log metric filter: {str(e)}"


# ============================================================================
# SNS Tools
# ============================================================================

@tool
def list_sns_topics(region: str = 'us-east-1') -> str:
    """List all SNS topics in the account."""
    try:
        sns = boto3.client('sns', region_name=region)
        response = sns.list_topics()
        topics = [t['TopicArn'] for t in response['Topics']]
        return f"Found {len(topics)} SNS topic(s):\n" + json.dumps(topics, indent=2)
    except Exception as e:
        return f"Error listing SNS topics: {str(e)}"


@tool
def create_sns_topic(topic_name: str, fifo: bool = False) -> str:
    """Create an SNS topic. Set fifo=True for FIFO topics (name must end with .fifo)."""
    try:
        sns = boto3.client('sns', region_name='us-east-1')
        attrs = {}
        if fifo:
            attrs['FifoTopic'] = 'true'
        response = sns.create_topic(Name=topic_name, Attributes=attrs)
        return f"Created SNS topic: {response['TopicArn']}"
    except Exception as e:
        return f"Error creating SNS topic: {str(e)}"


@tool
def publish_sns_message(topic_arn: str, message: str, subject: str = '') -> str:
    """Publish a message to an SNS topic."""
    try:
        sns = boto3.client('sns', region_name='us-east-1')
        kwargs = dict(TopicArn=topic_arn, Message=message)
        if subject:
            kwargs['Subject'] = subject
        response = sns.publish(**kwargs)
        return f"Published SNS message. MessageId: {response['MessageId']}"
    except Exception as e:
        return f"Error publishing SNS message: {str(e)}"


@tool
def list_sns_subscriptions(topic_arn: str = '') -> str:
    """List SNS subscriptions, optionally filtered by topic ARN."""
    try:
        sns = boto3.client('sns', region_name='us-east-1')
        if topic_arn:
            response = sns.list_subscriptions_by_topic(TopicArn=topic_arn)
        else:
            response = sns.list_subscriptions()
        subs = []
        for s in response['Subscriptions']:
            subs.append({'arn': s['SubscriptionArn'], 'protocol': s['Protocol'],
                         'endpoint': s['Endpoint'], 'topic': s['TopicArn']})
        return f"Found {len(subs)} subscription(s):\n" + json.dumps(subs, indent=2)
    except Exception as e:
        return f"Error listing SNS subscriptions: {str(e)}"


@tool
def subscribe_sns_topic(topic_arn: str, protocol: str, endpoint: str) -> str:
    """Subscribe an endpoint to an SNS topic. Protocol: email, sqs, lambda, https, sms."""
    try:
        sns = boto3.client('sns', region_name='us-east-1')
        response = sns.subscribe(TopicArn=topic_arn, Protocol=protocol, Endpoint=endpoint)
        return f"Subscribed {endpoint} to {topic_arn}. SubscriptionArn: {response['SubscriptionArn']}"
    except Exception as e:
        return f"Error subscribing to SNS topic: {str(e)}"


# ============================================================================
# SQS Tools
# ============================================================================

@tool
def list_sqs_queues(prefix: str = '', region: str = 'us-east-1') -> str:
    """List SQS queues, optionally filtered by name prefix."""
    try:
        sqs = boto3.client('sqs', region_name=region)
        kwargs = {}
        if prefix:
            kwargs['QueueNamePrefix'] = prefix
        response = sqs.list_queues(**kwargs)
        queues = response.get('QueueUrls', [])
        return f"Found {len(queues)} SQS queue(s):\n" + json.dumps(queues, indent=2)
    except Exception as e:
        return f"Error listing SQS queues: {str(e)}"


@tool
def analyze_sqs_queue(queue_url: str) -> str:
    """Analyze an SQS queue including message counts, age, and configuration."""
    try:
        sqs = boto3.client('sqs', region_name='us-east-1')
        attrs = [
            'ApproximateNumberOfMessages',
            'ApproximateNumberOfMessagesNotVisible',
            'ApproximateNumberOfMessagesDelayed',
            'CreatedTimestamp',
            'LastModifiedTimestamp',
            'VisibilityTimeout',
            'MaximumMessageSize',
            'MessageRetentionPeriod',
            'ReceiveMessageWaitTimeSeconds',
            'RedrivePolicy',
            'QueueArn',
        ]
        response = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=attrs)
        a = response['Attributes']
        analysis = {
            'queue_url': queue_url,
            'queue_arn': a.get('QueueArn'),
            'messages_available': int(a.get('ApproximateNumberOfMessages', 0)),
            'messages_in_flight': int(a.get('ApproximateNumberOfMessagesNotVisible', 0)),
            'messages_delayed': int(a.get('ApproximateNumberOfMessagesDelayed', 0)),
            'visibility_timeout_sec': int(a.get('VisibilityTimeout', 30)),
            'message_retention_sec': int(a.get('MessageRetentionPeriod', 345600)),
            'max_message_size_bytes': int(a.get('MaximumMessageSize', 262144)),
            'receive_wait_time_sec': int(a.get('ReceiveMessageWaitTimeSeconds', 0)),
            'redrive_policy': json.loads(a['RedrivePolicy']) if 'RedrivePolicy' in a else None,
            'created': datetime.fromtimestamp(int(a.get('CreatedTimestamp', 0))).isoformat(),
        }
        return f"SQS Queue Analysis:\n" + json.dumps(analysis, indent=2, default=str)
    except Exception as e:
        return f"Error analyzing SQS queue: {str(e)}"


@tool
def purge_sqs_queue(queue_url: str) -> str:
    """Purge all messages from an SQS queue. This action is irreversible."""
    try:
        sqs = boto3.client('sqs', region_name='us-east-1')
        sqs.purge_queue(QueueUrl=queue_url)
        return f"Purged all messages from SQS queue: {queue_url}"
    except Exception as e:
        return f"Error purging SQS queue: {str(e)}"


@tool
def send_sqs_message(queue_url: str, message_body: str, delay_seconds: int = 0) -> str:
    """Send a message to an SQS queue."""
    try:
        sqs = boto3.client('sqs', region_name='us-east-1')
        response = sqs.send_message(QueueUrl=queue_url, MessageBody=message_body, DelaySeconds=delay_seconds)
        return f"Sent SQS message. MessageId: {response['MessageId']}, MD5: {response['MD5OfMessageBody']}"
    except Exception as e:
        return f"Error sending SQS message: {str(e)}"


@tool
def get_sqs_queue_attributes(queue_url: str) -> str:
    """Get all attributes for an SQS queue."""
    try:
        sqs = boto3.client('sqs', region_name='us-east-1')
        response = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=['All'])
        return f"SQS Queue Attributes:\n" + json.dumps(response['Attributes'], indent=2)
    except Exception as e:
        return f"Error getting SQS queue attributes: {str(e)}"


# ============================================================================
# API Gateway Tools
# ============================================================================

@tool
def list_api_gateways(region: str = 'us-east-1') -> str:
    """List all REST APIs in API Gateway."""
    try:
        apigw = boto3.client('apigateway', region_name=region)
        response = apigw.get_rest_apis()
        apis = []
        for api in response['items']:
            apis.append({
                'id': api['id'],
                'name': api['name'],
                'description': api.get('description', ''),
                'created': api['createdDate'].isoformat(),
                'endpoint_type': api.get('endpointConfiguration', {}).get('types', []),
            })
        return f"Found {len(apis)} REST API(s):\n" + json.dumps(apis, indent=2, default=str)
    except Exception as e:
        return f"Error listing API Gateways: {str(e)}"


@tool
def analyze_api_gateway(api_id: str) -> str:
    """Analyze an API Gateway REST API including resources, stages, and usage plans."""
    try:
        apigw = boto3.client('apigateway', region_name='us-east-1')
        api = apigw.get_rest_api(restApiId=api_id)
        resources = apigw.get_resources(restApiId=api_id)
        stages = apigw.get_stages(restApiId=api_id)
        analysis = {
            'api_id': api_id,
            'name': api['name'],
            'description': api.get('description', ''),
            'created': api['createdDate'].isoformat(),
            'endpoint_types': api.get('endpointConfiguration', {}).get('types', []),
            'resource_count': len(resources['items']),
            'resources': [{'path': r['path'], 'methods': list(r.get('resourceMethods', {}).keys())}
                          for r in resources['items']],
            'stages': [{'name': s['stageName'], 'deployment_id': s.get('deploymentId', 'N/A'),
                        'cache_enabled': s.get('cacheClusterEnabled', False),
                        'logging_level': s.get('defaultRouteSettings', {}).get('loggingLevel', 'N/A')}
                       for s in stages['item']],
        }
        return f"API Gateway Analysis for {api_id}:\n" + json.dumps(analysis, indent=2, default=str)
    except Exception as e:
        return f"Error analyzing API Gateway: {str(e)}"


@tool
def create_api_gateway_deployment(api_id: str, stage_name: str, description: str = '') -> str:
    """Create a new deployment for an API Gateway REST API."""
    try:
        apigw = boto3.client('apigateway', region_name='us-east-1')
        response = apigw.create_deployment(
            restApiId=api_id,
            stageName=stage_name,
            description=description or f"Deployed by CloudOps SRE at {datetime.utcnow().isoformat()}",
        )
        return f"Created deployment {response['id']} for API {api_id} to stage '{stage_name}'."
    except Exception as e:
        return f"Error creating API Gateway deployment: {str(e)}"


@tool
def get_api_gateway_stages(api_id: str) -> str:
    """List all stages for an API Gateway REST API."""
    try:
        apigw = boto3.client('apigateway', region_name='us-east-1')
        response = apigw.get_stages(restApiId=api_id)
        stages = []
        for s in response['item']:
            stages.append({
                'stage_name': s['stageName'],
                'deployment_id': s.get('deploymentId', 'N/A'),
                'description': s.get('description', ''),
                'created': s['createdDate'].isoformat(),
                'last_updated': s['lastUpdatedDate'].isoformat(),
                'cache_enabled': s.get('cacheClusterEnabled', False),
                'tracing_enabled': s.get('tracingEnabled', False),
            })
        return f"Found {len(stages)} stage(s) for API {api_id}:\n" + json.dumps(stages, indent=2, default=str)
    except Exception as e:
        return f"Error getting API Gateway stages: {str(e)}"


# ============================================================================
# CloudFormation Tools
# ============================================================================

@tool
def list_cloudformation_stacks(status_filter: str = 'all', region: str = 'us-east-1') -> str:
    """List CloudFormation stacks, optionally filtered by status (e.g. CREATE_COMPLETE, UPDATE_ROLLBACK_FAILED)."""
    try:
        cfn = boto3.client('cloudformation', region_name=region)
        kwargs = {}
        if status_filter != 'all':
            kwargs['StackStatusFilter'] = [status_filter]
        response = cfn.list_stacks(**kwargs)
        stacks = []
        for s in response['StackSummaries']:
            stacks.append({
                'name': s['StackName'],
                'status': s['StackStatus'],
                'created': s['CreationTime'].isoformat(),
                'updated': s.get('LastUpdatedTime', '').isoformat() if s.get('LastUpdatedTime') else None,
                'description': s.get('TemplateDescription', ''),
            })
        return f"Found {len(stacks)} stack(s):\n" + json.dumps(stacks, indent=2, default=str)
    except Exception as e:
        return f"Error listing CloudFormation stacks: {str(e)}"


@tool
def analyze_cloudformation_stack(stack_name: str) -> str:
    """Analyze a CloudFormation stack including outputs, parameters, and resources."""
    try:
        cfn = boto3.client('cloudformation', region_name='us-east-1')
        stack_response = cfn.describe_stacks(StackName=stack_name)
        stack = stack_response['Stacks'][0]
        resources_response = cfn.list_stack_resources(StackName=stack_name)
        analysis = {
            'stack_name': stack_name,
            'status': stack['StackStatus'],
            'status_reason': stack.get('StackStatusReason', ''),
            'created': stack['CreationTime'].isoformat(),
            'updated': stack.get('LastUpdatedTime', '').isoformat() if stack.get('LastUpdatedTime') else None,
            'description': stack.get('Description', ''),
            'parameters': {p['ParameterKey']: p['ParameterValue'] for p in stack.get('Parameters', [])},
            'outputs': {o['OutputKey']: o['OutputValue'] for o in stack.get('Outputs', [])},
            'tags': {t['Key']: t['Value'] for t in stack.get('Tags', [])},
            'resources': [{'logical_id': r['LogicalResourceId'], 'type': r['ResourceType'],
                           'status': r['ResourceStatus']} for r in resources_response['StackResourceSummaries']],
        }
        return f"CloudFormation Stack Analysis for {stack_name}:\n" + json.dumps(analysis, indent=2, default=str)
    except Exception as e:
        return f"Error analyzing CloudFormation stack: {str(e)}"


@tool
def get_stack_events(stack_name: str, max_events: int = 20) -> str:
    """Get recent CloudFormation stack events."""
    try:
        cfn = boto3.client('cloudformation', region_name='us-east-1')
        response = cfn.describe_stack_events(StackName=stack_name)
        events = []
        for e in response['StackEvents'][:max_events]:
            events.append({
                'timestamp': e['Timestamp'].isoformat(),
                'resource': e['LogicalResourceId'],
                'type': e['ResourceType'],
                'status': e['ResourceStatus'],
                'reason': e.get('ResourceStatusReason', ''),
            })
        return f"Last {len(events)} events for stack {stack_name}:\n" + json.dumps(events, indent=2, default=str)
    except Exception as e:
        return f"Error getting stack events: {str(e)}"


@tool
def detect_stack_drift(stack_name: str) -> str:
    """Initiate drift detection for a CloudFormation stack and return the detection ID."""
    try:
        cfn = boto3.client('cloudformation', region_name='us-east-1')
        response = cfn.detect_stack_drift(StackName=stack_name)
        detection_id = response['StackDriftDetectionId']
        return f"Drift detection initiated for stack '{stack_name}'. DetectionId: {detection_id}. Poll describe_stack_drift_detection_status for results."
    except Exception as e:
        return f"Error detecting stack drift: {str(e)}"


# ============================================================================
# CodePipeline Tools
# ============================================================================

@tool
def list_pipelines(region: str = 'us-east-1') -> str:
    """List all CodePipeline pipelines."""
    try:
        cp = boto3.client('codepipeline', region_name=region)
        response = cp.list_pipelines()
        pipelines = [{'name': p['name'], 'updated': p['updated'].isoformat()} for p in response['pipelines']]
        return f"Found {len(pipelines)} pipeline(s):\n" + json.dumps(pipelines, indent=2, default=str)
    except Exception as e:
        return f"Error listing pipelines: {str(e)}"


@tool
def get_pipeline_state(pipeline_name: str) -> str:
    """Get the current state of all stages in a CodePipeline pipeline."""
    try:
        cp = boto3.client('codepipeline', region_name='us-east-1')
        response = cp.get_pipeline_state(name=pipeline_name)
        stages = []
        for stage in response['stageStates']:
            latest_execution = stage.get('latestExecution', {})
            stages.append({
                'stage': stage['stageName'],
                'status': latest_execution.get('status', 'N/A'),
                'execution_id': latest_execution.get('pipelineExecutionId', 'N/A'),
                'actions': [{
                    'name': a['actionName'],
                    'status': a.get('latestExecution', {}).get('status', 'N/A'),
                    'summary': a.get('latestExecution', {}).get('summary', ''),
                } for a in stage.get('actionStates', [])],
            })
        return f"Pipeline state for '{pipeline_name}':\n" + json.dumps(stages, indent=2, default=str)
    except Exception as e:
        return f"Error getting pipeline state: {str(e)}"


@tool
def retry_pipeline_stage(pipeline_name: str, stage_name: str, pipeline_execution_id: str) -> str:
    """Retry a failed CodePipeline stage."""
    try:
        cp = boto3.client('codepipeline', region_name='us-east-1')
        cp.retry_stage_execution(
            pipelineName=pipeline_name,
            stageName=stage_name,
            pipelineExecutionId=pipeline_execution_id,
            retryMode='FAILED_ACTIONS',
        )
        return f"Retrying stage '{stage_name}' in pipeline '{pipeline_name}' (execution: {pipeline_execution_id})."
    except Exception as e:
        return f"Error retrying pipeline stage: {str(e)}"


@tool
def start_pipeline_execution(pipeline_name: str) -> str:
    """Start a new execution of a CodePipeline pipeline."""
    try:
        cp = boto3.client('codepipeline', region_name='us-east-1')
        response = cp.start_pipeline_execution(name=pipeline_name)
        return f"Started pipeline '{pipeline_name}'. ExecutionId: {response['pipelineExecutionId']}"
    except Exception as e:
        return f"Error starting pipeline execution: {str(e)}"


# ============================================================================
# ECR Tools
# ============================================================================

@tool
def list_ecr_repositories(region: str = 'us-east-1') -> str:
    """List all ECR repositories."""
    try:
        ecr = boto3.client('ecr', region_name=region)
        response = ecr.describe_repositories()
        repos = []
        for r in response['repositories']:
            repos.append({
                'name': r['repositoryName'],
                'uri': r['repositoryUri'],
                'created': r['createdAt'].isoformat(),
                'image_tag_mutability': r['imageTagMutability'],
                'scan_on_push': r.get('imageScanningConfiguration', {}).get('scanOnPush', False),
            })
        return f"Found {len(repos)} ECR repository(ies):\n" + json.dumps(repos, indent=2, default=str)
    except Exception as e:
        return f"Error listing ECR repositories: {str(e)}"


@tool
def analyze_ecr_repository(repository_name: str) -> str:
    """Analyze an ECR repository including image count, size, and lifecycle policy."""
    try:
        ecr = boto3.client('ecr', region_name='us-east-1')
        repo = ecr.describe_repositories(repositoryNames=[repository_name])['repositories'][0]
        images = ecr.describe_images(repositoryName=repository_name)['imageDetails']
        total_size = sum(img.get('imageSizeInBytes', 0) for img in images)
        try:
            lifecycle = ecr.get_lifecycle_policy(repositoryName=repository_name)
            lifecycle_policy = lifecycle.get('lifecyclePolicyText', 'None')
        except ecr.exceptions.LifecyclePolicyNotFoundException:
            lifecycle_policy = 'None configured'
        analysis = {
            'repository_name': repository_name,
            'uri': repo['repositoryUri'],
            'image_count': len(images),
            'total_size_mb': round(total_size / 1024 / 1024, 2),
            'image_tag_mutability': repo['imageTagMutability'],
            'scan_on_push': repo.get('imageScanningConfiguration', {}).get('scanOnPush', False),
            'lifecycle_policy': lifecycle_policy,
            'recent_images': sorted(
                [{'digest': img['imageDigest'][:20], 'tags': img.get('imageTags', []),
                  'pushed': img['imagePushedAt'].isoformat(),
                  'size_mb': round(img.get('imageSizeInBytes', 0) / 1024 / 1024, 2)}
                 for img in images],
                key=lambda x: x['pushed'], reverse=True
            )[:10],
        }
        return f"ECR Repository Analysis for {repository_name}:\n" + json.dumps(analysis, indent=2, default=str)
    except Exception as e:
        return f"Error analyzing ECR repository: {str(e)}"


@tool
def get_ecr_image_scan_results(repository_name: str, image_tag: str = 'latest') -> str:
    """Get vulnerability scan results for an ECR image."""
    try:
        ecr = boto3.client('ecr', region_name='us-east-1')
        response = ecr.describe_image_scan_findings(
            repositoryName=repository_name,
            imageId={'imageTag': image_tag},
        )
        findings = response.get('imageScanFindings', {})
        severity_counts = findings.get('findingSeverityCounts', {})
        top_findings = []
        for f in findings.get('findings', [])[:20]:
            top_findings.append({
                'name': f['name'],
                'severity': f['severity'],
                'description': f.get('description', '')[:200],
                'uri': f.get('uri', ''),
            })
        result = {
            'repository': repository_name,
            'image_tag': image_tag,
            'scan_status': response.get('imageScanStatus', {}).get('status', 'N/A'),
            'severity_counts': severity_counts,
            'top_findings': top_findings,
        }
        return f"ECR Image Scan Results for {repository_name}:{image_tag}:\n" + json.dumps(result, indent=2, default=str)
    except Exception as e:
        return f"Error getting ECR scan results: {str(e)}"


@tool
def delete_ecr_images(repository_name: str, image_digests: str) -> str:
    """Delete ECR images by digest. image_digests is a JSON list of digest strings."""
    try:
        ecr = boto3.client('ecr', region_name='us-east-1')
        digests = json.loads(image_digests)
        image_ids = [{'imageDigest': d} for d in digests]
        response = ecr.batch_delete_image(repositoryName=repository_name, imageIds=image_ids)
        deleted = [i['imageDigest'] for i in response.get('imageIds', [])]
        failures = response.get('failures', [])
        return f"Deleted {len(deleted)} image(s) from {repository_name}. Failures: {len(failures)}."
    except Exception as e:
        return f"Error deleting ECR images: {str(e)}"


# ============================================================================
# Kinesis Tools
# ============================================================================

@tool
def list_kinesis_streams(region: str = 'us-east-1') -> str:
    """List all Kinesis Data Streams."""
    try:
        kinesis = boto3.client('kinesis', region_name=region)
        response = kinesis.list_streams()
        streams = response.get('StreamNames', [])
        return f"Found {len(streams)} Kinesis stream(s):\n" + json.dumps(streams, indent=2)
    except Exception as e:
        return f"Error listing Kinesis streams: {str(e)}"


@tool
def analyze_kinesis_stream(stream_name: str) -> str:
    """Analyze a Kinesis Data Stream including shard count, retention, and metrics."""
    try:
        kinesis = boto3.client('kinesis', region_name='us-east-1')
        response = kinesis.describe_stream_summary(StreamName=stream_name)
        s = response['StreamDescriptionSummary']
        analysis = {
            'stream_name': stream_name,
            'stream_arn': s['StreamARN'],
            'status': s['StreamStatus'],
            'shard_count': s['OpenShardCount'],
            'retention_hours': s['RetentionPeriodHours'],
            'encryption_type': s.get('EncryptionType', 'NONE'),
            'created': s['StreamCreationTimestamp'].isoformat(),
            'consumer_count': s.get('ConsumerCount', 0),
        }
        return f"Kinesis Stream Analysis for {stream_name}:\n" + json.dumps(analysis, indent=2, default=str)
    except Exception as e:
        return f"Error analyzing Kinesis stream: {str(e)}"


@tool
def get_kinesis_shard_iterator(stream_name: str, shard_id: str,
                               iterator_type: str = 'LATEST') -> str:
    """Get a shard iterator for a Kinesis stream. iterator_type: LATEST, TRIM_HORIZON, AT_SEQUENCE_NUMBER."""
    try:
        kinesis = boto3.client('kinesis', region_name='us-east-1')
        response = kinesis.get_shard_iterator(
            StreamName=stream_name,
            ShardId=shard_id,
            ShardIteratorType=iterator_type,
        )
        return f"Shard iterator for {stream_name}/{shard_id}: {response['ShardIterator'][:80]}..."
    except Exception as e:
        return f"Error getting Kinesis shard iterator: {str(e)}"


# ============================================================================
# Step Functions Tools
# ============================================================================

@tool
def list_state_machines(region: str = 'us-east-1') -> str:
    """List all Step Functions state machines."""
    try:
        sfn = boto3.client('stepfunctions', region_name=region)
        response = sfn.list_state_machines()
        machines = []
        for m in response['stateMachines']:
            machines.append({
                'name': m['name'],
                'arn': m['stateMachineArn'],
                'type': m['type'],
                'created': m['creationDate'].isoformat(),
            })
        return f"Found {len(machines)} state machine(s):\n" + json.dumps(machines, indent=2, default=str)
    except Exception as e:
        return f"Error listing state machines: {str(e)}"


@tool
def analyze_state_machine(state_machine_arn: str) -> str:
    """Analyze a Step Functions state machine including definition and recent executions."""
    try:
        sfn = boto3.client('stepfunctions', region_name='us-east-1')
        sm = sfn.describe_state_machine(stateMachineArn=state_machine_arn)
        executions = sfn.list_executions(stateMachineArn=state_machine_arn, maxResults=10)
        recent = []
        for e in executions['executions']:
            recent.append({
                'name': e['name'],
                'status': e['status'],
                'started': e['startDate'].isoformat(),
                'stopped': e.get('stopDate', '').isoformat() if e.get('stopDate') else None,
            })
        analysis = {
            'name': sm['name'],
            'arn': state_machine_arn,
            'type': sm['type'],
            'status': sm['status'],
            'created': sm['creationDate'].isoformat(),
            'role_arn': sm['roleArn'],
            'logging_level': sm.get('loggingConfiguration', {}).get('level', 'OFF'),
            'tracing_enabled': sm.get('tracingConfiguration', {}).get('enabled', False),
            'recent_executions': recent,
        }
        return f"State Machine Analysis:\n" + json.dumps(analysis, indent=2, default=str)
    except Exception as e:
        return f"Error analyzing state machine: {str(e)}"


@tool
def start_state_machine_execution(state_machine_arn: str, input_json: str = '{}',
                                  execution_name: str = '') -> str:
    """Start a Step Functions state machine execution."""
    try:
        sfn = boto3.client('stepfunctions', region_name='us-east-1')
        kwargs = dict(stateMachineArn=state_machine_arn, input=input_json)
        if execution_name:
            kwargs['name'] = execution_name
        response = sfn.start_execution(**kwargs)
        return f"Started execution: {response['executionArn']}. Started at: {response['startDate'].isoformat()}"
    except Exception as e:
        return f"Error starting state machine execution: {str(e)}"


@tool
def stop_state_machine_execution(execution_arn: str, cause: str = '', error: str = '') -> str:
    """Stop a running Step Functions execution."""
    try:
        sfn = boto3.client('stepfunctions', region_name='us-east-1')
        kwargs = dict(executionArn=execution_arn)
        if cause:
            kwargs['cause'] = cause
        if error:
            kwargs['error'] = error
        sfn.stop_execution(**kwargs)
        return f"Stopped execution: {execution_arn}"
    except Exception as e:
        return f"Error stopping state machine execution: {str(e)}"


# ============================================================================
# EventBridge Tools
# ============================================================================

@tool
def list_event_buses(region: str = 'us-east-1') -> str:
    """List all EventBridge event buses."""
    try:
        eb = boto3.client('events', region_name=region)
        response = eb.list_event_buses()
        buses = [{'name': b['Name'], 'arn': b['Arn'], 'policy': 'set' if b.get('Policy') else 'none'}
                 for b in response['EventBuses']]
        return f"Found {len(buses)} event bus(es):\n" + json.dumps(buses, indent=2)
    except Exception as e:
        return f"Error listing event buses: {str(e)}"


@tool
def list_event_rules(event_bus: str = 'default', region: str = 'us-east-1') -> str:
    """List EventBridge rules on a given event bus."""
    try:
        eb = boto3.client('events', region_name=region)
        response = eb.list_rules(EventBusName=event_bus)
        rules = []
        for r in response['Rules']:
            rules.append({
                'name': r['Name'],
                'state': r['State'],
                'schedule': r.get('ScheduleExpression', ''),
                'event_pattern': r.get('EventPattern', ''),
                'description': r.get('Description', ''),
            })
        return f"Found {len(rules)} rule(s) on bus '{event_bus}':\n" + json.dumps(rules, indent=2)
    except Exception as e:
        return f"Error listing event rules: {str(e)}"


@tool
def enable_event_rule(rule_name: str, event_bus: str = 'default') -> str:
    """Enable a disabled EventBridge rule."""
    try:
        eb = boto3.client('events', region_name='us-east-1')
        eb.enable_rule(Name=rule_name, EventBusName=event_bus)
        return f"Enabled EventBridge rule '{rule_name}' on bus '{event_bus}'."
    except Exception as e:
        return f"Error enabling event rule: {str(e)}"


@tool
def disable_event_rule(rule_name: str, event_bus: str = 'default') -> str:
    """Disable an EventBridge rule."""
    try:
        eb = boto3.client('events', region_name='us-east-1')
        eb.disable_rule(Name=rule_name, EventBusName=event_bus)
        return f"Disabled EventBridge rule '{rule_name}' on bus '{event_bus}'."
    except Exception as e:
        return f"Error disabling event rule: {str(e)}"


@tool
def put_event(source: str, detail_type: str, detail: str, event_bus: str = 'default') -> str:
    """Put a custom event onto an EventBridge event bus. detail must be a JSON string."""
    try:
        eb = boto3.client('events', region_name='us-east-1')
        response = eb.put_events(Entries=[{
            'Source': source,
            'DetailType': detail_type,
            'Detail': detail,
            'EventBusName': event_bus,
        }])
        failed = response.get('FailedEntryCount', 0)
        if failed:
            return f"Event put failed. Errors: {json.dumps(response['Entries'], default=str)}"
        return f"Event published successfully. EventId: {response['Entries'][0].get('EventId', 'N/A')}"
    except Exception as e:
        return f"Error putting event: {str(e)}"


# ============================================================================
# Systems Manager (SSM) Tools
# ============================================================================

@tool
def list_ssm_parameters(path: str = '/', region: str = 'us-east-1') -> str:
    """List SSM Parameter Store parameters under a given path."""
    try:
        ssm = boto3.client('ssm', region_name=region)
        response = ssm.get_parameters_by_path(Path=path, Recursive=True, WithDecryption=False)
        params = []
        for p in response['Parameters']:
            params.append({
                'name': p['Name'],
                'type': p['Type'],
                'version': p['Version'],
                'last_modified': p['LastModifiedDate'].isoformat(),
            })
        return f"Found {len(params)} SSM parameter(s) under '{path}':\n" + json.dumps(params, indent=2, default=str)
    except Exception as e:
        return f"Error listing SSM parameters: {str(e)}"


@tool
def get_ssm_parameter(name: str, with_decryption: bool = True) -> str:
    """Get the value of an SSM Parameter Store parameter."""
    try:
        ssm = boto3.client('ssm', region_name='us-east-1')
        response = ssm.get_parameter(Name=name, WithDecryption=with_decryption)
        p = response['Parameter']
        return f"SSM Parameter '{name}':\n  Type: {p['Type']}\n  Version: {p['Version']}\n  Value: {p['Value']}"
    except Exception as e:
        return f"Error getting SSM parameter: {str(e)}"


@tool
def put_ssm_parameter(name: str, value: str, param_type: str = 'String',
                      description: str = '', overwrite: bool = True) -> str:
    """Create or update an SSM Parameter Store parameter."""
    try:
        ssm = boto3.client('ssm', region_name='us-east-1')
        kwargs = dict(Name=name, Value=value, Type=param_type, Overwrite=overwrite)
        if description:
            kwargs['Description'] = description
        response = ssm.put_parameter(**kwargs)
        return f"SSM parameter '{name}' saved. Version: {response['Version']}, Tier: {response.get('Tier', 'Standard')}"
    except Exception as e:
        return f"Error putting SSM parameter: {str(e)}"


@tool
def run_ssm_command(instance_ids: str, command: str, document_name: str = 'AWS-RunShellScript') -> str:
    """Run an SSM command on one or more EC2 instances. instance_ids is a JSON list."""
    try:
        ssm = boto3.client('ssm', region_name='us-east-1')
        ids = json.loads(instance_ids)
        response = ssm.send_command(
            InstanceIds=ids,
            DocumentName=document_name,
            Parameters={'commands': [command]},
            Comment=f"CloudOps SRE command at {datetime.utcnow().isoformat()}",
        )
        cmd = response['Command']
        return f"SSM command sent. CommandId: {cmd['CommandId']}. Status: {cmd['Status']}. Targets: {ids}"
    except Exception as e:
        return f"Error running SSM command: {str(e)}"


@tool
def get_ssm_command_output(command_id: str, instance_id: str) -> str:
    """Get the output of a previously run SSM command."""
    try:
        ssm = boto3.client('ssm', region_name='us-east-1')
        response = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
        return (f"SSM Command Output for {command_id} on {instance_id}:\n"
                f"  Status: {response['Status']}\n"
                f"  Stdout: {response.get('StandardOutputContent', '')}\n"
                f"  Stderr: {response.get('StandardErrorContent', '')}")
    except Exception as e:
        return f"Error getting SSM command output: {str(e)}"


@tool
def list_ssm_patch_baselines(region: str = 'us-east-1') -> str:
    """List SSM patch baselines."""
    try:
        ssm = boto3.client('ssm', region_name=region)
        response = ssm.describe_patch_baselines()
        baselines = []
        for b in response['BaselineIdentities']:
            baselines.append({
                'id': b['BaselineId'],
                'name': b['BaselineName'],
                'os': b['OperatingSystem'],
                'default': b['DefaultBaseline'],
                'description': b.get('BaselineDescription', ''),
            })
        return f"Found {len(baselines)} patch baseline(s):\n" + json.dumps(baselines, indent=2)
    except Exception as e:
        return f"Error listing SSM patch baselines: {str(e)}"


# ============================================================================
# Cost Explorer Tools
# ============================================================================

@tool
def get_cost_and_usage(days: int = 30, granularity: str = 'MONTHLY',
                       group_by: str = 'SERVICE') -> str:
    """Get AWS cost and usage data for the past N days, grouped by SERVICE or LINKED_ACCOUNT."""
    try:
        ce = boto3.client('ce', region_name='us-east-1')
        end = datetime.utcnow().strftime('%Y-%m-%d')
        start = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%d')
        response = ce.get_cost_and_usage(
            TimePeriod={'Start': start, 'End': end},
            Granularity=granularity,
            Metrics=['UnblendedCost'],
            GroupBy=[{'Type': 'DIMENSION', 'Key': group_by}],
        )
        results = []
        for period in response['ResultsByTime']:
            period_data = {'period': period['TimePeriod'], 'groups': []}
            for group in period['Groups']:
                period_data['groups'].append({
                    'key': group['Keys'][0],
                    'cost_usd': round(float(group['Metrics']['UnblendedCost']['Amount']), 4),
                })
            period_data['groups'].sort(key=lambda x: x['cost_usd'], reverse=True)
            results.append(period_data)
        return f"Cost and usage ({start} to {end}, grouped by {group_by}):\n" + json.dumps(results, indent=2)
    except Exception as e:
        return f"Error getting cost and usage: {str(e)}"


@tool
def get_cost_forecast(days_ahead: int = 30, granularity: str = 'MONTHLY') -> str:
    """Get AWS cost forecast for the next N days."""
    try:
        ce = boto3.client('ce', region_name='us-east-1')
        start = datetime.utcnow().strftime('%Y-%m-%d')
        end = (datetime.utcnow() + timedelta(days=days_ahead)).strftime('%Y-%m-%d')
        response = ce.get_cost_forecast(
            TimePeriod={'Start': start, 'End': end},
            Metric='UNBLENDED_COST',
            Granularity=granularity,
        )
        total = response['Total']
        result = {
            'forecast_period': f"{start} to {end}",
            'total_forecast_usd': round(float(total['Amount']), 2),
            'unit': total['Unit'],
            'forecast_results': [{'period': r['TimePeriod'],
                                   'mean_usd': round(float(r['MeanValue']), 2),
                                   'lower_bound': round(float(r.get('PredictionIntervalLowerBound', 0)), 2),
                                   'upper_bound': round(float(r.get('PredictionIntervalUpperBound', 0)), 2)}
                                  for r in response.get('ForecastResultsByTime', [])],
        }
        return f"Cost Forecast:\n" + json.dumps(result, indent=2)
    except Exception as e:
        return f"Error getting cost forecast: {str(e)}"


@tool
def get_rightsizing_recommendations(service: str = 'AmazonEC2') -> str:
    """Get AWS Cost Explorer rightsizing recommendations for a service."""
    try:
        ce = boto3.client('ce', region_name='us-east-1')
        response = ce.get_rightsizing_recommendation(Service=service)
        recs = []
        for r in response.get('RightsizingRecommendations', [])[:20]:
            current = r.get('CurrentInstance', {})
            recs.append({
                'instance_id': current.get('ResourceId', 'N/A'),
                'instance_type': current.get('ResourceDetails', {}).get('EC2ResourceDetails', {}).get('InstanceType', 'N/A'),
                'recommendation_type': r.get('RightsizingType', 'N/A'),
                'estimated_monthly_savings': r.get('ModifyRecommendationDetail', {}).get('TargetInstances', [{}])[0].get('EstimatedMonthlySavings', 'N/A') if r.get('ModifyRecommendationDetail') else 'N/A',
            })
        summary = response.get('Summary', {})
        return (f"Rightsizing Recommendations for {service}:\n"
                f"Total recommendations: {summary.get('TotalRecommendationCount', 0)}\n"
                f"Estimated monthly savings: ${summary.get('EstimatedTotalMonthlySavingsAmount', 0)}\n"
                + json.dumps(recs, indent=2, default=str))
    except Exception as e:
        return f"Error getting rightsizing recommendations: {str(e)}"


# ============================================================================
# AWS Backup Tools
# ============================================================================

@tool
def list_backup_plans(region: str = 'us-east-1') -> str:
    """List all AWS Backup plans."""
    try:
        backup = boto3.client('backup', region_name=region)
        response = backup.list_backup_plans()
        plans = []
        for p in response['BackupPlansList']:
            plans.append({
                'id': p['BackupPlanId'],
                'name': p['BackupPlanName'],
                'arn': p['BackupPlanArn'],
                'version_id': p['VersionId'],
                'created': p['CreationDate'].isoformat(),
            })
        return f"Found {len(plans)} backup plan(s):\n" + json.dumps(plans, indent=2, default=str)
    except Exception as e:
        return f"Error listing backup plans: {str(e)}"


@tool
def list_backup_jobs(days: int = 7, state: str = 'all', region: str = 'us-east-1') -> str:
    """List AWS Backup jobs from the past N days, optionally filtered by state."""
    try:
        backup = boto3.client('backup', region_name=region)
        kwargs = {}
        if state != 'all':
            kwargs['ByState'] = state.upper()
        response = backup.list_backup_jobs(**kwargs)
        jobs = []
        cutoff = datetime.utcnow() - timedelta(days=days)
        for j in response['BackupJobs']:
            if j['CreationDate'].replace(tzinfo=None) >= cutoff:
                jobs.append({
                    'job_id': j['BackupJobId'],
                    'resource_arn': j['ResourceArn'],
                    'resource_type': j['ResourceType'],
                    'state': j['State'],
                    'created': j['CreationDate'].isoformat(),
                    'completion_date': j.get('CompletionDate', '').isoformat() if j.get('CompletionDate') else None,
                    'backup_size_bytes': j.get('BackupSizeInBytes', 0),
                })
        return f"Found {len(jobs)} backup job(s) in last {days} days:\n" + json.dumps(jobs, indent=2, default=str)
    except Exception as e:
        return f"Error listing backup jobs: {str(e)}"


@tool
def start_backup_job(backup_vault_name: str, resource_arn: str, iam_role_arn: str) -> str:
    """Start an on-demand AWS Backup job."""
    try:
        backup = boto3.client('backup', region_name='us-east-1')
        response = backup.start_backup_job(
            BackupVaultName=backup_vault_name,
            ResourceArn=resource_arn,
            IamRoleArn=iam_role_arn,
        )
        return f"Started backup job. JobId: {response['BackupJobId']}, RecoveryPointArn: {response.get('RecoveryPointArn', 'pending')}"
    except Exception as e:
        return f"Error starting backup job: {str(e)}"


@tool
def get_backup_job_status(job_id: str) -> str:
    """Get the status of an AWS Backup job."""
    try:
        backup = boto3.client('backup', region_name='us-east-1')
        response = backup.describe_backup_job(BackupJobId=job_id)
        result = {
            'job_id': job_id,
            'state': response['State'],
            'status_message': response.get('StatusMessage', ''),
            'resource_arn': response['ResourceArn'],
            'resource_type': response['ResourceType'],
            'backup_size_bytes': response.get('BackupSizeInBytes', 0),
            'created': response['CreationDate'].isoformat(),
            'completion_date': response.get('CompletionDate', '').isoformat() if response.get('CompletionDate') else None,
            'percent_done': response.get('PercentDone', '0'),
        }
        return f"Backup Job Status:\n" + json.dumps(result, indent=2, default=str)
    except Exception as e:
        return f"Error getting backup job status: {str(e)}"


# ============================================================================
# Glue Tools
# ============================================================================

@tool
def list_glue_jobs(region: str = 'us-east-1') -> str:
    """List all AWS Glue ETL jobs."""
    try:
        glue = boto3.client('glue', region_name=region)
        response = glue.list_jobs()
        jobs = response.get('JobNames', [])
        return f"Found {len(jobs)} Glue job(s):\n" + json.dumps(jobs, indent=2)
    except Exception as e:
        return f"Error listing Glue jobs: {str(e)}"


@tool
def analyze_glue_job(job_name: str) -> str:
    """Analyze a Glue job including configuration and recent run history."""
    try:
        glue = boto3.client('glue', region_name='us-east-1')
        job = glue.get_job(JobName=job_name)['Job']
        runs = glue.get_job_runs(JobName=job_name, MaxResults=5)['JobRuns']
        analysis = {
            'job_name': job_name,
            'description': job.get('Description', ''),
            'role': job.get('Role', ''),
            'glue_version': job.get('GlueVersion', 'N/A'),
            'worker_type': job.get('WorkerType', 'N/A'),
            'number_of_workers': job.get('NumberOfWorkers', 0),
            'max_retries': job.get('MaxRetries', 0),
            'timeout_minutes': job.get('Timeout', 0),
            'created': job['CreatedOn'].isoformat(),
            'last_modified': job['LastModifiedOn'].isoformat(),
            'recent_runs': [{
                'run_id': r['Id'],
                'status': r['JobRunState'],
                'started': r['StartedOn'].isoformat(),
                'completed': r.get('CompletedOn', '').isoformat() if r.get('CompletedOn') else None,
                'execution_time_sec': r.get('ExecutionTime', 0),
                'error': r.get('ErrorMessage', ''),
            } for r in runs],
        }
        return f"Glue Job Analysis for {job_name}:\n" + json.dumps(analysis, indent=2, default=str)
    except Exception as e:
        return f"Error analyzing Glue job: {str(e)}"


@tool
def start_glue_job(job_name: str, arguments: str = '{}') -> str:
    """Start a Glue ETL job run. arguments is a JSON dict of job parameters."""
    try:
        glue = boto3.client('glue', region_name='us-east-1')
        args = json.loads(arguments)
        response = glue.start_job_run(JobName=job_name, Arguments=args)
        return f"Started Glue job '{job_name}'. RunId: {response['JobRunId']}"
    except Exception as e:
        return f"Error starting Glue job: {str(e)}"


@tool
def stop_glue_job(job_name: str, run_id: str) -> str:
    """Stop a running Glue job run."""
    try:
        glue = boto3.client('glue', region_name='us-east-1')
        glue.batch_stop_job_run(JobName=job_name, JobRunIds=[run_id])
        return f"Stopped Glue job run '{run_id}' for job '{job_name}'."
    except Exception as e:
        return f"Error stopping Glue job: {str(e)}"


@tool
def get_glue_job_run(job_name: str, run_id: str) -> str:
    """Get details of a specific Glue job run."""
    try:
        glue = boto3.client('glue', region_name='us-east-1')
        response = glue.get_job_run(JobName=job_name, RunId=run_id)
        r = response['JobRun']
        result = {
            'run_id': run_id,
            'job_name': job_name,
            'status': r['JobRunState'],
            'started': r['StartedOn'].isoformat(),
            'completed': r.get('CompletedOn', '').isoformat() if r.get('CompletedOn') else None,
            'execution_time_sec': r.get('ExecutionTime', 0),
            'dpu_seconds': r.get('DPUSeconds', 0),
            'error': r.get('ErrorMessage', ''),
            'log_group': r.get('LogGroupName', ''),
        }
        return f"Glue Job Run Details:\n" + json.dumps(result, indent=2, default=str)
    except Exception as e:
        return f"Error getting Glue job run: {str(e)}"


# ============================================================================
# Athena Tools
# ============================================================================

@tool
def list_athena_workgroups(region: str = 'us-east-1') -> str:
    """List all Athena workgroups."""
    try:
        athena = boto3.client('athena', region_name=region)
        response = athena.list_work_groups()
        wgs = []
        for w in response['WorkGroups']:
            wgs.append({'name': w['Name'], 'state': w['State'], 'description': w.get('Description', '')})
        return f"Found {len(wgs)} Athena workgroup(s):\n" + json.dumps(wgs, indent=2)
    except Exception as e:
        return f"Error listing Athena workgroups: {str(e)}"


@tool
def start_athena_query(query: str, database: str, output_location: str,
                       workgroup: str = 'primary') -> str:
    """Start an Athena query execution and return the query execution ID."""
    try:
        athena = boto3.client('athena', region_name='us-east-1')
        response = athena.start_query_execution(
            QueryString=query,
            QueryExecutionContext={'Database': database},
            ResultConfiguration={'OutputLocation': output_location},
            WorkGroup=workgroup,
        )
        return f"Started Athena query. ExecutionId: {response['QueryExecutionId']}"
    except Exception as e:
        return f"Error starting Athena query: {str(e)}"


@tool
def get_athena_query_results(query_execution_id: str, max_rows: int = 100) -> str:
    """Get results of a completed Athena query execution."""
    try:
        athena = boto3.client('athena', region_name='us-east-1')
        # Check status first
        status_response = athena.get_query_execution(QueryExecutionId=query_execution_id)
        state = status_response['QueryExecution']['Status']['State']
        if state != 'SUCCEEDED':
            reason = status_response['QueryExecution']['Status'].get('StateChangeReason', '')
            return f"Query {query_execution_id} is in state '{state}'. Reason: {reason}"
        results = athena.get_query_results(QueryExecutionId=query_execution_id, MaxResults=max_rows)
        columns = [c['Label'] for c in results['ResultSet']['ResultSetMetadata']['ColumnInfo']]
        rows = []
        for row in results['ResultSet']['Rows'][1:]:  # skip header row
            rows.append(dict(zip(columns, [d.get('VarCharValue', '') for d in row['Data']])))
        return f"Athena Query Results ({len(rows)} rows):\n" + json.dumps(rows, indent=2)
    except Exception as e:
        return f"Error getting Athena query results: {str(e)}"


# ============================================================================
# OpenSearch Tools — Service (managed domains)
# ============================================================================

@tool
def list_opensearch_domains(region: str = 'us-east-1') -> str:
    """List all OpenSearch Service (managed) domains."""
    try:
        os_client = boto3.client('opensearch', region_name=region)
        response = os_client.list_domain_names()
        domains = [d['DomainName'] for d in response['DomainNames']]
        return f"Found {len(domains)} OpenSearch domain(s):\n" + json.dumps(domains, indent=2)
    except Exception as e:
        return f"Error listing OpenSearch domains: {str(e)}"


@tool
def analyze_opensearch_domain(domain_name: str) -> str:
    """Analyze an OpenSearch Service domain including cluster config, storage, and access."""
    try:
        os_client = boto3.client('opensearch', region_name='us-east-1')
        response = os_client.describe_domain(DomainName=domain_name)
        d = response['DomainStatus']
        cluster = d.get('ClusterConfig', {})
        analysis = {
            'domain_name': domain_name,
            'domain_id': d['DomainId'],
            'arn': d['ARN'],
            'engine_version': d.get('EngineVersion', 'N/A'),
            'endpoint': d.get('Endpoint', d.get('Endpoints', {}).get('vpc', 'N/A')),
            'processing': d.get('Processing', False),
            'upgrade_processing': d.get('UpgradeProcessing', False),
            'instance_type': cluster.get('InstanceType', 'N/A'),
            'instance_count': cluster.get('InstanceCount', 0),
            'dedicated_master': cluster.get('DedicatedMasterEnabled', False),
            'master_type': cluster.get('DedicatedMasterType', 'N/A'),
            'zone_awareness': cluster.get('ZoneAwarenessEnabled', False),
            'ebs_enabled': d.get('EBSOptions', {}).get('EBSEnabled', False),
            'volume_size_gb': d.get('EBSOptions', {}).get('VolumeSize', 0),
            'encryption_at_rest': d.get('EncryptionAtRestOptions', {}).get('Enabled', False),
            'node_to_node_encryption': d.get('NodeToNodeEncryptionOptions', {}).get('Enabled', False),
            'vpc_id': d.get('VPCOptions', {}).get('VPCId', 'public'),
        }
        return f"OpenSearch Domain Analysis for {domain_name}:\n" + json.dumps(analysis, indent=2, default=str)
    except Exception as e:
        return f"Error analyzing OpenSearch domain: {str(e)}"


# ============================================================================
# OpenSearch Serverless Tools (aoss — separate service from managed OpenSearch)
# ============================================================================

@tool
def list_opensearch_serverless_collections(region: str = 'us-east-1') -> str:
    """List all OpenSearch Serverless collections in the account."""
    try:
        aoss = boto3.client('opensearchserverless', region_name=region)
        response = aoss.list_collections()
        items = response.get('collectionSummaries', [])
        if not items:
            return "No OpenSearch Serverless collections found."
        lines = [f"Found {len(items)} OpenSearch Serverless collection(s):\n"]
        for c in items:
            lines.append(
                f"  {c['id']}  name={c['name']}  "
                f"type={c.get('collectionType', 'N/A')}  "
                f"status={c.get('status', 'N/A')}"
            )
        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing OpenSearch Serverless collections: {str(e)}"


@tool
def analyze_opensearch_serverless_collection(collection_id: str, region: str = 'us-east-1') -> str:
    """Analyze an OpenSearch Serverless collection including endpoints, encryption, and access policies."""
    try:
        aoss = boto3.client('opensearchserverless', region_name=region)
        response = aoss.batch_get_collection(ids=[collection_id])
        items = response.get('collectionDetails', [])
        if not items:
            return f"Collection {collection_id} not found."
        c = items[0]
        analysis = {
            'id': c['id'],
            'name': c['name'],
            'status': c['status'],
            'type': c.get('type', 'N/A'),
            'description': c.get('description', ''),
            'collection_endpoint': c.get('collectionEndpoint', 'N/A'),
            'dashboard_endpoint': c.get('dashboardEndpoint', 'N/A'),
            'kms_key_arn': c.get('kmsKeyArn', 'AWS managed'),
            'created': c.get('createdDate', 0),
            'last_modified': c.get('lastModifiedDate', 0),
            'arn': c.get('arn', 'N/A'),
        }
        return f"OpenSearch Serverless Collection Analysis:\n" + json.dumps(analysis, indent=2, default=str)
    except Exception as e:
        return f"Error analyzing OpenSearch Serverless collection: {str(e)}"


@tool
def list_opensearch_serverless_indexes(collection_id: str, region: str = 'us-east-1') -> str:
    """List indexes in an OpenSearch Serverless collection."""
    try:
        aoss = boto3.client('opensearchserverless', region_name=region)
        # Get collection endpoint first
        resp = aoss.batch_get_collection(ids=[collection_id])
        items = resp.get('collectionDetails', [])
        if not items:
            return f"Collection {collection_id} not found."
        endpoint = items[0].get('collectionEndpoint', '')
        return (
            f"Collection {collection_id} endpoint: {endpoint}\n"
            f"To list indexes, use the OpenSearch REST API:\n"
            f"  GET {endpoint}/_cat/indices?v\n"
            f"Or use the AWS Console → OpenSearch Serverless → {items[0]['name']} → Indexes"
        )
    except Exception as e:
        return f"Error getting collection info: {str(e)}"


@tool
def list_opensearch_serverless_access_policies(region: str = 'us-east-1') -> str:
    """List all OpenSearch Serverless data access policies."""
    try:
        aoss = boto3.client('opensearchserverless', region_name=region)
        response = aoss.list_access_policies(type='data')
        policies = response.get('accessPolicySummaries', [])
        if not policies:
            return "No OpenSearch Serverless access policies found."
        lines = [f"Found {len(policies)} access policy(ies):\n"]
        for p in policies:
            lines.append(
                f"  {p['name']}  type={p['type']}  "
                f"created={p.get('createdDate', 'N/A')}"
            )
        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing OpenSearch Serverless access policies: {str(e)}"


@tool
def create_opensearch_serverless_collection(name: str, collection_type: str = 'SEARCH',
                                             description: str = '', region: str = 'us-east-1') -> str:
    """Create an OpenSearch Serverless collection. type: SEARCH, TIMESERIES, or VECTORSEARCH."""
    try:
        aoss = boto3.client('opensearchserverless', region_name=region)
        kwargs = dict(name=name, type=collection_type)
        if description:
            kwargs['description'] = description
        response = aoss.create_collection(**kwargs)
        c = response['createCollectionDetail']
        return (
            f"Creating OpenSearch Serverless collection '{name}'.\n"
            f"  ID: {c['id']}\n"
            f"  Status: {c['status']}\n"
            f"  Type: {c['type']}\n"
            f"Note: Collection takes 2-5 minutes to become ACTIVE."
        )
    except Exception as e:
        return f"Error creating OpenSearch Serverless collection: {str(e)}"


# ============================================================================
# MSK (Managed Kafka) Tools
# ============================================================================

@tool
def list_msk_clusters(region: str = 'us-east-1') -> str:
    """List all MSK (Managed Streaming for Kafka) clusters."""
    try:
        msk = boto3.client('kafka', region_name=region)
        response = msk.list_clusters()
        clusters = []
        for c in response['ClusterInfoList']:
            clusters.append({
                'name': c['ClusterName'],
                'arn': c['ClusterArn'],
                'state': c['State'],
                'kafka_version': c['CurrentBrokerSoftwareInfo']['KafkaVersion'],
                'broker_count': c['NumberOfBrokerNodes'],
                'created': c['CreationTime'].isoformat(),
            })
        return f"Found {len(clusters)} MSK cluster(s):\n" + json.dumps(clusters, indent=2, default=str)
    except Exception as e:
        return f"Error listing MSK clusters: {str(e)}"


@tool
def analyze_msk_cluster(cluster_arn: str) -> str:
    """Analyze an MSK cluster including broker details and configuration."""
    try:
        msk = boto3.client('kafka', region_name='us-east-1')
        response = msk.describe_cluster(ClusterArn=cluster_arn)
        c = response['ClusterInfo']
        broker_info = c.get('BrokerNodeGroupInfo', {})
        analysis = {
            'cluster_name': c['ClusterName'],
            'cluster_arn': cluster_arn,
            'state': c['State'],
            'kafka_version': c['CurrentBrokerSoftwareInfo']['KafkaVersion'],
            'broker_count': c['NumberOfBrokerNodes'],
            'broker_instance_type': broker_info.get('InstanceType', 'N/A'),
            'broker_storage_gb': broker_info.get('StorageInfo', {}).get('EbsStorageInfo', {}).get('VolumeSize', 0),
            'availability_zones': broker_info.get('ClientSubnets', []),
            'encryption_in_transit': c.get('EncryptionInfo', {}).get('EncryptionInTransit', {}).get('ClientBroker', 'N/A'),
            'enhanced_monitoring': c.get('EnhancedMonitoring', 'DEFAULT'),
            'created': c['CreationTime'].isoformat(),
        }
        return f"MSK Cluster Analysis:\n" + json.dumps(analysis, indent=2, default=str)
    except Exception as e:
        return f"Error analyzing MSK cluster: {str(e)}"


@tool
def reboot_msk_broker(cluster_arn: str, broker_ids: str) -> str:
    """Reboot MSK broker nodes. broker_ids is a JSON list of broker ID integers."""
    try:
        msk = boto3.client('kafka', region_name='us-east-1')
        ids = json.loads(broker_ids)
        response = msk.reboot_broker(ClusterArn=cluster_arn, BrokerIds=ids)
        return f"Reboot initiated for MSK brokers {ids}. ClusterOperationArn: {response['ClusterOperationArn']}"
    except Exception as e:
        return f"Error rebooting MSK broker: {str(e)}"


# ============================================================================
# AppSync Tools
# ============================================================================

@tool
def list_appsync_apis(region: str = 'us-east-1') -> str:
    """List all AppSync GraphQL APIs."""
    try:
        appsync = boto3.client('appsync', region_name=region)
        response = appsync.list_graphql_apis()
        apis = []
        for a in response['graphqlApis']:
            apis.append({
                'name': a['name'],
                'api_id': a['apiId'],
                'authentication_type': a['authenticationType'],
                'arn': a['arn'],
                'uris': a.get('uris', {}),
            })
        return f"Found {len(apis)} AppSync API(s):\n" + json.dumps(apis, indent=2)
    except Exception as e:
        return f"Error listing AppSync APIs: {str(e)}"


@tool
def analyze_appsync_api(api_id: str) -> str:
    """Analyze an AppSync GraphQL API including schema, resolvers, and data sources."""
    try:
        appsync = boto3.client('appsync', region_name='us-east-1')
        api = appsync.get_graphql_api(apiId=api_id)['graphqlApi']
        data_sources = appsync.list_data_sources(apiId=api_id)['dataSources']
        resolvers_info = []
        try:
            types = appsync.list_types(apiId=api_id, format='JSON')['types']
            for t in types[:5]:
                try:
                    res = appsync.list_resolvers(apiId=api_id, typeName=t['name'])
                    for r in res['resolvers']:
                        resolvers_info.append({'type': t['name'], 'field': r['fieldName'],
                                               'data_source': r.get('dataSourceName', 'N/A')})
                except Exception:
                    pass
        except Exception:
            pass
        analysis = {
            'api_id': api_id,
            'name': api['name'],
            'authentication_type': api['authenticationType'],
            'arn': api['arn'],
            'uris': api.get('uris', {}),
            'log_config': api.get('logConfig', {}),
            'xray_enabled': api.get('xrayEnabled', False),
            'data_sources': [{'name': ds['name'], 'type': ds['type']} for ds in data_sources],
            'sample_resolvers': resolvers_info[:10],
        }
        return f"AppSync API Analysis for {api_id}:\n" + json.dumps(analysis, indent=2, default=str)
    except Exception as e:
        return f"Error analyzing AppSync API: {str(e)}"


# ============================================================================
# Cognito Tools
# ============================================================================

@tool
def list_cognito_user_pools(region: str = 'us-east-1') -> str:
    """List all Cognito User Pools."""
    try:
        cognito = boto3.client('cognito-idp', region_name=region)
        response = cognito.list_user_pools(MaxResults=60)
        pools = []
        for p in response['UserPools']:
            pools.append({'id': p['Id'], 'name': p['Name'], 'created': p['CreationDate'].isoformat(),
                          'last_modified': p['LastModifiedDate'].isoformat()})
        return f"Found {len(pools)} Cognito User Pool(s):\n" + json.dumps(pools, indent=2, default=str)
    except Exception as e:
        return f"Error listing Cognito user pools: {str(e)}"


@tool
def analyze_cognito_user_pool(user_pool_id: str) -> str:
    """Analyze a Cognito User Pool including policies, MFA, and app clients."""
    try:
        cognito = boto3.client('cognito-idp', region_name='us-east-1')
        pool = cognito.describe_user_pool(UserPoolId=user_pool_id)['UserPool']
        clients = cognito.list_user_pool_clients(UserPoolId=user_pool_id, MaxResults=10)['UserPoolClients']
        analysis = {
            'pool_id': user_pool_id,
            'name': pool['Name'],
            'status': pool.get('Status', 'N/A'),
            'user_count': pool.get('EstimatedNumberOfUsers', 0),
            'mfa_configuration': pool.get('MfaConfiguration', 'OFF'),
            'password_policy': pool.get('Policies', {}).get('PasswordPolicy', {}),
            'email_verification': pool.get('AutoVerifiedAttributes', []),
            'username_attributes': pool.get('UsernameAttributes', []),
            'schema_attributes': [a['Name'] for a in pool.get('SchemaAttributes', [])],
            'app_clients': [{'client_id': c['ClientId'], 'name': c['ClientName']} for c in clients],
            'created': pool['CreationDate'].isoformat(),
            'last_modified': pool['LastModifiedDate'].isoformat(),
        }
        return f"Cognito User Pool Analysis for {user_pool_id}:\n" + json.dumps(analysis, indent=2, default=str)
    except Exception as e:
        return f"Error analyzing Cognito user pool: {str(e)}"


@tool
def list_cognito_users(user_pool_id: str, limit: int = 20, filter_str: str = '') -> str:
    """List users in a Cognito User Pool with optional filter."""
    try:
        cognito = boto3.client('cognito-idp', region_name='us-east-1')
        kwargs = dict(UserPoolId=user_pool_id, Limit=limit)
        if filter_str:
            kwargs['Filter'] = filter_str
        response = cognito.list_users(**kwargs)
        users = []
        for u in response['Users']:
            attrs = {a['Name']: a['Value'] for a in u.get('Attributes', [])}
            users.append({
                'username': u['Username'],
                'status': u['UserStatus'],
                'enabled': u['Enabled'],
                'email': attrs.get('email', 'N/A'),
                'created': u['UserCreateDate'].isoformat(),
                'last_modified': u['UserLastModifiedDate'].isoformat(),
            })
        return f"Found {len(users)} user(s) in pool {user_pool_id}:\n" + json.dumps(users, indent=2, default=str)
    except Exception as e:
        return f"Error listing Cognito users: {str(e)}"


# ============================================================================
# SES Tools
# ============================================================================

@tool
def get_ses_sending_stats(region: str = 'us-east-1') -> str:
    """Get SES email sending statistics for the past two weeks."""
    try:
        ses = boto3.client('ses', region_name=region)
        response = ses.get_send_statistics()
        datapoints = sorted(response['SendDataPoints'], key=lambda x: x['Timestamp'])
        totals = {'delivery_attempts': 0, 'bounces': 0, 'complaints': 0, 'rejects': 0}
        for dp in datapoints:
            totals['delivery_attempts'] += dp.get('DeliveryAttempts', 0)
            totals['bounces'] += dp.get('Bounces', 0)
            totals['complaints'] += dp.get('Complaints', 0)
            totals['rejects'] += dp.get('Rejects', 0)
        bounce_rate = round(totals['bounces'] / max(totals['delivery_attempts'], 1) * 100, 2)
        complaint_rate = round(totals['complaints'] / max(totals['delivery_attempts'], 1) * 100, 2)
        result = {
            'totals_last_14_days': totals,
            'bounce_rate_pct': bounce_rate,
            'complaint_rate_pct': complaint_rate,
            'recent_datapoints': [{'timestamp': dp['Timestamp'].isoformat(),
                                    'sent': dp.get('DeliveryAttempts', 0),
                                    'bounces': dp.get('Bounces', 0)} for dp in datapoints[-7:]],
        }
        return f"SES Sending Statistics:\n" + json.dumps(result, indent=2, default=str)
    except Exception as e:
        return f"Error getting SES sending stats: {str(e)}"


@tool
def list_ses_identities(identity_type: str = 'EmailAddress', region: str = 'us-east-1') -> str:
    """List SES verified identities (EmailAddress or Domain)."""
    try:
        ses = boto3.client('ses', region_name=region)
        response = ses.list_identities(IdentityType=identity_type)
        identities = response.get('Identities', [])
        if identities:
            verification = ses.get_identity_verification_attributes(Identities=identities)
            result = []
            for identity in identities:
                attrs = verification['VerificationAttributes'].get(identity, {})
                result.append({'identity': identity, 'status': attrs.get('VerificationStatus', 'N/A')})
        else:
            result = []
        return f"Found {len(result)} SES {identity_type} identity(ies):\n" + json.dumps(result, indent=2)
    except Exception as e:
        return f"Error listing SES identities: {str(e)}"


@tool
def check_ses_sending_quota(region: str = 'us-east-1') -> str:
    """Check SES sending quota and current usage."""
    try:
        ses = boto3.client('ses', region_name=region)
        response = ses.get_send_quota()
        result = {
            'max_24_hour_send': response['Max24HourSend'],
            'max_send_rate_per_second': response['MaxSendRate'],
            'sent_last_24_hours': response['SentLast24Hours'],
            'remaining_quota': response['Max24HourSend'] - response['SentLast24Hours'],
            'utilization_pct': round(response['SentLast24Hours'] / max(response['Max24HourSend'], 1) * 100, 2),
        }
        return f"SES Sending Quota:\n" + json.dumps(result, indent=2)
    except Exception as e:
        return f"Error checking SES sending quota: {str(e)}"


# ============================================================================
# CloudTrail Tools
# ============================================================================

@tool
def list_cloudtrail_trails(region: str = 'us-east-1') -> str:
    """List all CloudTrail trails."""
    try:
        ct = boto3.client('cloudtrail', region_name=region)
        response = ct.describe_trails(includeShadowTrails=False)
        trails = []
        for t in response['trailList']:
            trails.append({
                'name': t['Name'],
                'arn': t['TrailARN'],
                's3_bucket': t['S3BucketName'],
                'is_multi_region': t.get('IsMultiRegionTrail', False),
                'log_file_validation': t.get('LogFileValidationEnabled', False),
                'cloudwatch_logs_arn': t.get('CloudWatchLogsLogGroupArn', 'N/A'),
            })
        return f"Found {len(trails)} CloudTrail trail(s):\n" + json.dumps(trails, indent=2)
    except Exception as e:
        return f"Error listing CloudTrail trails: {str(e)}"


@tool
def analyze_cloudtrail_trail(trail_name: str) -> str:
    """Analyze a CloudTrail trail including status and event selectors."""
    try:
        ct = boto3.client('cloudtrail', region_name='us-east-1')
        trail = ct.get_trail(Name=trail_name)['Trail']
        status = ct.get_trail_status(Name=trail_name)
        try:
            selectors = ct.get_event_selectors(TrailName=trail_name)
            event_selectors = selectors.get('EventSelectors', [])
        except Exception:
            event_selectors = []
        analysis = {
            'trail_name': trail_name,
            'arn': trail['TrailARN'],
            's3_bucket': trail['S3BucketName'],
            'is_logging': status.get('IsLogging', False),
            'latest_delivery_time': status.get('LatestDeliveryTime', '').isoformat() if status.get('LatestDeliveryTime') else None,
            'latest_delivery_error': status.get('LatestDeliveryError', ''),
            'is_multi_region': trail.get('IsMultiRegionTrail', False),
            'log_file_validation': trail.get('LogFileValidationEnabled', False),
            'kms_key_id': trail.get('KMSKeyId', 'None'),
            'event_selectors': event_selectors,
        }
        return f"CloudTrail Trail Analysis for {trail_name}:\n" + json.dumps(analysis, indent=2, default=str)
    except Exception as e:
        return f"Error analyzing CloudTrail trail: {str(e)}"


@tool
def lookup_cloudtrail_events(lookup_attribute: str, lookup_value: str,
                             hours: int = 24, max_results: int = 20) -> str:
    """Look up CloudTrail events by attribute (EventName, Username, ResourceName, etc.)."""
    try:
        ct = boto3.client('cloudtrail', region_name='us-east-1')
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=hours)
        response = ct.lookup_events(
            LookupAttributes=[{'AttributeKey': lookup_attribute, 'AttributeValue': lookup_value}],
            StartTime=start_time,
            EndTime=end_time,
            MaxResults=max_results,
        )
        events = []
        for e in response['Events']:
            events.append({
                'event_id': e['EventId'],
                'event_name': e['EventName'],
                'event_time': e['EventTime'].isoformat(),
                'username': e.get('Username', 'N/A'),
                'source_ip': e.get('CloudTrailEvent', '{}'),
                'resources': [{'type': r.get('ResourceType', ''), 'name': r.get('ResourceName', '')}
                               for r in e.get('Resources', [])],
            })
        return f"Found {len(events)} CloudTrail event(s) for {lookup_attribute}={lookup_value}:\n" + json.dumps(events, indent=2, default=str)
    except Exception as e:
        return f"Error looking up CloudTrail events: {str(e)}"


# ============================================================================
# AWS Config Tools
# ============================================================================

@tool
def list_config_rules(region: str = 'us-east-1') -> str:
    """List all AWS Config rules."""
    try:
        config = boto3.client('config', region_name=region)
        response = config.describe_config_rules()
        rules = []
        for r in response['ConfigRules']:
            rules.append({
                'name': r['ConfigRuleName'],
                'arn': r['ConfigRuleArn'],
                'state': r.get('ConfigRuleState', 'N/A'),
                'source': r['Source']['Owner'],
                'source_identifier': r['Source'].get('SourceIdentifier', 'custom'),
                'description': r.get('Description', ''),
            })
        return f"Found {len(rules)} Config rule(s):\n" + json.dumps(rules, indent=2)
    except Exception as e:
        return f"Error listing Config rules: {str(e)}"


@tool
def get_config_compliance_summary(region: str = 'us-east-1') -> str:
    """Get AWS Config compliance summary across all rules."""
    try:
        config = boto3.client('config', region_name=region)
        response = config.get_compliance_summary_by_config_rule()
        summary = response['ComplianceSummary']
        result = {
            'compliant_rules': summary.get('CompliantResourceCount', {}).get('CappedCount', 0),
            'non_compliant_rules': summary.get('NonCompliantResourceCount', {}).get('CappedCount', 0),
            'compliance_time': summary.get('ComplianceSummaryTimestamp', '').isoformat() if summary.get('ComplianceSummaryTimestamp') else None,
        }
        return f"AWS Config Compliance Summary:\n" + json.dumps(result, indent=2, default=str)
    except Exception as e:
        return f"Error getting Config compliance summary: {str(e)}"


@tool
def get_noncompliant_resources(config_rule_name: str, region: str = 'us-east-1') -> str:
    """Get non-compliant resources for a specific AWS Config rule."""
    try:
        config = boto3.client('config', region_name=region)
        response = config.get_compliance_details_by_config_rule(
            ConfigRuleName=config_rule_name,
            ComplianceTypes=['NON_COMPLIANT'],
            Limit=50,
        )
        resources = []
        for r in response['EvaluationResults']:
            qualifier = r['EvaluationResultIdentifier']['EvaluationResultQualifier']
            resources.append({
                'resource_type': qualifier['ResourceType'],
                'resource_id': qualifier['ResourceId'],
                'compliance_type': r['ComplianceType'],
                'result_recorded_time': r['ResultRecordedTime'].isoformat(),
                'annotation': r.get('Annotation', ''),
            })
        return f"Non-compliant resources for rule '{config_rule_name}':\n" + json.dumps(resources, indent=2, default=str)
    except Exception as e:
        return f"Error getting non-compliant resources: {str(e)}"


# ============================================================================
# Trusted Advisor Tools
# ============================================================================

@tool
def get_trusted_advisor_checks(language: str = 'en', region: str = 'us-east-1') -> str:
    """List all available Trusted Advisor checks."""
    try:
        support = boto3.client('support', region_name='us-east-1')  # Support API is global
        response = support.describe_trusted_advisor_checks(language=language)
        checks = []
        for c in response['checks']:
            checks.append({
                'id': c['id'],
                'name': c['name'],
                'category': c['category'],
                'description': c['description'][:150] + '...' if len(c['description']) > 150 else c['description'],
            })
        return f"Found {len(checks)} Trusted Advisor check(s):\n" + json.dumps(checks, indent=2)
    except Exception as e:
        return f"Error getting Trusted Advisor checks: {str(e)}"


@tool
def get_trusted_advisor_check_result(check_id: str, language: str = 'en') -> str:
    """Get the result of a specific Trusted Advisor check."""
    try:
        support = boto3.client('support', region_name='us-east-1')
        response = support.describe_trusted_advisor_check_result(checkId=check_id, language=language)
        result = response['result']
        summary = result.get('categorySpecificSummary', {})
        check_result = {
            'check_id': check_id,
            'status': result['status'],
            'timestamp': result['timestamp'],
            'resources_summary': result.get('resourcesSummary', {}),
            'flagged_resources': result.get('flaggedResources', [])[:10],
        }
        return f"Trusted Advisor Check Result:\n" + json.dumps(check_result, indent=2, default=str)
    except Exception as e:
        return f"Error getting Trusted Advisor check result: {str(e)}"


# ============================================================================
# AWS Health Tools
# ============================================================================

@tool
def describe_aws_health_events(region_filter: str = 'us-east-1', event_status: str = 'open') -> str:
    """Describe AWS Health events affecting your account."""
    try:
        health = boto3.client('health', region_name='us-east-1')  # Health API is global
        filter_kwargs = {'filter': {'eventStatusCodes': [event_status]}}
        if region_filter:
            filter_kwargs['filter']['regions'] = [region_filter]
        response = health.describe_events(**filter_kwargs)
        events = []
        for e in response['events']:
            events.append({
                'arn': e['arn'],
                'service': e['service'],
                'event_type_code': e['eventTypeCode'],
                'event_type_category': e['eventTypeCategory'],
                'region': e.get('region', 'global'),
                'status': e['statusCode'],
                'start_time': e.get('startTime', '').isoformat() if e.get('startTime') else None,
                'end_time': e.get('endTime', '').isoformat() if e.get('endTime') else None,
            })
        return f"Found {len(events)} AWS Health event(s):\n" + json.dumps(events, indent=2, default=str)
    except Exception as e:
        return f"Error describing AWS Health events: {str(e)}"


@tool
def describe_affected_resources(event_arn: str) -> str:
    """Describe AWS resources affected by a specific Health event."""
    try:
        health = boto3.client('health', region_name='us-east-1')
        response = health.describe_affected_entities(filter={'eventArns': [event_arn]})
        entities = []
        for e in response['entities']:
            entities.append({
                'entity_arn': e.get('entityArn', 'N/A'),
                'entity_value': e.get('entityValue', 'N/A'),
                'entity_url': e.get('entityUrl', ''),
                'status': e.get('statusCode', 'N/A'),
                'last_updated': e.get('lastUpdatedTime', '').isoformat() if e.get('lastUpdatedTime') else None,
            })
        return f"Affected resources for event {event_arn}:\n" + json.dumps(entities, indent=2, default=str)
    except Exception as e:
        return f"Error describing affected resources: {str(e)}"


# ============================================================================
# Redshift Tools
# ============================================================================

@tool
def list_redshift_clusters(region: str = 'us-east-1') -> str:
    """List all Redshift clusters."""
    try:
        rs = boto3.client('redshift', region_name=region)
        response = rs.describe_clusters()
        clusters = []
        for c in response['Clusters']:
            clusters.append({
                'identifier': c['ClusterIdentifier'],
                'status': c['ClusterStatus'],
                'node_type': c['NodeType'],
                'number_of_nodes': c['NumberOfNodes'],
                'db_name': c['DBName'],
                'endpoint': c.get('Endpoint', {}).get('Address', 'N/A'),
                'port': c.get('Endpoint', {}).get('Port', 5439),
                'created': c['ClusterCreateTime'].isoformat(),
                'encrypted': c.get('Encrypted', False),
            })
        return f"Found {len(clusters)} Redshift cluster(s):\n" + json.dumps(clusters, indent=2, default=str)
    except Exception as e:
        return f"Error listing Redshift clusters: {str(e)}"


@tool
def analyze_redshift_cluster(cluster_identifier: str) -> str:
    """Analyze a Redshift cluster including performance, storage, and maintenance settings."""
    try:
        rs = boto3.client('redshift', region_name='us-east-1')
        response = rs.describe_clusters(ClusterIdentifier=cluster_identifier)
        c = response['Clusters'][0]
        analysis = {
            'identifier': cluster_identifier,
            'status': c['ClusterStatus'],
            'availability_status': c.get('ClusterAvailabilityStatus', 'N/A'),
            'node_type': c['NodeType'],
            'number_of_nodes': c['NumberOfNodes'],
            'db_name': c['DBName'],
            'master_username': c['MasterUsername'],
            'endpoint': c.get('Endpoint', {}).get('Address', 'N/A'),
            'port': c.get('Endpoint', {}).get('Port', 5439),
            'vpc_id': c.get('VpcId', 'N/A'),
            'encrypted': c.get('Encrypted', False),
            'kms_key_id': c.get('KmsKeyId', 'None'),
            'enhanced_vpc_routing': c.get('EnhancedVpcRouting', False),
            'publicly_accessible': c.get('PubliclyAccessible', False),
            'automated_snapshot_retention': c.get('AutomatedSnapshotRetentionPeriod', 0),
            'preferred_maintenance_window': c.get('PreferredMaintenanceWindow', 'N/A'),
            'cluster_version': c.get('ClusterVersion', 'N/A'),
            'allow_version_upgrade': c.get('AllowVersionUpgrade', True),
            'created': c['ClusterCreateTime'].isoformat(),
        }
        return f"Redshift Cluster Analysis for {cluster_identifier}:\n" + json.dumps(analysis, indent=2, default=str)
    except Exception as e:
        return f"Error analyzing Redshift cluster: {str(e)}"


@tool
def pause_redshift_cluster(cluster_identifier: str) -> str:
    """Pause a Redshift cluster to stop billing for compute."""
    try:
        rs = boto3.client('redshift', region_name='us-east-1')
        response = rs.pause_cluster(ClusterIdentifier=cluster_identifier)
        return f"Pausing Redshift cluster '{cluster_identifier}'. Status: {response['Cluster']['ClusterStatus']}"
    except Exception as e:
        return f"Error pausing Redshift cluster: {str(e)}"


@tool
def resume_redshift_cluster(cluster_identifier: str) -> str:
    """Resume a paused Redshift cluster."""
    try:
        rs = boto3.client('redshift', region_name='us-east-1')
        response = rs.resume_cluster(ClusterIdentifier=cluster_identifier)
        return f"Resuming Redshift cluster '{cluster_identifier}'. Status: {response['Cluster']['ClusterStatus']}"
    except Exception as e:
        return f"Error resuming Redshift cluster: {str(e)}"


# ============================================================================
# Neptune Tools
# ============================================================================

@tool
def list_neptune_clusters(region: str = 'us-east-1') -> str:
    """List all Neptune graph database clusters."""
    try:
        neptune = boto3.client('neptune', region_name=region)
        response = neptune.describe_db_clusters(Filters=[{'Name': 'engine', 'Values': ['neptune']}])
        clusters = []
        for c in response['DBClusters']:
            clusters.append({
                'identifier': c['DBClusterIdentifier'],
                'status': c['Status'],
                'engine': c['Engine'],
                'engine_version': c['EngineVersion'],
                'endpoint': c.get('Endpoint', 'N/A'),
                'reader_endpoint': c.get('ReaderEndpoint', 'N/A'),
                'multi_az': c.get('MultiAZ', False),
                'created': c['ClusterCreateTime'].isoformat(),
            })
        return f"Found {len(clusters)} Neptune cluster(s):\n" + json.dumps(clusters, indent=2, default=str)
    except Exception as e:
        return f"Error listing Neptune clusters: {str(e)}"


@tool
def analyze_neptune_cluster(cluster_identifier: str) -> str:
    """Analyze a Neptune cluster including instances, storage, and backup settings."""
    try:
        neptune = boto3.client('neptune', region_name='us-east-1')
        response = neptune.describe_db_clusters(DBClusterIdentifier=cluster_identifier)
        c = response['DBClusters'][0]
        analysis = {
            'identifier': cluster_identifier,
            'status': c['Status'],
            'engine_version': c['EngineVersion'],
            'endpoint': c.get('Endpoint', 'N/A'),
            'reader_endpoint': c.get('ReaderEndpoint', 'N/A'),
            'port': c.get('Port', 8182),
            'multi_az': c.get('MultiAZ', False),
            'storage_encrypted': c.get('StorageEncrypted', False),
            'kms_key_id': c.get('KmsKeyId', 'None'),
            'backup_retention_days': c.get('BackupRetentionPeriod', 1),
            'preferred_backup_window': c.get('PreferredBackupWindow', 'N/A'),
            'iam_auth_enabled': c.get('IAMDatabaseAuthenticationEnabled', False),
            'deletion_protection': c.get('DeletionProtection', False),
            'cluster_members': [{'instance': m['DBInstanceIdentifier'], 'writer': m['IsClusterWriter']}
                                 for m in c.get('DBClusterMembers', [])],
            'created': c['ClusterCreateTime'].isoformat(),
        }
        return f"Neptune Cluster Analysis for {cluster_identifier}:\n" + json.dumps(analysis, indent=2, default=str)
    except Exception as e:
        return f"Error analyzing Neptune cluster: {str(e)}"


# ============================================================================
# Timestream Tools
# ============================================================================

@tool
def list_timestream_databases(region: str = 'us-east-1') -> str:
    """List all Amazon Timestream databases."""
    try:
        ts = boto3.client('timestream-write', region_name=region)
        response = ts.list_databases()
        dbs = []
        for d in response['Databases']:
            dbs.append({
                'name': d['DatabaseName'],
                'arn': d['Arn'],
                'table_count': d['TableCount'],
                'kms_key_id': d.get('KmsKeyId', 'None'),
                'created': d['CreationTime'].isoformat(),
            })
        return f"Found {len(dbs)} Timestream database(s):\n" + json.dumps(dbs, indent=2, default=str)
    except Exception as e:
        return f"Error listing Timestream databases: {str(e)}"


@tool
def list_timestream_tables(database_name: str, region: str = 'us-east-1') -> str:
    """List all tables in a Timestream database."""
    try:
        ts = boto3.client('timestream-write', region_name=region)
        response = ts.list_tables(DatabaseName=database_name)
        tables = []
        for t in response['Tables']:
            retention = t.get('RetentionProperties', {})
            tables.append({
                'name': t['TableName'],
                'status': t['TableStatus'],
                'memory_retention_hours': retention.get('MemoryStoreRetentionPeriodInHours', 0),
                'magnetic_retention_days': retention.get('MagneticStoreRetentionPeriodInDays', 0),
                'created': t['CreationTime'].isoformat(),
            })
        return f"Found {len(tables)} table(s) in Timestream database '{database_name}':\n" + json.dumps(tables, indent=2, default=str)
    except Exception as e:
        return f"Error listing Timestream tables: {str(e)}"


# ============================================================================
# QLDB Tools
# ============================================================================

@tool
def list_qldb_ledgers(region: str = 'us-east-1') -> str:
    """List all QLDB ledgers."""
    try:
        qldb = boto3.client('qldb', region_name=region)
        response = qldb.list_ledgers()
        ledgers = []
        for l in response['Ledgers']:
            ledgers.append({
                'name': l['Name'],
                'state': l['State'],
                'created': l['CreationDateTime'].isoformat(),
            })
        return f"Found {len(ledgers)} QLDB ledger(s):\n" + json.dumps(ledgers, indent=2, default=str)
    except Exception as e:
        return f"Error listing QLDB ledgers: {str(e)}"


@tool
def analyze_qldb_ledger(ledger_name: str) -> str:
    """Analyze a QLDB ledger including permissions mode and export configuration."""
    try:
        qldb = boto3.client('qldb', region_name='us-east-1')
        response = qldb.describe_ledger(Name=ledger_name)
        analysis = {
            'name': ledger_name,
            'arn': response['Arn'],
            'state': response['State'],
            'permissions_mode': response['PermissionsMode'],
            'deletion_protection': response.get('DeletionProtection', True),
            'encryption_description': response.get('EncryptionDescription', {}),
            'created': response['CreationDateTime'].isoformat(),
        }
        return f"QLDB Ledger Analysis for {ledger_name}:\n" + json.dumps(analysis, indent=2, default=str)
    except Exception as e:
        return f"Error analyzing QLDB ledger: {str(e)}"


# ============================================================================
# DMS (Database Migration Service) Tools
# ============================================================================

@tool
def list_dms_tasks(region: str = 'us-east-1') -> str:
    """List all DMS replication tasks."""
    try:
        dms = boto3.client('dms', region_name=region)
        response = dms.describe_replication_tasks()
        tasks = []
        for t in response['ReplicationTasks']:
            tasks.append({
                'identifier': t['ReplicationTaskIdentifier'],
                'arn': t['ReplicationTaskArn'],
                'status': t['Status'],
                'migration_type': t['MigrationType'],
                'source_endpoint': t['SourceEndpointArn'].split(':')[-1],
                'target_endpoint': t['TargetEndpointArn'].split(':')[-1],
                'created': t['ReplicationTaskCreationDate'].isoformat(),
            })
        return f"Found {len(tasks)} DMS task(s):\n" + json.dumps(tasks, indent=2, default=str)
    except Exception as e:
        return f"Error listing DMS tasks: {str(e)}"


@tool
def analyze_dms_task(task_arn: str) -> str:
    """Analyze a DMS replication task including statistics and table mapping."""
    try:
        dms = boto3.client('dms', region_name='us-east-1')
        response = dms.describe_replication_tasks(Filters=[{'Name': 'replication-task-arn', 'Values': [task_arn]}])
        if not response['ReplicationTasks']:
            return f"DMS task {task_arn} not found."
        t = response['ReplicationTasks'][0]
        stats = t.get('ReplicationTaskStats', {})
        analysis = {
            'identifier': t['ReplicationTaskIdentifier'],
            'arn': task_arn,
            'status': t['Status'],
            'migration_type': t['MigrationType'],
            'full_load_progress_pct': stats.get('FullLoadProgressPercent', 0),
            'elapsed_time_ms': stats.get('ElapsedTimeMillis', 0),
            'tables_loaded': stats.get('TablesLoaded', 0),
            'tables_loading': stats.get('TablesLoading', 0),
            'tables_queued': stats.get('TablesQueued', 0),
            'tables_errored': stats.get('TablesErrored', 0),
            'start_date': t.get('ReplicationTaskStartDate', '').isoformat() if t.get('ReplicationTaskStartDate') else None,
            'stop_reason': t.get('StopReason', ''),
        }
        return f"DMS Task Analysis:\n" + json.dumps(analysis, indent=2, default=str)
    except Exception as e:
        return f"Error analyzing DMS task: {str(e)}"


@tool
def start_dms_task(task_arn: str, start_type: str = 'start-replication') -> str:
    """Start a DMS replication task. start_type: start-replication, resume-processing, reload-target."""
    try:
        dms = boto3.client('dms', region_name='us-east-1')
        response = dms.start_replication_task(
            ReplicationTaskArn=task_arn,
            StartReplicationTaskType=start_type,
        )
        return f"Started DMS task. Status: {response['ReplicationTask']['Status']}"
    except Exception as e:
        return f"Error starting DMS task: {str(e)}"


@tool
def stop_dms_task(task_arn: str) -> str:
    """Stop a running DMS replication task."""
    try:
        dms = boto3.client('dms', region_name='us-east-1')
        response = dms.stop_replication_task(ReplicationTaskArn=task_arn)
        return f"Stopped DMS task. Status: {response['ReplicationTask']['Status']}"
    except Exception as e:
        return f"Error stopping DMS task: {str(e)}"


# ============================================================================
# Transfer Family Tools
# ============================================================================

@tool
def list_transfer_servers(region: str = 'us-east-1') -> str:
    """List all AWS Transfer Family servers."""
    try:
        transfer = boto3.client('transfer', region_name=region)
        response = transfer.list_servers()
        servers = []
        for s in response['Servers']:
            servers.append({
                'server_id': s['ServerId'],
                'arn': s['Arn'],
                'state': s['State'],
                'endpoint_type': s['EndpointType'],
                'protocols': s.get('Protocols', []),
                'identity_provider': s['IdentityProviderType'],
                'user_count': s.get('UserCount', 0),
            })
        return f"Found {len(servers)} Transfer Family server(s):\n" + json.dumps(servers, indent=2)
    except Exception as e:
        return f"Error listing Transfer Family servers: {str(e)}"


@tool
def analyze_transfer_server(server_id: str) -> str:
    """Analyze an AWS Transfer Family server including users and configuration."""
    try:
        transfer = boto3.client('transfer', region_name='us-east-1')
        server = transfer.describe_server(ServerId=server_id)['Server']
        users = transfer.list_users(ServerId=server_id)['Users']
        analysis = {
            'server_id': server_id,
            'arn': server['Arn'],
            'state': server['State'],
            'endpoint_type': server['EndpointType'],
            'endpoint': server.get('EndpointDetails', {}).get('AddressAllocationIds', []),
            'protocols': server.get('Protocols', []),
            'identity_provider': server['IdentityProviderType'],
            'logging_role': server.get('LoggingRole', 'None'),
            'security_policy': server.get('SecurityPolicyName', 'N/A'),
            'users': [{'username': u['UserName'], 'role': u['Role'], 'home_directory': u.get('HomeDirectory', '/')}
                      for u in users],
        }
        return f"Transfer Family Server Analysis for {server_id}:\n" + json.dumps(analysis, indent=2, default=str)
    except Exception as e:
        return f"Error analyzing Transfer Family server: {str(e)}"


# ============================================================================
# DataSync Tools
# ============================================================================

@tool
def list_datasync_tasks(region: str = 'us-east-1') -> str:
    """List all DataSync tasks."""
    try:
        ds = boto3.client('datasync', region_name=region)
        response = ds.list_tasks()
        tasks = []
        for t in response['Tasks']:
            tasks.append({'task_arn': t['TaskArn'], 'name': t.get('Name', 'N/A'), 'status': t['Status']})
        return f"Found {len(tasks)} DataSync task(s):\n" + json.dumps(tasks, indent=2)
    except Exception as e:
        return f"Error listing DataSync tasks: {str(e)}"


@tool
def analyze_datasync_task(task_arn: str) -> str:
    """Analyze a DataSync task including source, destination, and schedule."""
    try:
        ds = boto3.client('datasync', region_name='us-east-1')
        task = ds.describe_task(TaskArn=task_arn)
        analysis = {
            'task_arn': task_arn,
            'name': task.get('Name', 'N/A'),
            'status': task['Status'],
            'source_location_arn': task['SourceLocationArn'],
            'destination_location_arn': task['DestinationLocationArn'],
            'cloud_watch_log_group_arn': task.get('CloudWatchLogGroupArn', 'None'),
            'schedule': task.get('Schedule', {}).get('ScheduleExpression', 'None'),
            'options': task.get('Options', {}),
            'created': task['CreationTime'].isoformat(),
        }
        return f"DataSync Task Analysis:\n" + json.dumps(analysis, indent=2, default=str)
    except Exception as e:
        return f"Error analyzing DataSync task: {str(e)}"


@tool
def start_datasync_task(task_arn: str) -> str:
    """Start a DataSync task execution."""
    try:
        ds = boto3.client('datasync', region_name='us-east-1')
        response = ds.start_task_execution(TaskArn=task_arn)
        return f"Started DataSync task execution. ExecutionArn: {response['TaskExecutionArn']}"
    except Exception as e:
        return f"Error starting DataSync task: {str(e)}"


# ============================================================================
# WAF Classic Tools
# ============================================================================

@tool
def list_waf_rules(region: str = 'us-east-1') -> str:
    """List WAF Classic rules (legacy WAF, not WAFv2)."""
    try:
        waf_classic = boto3.client('waf-regional', region_name=region)
        response = waf_classic.list_rules()
        rules = []
        for r in response['Rules']:
            rules.append({'rule_id': r['RuleId'], 'name': r['Name']})
        return f"Found {len(rules)} WAF Classic rule(s):\n" + json.dumps(rules, indent=2)
    except Exception as e:
        return f"Error listing WAF Classic rules: {str(e)}"


# ============================================================================
# Shield Tools
# ============================================================================

@tool
def get_shield_protection_status() -> str:
    """Get AWS Shield subscription status and protection summary."""
    try:
        shield = boto3.client('shield', region_name='us-east-1')
        subscription = shield.describe_subscription()['Subscription']
        result = {
            'start_time': subscription['StartTime'].isoformat(),
            'end_time': subscription.get('EndTime', '').isoformat() if subscription.get('EndTime') else 'Active',
            'time_commitment_in_days': subscription.get('TimeCommitmentInDays', 0),
            'auto_renew': subscription.get('AutoRenew', 'DISABLED'),
            'proactive_engagement_status': subscription.get('ProactiveEngagementStatus', 'N/A'),
        }
        return f"AWS Shield Subscription Status:\n" + json.dumps(result, indent=2, default=str)
    except Exception as e:
        return f"Error getting Shield protection status: {str(e)}"


@tool
def list_shield_protections() -> str:
    """List all AWS Shield Advanced protections."""
    try:
        shield = boto3.client('shield', region_name='us-east-1')
        response = shield.list_protections()
        protections = []
        for p in response['Protections']:
            protections.append({
                'id': p['Id'],
                'name': p['Name'],
                'resource_arn': p['ResourceArn'],
            })
        return f"Found {len(protections)} Shield protection(s):\n" + json.dumps(protections, indent=2)
    except Exception as e:
        return f"Error listing Shield protections: {str(e)}"


# ============================================================================
# Macie Tools
# ============================================================================

@tool
def get_macie_status() -> str:
    """Get Amazon Macie status and configuration for the current account."""
    try:
        macie = boto3.client('macie2', region_name='us-east-1')
        response = macie.get_macie_session()
        result = {
            'status': response['status'],
            'service_role': response.get('serviceRole', 'N/A'),
            'created': response['createdAt'].isoformat(),
            'updated': response['updatedAt'].isoformat(),
            'finding_publishing_frequency': response.get('findingPublishingFrequency', 'N/A'),
        }
        return f"Amazon Macie Status:\n" + json.dumps(result, indent=2, default=str)
    except Exception as e:
        return f"Error getting Macie status: {str(e)}"


@tool
def list_macie_findings(severity: str = 'HIGH', max_results: int = 20) -> str:
    """List Amazon Macie findings filtered by severity (LOW/MEDIUM/HIGH/CRITICAL)."""
    try:
        macie = boto3.client('macie2', region_name='us-east-1')
        response = macie.list_findings(
            findingCriteria={'criterion': {'severity.description': {'eq': [severity]}}},
            maxResults=max_results,
        )
        finding_ids = response.get('findingIds', [])
        if not finding_ids:
            return f"No Macie findings found with severity {severity}."
        details = macie.get_findings(findingIds=finding_ids)
        findings = []
        for f in details['findings']:
            findings.append({
                'id': f['id'],
                'type': f['type'],
                'severity': f['severity']['description'],
                'title': f['title'],
                'description': f['description'][:200],
                'resource_arn': f.get('resourcesAffected', {}).get('s3Bucket', {}).get('arn', 'N/A'),
                'created': f['createdAt'],
            })
        return f"Found {len(findings)} Macie finding(s) with severity {severity}:\n" + json.dumps(findings, indent=2, default=str)
    except Exception as e:
        return f"Error listing Macie findings: {str(e)}"


# ============================================================================
# Inspector Tools
# ============================================================================

@tool
def list_inspector_findings(severity: str = 'HIGH', max_results: int = 20, region: str = 'us-east-1') -> str:
    """List Amazon Inspector v2 findings filtered by severity."""
    try:
        inspector = boto3.client('inspector2', region_name=region)
        response = inspector.list_findings(
            filterCriteria={'severity': [{'comparison': 'EQUALS', 'value': severity}]},
            maxResults=max_results,
        )
        findings = []
        for f in response['findings']:
            findings.append({
                'finding_arn': f['findingArn'],
                'type': f['type'],
                'severity': f['severity'],
                'title': f['title'],
                'description': f.get('description', '')[:200],
                'resource_type': f.get('resources', [{}])[0].get('type', 'N/A'),
                'resource_id': f.get('resources', [{}])[0].get('id', 'N/A'),
                'first_observed': f['firstObservedAt'].isoformat(),
                'status': f['status'],
            })
        return f"Found {len(findings)} Inspector finding(s) with severity {severity}:\n" + json.dumps(findings, indent=2, default=str)
    except Exception as e:
        return f"Error listing Inspector findings: {str(e)}"


@tool
def get_inspector_coverage(region: str = 'us-east-1') -> str:
    """Get Amazon Inspector v2 coverage summary across resource types."""
    try:
        inspector = boto3.client('inspector2', region_name=region)
        response = inspector.list_coverage_statistics()
        counts = response.get('countsByGroup', [])
        result = {
            'total_covered_resources': response.get('totalCounts', 0),
            'coverage_by_group': [{'group_key': g['groupKey'], 'count': g['counts']} for g in counts],
        }
        return f"Inspector Coverage Summary:\n" + json.dumps(result, indent=2, default=str)
    except Exception as e:
        return f"Error getting Inspector coverage: {str(e)}"


# ============================================================================
# Security Hub Tools
# ============================================================================

@tool
def get_security_hub_findings(severity: str = 'HIGH', max_results: int = 20, region: str = 'us-east-1') -> str:
    """Get Security Hub findings filtered by severity (INFORMATIONAL/LOW/MEDIUM/HIGH/CRITICAL)."""
    try:
        sh = boto3.client('securityhub', region_name=region)
        response = sh.get_findings(
            Filters={'SeverityLabel': [{'Value': severity, 'Comparison': 'EQUALS'}],
                     'RecordState': [{'Value': 'ACTIVE', 'Comparison': 'EQUALS'}]},
            MaxResults=max_results,
        )
        findings = []
        for f in response['Findings']:
            findings.append({
                'id': f['Id'],
                'title': f['Title'],
                'description': f.get('Description', '')[:200],
                'severity': f['Severity']['Label'],
                'product_name': f.get('ProductName', 'N/A'),
                'resource_type': f.get('Resources', [{}])[0].get('Type', 'N/A'),
                'resource_id': f.get('Resources', [{}])[0].get('Id', 'N/A'),
                'created': f['CreatedAt'],
                'workflow_status': f.get('Workflow', {}).get('Status', 'N/A'),
            })
        return f"Found {len(findings)} Security Hub finding(s) with severity {severity}:\n" + json.dumps(findings, indent=2, default=str)
    except Exception as e:
        return f"Error getting Security Hub findings: {str(e)}"


@tool
def get_security_hub_summary(region: str = 'us-east-1') -> str:
    """Get a summary of Security Hub findings by severity and compliance status."""
    try:
        sh = boto3.client('securityhub', region_name=region)
        severities = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFORMATIONAL']
        summary = {}
        for sev in severities:
            response = sh.get_findings(
                Filters={'SeverityLabel': [{'Value': sev, 'Comparison': 'EQUALS'}],
                         'RecordState': [{'Value': 'ACTIVE', 'Comparison': 'EQUALS'}]},
                MaxResults=1,
            )
            # Use list_findings_v2 count if available, else approximate
            summary[sev] = len(response['Findings'])
        return f"Security Hub Findings Summary (active):\n" + json.dumps(summary, indent=2)
    except Exception as e:
        return f"Error getting Security Hub summary: {str(e)}"


@tool
def enable_security_hub_standard(standards_arn: str, region: str = 'us-east-1') -> str:
    """Enable a Security Hub security standard by ARN (e.g. CIS AWS Foundations, AWS Foundational)."""
    try:
        sh = boto3.client('securityhub', region_name=region)
        response = sh.batch_enable_standards(
            StandardsSubscriptionRequests=[{'StandardsArn': standards_arn}]
        )
        subs = response.get('StandardsSubscriptions', [])
        if subs:
            return f"Enabled Security Hub standard. SubscriptionArn: {subs[0]['StandardsSubscriptionArn']}, Status: {subs[0]['StandardsStatus']}"
        return f"No subscription returned for standard {standards_arn}."
    except Exception as e:
        return f"Error enabling Security Hub standard: {str(e)}"


# ============================================================================
# Detective Tools
# ============================================================================

@tool
def list_detective_graphs(region: str = 'us-east-1') -> str:
    """List all Amazon Detective behavior graphs."""
    try:
        detective = boto3.client('detective', region_name=region)
        response = detective.list_graphs()
        graphs = []
        for g in response['GraphList']:
            graphs.append({'arn': g['Arn'], 'created': g['CreatedTime'].isoformat()})
        return f"Found {len(graphs)} Detective graph(s):\n" + json.dumps(graphs, indent=2, default=str)
    except Exception as e:
        return f"Error listing Detective graphs: {str(e)}"


# ============================================================================
# Access Analyzer Tools
# ============================================================================

@tool
def list_access_analyzers(region: str = 'us-east-1') -> str:
    """List all IAM Access Analyzers."""
    try:
        aa = boto3.client('accessanalyzer', region_name=region)
        response = aa.list_analyzers()
        analyzers = []
        for a in response['analyzers']:
            analyzers.append({
                'name': a['name'],
                'arn': a['arn'],
                'type': a['type'],
                'status': a['status'],
                'created': a['createdAt'].isoformat(),
                'last_resource_analyzed': a.get('lastResourceAnalyzed', 'N/A'),
            })
        return f"Found {len(analyzers)} Access Analyzer(s):\n" + json.dumps(analyzers, indent=2, default=str)
    except Exception as e:
        return f"Error listing Access Analyzers: {str(e)}"


@tool
def list_access_analyzer_findings(analyzer_arn: str, status: str = 'ACTIVE', region: str = 'us-east-1') -> str:
    """List findings from an IAM Access Analyzer."""
    try:
        aa = boto3.client('accessanalyzer', region_name=region)
        response = aa.list_findings(analyzerArn=analyzer_arn, filter={'status': {'eq': [status]}})
        findings = []
        for f in response['findings']:
            findings.append({
                'id': f['id'],
                'resource_type': f['resourceType'],
                'resource': f['resource'],
                'status': f['status'],
                'condition': f.get('condition', {}),
                'principal': f.get('principal', {}),
                'action': f.get('action', []),
                'created': f['createdAt'].isoformat(),
            })
        return f"Found {len(findings)} Access Analyzer finding(s) (status={status}):\n" + json.dumps(findings, indent=2, default=str)
    except Exception as e:
        return f"Error listing Access Analyzer findings: {str(e)}"


# ============================================================================
# RAM (Resource Access Manager) Tools
# ============================================================================

@tool
def list_ram_resources(resource_owner: str = 'SELF', region: str = 'us-east-1') -> str:
    """List RAM shared resources. resource_owner: SELF or OTHER-ACCOUNTS."""
    try:
        ram = boto3.client('ram', region_name=region)
        response = ram.list_resources(resourceOwner=resource_owner)
        resources = []
        for r in response['resources']:
            resources.append({
                'arn': r['arn'],
                'type': r['type'],
                'resource_share_arn': r['resourceShareArn'],
                'status': r['status'],
                'created': r['creationTime'].isoformat(),
            })
        return f"Found {len(resources)} RAM resource(s) (owner={resource_owner}):\n" + json.dumps(resources, indent=2, default=str)
    except Exception as e:
        return f"Error listing RAM resources: {str(e)}"


@tool
def list_ram_resource_shares(resource_owner: str = 'SELF', region: str = 'us-east-1') -> str:
    """List RAM resource shares."""
    try:
        ram = boto3.client('ram', region_name=region)
        response = ram.get_resource_shares(resourceOwner=resource_owner)
        shares = []
        for s in response['resourceShares']:
            shares.append({
                'arn': s['resourceShareArn'],
                'name': s['name'],
                'status': s['status'],
                'allow_external_principals': s.get('allowExternalPrincipals', False),
                'created': s['creationTime'].isoformat(),
            })
        return f"Found {len(shares)} RAM resource share(s):\n" + json.dumps(shares, indent=2, default=str)
    except Exception as e:
        return f"Error listing RAM resource shares: {str(e)}"


# ============================================================================
# Service Quotas Tools
# ============================================================================

@tool
def list_service_quotas(service_code: str, region: str = 'us-east-1') -> str:
    """List service quotas for a given AWS service (e.g. ec2, lambda, s3)."""
    try:
        sq = boto3.client('service-quotas', region_name=region)
        response = sq.list_service_quotas(ServiceCode=service_code)
        quotas = []
        for q in response['Quotas']:
            quotas.append({
                'quota_name': q['QuotaName'],
                'quota_code': q['QuotaCode'],
                'value': q['Value'],
                'unit': q.get('Unit', 'None'),
                'adjustable': q.get('Adjustable', False),
                'global_quota': q.get('GlobalQuota', False),
            })
        return f"Found {len(quotas)} quota(s) for service '{service_code}':\n" + json.dumps(quotas, indent=2)
    except Exception as e:
        return f"Error listing service quotas: {str(e)}"


@tool
def request_quota_increase(service_code: str, quota_code: str, desired_value: float, region: str = 'us-east-1') -> str:
    """Request a service quota increase."""
    try:
        sq = boto3.client('service-quotas', region_name=region)
        response = sq.request_service_quota_increase(
            ServiceCode=service_code,
            QuotaCode=quota_code,
            DesiredValue=desired_value,
        )
        req = response['RequestedQuota']
        return (f"Quota increase requested. CaseId: {req['CaseId']}, "
                f"Status: {req['Status']}, Desired: {desired_value}")
    except Exception as e:
        return f"Error requesting quota increase: {str(e)}"


# ============================================================================
# Budgets Tools
# ============================================================================

@tool
def list_budgets(account_id: str, region: str = 'us-east-1') -> str:
    """List all AWS Budgets for an account."""
    try:
        budgets = boto3.client('budgets', region_name=region)
        response = budgets.describe_budgets(AccountId=account_id)
        budget_list = []
        for b in response['Budgets']:
            budget_list.append({
                'name': b['BudgetName'],
                'type': b['BudgetType'],
                'limit_amount': b['BudgetLimit']['Amount'],
                'limit_unit': b['BudgetLimit']['Unit'],
                'actual_spend': b.get('CalculatedSpend', {}).get('ActualSpend', {}).get('Amount', 'N/A'),
                'forecasted_spend': b.get('CalculatedSpend', {}).get('ForecastedSpend', {}).get('Amount', 'N/A'),
                'time_unit': b['TimeUnit'],
            })
        return f"Found {len(budget_list)} budget(s):\n" + json.dumps(budget_list, indent=2, default=str)
    except Exception as e:
        return f"Error listing budgets: {str(e)}"


@tool
def create_budget_alert(account_id: str, budget_name: str, limit_amount: float,
                        limit_unit: str, threshold_pct: float, sns_topic_arn: str) -> str:
    """Create an AWS Budget with an SNS alert at a given threshold percentage."""
    try:
        budgets = boto3.client('budgets', region_name='us-east-1')
        now = datetime.utcnow()
        budgets.create_budget(
            AccountId=account_id,
            Budget={
                'BudgetName': budget_name,
                'BudgetLimit': {'Amount': str(limit_amount), 'Unit': limit_unit},
                'TimeUnit': 'MONTHLY',
                'BudgetType': 'COST',
                'TimePeriod': {
                    'Start': datetime(now.year, now.month, 1),
                    'End': datetime(2087, 6, 15),
                },
            },
            NotificationsWithSubscribers=[{
                'Notification': {
                    'NotificationType': 'ACTUAL',
                    'ComparisonOperator': 'GREATER_THAN',
                    'Threshold': threshold_pct,
                    'ThresholdType': 'PERCENTAGE',
                },
                'Subscribers': [{'SubscriptionType': 'SNS', 'Address': sns_topic_arn}],
            }],
        )
        return f"Created budget '{budget_name}' with limit {limit_amount} {limit_unit} and {threshold_pct}% alert to {sns_topic_arn}."
    except Exception as e:
        return f"Error creating budget alert: {str(e)}"


# ============================================================================
# Organizations Tools
# ============================================================================

@tool
def list_org_accounts(region: str = 'us-east-1') -> str:
    """List all accounts in the AWS Organization."""
    try:
        org = boto3.client('organizations', region_name=region)
        response = org.list_accounts()
        accounts = []
        for a in response['Accounts']:
            accounts.append({
                'id': a['Id'],
                'name': a['Name'],
                'email': a['Email'],
                'status': a['Status'],
                'joined_method': a['JoinedMethod'],
                'joined_timestamp': a['JoinedTimestamp'].isoformat(),
            })
        return f"Found {len(accounts)} organization account(s):\n" + json.dumps(accounts, indent=2, default=str)
    except Exception as e:
        return f"Error listing organization accounts: {str(e)}"


@tool
def describe_org_policy(policy_id: str, region: str = 'us-east-1') -> str:
    """Describe an AWS Organizations policy (SCP, tag policy, etc.)."""
    try:
        org = boto3.client('organizations', region_name=region)
        response = org.describe_policy(PolicyId=policy_id)
        policy = response['Policy']
        result = {
            'policy_id': policy_id,
            'name': policy['PolicySummary']['Name'],
            'description': policy['PolicySummary']['Description'],
            'type': policy['PolicySummary']['Type'],
            'aws_managed': policy['PolicySummary']['AwsManaged'],
            'content': json.loads(policy['Content']),
        }
        return f"Organization Policy {policy_id}:\n" + json.dumps(result, indent=2, default=str)
    except Exception as e:
        return f"Error describing organization policy: {str(e)}"


# ============================================================================
# Control Tower Tools
# ============================================================================

@tool
def list_landing_zones(region: str = 'us-east-1') -> str:
    """List AWS Control Tower landing zones."""
    try:
        ct = boto3.client('controltower', region_name=region)
        response = ct.list_landing_zones()
        zones = []
        for z in response.get('landingZones', []):
            zones.append({'arn': z['arn']})
        if not zones:
            return "No Control Tower landing zones found (or Control Tower not enabled)."
        return f"Found {len(zones)} landing zone(s):\n" + json.dumps(zones, indent=2)
    except Exception as e:
        return f"Error listing Control Tower landing zones: {str(e)}"


# ============================================================================
# EKS Tools
# ============================================================================

@tool
def list_eks_clusters(region: str = 'us-east-1') -> str:
    """List all EKS clusters."""
    try:
        eks = boto3.client('eks', region_name=region)
        response = eks.list_clusters()
        clusters = response.get('clusters', [])
        return f"Found {len(clusters)} EKS cluster(s):\n" + json.dumps(clusters, indent=2)
    except Exception as e:
        return f"Error listing EKS clusters: {str(e)}"


@tool
def analyze_eks_cluster(cluster_name: str) -> str:
    """Analyze an EKS cluster including version, networking, logging, and node groups."""
    try:
        eks = boto3.client('eks', region_name='us-east-1')
        cluster = eks.describe_cluster(name=cluster_name)['cluster']
        node_groups = eks.list_nodegroups(clusterName=cluster_name)['nodegroups']
        ng_details = []
        for ng in node_groups[:5]:
            ng_info = eks.describe_nodegroup(clusterName=cluster_name, nodegroupName=ng)['nodegroup']
            ng_details.append({
                'name': ng,
                'status': ng_info['status'],
                'instance_types': ng_info.get('instanceTypes', []),
                'desired_size': ng_info.get('scalingConfig', {}).get('desiredSize', 0),
                'min_size': ng_info.get('scalingConfig', {}).get('minSize', 0),
                'max_size': ng_info.get('scalingConfig', {}).get('maxSize', 0),
                'ami_type': ng_info.get('amiType', 'N/A'),
                'capacity_type': ng_info.get('capacityType', 'ON_DEMAND'),
            })
        analysis = {
            'cluster_name': cluster_name,
            'status': cluster['status'],
            'kubernetes_version': cluster['version'],
            'endpoint': cluster.get('endpoint', 'N/A'),
            'role_arn': cluster['roleArn'],
            'vpc_id': cluster.get('resourcesVpcConfig', {}).get('vpcId', 'N/A'),
            'public_access': cluster.get('resourcesVpcConfig', {}).get('endpointPublicAccess', False),
            'private_access': cluster.get('resourcesVpcConfig', {}).get('endpointPrivateAccess', False),
            'logging_enabled': [t for t, v in cluster.get('logging', {}).get('clusterLogging', [{}])[0].get('types', {}).items() if v] if cluster.get('logging') else [],
            'platform_version': cluster.get('platformVersion', 'N/A'),
            'created': cluster['createdAt'].isoformat(),
            'node_groups': ng_details,
        }
        return f"EKS Cluster Analysis for {cluster_name}:\n" + json.dumps(analysis, indent=2, default=str)
    except Exception as e:
        return f"Error analyzing EKS cluster: {str(e)}"


@tool
def update_eks_nodegroup(cluster_name: str, nodegroup_name: str,
                         desired_size: int, min_size: int, max_size: int) -> str:
    """Update the scaling configuration of an EKS managed node group."""
    try:
        eks = boto3.client('eks', region_name='us-east-1')
        response = eks.update_nodegroup_config(
            clusterName=cluster_name,
            nodegroupName=nodegroup_name,
            scalingConfig={'minSize': min_size, 'maxSize': max_size, 'desiredSize': desired_size},
        )
        update = response['update']
        return f"Updated EKS node group '{nodegroup_name}' in cluster '{cluster_name}'. UpdateId: {update['id']}, Status: {update['status']}"
    except Exception as e:
        return f"Error updating EKS node group: {str(e)}"


# ============================================================================
# ECS Task Definition Tools
# ============================================================================

@tool
def list_ecs_task_definitions(family_prefix: str = '', region: str = 'us-east-1') -> str:
    """List ECS task definition families, optionally filtered by family prefix."""
    try:
        ecs = boto3.client('ecs', region_name=region)
        kwargs = {'status': 'ACTIVE'}
        if family_prefix:
            kwargs['familyPrefix'] = family_prefix
        response = ecs.list_task_definition_families(**kwargs)
        families = response.get('families', [])
        return f"Found {len(families)} ECS task definition family(ies):\n" + json.dumps(families, indent=2)
    except Exception as e:
        return f"Error listing ECS task definitions: {str(e)}"


@tool
def deregister_task_definition(task_definition_arn: str) -> str:
    """Deregister an ECS task definition revision."""
    try:
        ecs = boto3.client('ecs', region_name='us-east-1')
        response = ecs.deregister_task_definition(taskDefinition=task_definition_arn)
        td = response['taskDefinition']
        return f"Deregistered task definition {task_definition_arn}. Status: {td['status']}"
    except Exception as e:
        return f"Error deregistering task definition: {str(e)}"


# ============================================================================
# AWS Batch Tools
# ============================================================================

@tool
def list_batch_job_queues(region: str = 'us-east-1') -> str:
    """List all AWS Batch job queues."""
    try:
        batch = boto3.client('batch', region_name=region)
        response = batch.describe_job_queues()
        queues = []
        for q in response['jobQueues']:
            queues.append({
                'name': q['jobQueueName'],
                'arn': q['jobQueueArn'],
                'state': q['state'],
                'status': q['status'],
                'priority': q['priority'],
                'compute_environments': [ce['computeEnvironment'].split('/')[-1]
                                          for ce in q['computeEnvironmentOrder']],
            })
        return f"Found {len(queues)} Batch job queue(s):\n" + json.dumps(queues, indent=2)
    except Exception as e:
        return f"Error listing Batch job queues: {str(e)}"


@tool
def analyze_batch_job_queue(job_queue_name: str) -> str:
    """Analyze a Batch job queue including pending, running, and failed job counts."""
    try:
        batch = boto3.client('batch', region_name='us-east-1')
        queue = batch.describe_job_queues(jobQueues=[job_queue_name])['jobQueues'][0]
        job_counts = {}
        for status in ['SUBMITTED', 'PENDING', 'RUNNABLE', 'STARTING', 'RUNNING', 'SUCCEEDED', 'FAILED']:
            jobs = batch.list_jobs(jobQueue=job_queue_name, jobStatus=status)
            job_counts[status] = len(jobs['jobSummaryList'])
        analysis = {
            'queue_name': job_queue_name,
            'state': queue['state'],
            'status': queue['status'],
            'priority': queue['priority'],
            'compute_environments': [ce['computeEnvironment'].split('/')[-1]
                                      for ce in queue['computeEnvironmentOrder']],
            'job_counts': job_counts,
        }
        return f"Batch Job Queue Analysis for {job_queue_name}:\n" + json.dumps(analysis, indent=2)
    except Exception as e:
        return f"Error analyzing Batch job queue: {str(e)}"


@tool
def submit_batch_job(job_name: str, job_queue: str, job_definition: str,
                     parameters: str = '{}', container_overrides: str = '{}') -> str:
    """Submit a job to an AWS Batch job queue."""
    try:
        batch = boto3.client('batch', region_name='us-east-1')
        kwargs = dict(
            jobName=job_name,
            jobQueue=job_queue,
            jobDefinition=job_definition,
            parameters=json.loads(parameters),
        )
        overrides = json.loads(container_overrides)
        if overrides:
            kwargs['containerOverrides'] = overrides
        response = batch.submit_job(**kwargs)
        return f"Submitted Batch job '{job_name}'. JobId: {response['jobId']}"
    except Exception as e:
        return f"Error submitting Batch job: {str(e)}"


@tool
def cancel_batch_job(job_id: str, reason: str = 'Cancelled by CloudOps SRE') -> str:
    """Cancel or terminate an AWS Batch job."""
    try:
        batch = boto3.client('batch', region_name='us-east-1')
        batch.cancel_job(jobId=job_id, reason=reason)
        return f"Cancelled Batch job {job_id}. Reason: {reason}"
    except Exception as e:
        return f"Error cancelling Batch job: {str(e)}"


# ============================================================================
# SageMaker Tools — comprehensive coverage
# ============================================================================

@tool
def list_sagemaker_domains(region: str = 'us-east-1') -> str:
    """List all SageMaker Studio domains in the account."""
    try:
        sm = boto3.client('sagemaker', region_name=region)
        response = sm.list_domains()
        domains = []
        for d in response['Domains']:
            domains.append({
                'domain_id': d['DomainId'],
                'domain_name': d['DomainName'],
                'status': d['Status'],
                'url': d.get('Url', 'N/A'),
                'created': d['CreationTime'].isoformat(),
                'last_modified': d['LastModifiedTime'].isoformat(),
            })
        if not domains:
            return f"No SageMaker domains found in {region}."
        lines = [f"Found {len(domains)} SageMaker domain(s):\n"]
        for d in domains:
            lines.append(f"  {d['domain_id']}  name={d['domain_name']}  [{d['status']}]  url={d['url']}")
        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing SageMaker domains: {str(e)}"


@tool
def analyze_sagemaker_domain(domain_id: str, region: str = 'us-east-1') -> str:
    """Analyze a SageMaker Studio domain including auth mode, VPC config, and user profiles."""
    try:
        sm = boto3.client('sagemaker', region_name=region)
        domain = sm.describe_domain(DomainId=domain_id)
        profiles = sm.list_user_profiles(DomainIdEquals=domain_id)
        analysis = {
            'domain_id': domain_id,
            'domain_name': domain['DomainName'],
            'status': domain['Status'],
            'auth_mode': domain['AuthMode'],
            'url': domain.get('Url', 'N/A'),
            'vpc_id': domain.get('VpcId', 'N/A'),
            'subnet_ids': domain.get('SubnetIds', []),
            'kms_key_id': domain.get('KmsKeyId', 'None'),
            'app_network_access': domain.get('AppNetworkAccessType', 'N/A'),
            'user_profile_count': len(profiles['UserProfiles']),
            'user_profiles': [{'name': p['UserProfileName'], 'status': p['Status']}
                               for p in profiles['UserProfiles']],
            'created': domain['CreationTime'].isoformat(),
            'last_modified': domain['LastModifiedTime'].isoformat(),
        }
        return f"SageMaker Domain Analysis for {domain_id}:\n" + json.dumps(analysis, indent=2, default=str)
    except Exception as e:
        return f"Error analyzing SageMaker domain: {str(e)}"


@tool
def list_sagemaker_user_profiles(domain_id: str, region: str = 'us-east-1') -> str:
    """List all user profiles in a SageMaker Studio domain."""
    try:
        sm = boto3.client('sagemaker', region_name=region)
        response = sm.list_user_profiles(DomainIdEquals=domain_id)
        profiles = []
        for p in response['UserProfiles']:
            profiles.append({
                'name': p['UserProfileName'],
                'status': p['Status'],
                'created': p['CreationTime'].isoformat(),
                'last_modified': p['LastModifiedTime'].isoformat(),
            })
        if not profiles:
            return f"No user profiles found in domain {domain_id}."
        lines = [f"Found {len(profiles)} user profile(s) in domain {domain_id}:\n"]
        for p in profiles:
            lines.append(f"  {p['name']}  [{p['status']}]  created={p['created'][:10]}")
        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing SageMaker user profiles: {str(e)}"


@tool
def list_sagemaker_apps(domain_id: str, region: str = 'us-east-1') -> str:
    """List all running apps (JupyterServer, KernelGateway, etc.) in a SageMaker domain."""
    try:
        sm = boto3.client('sagemaker', region_name=region)
        response = sm.list_apps(DomainIdEquals=domain_id)
        apps = []
        for a in response['Apps']:
            apps.append({
                'app_name': a['AppName'],
                'app_type': a['AppType'],
                'status': a['Status'],
                'user_profile': a.get('UserProfileName', 'N/A'),
                'created': a['CreationTime'].isoformat(),
            })
        if not apps:
            return f"No apps found in domain {domain_id}."
        lines = [f"Found {len(apps)} app(s) in domain {domain_id}:\n"]
        for a in apps:
            lines.append(f"  {a['app_name']}  type={a['app_type']}  [{a['status']}]  user={a['user_profile']}")
        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing SageMaker apps: {str(e)}"


@tool
def list_sagemaker_models(region: str = 'us-east-1') -> str:
    """List all SageMaker models in the account."""
    try:
        sm = boto3.client('sagemaker', region_name=region)
        response = sm.list_models()
        models = []
        for m in response['Models']:
            models.append({
                'name': m['ModelName'],
                'arn': m['ModelArn'],
                'created': m['CreationTime'].isoformat(),
            })
        if not models:
            return f"No SageMaker models found in {region}."
        lines = [f"Found {len(models)} SageMaker model(s):\n"]
        for m in models:
            lines.append(f"  {m['name']}  created={m['created'][:10]}")
        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing SageMaker models: {str(e)}"


@tool
def list_sagemaker_notebooks(region: str = 'us-east-1') -> str:
    """List all SageMaker notebook instances."""
    try:
        sm = boto3.client('sagemaker', region_name=region)
        response = sm.list_notebook_instances()
        notebooks = []
        for n in response['NotebookInstances']:
            notebooks.append({
                'name': n['NotebookInstanceName'],
                'status': n['NotebookInstanceStatus'],
                'instance_type': n.get('InstanceType', 'N/A'),
                'url': n.get('Url', 'N/A'),
                'created': n['CreationTime'].isoformat(),
                'last_modified': n['LastModifiedTime'].isoformat(),
            })
        if not notebooks:
            return f"No SageMaker notebook instances found in {region}."
        lines = [f"Found {len(notebooks)} notebook instance(s):\n"]
        for n in notebooks:
            lines.append(f"  {n['name']}  [{n['status']}]  type={n['instance_type']}")
        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing SageMaker notebooks: {str(e)}"


@tool
def start_sagemaker_notebook(notebook_name: str, region: str = 'us-east-1') -> str:
    """Start a stopped SageMaker notebook instance."""
    try:
        sm = boto3.client('sagemaker', region_name=region)
        sm.start_notebook_instance(NotebookInstanceName=notebook_name)
        return f"Starting SageMaker notebook instance '{notebook_name}'."
    except Exception as e:
        return f"Error starting SageMaker notebook: {str(e)}"


@tool
def stop_sagemaker_notebook(notebook_name: str, region: str = 'us-east-1') -> str:
    """Stop a running SageMaker notebook instance."""
    try:
        sm = boto3.client('sagemaker', region_name=region)
        sm.stop_notebook_instance(NotebookInstanceName=notebook_name)
        return f"Stopping SageMaker notebook instance '{notebook_name}'."
    except Exception as e:
        return f"Error stopping SageMaker notebook: {str(e)}"


@tool
def list_sagemaker_pipelines(region: str = 'us-east-1') -> str:
    """List all SageMaker ML pipelines."""
    try:
        sm = boto3.client('sagemaker', region_name=region)
        response = sm.list_pipelines()
        pipelines = []
        for p in response['PipelineSummaries']:
            pipelines.append({
                'name': p['PipelineName'],
                'display_name': p.get('PipelineDisplayName', p['PipelineName']),
                'status': p.get('PipelineStatus', 'N/A'),
                'created': p['CreationTime'].isoformat(),
                'last_modified': p['LastModifiedTime'].isoformat(),
            })
        if not pipelines:
            return f"No SageMaker pipelines found in {region}."
        lines = [f"Found {len(pipelines)} SageMaker pipeline(s):\n"]
        for p in pipelines:
            lines.append(f"  {p['name']}  [{p['status']}]  created={p['created'][:10]}")
        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing SageMaker pipelines: {str(e)}"


@tool
def list_sagemaker_experiments(region: str = 'us-east-1') -> str:
    """List all SageMaker experiments."""
    try:
        sm = boto3.client('sagemaker', region_name=region)
        response = sm.list_experiments()
        experiments = []
        for e in response['ExperimentSummaries']:
            experiments.append({
                'name': e['ExperimentName'],
                'display_name': e.get('DisplayName', e['ExperimentName']),
                'created': e['CreationTime'].isoformat(),
                'last_modified': e['LastModifiedTime'].isoformat(),
            })
        if not experiments:
            return f"No SageMaker experiments found in {region}."
        lines = [f"Found {len(experiments)} SageMaker experiment(s):\n"]
        for e in experiments:
            lines.append(f"  {e['name']}  created={e['created'][:10]}")
        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing SageMaker experiments: {str(e)}"


@tool
def list_sagemaker_feature_groups(region: str = 'us-east-1') -> str:
    """List all SageMaker Feature Store feature groups."""
    try:
        sm = boto3.client('sagemaker', region_name=region)
        response = sm.list_feature_groups()
        groups = []
        for g in response['FeatureGroupSummaries']:
            groups.append({
                'name': g['FeatureGroupName'],
                'status': g.get('FeatureGroupStatus', 'N/A'),
                'offline_store_status': g.get('OfflineStoreStatus', {}).get('Status', 'N/A'),
                'created': g['CreationTime'].isoformat(),
            })
        if not groups:
            return f"No SageMaker feature groups found in {region}."
        lines = [f"Found {len(groups)} feature group(s):\n"]
        for g in groups:
            lines.append(f"  {g['name']}  [{g['status']}]  offline={g['offline_store_status']}")
        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing SageMaker feature groups: {str(e)}"


@tool
def list_sagemaker_model_packages(region: str = 'us-east-1') -> str:
    """List all SageMaker model packages (model registry)."""
    try:
        sm = boto3.client('sagemaker', region_name=region)
        response = sm.list_model_packages()
        packages = []
        for p in response['ModelPackageSummaryList']:
            packages.append({
                'name': p['ModelPackageName'],
                'arn': p['ModelPackageArn'],
                'status': p['ModelPackageStatus'],
                'approval_status': p.get('ModelApprovalStatus', 'N/A'),
                'created': p['CreationTime'].isoformat(),
            })
        if not packages:
            return f"No SageMaker model packages found in {region}."
        lines = [f"Found {len(packages)} model package(s):\n"]
        for p in packages:
            lines.append(f"  {p['name']}  [{p['status']}]  approval={p['approval_status']}")
        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing SageMaker model packages: {str(e)}"


@tool
def stop_sagemaker_training_job(job_name: str, region: str = 'us-east-1') -> str:
    """Stop a running SageMaker training job."""
    try:
        sm = boto3.client('sagemaker', region_name=region)
        sm.stop_training_job(TrainingJobName=job_name)
        return f"Stopped SageMaker training job '{job_name}'."
    except Exception as e:
        return f"Error stopping SageMaker training job: {str(e)}"


@tool
def delete_sagemaker_endpoint(endpoint_name: str, region: str = 'us-east-1') -> str:
    """Delete a SageMaker inference endpoint. This stops billing for the endpoint immediately."""
    try:
        sm = boto3.client('sagemaker', region_name=region)
        sm.delete_endpoint(EndpointName=endpoint_name)
        return f"Deleted SageMaker endpoint '{endpoint_name}'. Billing stopped."
    except Exception as e:
        return f"Error deleting SageMaker endpoint: {str(e)}"


@tool
def list_sagemaker_endpoints(region: str = 'us-east-1') -> str:
    """List all SageMaker inference endpoints."""
    try:
        sm = boto3.client('sagemaker', region_name=region)
        response = sm.list_endpoints()
        endpoints = []
        for e in response['Endpoints']:
            endpoints.append({
                'name': e['EndpointName'],
                'arn': e['EndpointArn'],
                'status': e['EndpointStatus'],
                'created': e['CreationTime'].isoformat(),
                'last_modified': e['LastModifiedTime'].isoformat(),
            })
        if not endpoints:
            return f"No SageMaker endpoints found in {region}."
        lines = [f"Found {len(endpoints)} SageMaker endpoint(s):\n"]
        for e in endpoints:
            lines.append(f"  {e['name']}  [{e['status']}]  created={e['created'][:10]}")
        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing SageMaker endpoints: {str(e)}"


@tool
def analyze_sagemaker_endpoint(endpoint_name: str) -> str:
    """Analyze a SageMaker endpoint including instance type, variant config, and data capture."""
    try:
        sm = boto3.client('sagemaker', region_name='us-east-1')
        endpoint = sm.describe_endpoint(EndpointName=endpoint_name)
        config_name = endpoint['EndpointConfigName']
        config = sm.describe_endpoint_config(EndpointConfigName=config_name)
        analysis = {
            'endpoint_name': endpoint_name,
            'status': endpoint['EndpointStatus'],
            'endpoint_config': config_name,
            'failure_reason': endpoint.get('FailureReason', ''),
            'created': endpoint['CreationTime'].isoformat(),
            'last_modified': endpoint['LastModifiedTime'].isoformat(),
            'production_variants': [{
                'variant_name': v['VariantName'],
                'model_name': v['ModelName'],
                'instance_type': v.get('InstanceType', 'serverless'),
                'initial_instance_count': v.get('InitialInstanceCount', 0),
                'initial_weight': v.get('InitialVariantWeight', 1.0),
            } for v in config.get('ProductionVariants', [])],
            'data_capture_enabled': config.get('DataCaptureConfig', {}).get('EnableCapture', False),
            'kms_key_id': config.get('KmsKeyId', 'None'),
        }
        return f"SageMaker Endpoint Analysis for {endpoint_name}:\n" + json.dumps(analysis, indent=2, default=str)
    except Exception as e:
        return f"Error analyzing SageMaker endpoint: {str(e)}"


@tool
def list_sagemaker_training_jobs(status_filter: str = 'InProgress', max_results: int = 20,
                                  region: str = 'us-east-1') -> str:
    """List SageMaker training jobs filtered by status (InProgress/Completed/Failed/Stopped)."""
    try:
        sm = boto3.client('sagemaker', region_name=region)
        kwargs = {'MaxResults': max_results}
        if status_filter != 'all':
            kwargs['StatusEquals'] = status_filter
        response = sm.list_training_jobs(**kwargs)
        jobs = []
        for j in response['TrainingJobSummaries']:
            jobs.append({
                'name': j['TrainingJobName'],
                'arn': j['TrainingJobArn'],
                'status': j['TrainingJobStatus'],
                'created': j['CreationTime'].isoformat(),
                'training_end': j.get('TrainingEndTime', '').isoformat() if j.get('TrainingEndTime') else None,
            })
        return f"Found {len(jobs)} SageMaker training job(s) (status={status_filter}):\n" + json.dumps(jobs, indent=2, default=str)
    except Exception as e:
        return f"Error listing SageMaker training jobs: {str(e)}"


# ============================================================================
# Bedrock Tools
# ============================================================================

@tool
def list_bedrock_models(region: str = 'us-east-1') -> str:
    """List available Amazon Bedrock foundation models."""
    try:
        bedrock = boto3.client('bedrock', region_name=region)
        response = bedrock.list_foundation_models()
        models = []
        for m in response['modelSummaries']:
            models.append({
                'model_id': m['modelId'],
                'model_name': m['modelName'],
                'provider': m['providerName'],
                'input_modalities': m.get('inputModalities', []),
                'output_modalities': m.get('outputModalities', []),
                'response_streaming': m.get('responseStreamingSupported', False),
                'inference_types': m.get('inferenceTypesSupported', []),
            })
        return f"Found {len(models)} Bedrock foundation model(s):\n" + json.dumps(models, indent=2)
    except Exception as e:
        return f"Error listing Bedrock models: {str(e)}"


@tool
def list_bedrock_knowledge_bases(region: str = 'us-east-1') -> str:
    """List all Bedrock Knowledge Bases."""
    try:
        bedrock_agent = boto3.client('bedrock-agent', region_name=region)
        response = bedrock_agent.list_knowledge_bases()
        kbs = []
        for kb in response['knowledgeBaseSummaries']:
            kbs.append({
                'knowledge_base_id': kb['knowledgeBaseId'],
                'name': kb['name'],
                'status': kb['status'],
                'description': kb.get('description', ''),
                'updated': kb['updatedAt'].isoformat(),
            })
        return f"Found {len(kbs)} Bedrock Knowledge Base(s):\n" + json.dumps(kbs, indent=2, default=str)
    except Exception as e:
        return f"Error listing Bedrock Knowledge Bases: {str(e)}"


@tool
def analyze_bedrock_knowledge_base(knowledge_base_id: str, region: str = 'us-east-1') -> str:
    """Analyze a Bedrock Knowledge Base including storage config and data sources."""
    try:
        bedrock_agent = boto3.client('bedrock-agent', region_name=region)
        kb = bedrock_agent.get_knowledge_base(knowledgeBaseId=knowledge_base_id)['knowledgeBase']
        data_sources = bedrock_agent.list_data_sources(knowledgeBaseId=knowledge_base_id)['dataSourceSummaries']
        analysis = {
            'knowledge_base_id': knowledge_base_id,
            'name': kb['name'],
            'status': kb['status'],
            'description': kb.get('description', ''),
            'role_arn': kb['roleArn'],
            'embedding_model': kb.get('knowledgeBaseConfiguration', {}).get('vectorKnowledgeBaseConfiguration', {}).get('embeddingModelArn', 'N/A'),
            'storage_type': kb.get('storageConfiguration', {}).get('type', 'N/A'),
            'created': kb['createdAt'].isoformat(),
            'updated': kb['updatedAt'].isoformat(),
            'data_sources': [{
                'id': ds['dataSourceId'],
                'name': ds['name'],
                'status': ds['status'],
                'updated': ds['updatedAt'].isoformat(),
            } for ds in data_sources],
        }
        return f"Bedrock Knowledge Base Analysis for {knowledge_base_id}:\n" + json.dumps(analysis, indent=2, default=str)
    except Exception as e:
        return f"Error analyzing Bedrock Knowledge Base: {str(e)}"


# ============================================================================
# App Runner Tools
# ============================================================================

@tool
def list_apprunner_services(region: str = 'us-east-1') -> str:
    """List all App Runner services."""
    try:
        ar = boto3.client('apprunner', region_name=region)
        response = ar.list_services()
        services = []
        for s in response['ServiceSummaryList']:
            services.append({
                'name': s['ServiceName'],
                'arn': s['ServiceArn'],
                'id': s['ServiceId'],
                'status': s['Status'],
                'url': s.get('ServiceUrl', 'N/A'),
                'created': s['CreatedAt'].isoformat(),
                'updated': s['UpdatedAt'].isoformat(),
            })
        return f"Found {len(services)} App Runner service(s):\n" + json.dumps(services, indent=2, default=str)
    except Exception as e:
        return f"Error listing App Runner services: {str(e)}"


@tool
def analyze_apprunner_service(service_arn: str) -> str:
    """Analyze an App Runner service including source, instance config, and health check."""
    try:
        ar = boto3.client('apprunner', region_name='us-east-1')
        response = ar.describe_service(ServiceArn=service_arn)
        s = response['Service']
        source = s.get('SourceConfiguration', {})
        instance = s.get('InstanceConfiguration', {})
        health = s.get('HealthCheckConfiguration', {})
        analysis = {
            'service_name': s['ServiceName'],
            'service_arn': service_arn,
            'status': s['Status'],
            'url': s.get('ServiceUrl', 'N/A'),
            'source_type': 'ECR' if source.get('ImageRepository') else 'CodeRepository',
            'image_uri': source.get('ImageRepository', {}).get('ImageIdentifier', 'N/A'),
            'cpu': instance.get('Cpu', 'N/A'),
            'memory': instance.get('Memory', 'N/A'),
            'health_check_protocol': health.get('Protocol', 'TCP'),
            'health_check_path': health.get('Path', '/'),
            'auto_deployments': source.get('AutoDeploymentsEnabled', False),
            'created': s['CreatedAt'].isoformat(),
            'updated': s['UpdatedAt'].isoformat(),
        }
        return f"App Runner Service Analysis:\n" + json.dumps(analysis, indent=2, default=str)
    except Exception as e:
        return f"Error analyzing App Runner service: {str(e)}"


@tool
def pause_apprunner_service(service_arn: str) -> str:
    """Pause an App Runner service to stop billing for compute."""
    try:
        ar = boto3.client('apprunner', region_name='us-east-1')
        response = ar.pause_service(ServiceArn=service_arn)
        return f"Pausing App Runner service. Status: {response['Service']['Status']}"
    except Exception as e:
        return f"Error pausing App Runner service: {str(e)}"


@tool
def resume_apprunner_service(service_arn: str) -> str:
    """Resume a paused App Runner service."""
    try:
        ar = boto3.client('apprunner', region_name='us-east-1')
        response = ar.resume_service(ServiceArn=service_arn)
        return f"Resuming App Runner service. Status: {response['Service']['Status']}"
    except Exception as e:
        return f"Error resuming App Runner service: {str(e)}"


# ============================================================================
# Lightsail Tools
# ============================================================================

@tool
def list_lightsail_instances(region: str = 'us-east-1') -> str:
    """List all Lightsail instances."""
    try:
        ls = boto3.client('lightsail', region_name=region)
        response = ls.get_instances()
        instances = []
        for i in response['instances']:
            instances.append({
                'name': i['name'],
                'arn': i['arn'],
                'state': i['state']['name'],
                'bundle_id': i['bundleId'],
                'blueprint_name': i['blueprintName'],
                'public_ip': i.get('publicIpAddress', 'N/A'),
                'private_ip': i.get('privateIpAddress', 'N/A'),
                'availability_zone': i['location']['availabilityZone'],
                'created': i['createdAt'].isoformat(),
            })
        return f"Found {len(instances)} Lightsail instance(s):\n" + json.dumps(instances, indent=2, default=str)
    except Exception as e:
        return f"Error listing Lightsail instances: {str(e)}"


@tool
def analyze_lightsail_instance(instance_name: str) -> str:
    """Analyze a Lightsail instance including hardware, networking, and snapshots."""
    try:
        ls = boto3.client('lightsail', region_name='us-east-1')
        instance = ls.get_instance(instanceName=instance_name)['instance']
        snapshots = ls.get_instance_snapshots()['instanceSnapshots']
        instance_snapshots = [s for s in snapshots if s.get('fromInstanceName') == instance_name]
        hardware = instance.get('hardware', {})
        networking = instance.get('networking', {})
        analysis = {
            'name': instance_name,
            'arn': instance['arn'],
            'state': instance['state']['name'],
            'bundle_id': instance['bundleId'],
            'blueprint_name': instance['blueprintName'],
            'blueprint_id': instance['blueprintId'],
            'cpu_count': hardware.get('cpuCount', 0),
            'ram_size_gb': hardware.get('ramSizeInGb', 0),
            'disk_size_gb': sum(d.get('sizeInGb', 0) for d in hardware.get('disks', [])),
            'public_ip': instance.get('publicIpAddress', 'N/A'),
            'private_ip': instance.get('privateIpAddress', 'N/A'),
            'ipv6_addresses': instance.get('ipv6Addresses', []),
            'monthly_transfer_gb': networking.get('monthlyTransfer', {}).get('gbPerMonthAllocated', 0),
            'availability_zone': instance['location']['availabilityZone'],
            'ssh_key_name': instance.get('sshKeyName', 'N/A'),
            'username': instance.get('username', 'N/A'),
            'snapshot_count': len(instance_snapshots),
            'created': instance['createdAt'].isoformat(),
        }
        return f"Lightsail Instance Analysis for {instance_name}:\n" + json.dumps(analysis, indent=2, default=str)
    except Exception as e:
        return f"Error analyzing Lightsail instance: {str(e)}"


# ============================================================================
# Agent self-description — answers "what can you do?"
# ============================================================================

@tool
def describe_agent_capabilities() -> str:
    """
    Describe what this agent can do. Call this when the user asks
    'what can you do', 'what are your capabilities', 'help', etc.
    """
    return """I'm an autonomous AWS CloudOps agent. Here's what I can do:

COMPUTE
  • List, start, stop, reboot, resize EC2 instances
  • Analyze EC2 health, metrics, security groups
  • List and analyze Lambda functions, update memory/timeout, invoke them
  • Manage ECS clusters and services (scale, restart, task definitions)
  • Manage EKS clusters and node groups
  • Manage Auto Scaling Groups and scaling policies
  • Manage Batch job queues and jobs
  • Manage SageMaker endpoints and training jobs
  • Manage App Runner services and Lightsail instances

STORAGE
  • List all S3 buckets, analyze bucket security, manage versioning/encryption/lifecycle
  • List and manage EBS volumes and snapshots
  • List and manage EFS filesystems and access points
  • Manage ECR repositories and image scan results
  • Manage AWS Backup plans and jobs
  • Manage DataSync and Transfer Family

NETWORK
  • List and analyze VPCs, subnets, security groups, route tables
  • Manage Internet Gateways and NAT Gateways
  • List and analyze Load Balancers (ALB/NLB)
  • Manage CloudFront distributions and cache invalidations
  • Manage Route53 hosted zones and DNS records
  • Manage API Gateway REST APIs and deployments
  • Manage SNS topics and subscriptions, SQS queues
  • Manage Kinesis streams, MSK (Kafka) clusters
  • Manage EventBridge rules and event buses

DATABASE
  • List and analyze RDS instances, create snapshots, modify config
  • List and analyze DynamoDB tables, update capacity, enable PITR
  • Manage ElastiCache and DocumentDB clusters
  • Manage Redshift clusters (pause/resume)
  • Manage Neptune, OpenSearch Service, OpenSearch Serverless
  • Manage Glue ETL jobs, Athena queries, Timestream, QLDB
  • Manage DMS migration tasks

SECURITY
  • List and analyze IAM users, roles, policies
  • Find IAM users without MFA
  • Rotate access keys, manage Secrets Manager secrets
  • Manage KMS keys, WAF rules, GuardDuty findings
  • Check Security Hub, Inspector, Macie findings
  • Analyze CloudTrail events and audit logs
  • Manage Config rules and compliance
  • Manage Access Analyzer, Cognito user pools

COST & OPERATIONS
  • Get cost and usage breakdown by service
  • Get cost forecasts and rightsizing recommendations
  • Manage budgets and alerts
  • Manage CloudFormation stacks (list, analyze, drift detection)
  • Manage CodePipeline (list, state, retry, start)
  • Manage SSM Parameter Store and run commands on EC2
  • Manage Step Functions state machines
  • Check AWS Health events and Trusted Advisor
  • Manage service quotas and Organizations

Just tell me what you need in plain English — I'll figure out the right tools."""


# ============================================================================
# Cross-service resource search — find a resource by name across all services
# ============================================================================

@tool
def search_resource_by_name(name: str, region: str = 'us-east-1') -> str:
    """
    Search for a resource by name across all major AWS services.
    Use this when the user refers to a resource by name but you don't know
    what type it is (RDS, DynamoDB, EC2, S3, etc.).
    Returns all matches found, or clearly states nothing was found.
    """
    results = []
    not_found = []

    checks = [
        # RDS instances
        ('RDS instance', lambda: [
            f"RDS: {db['DBInstanceIdentifier']} (engine={db['Engine']}, status={db['DBInstanceStatus']})"
            for db in boto3.client('rds', region_name=region)
                .describe_db_instances()['DBInstances']
            if name.lower() in db['DBInstanceIdentifier'].lower()
        ]),
        # RDS clusters
        ('RDS cluster', lambda: [
            f"RDS Cluster: {c['DBClusterIdentifier']} (engine={c['Engine']}, status={c['Status']})"
            for c in boto3.client('rds', region_name=region)
                .describe_db_clusters()['DBClusters']
            if name.lower() in c['DBClusterIdentifier'].lower()
        ]),
        # DynamoDB tables
        ('DynamoDB table', lambda: [
            f"DynamoDB: {t}"
            for t in boto3.client('dynamodb', region_name=region)
                .list_tables()['TableNames']
            if name.lower() in t.lower()
        ]),
        # ElastiCache clusters
        ('ElastiCache cluster', lambda: [
            f"ElastiCache: {c['CacheClusterId']} (engine={c['Engine']}, status={c['CacheClusterStatus']})"
            for c in boto3.client('elasticache', region_name=region)
                .describe_cache_clusters()['CacheClusters']
            if name.lower() in c['CacheClusterId'].lower()
        ]),
        # EC2 instances (by Name tag)
        ('EC2 instance', lambda: [
            f"EC2: {i['InstanceId']} (type={i['InstanceType']}, state={i['State']['Name']}, "
            f"Name={next((t['Value'] for t in i.get('Tags',[]) if t['Key']=='Name'), 'N/A')})"
            for r in boto3.client('ec2', region_name=region)
                .describe_instances()['Reservations']
            for i in r['Instances']
            if any(name.lower() in t['Value'].lower()
                   for t in i.get('Tags', []) if t['Key'] == 'Name')
        ]),
        # S3 buckets
        ('S3 bucket', lambda: [
            f"S3: {b['Name']} (created={b['CreationDate'].isoformat()[:10]})"
            for b in boto3.client('s3', region_name=region)
                .list_buckets()['Buckets']
            if name.lower() in b['Name'].lower()
        ]),
        # DocumentDB clusters
        ('DocumentDB cluster', lambda: [
            f"DocumentDB: {c['DBClusterIdentifier']} (status={c['Status']})"
            for c in boto3.client('docdb', region_name=region)
                .describe_db_clusters(
                    Filters=[{'Name': 'engine', 'Values': ['docdb']}]
                )['DBClusters']
            if name.lower() in c['DBClusterIdentifier'].lower()
        ]),
        # Redshift clusters
        ('Redshift cluster', lambda: [
            f"Redshift: {c['ClusterIdentifier']} (status={c['ClusterStatus']})"
            for c in boto3.client('redshift', region_name=region)
                .describe_clusters()['Clusters']
            if name.lower() in c['ClusterIdentifier'].lower()
        ]),
        # OpenSearch domains
        ('OpenSearch domain', lambda: [
            f"OpenSearch: {d}"
            for d in boto3.client('opensearch', region_name=region)
                .list_domain_names()['DomainNames']
            if name.lower() in d['DomainName'].lower()
        ] if boto3.client('opensearch', region_name=region).list_domain_names()['DomainNames'] else []),
    ]

    for service_name, check_fn in checks:
        try:
            matches = check_fn()
            if matches:
                results.extend(matches)
        except Exception as e:
            not_found.append(f"{service_name}: search failed ({str(e)[:60]})")

    if results:
        lines = [f"Found {len(results)} resource(s) matching '{name}':\n"]
        for r in results:
            lines.append(f"  ✅ {r}")
        if not_found:
            lines.append(f"\n(Could not search: {', '.join(not_found)})")
        return '\n'.join(lines)
    else:
        msg = f"No resources found matching '{name}' in {region}.\n"
        msg += "Searched: RDS instances, RDS clusters, DynamoDB tables, ElastiCache, "
        msg += "EC2 instances (by Name tag), S3 buckets, DocumentDB, Redshift, OpenSearch.\n"
        if not_found:
            msg += f"Search errors: {', '.join(not_found)}\n"
        msg += "\nPlease clarify: what type of resource is it, and what is its exact identifier?"
        return msg
