"""
Comprehensive AWS Database Service Tools
Covers RDS, DynamoDB, ElastiCache, DocumentDB, Neptune, and Redshift operations.
"""

from src.core.aws_clients import boto3  # account-aware boto3 shim (cross-account support)
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from strands import tool
import structlog

logger = structlog.get_logger()


# ============================================================================
# RDS Tools (Expanded)
# ============================================================================

@tool
def analyze_rds_instance(db_instance_id: str) -> str:
    """Comprehensive RDS instance analysis including performance, backups, and configuration."""
    try:
        rds = boto3.client('rds', region_name='us-east-1')
        cloudwatch = boto3.client('cloudwatch', region_name='us-east-1')
        
        # Get instance details
        response = rds.describe_db_instances(DBInstanceIdentifier=db_instance_id)
        if not response['DBInstances']:
            return f"RDS instance {db_instance_id} not found"
        
        instance = response['DBInstances'][0]
        
        # Get parameter group details
        param_groups = []
        for pg in instance['DBParameterGroups']:
            param_groups.append({
                'name': pg['DBParameterGroupName'],
                'status': pg['ParameterApplyStatus']
            })
        
        # Get security groups
        security_groups = []
        for sg in instance.get('VpcSecurityGroups', []):
            security_groups.append({
                'group_id': sg['VpcSecurityGroupId'],
                'status': sg['Status']
            })
        
        # Get recent CloudWatch metrics
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=1)
        
        metrics = {}
        metric_names = ['CPUUtilization', 'DatabaseConnections', 'FreeableMemory', 
                       'ReadLatency', 'WriteLatency', 'ReadIOPS', 'WriteIOPS']
        
        for metric_name in metric_names:
            try:
                metric_response = cloudwatch.get_metric_statistics(
                    Namespace='AWS/RDS',
                    MetricName=metric_name,
                    Dimensions=[{'Name': 'DBInstanceIdentifier', 'Value': db_instance_id}],
                    StartTime=start_time,
                    EndTime=end_time,
                    Period=3600,
                    Statistics=['Average', 'Maximum']
                )
                
                if metric_response['Datapoints']:
                    latest = max(metric_response['Datapoints'], key=lambda x: x['Timestamp'])
                    metrics[metric_name] = latest
            except:
                metrics[metric_name] = {'Average': 0, 'Maximum': 0}
        
        # Get automated backups
        backup_retention = instance.get('BackupRetentionPeriod', 0)
        
        analysis = {
            'db_instance_id': db_instance_id,
            'engine': instance['Engine'],
            'engine_version': instance['EngineVersion'],
            'instance_class': instance['DBInstanceClass'],
            'status': instance['DBInstanceStatus'],
            'allocated_storage': instance['AllocatedStorage'],
            'storage_type': instance['StorageType'],
            'storage_encrypted': instance['StorageEncrypted'],
            'multi_az': instance['MultiAZ'],
            'publicly_accessible': instance['PubliclyAccessible'],
            'endpoint': instance.get('Endpoint', {}).get('Address', 'N/A'),
            'port': instance.get('Endpoint', {}).get('Port', 'N/A'),
            'vpc_id': instance.get('DBSubnetGroup', {}).get('VpcId', 'N/A'),
            'availability_zone': instance.get('AvailabilityZone', 'N/A'),
            'backup_retention_days': backup_retention,
            'backup_window': instance.get('PreferredBackupWindow', 'N/A'),
            'maintenance_window': instance.get('PreferredMaintenanceWindow', 'N/A'),
            'parameter_groups': param_groups,
            'security_groups': security_groups,
            'recent_metrics': metrics,
            'created_time': instance.get('InstanceCreateTime', '').isoformat() if instance.get('InstanceCreateTime') else 'N/A'
        }
        
        return f"RDS Instance Analysis for {db_instance_id}:\n" + json.dumps(analysis, indent=2, default=str)
        
    except Exception as e:
        return f"Error analyzing RDS instance: {str(e)}"


@tool
def create_rds_snapshot(db_instance_id: str, snapshot_id: str = None) -> str:
    """Create a manual snapshot of an RDS instance."""
    try:
        rds = boto3.client('rds', region_name='us-east-1')
        
        if not snapshot_id:
            timestamp = datetime.utcnow().strftime('%Y%m%d-%H%M%S')
            snapshot_id = f"{db_instance_id}-manual-{timestamp}"
        
        response = rds.create_db_snapshot(
            DBSnapshotIdentifier=snapshot_id,
            DBInstanceIdentifier=db_instance_id,
            Tags=[
                {'Key': 'CreatedBy', 'Value': 'CloudOps-SRE'},
                {'Key': 'CreatedAt', 'Value': datetime.utcnow().isoformat()}
            ]
        )
        
        return f"Created RDS snapshot {snapshot_id} for instance {db_instance_id}. Status: {response['DBSnapshot']['Status']}"
        
    except Exception as e:
        return f"Error creating RDS snapshot: {str(e)}"


@tool
def modify_rds_instance(db_instance_id: str, instance_class: str = None, 
                       allocated_storage: int = None, backup_retention: int = None,
                       apply_immediately: bool = False) -> str:
    """Modify RDS instance configuration."""
    try:
        rds = boto3.client('rds', region_name='us-east-1')
        
        modify_params = {
            'DBInstanceIdentifier': db_instance_id,
            'ApplyImmediately': apply_immediately
        }
        changes = []
        
        if instance_class:
            modify_params['DBInstanceClass'] = instance_class
            changes.append(f"instance class to {instance_class}")
        
        if allocated_storage:
            modify_params['AllocatedStorage'] = allocated_storage
            changes.append(f"storage to {allocated_storage} GB")
        
        if backup_retention is not None:
            modify_params['BackupRetentionPeriod'] = backup_retention
            changes.append(f"backup retention to {backup_retention} days")
        
        if not changes:
            return "No modifications specified"
        
        response = rds.modify_db_instance(**modify_params)
        
        apply_time = "immediately" if apply_immediately else "during next maintenance window"
        return f"Modified RDS instance {db_instance_id}: {', '.join(changes)}. Changes will be applied {apply_time}."
        
    except Exception as e:
        return f"Error modifying RDS instance: {str(e)}"


# ============================================================================
# DynamoDB Tools (Expanded)
# ============================================================================

@tool
def analyze_dynamodb_table(table_name: str) -> str:
    """Comprehensive DynamoDB table analysis including capacity, indexes, and metrics."""
    try:
        dynamodb = boto3.client('dynamodb', region_name='us-east-1')
        cloudwatch = boto3.client('cloudwatch', region_name='us-east-1')
        
        # Get table details
        response = dynamodb.describe_table(TableName=table_name)
        table = response['Table']
        
        # Get table metrics
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=1)
        
        metrics = {}
        metric_names = ['ConsumedReadCapacityUnits', 'ConsumedWriteCapacityUnits', 
                       'ThrottledRequests', 'ItemCount', 'TableSizeBytes']
        
        for metric_name in metric_names:
            try:
                metric_response = cloudwatch.get_metric_statistics(
                    Namespace='AWS/DynamoDB',
                    MetricName=metric_name,
                    Dimensions=[{'Name': 'TableName', 'Value': table_name}],
                    StartTime=start_time,
                    EndTime=end_time,
                    Period=3600,
                    Statistics=['Sum', 'Average', 'Maximum']
                )
                
                if metric_response['Datapoints']:
                    latest = max(metric_response['Datapoints'], key=lambda x: x['Timestamp'])
                    metrics[metric_name] = latest
            except:
                metrics[metric_name] = {'Sum': 0, 'Average': 0, 'Maximum': 0}
        
        # Parse key schema
        key_schema = []
        for key in table['KeySchema']:
            key_schema.append({
                'attribute_name': key['AttributeName'],
                'key_type': key['KeyType']
            })
        
        # Parse global secondary indexes
        gsi_info = []
        for gsi in table.get('GlobalSecondaryIndexes', []):
            gsi_info.append({
                'index_name': gsi['IndexName'],
                'status': gsi['IndexStatus'],
                'key_schema': [{'attribute': k['AttributeName'], 'type': k['KeyType']} for k in gsi['KeySchema']],
                'projection_type': gsi['Projection']['ProjectionType'],
                'read_capacity': gsi.get('ProvisionedThroughput', {}).get('ReadCapacityUnits', 'On-Demand'),
                'write_capacity': gsi.get('ProvisionedThroughput', {}).get('WriteCapacityUnits', 'On-Demand')
            })
        
        # Parse local secondary indexes
        lsi_info = []
        for lsi in table.get('LocalSecondaryIndexes', []):
            lsi_info.append({
                'index_name': lsi['IndexName'],
                'key_schema': [{'attribute': k['AttributeName'], 'type': k['KeyType']} for k in lsi['KeySchema']],
                'projection_type': lsi['Projection']['ProjectionType']
            })
        
        analysis = {
            'table_name': table_name,
            'status': table['TableStatus'],
            'creation_date': table['CreationDateTime'].isoformat(),
            'item_count': table['ItemCount'],
            'table_size_bytes': table['TableSizeBytes'],
            'billing_mode': table.get('BillingModeSummary', {}).get('BillingMode', 'PROVISIONED'),
            'read_capacity': table.get('ProvisionedThroughput', {}).get('ReadCapacityUnits', 'On-Demand'),
            'write_capacity': table.get('ProvisionedThroughput', {}).get('WriteCapacityUnits', 'On-Demand'),
            'key_schema': key_schema,
            'attribute_definitions': [{'name': attr['AttributeName'], 'type': attr['AttributeType']} 
                                    for attr in table['AttributeDefinitions']],
            'global_secondary_indexes': gsi_info,
            'local_secondary_indexes': lsi_info,
            'stream_enabled': 'StreamSpecification' in table,
            'encryption_enabled': 'SSEDescription' in table,
            'point_in_time_recovery': table.get('PointInTimeRecoveryDescription', {}).get('PointInTimeRecoveryStatus') == 'ENABLED',
            'recent_metrics': metrics
        }
        
        return f"DynamoDB Table Analysis for {table_name}:\n" + json.dumps(analysis, indent=2, default=str)
        
    except Exception as e:
        return f"Error analyzing DynamoDB table: {str(e)}"


@tool
def update_dynamodb_capacity(table_name: str, read_capacity: int = None, 
                           write_capacity: int = None, index_name: str = None) -> str:
    """Update DynamoDB table or GSI provisioned capacity."""
    try:
        dynamodb = boto3.client('dynamodb', region_name='us-east-1')
        
        if index_name:
            # Update GSI capacity
            if not read_capacity or not write_capacity:
                return "Both read and write capacity must be specified for GSI updates"
            
            response = dynamodb.update_table(
                TableName=table_name,
                GlobalSecondaryIndexUpdates=[{
                    'Update': {
                        'IndexName': index_name,
                        'ProvisionedThroughput': {
                            'ReadCapacityUnits': read_capacity,
                            'WriteCapacityUnits': write_capacity
                        }
                    }
                }]
            )
            
            return f"Updated GSI {index_name} capacity: {read_capacity} RCU, {write_capacity} WCU"
        else:
            # Update table capacity
            update_params = {'TableName': table_name}
            changes = []
            
            if read_capacity and write_capacity:
                update_params['ProvisionedThroughput'] = {
                    'ReadCapacityUnits': read_capacity,
                    'WriteCapacityUnits': write_capacity
                }
                changes.append(f"table capacity: {read_capacity} RCU, {write_capacity} WCU")
            
            if not changes:
                return "No capacity changes specified"
            
            response = dynamodb.update_table(**update_params)
            
            return f"Updated DynamoDB table {table_name}: {', '.join(changes)}"
        
    except Exception as e:
        return f"Error updating DynamoDB capacity: {str(e)}"


@tool
def enable_dynamodb_point_in_time_recovery(table_name: str) -> str:
    """Enable point-in-time recovery for a DynamoDB table."""
    try:
        dynamodb = boto3.client('dynamodb', region_name='us-east-1')
        
        response = dynamodb.update_continuous_backups(
            TableName=table_name,
            PointInTimeRecoverySpecification={'PointInTimeRecoveryEnabled': True}
        )
        
        return f"Enabled point-in-time recovery for DynamoDB table {table_name}"
        
    except Exception as e:
        return f"Error enabling point-in-time recovery: {str(e)}"


# ============================================================================
# ElastiCache Tools
# ============================================================================

@tool
def analyze_elasticache_cluster(cluster_id: str) -> str:
    """Analyze ElastiCache cluster including nodes, performance, and configuration."""
    try:
        elasticache = boto3.client('elasticache', region_name='us-east-1')
        cloudwatch = boto3.client('cloudwatch', region_name='us-east-1')
        
        # Try Redis first, then Memcached
        try:
            response = elasticache.describe_replication_groups(ReplicationGroupId=cluster_id)
            if response['ReplicationGroups']:
                cluster_type = 'redis'
                cluster = response['ReplicationGroups'][0]
                cluster_info = {
                    'cluster_id': cluster_id,
                    'description': cluster['Description'],
                    'status': cluster['Status'],
                    'node_type': cluster['CacheNodeType'],
                    'engine': 'redis',
                    'engine_version': cluster['CacheClusterRedisVersion'],
                    'num_cache_clusters': cluster['NumCacheClusters'],
                    'automatic_failover': cluster['AutomaticFailover'],
                    'multi_az': cluster['MultiAZ'],
                    'snapshot_retention_limit': cluster['SnapshotRetentionLimit'],
                    'snapshot_window': cluster.get('SnapshotWindow', 'N/A'),
                    'maintenance_window': cluster.get('PreferredMaintenanceWindow', 'N/A')
                }
        except:
            # Try Memcached
            response = elasticache.describe_cache_clusters(CacheClusterId=cluster_id, ShowCacheNodeInfo=True)
            if response['CacheClusters']:
                cluster_type = 'memcached'
                cluster = response['CacheClusters'][0]
                cluster_info = {
                    'cluster_id': cluster_id,
                    'status': cluster['CacheClusterStatus'],
                    'node_type': cluster['CacheNodeType'],
                    'engine': cluster['Engine'],
                    'engine_version': cluster['EngineVersion'],
                    'num_cache_nodes': cluster['NumCacheNodes'],
                    'maintenance_window': cluster.get('PreferredMaintenanceWindow', 'N/A'),
                    'cache_nodes': [{'id': node['CacheNodeId'], 'status': node['CacheNodeStatus'], 
                                   'endpoint': node.get('Endpoint', {}).get('Address', 'N/A')} 
                                  for node in cluster.get('CacheNodes', [])]
                }
            else:
                return f"ElastiCache cluster {cluster_id} not found"
        
        # Get CloudWatch metrics
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=1)
        
        metrics = {}
        if cluster_type == 'redis':
            metric_names = ['CPUUtilization', 'DatabaseMemoryUsagePercentage', 'CurrConnections', 
                           'CacheHits', 'CacheMisses', 'ReplicationLag']
        else:
            metric_names = ['CPUUtilization', 'SwapUsage', 'CurrConnections', 
                           'CmdGet', 'CmdSet', 'GetHits', 'GetMisses']
        
        for metric_name in metric_names:
            try:
                metric_response = cloudwatch.get_metric_statistics(
                    Namespace='AWS/ElastiCache',
                    MetricName=metric_name,
                    Dimensions=[{'Name': 'CacheClusterId', 'Value': cluster_id}],
                    StartTime=start_time,
                    EndTime=end_time,
                    Period=3600,
                    Statistics=['Average', 'Maximum']
                )
                
                if metric_response['Datapoints']:
                    latest = max(metric_response['Datapoints'], key=lambda x: x['Timestamp'])
                    metrics[metric_name] = latest
            except:
                metrics[metric_name] = {'Average': 0, 'Maximum': 0}
        
        cluster_info['recent_metrics'] = metrics
        
        return f"ElastiCache Cluster Analysis for {cluster_id}:\n" + json.dumps(cluster_info, indent=2, default=str)
        
    except Exception as e:
        return f"Error analyzing ElastiCache cluster: {str(e)}"


@tool
def create_elasticache_snapshot(cluster_id: str, snapshot_name: str = None) -> str:
    """Create a snapshot of a Redis ElastiCache cluster."""
    try:
        elasticache = boto3.client('elasticache', region_name='us-east-1')
        
        if not snapshot_name:
            timestamp = datetime.utcnow().strftime('%Y%m%d-%H%M%S')
            snapshot_name = f"{cluster_id}-manual-{timestamp}"
        
        response = elasticache.create_snapshot(
            ReplicationGroupId=cluster_id,
            SnapshotName=snapshot_name
        )
        
        return f"Created ElastiCache snapshot {snapshot_name} for cluster {cluster_id}"
        
    except Exception as e:
        return f"Error creating ElastiCache snapshot: {str(e)}"


# ============================================================================
# DocumentDB Tools
# ============================================================================

@tool
def analyze_documentdb_cluster(cluster_id: str) -> str:
    """Analyze DocumentDB cluster including instances and configuration."""
    try:
        docdb = boto3.client('docdb', region_name='us-east-1')
        cloudwatch = boto3.client('cloudwatch', region_name='us-east-1')
        
        # Get cluster details
        response = docdb.describe_db_clusters(DBClusterIdentifier=cluster_id)
        if not response['DBClusters']:
            return f"DocumentDB cluster {cluster_id} not found"
        
        cluster = response['DBClusters'][0]
        
        # Get cluster instances
        instances_response = docdb.describe_db_instances(
            Filters=[{'Name': 'db-cluster-id', 'Values': [cluster_id]}]
        )
        
        instances = []
        for instance in instances_response['DBInstances']:
            instances.append({
                'instance_id': instance['DBInstanceIdentifier'],
                'instance_class': instance['DBInstanceClass'],
                'status': instance['DBInstanceStatus'],
                'availability_zone': instance['AvailabilityZone'],
                'endpoint': instance.get('Endpoint', {}).get('Address', 'N/A')
            })
        
        # Get CloudWatch metrics
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=1)
        
        metrics = {}
        metric_names = ['CPUUtilization', 'DatabaseConnections', 'FreeableMemory', 'WriteLatency', 'ReadLatency']
        
        for metric_name in metric_names:
            try:
                metric_response = cloudwatch.get_metric_statistics(
                    Namespace='AWS/DocDB',
                    MetricName=metric_name,
                    Dimensions=[{'Name': 'DBClusterIdentifier', 'Value': cluster_id}],
                    StartTime=start_time,
                    EndTime=end_time,
                    Period=3600,
                    Statistics=['Average', 'Maximum']
                )
                
                if metric_response['Datapoints']:
                    latest = max(metric_response['Datapoints'], key=lambda x: x['Timestamp'])
                    metrics[metric_name] = latest
            except:
                metrics[metric_name] = {'Average': 0, 'Maximum': 0}
        
        analysis = {
            'cluster_id': cluster_id,
            'status': cluster['Status'],
            'engine': cluster['Engine'],
            'engine_version': cluster['EngineVersion'],
            'master_username': cluster['MasterUsername'],
            'port': cluster['Port'],
            'vpc_security_groups': [sg['VpcSecurityGroupId'] for sg in cluster.get('VpcSecurityGroups', [])],
            'backup_retention_period': cluster['BackupRetentionPeriod'],
            'preferred_backup_window': cluster.get('PreferredBackupWindow', 'N/A'),
            'preferred_maintenance_window': cluster.get('PreferredMaintenanceWindow', 'N/A'),
            'storage_encrypted': cluster['StorageEncrypted'],
            'instances': instances,
            'recent_metrics': metrics
        }
        
        return f"DocumentDB Cluster Analysis for {cluster_id}:\n" + json.dumps(analysis, indent=2, default=str)
        
    except Exception as e:
        return f"Error analyzing DocumentDB cluster: {str(e)}"


@tool
def create_documentdb_snapshot(cluster_id: str, snapshot_id: str = None) -> str:
    """Create a manual snapshot of a DocumentDB cluster."""
    try:
        docdb = boto3.client('docdb', region_name='us-east-1')
        
        if not snapshot_id:
            timestamp = datetime.utcnow().strftime('%Y%m%d-%H%M%S')
            snapshot_id = f"{cluster_id}-manual-{timestamp}"
        
        response = docdb.create_db_cluster_snapshot(
            DBClusterSnapshotIdentifier=snapshot_id,
            DBClusterIdentifier=cluster_id,
            Tags=[
                {'Key': 'CreatedBy', 'Value': 'CloudOps-SRE'},
                {'Key': 'CreatedAt', 'Value': datetime.utcnow().isoformat()}
            ]
        )
        
        return f"Created DocumentDB snapshot {snapshot_id} for cluster {cluster_id}"
        
    except Exception as e:
        return f"Error creating DocumentDB snapshot: {str(e)}"


# ============================================================================
# Database — List tools
# ============================================================================

@tool
def list_rds_instances(region: str = 'us-east-1') -> str:
    """List all RDS database instances in the account."""
    try:
        rds = boto3.client('rds', region_name=region)
        paginator = rds.get_paginator('describe_db_instances')
        instances = []
        for page in paginator.paginate():
            for db in page['DBInstances']:
                instances.append({
                    'identifier': db['DBInstanceIdentifier'],
                    'engine': f"{db['Engine']} {db['EngineVersion']}",
                    'class': db['DBInstanceClass'],
                    'status': db['DBInstanceStatus'],
                    'storage_gb': db['AllocatedStorage'],
                    'multi_az': db['MultiAZ'],
                    'endpoint': db.get('Endpoint', {}).get('Address', 'N/A'),
                })
        if not instances:
            return f"No RDS instances found in {region}."
        lines = [f"Found {len(instances)} RDS instance(s):\n"]
        for db in instances:
            lines.append(
                f"  {db['identifier']}  {db['engine']}  {db['class']}  "
                f"[{db['status']}]  storage={db['storage_gb']}GB  multi_az={db['multi_az']}"
            )
        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing RDS instances: {str(e)}"


@tool
def list_dynamodb_tables(region: str = 'us-east-1') -> str:
    """List all DynamoDB tables in the account."""
    try:
        ddb = boto3.client('dynamodb', region_name=region)
        paginator = ddb.get_paginator('list_tables')
        tables = []
        for page in paginator.paginate():
            tables.extend(page['TableNames'])
        if not tables:
            return f"No DynamoDB tables found in {region}."
        lines = [f"Found {len(tables)} DynamoDB table(s) in {region}:\n"]
        for t in tables:
            lines.append(f"  {t}")
        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing DynamoDB tables: {str(e)}"


@tool
def list_elasticache_clusters(region: str = 'us-east-1') -> str:
    """List all ElastiCache clusters (Redis and Memcached)."""
    try:
        ec = boto3.client('elasticache', region_name=region)
        response = ec.describe_cache_clusters(ShowCacheNodeInfo=False)
        clusters = []
        for c in response['CacheClusters']:
            clusters.append({
                'cluster_id': c['CacheClusterId'],
                'engine': f"{c['Engine']} {c['EngineVersion']}",
                'node_type': c['CacheNodeType'],
                'status': c['CacheClusterStatus'],
                'num_nodes': c['NumCacheNodes'],
            })
        if not clusters:
            return f"No ElastiCache clusters found in {region}."
        lines = [f"Found {len(clusters)} ElastiCache cluster(s):\n"]
        for c in clusters:
            lines.append(
                f"  {c['cluster_id']}  {c['engine']}  {c['node_type']}  "
                f"[{c['status']}]  nodes={c['num_nodes']}"
            )
        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing ElastiCache clusters: {str(e)}"


@tool
def list_documentdb_clusters(region: str = 'us-east-1') -> str:
    """List all DocumentDB clusters in the account."""
    try:
        docdb = boto3.client('docdb', region_name=region)
        response = docdb.describe_db_clusters(
            Filters=[{'Name': 'engine', 'Values': ['docdb']}]
        )
        clusters = []
        for c in response['DBClusters']:
            clusters.append({
                'identifier': c['DBClusterIdentifier'],
                'status': c['Status'],
                'engine_version': c['EngineVersion'],
                'endpoint': c.get('Endpoint', 'N/A'),
                'instances': len(c.get('DBClusterMembers', [])),
                'multi_az': c.get('MultiAZ', False),
            })
        if not clusters:
            return f"No DocumentDB clusters found in {region}."
        lines = [f"Found {len(clusters)} DocumentDB cluster(s):\n"]
        for c in clusters:
            lines.append(
                f"  {c['identifier']}  [{c['status']}]  v{c['engine_version']}  "
                f"instances={c['instances']}  endpoint={c['endpoint']}"
            )
        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing DocumentDB clusters: {str(e)}"
