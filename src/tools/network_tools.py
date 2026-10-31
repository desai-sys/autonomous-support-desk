"""
Comprehensive AWS Network Service Tools
Covers VPC, Route53, CloudFront, ALB/NLB, API Gateway, and Direct Connect operations.
"""

from src.core.aws_clients import boto3  # account-aware boto3 shim (cross-account support)
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from strands import tool
import structlog

logger = structlog.get_logger()


# ============================================================================
# VPC Tools
# ============================================================================

@tool
def analyze_vpc(vpc_id: str) -> str:
    """Comprehensive VPC analysis including subnets, route tables, security groups, and NACLs."""
    try:
        ec2 = boto3.client('ec2', region_name='us-east-1')
        
        # Get VPC details
        vpc_response = ec2.describe_vpcs(VpcIds=[vpc_id])
        if not vpc_response['Vpcs']:
            return f"VPC {vpc_id} not found"
        
        vpc = vpc_response['Vpcs'][0]
        
        # Get subnets
        subnets_response = ec2.describe_subnets(
            Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
        )
        
        subnets = []
        for subnet in subnets_response['Subnets']:
            subnets.append({
                'subnet_id': subnet['SubnetId'],
                'cidr_block': subnet['CidrBlock'],
                'availability_zone': subnet['AvailabilityZone'],
                'available_ip_count': subnet['AvailableIpAddressCount'],
                'map_public_ip': subnet['MapPublicIpOnLaunch'],
                'state': subnet['State']
            })
        
        # Get route tables
        route_tables_response = ec2.describe_route_tables(
            Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
        )
        
        route_tables = []
        for rt in route_tables_response['RouteTables']:
            routes = []
            for route in rt['Routes']:
                routes.append({
                    'destination': route['DestinationCidrBlock'] if 'DestinationCidrBlock' in route else route.get('DestinationPrefixListId', 'N/A'),
                    'target': route.get('GatewayId', route.get('InstanceId', route.get('NetworkInterfaceId', 'local'))),
                    'state': route['State']
                })
            
            route_tables.append({
                'route_table_id': rt['RouteTableId'],
                'main': any(assoc.get('Main', False) for assoc in rt['Associations']),
                'routes': routes,
                'associated_subnets': [assoc['SubnetId'] for assoc in rt['Associations'] if 'SubnetId' in assoc]
            })
        
        # Get security groups
        sg_response = ec2.describe_security_groups(
            Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
        )
        
        security_groups = []
        for sg in sg_response['SecurityGroups']:
            security_groups.append({
                'group_id': sg['GroupId'],
                'group_name': sg['GroupName'],
                'description': sg['Description'],
                'inbound_rules': len(sg['IpPermissions']),
                'outbound_rules': len(sg['IpPermissionsEgress'])
            })
        
        # Get internet gateways
        igw_response = ec2.describe_internet_gateways(
            Filters=[{'Name': 'attachment.vpc-id', 'Values': [vpc_id]}]
        )
        
        # Get NAT gateways
        nat_response = ec2.describe_nat_gateways(
            Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
        )
        
        analysis = {
            'vpc_id': vpc_id,
            'cidr_block': vpc['CidrBlock'],
            'state': vpc['State'],
            'is_default': vpc['IsDefault'],
            'dhcp_options_id': vpc['DhcpOptionsId'],
            'instance_tenancy': vpc['InstanceTenancy'],
            'subnets': {
                'count': len(subnets),
                'public_subnets': len([s for s in subnets if s['map_public_ip']]),
                'private_subnets': len([s for s in subnets if not s['map_public_ip']]),
                'details': subnets
            },
            'route_tables': {
                'count': len(route_tables),
                'details': route_tables
            },
            'security_groups': {
                'count': len(security_groups),
                'details': security_groups
            },
            'internet_gateways': len(igw_response['InternetGateways']),
            'nat_gateways': len(nat_response['NatGateways']),
            'tags': {tag['Key']: tag['Value'] for tag in vpc.get('Tags', [])}
        }
        
        return f"VPC Analysis for {vpc_id}:\n" + json.dumps(analysis, indent=2, default=str)
        
    except Exception as e:
        return f"Error analyzing VPC: {str(e)}"


@tool
def create_vpc_subnet(vpc_id: str, cidr_block: str, availability_zone: str, 
                     subnet_name: str, map_public_ip: bool = False) -> str:
    """Create a new subnet in a VPC."""
    try:
        ec2 = boto3.client('ec2', region_name='us-east-1')
        
        response = ec2.create_subnet(
            VpcId=vpc_id,
            CidrBlock=cidr_block,
            AvailabilityZone=availability_zone,
            TagSpecifications=[{
                'ResourceType': 'subnet',
                'Tags': [
                    {'Key': 'Name', 'Value': subnet_name},
                    {'Key': 'CreatedBy', 'Value': 'CloudOps-SRE'}
                ]
            }]
        )
        
        subnet_id = response['Subnet']['SubnetId']
        
        # Set public IP mapping if requested
        if map_public_ip:
            ec2.modify_subnet_attribute(
                SubnetId=subnet_id,
                MapPublicIpOnLaunch={'Value': True}
            )
        
        return f"Created subnet {subnet_id} ({subnet_name}) in VPC {vpc_id} with CIDR {cidr_block}"
        
    except Exception as e:
        return f"Error creating VPC subnet: {str(e)}"


@tool
def analyze_security_group(group_id: str) -> str:
    """Analyze security group rules and associated resources."""
    try:
        ec2 = boto3.client('ec2', region_name='us-east-1')
        
        # Get security group details
        response = ec2.describe_security_groups(GroupIds=[group_id])
        if not response['SecurityGroups']:
            return f"Security group {group_id} not found"
        
        sg = response['SecurityGroups'][0]
        
        # Parse inbound rules
        inbound_rules = []
        for rule in sg['IpPermissions']:
            protocol = rule['IpProtocol']
            from_port = rule.get('FromPort', 'All')
            to_port = rule.get('ToPort', 'All')
            
            sources = []
            for ip_range in rule.get('IpRanges', []):
                sources.append(f"CIDR: {ip_range['CidrIp']}")
            for sg_ref in rule.get('UserIdGroupPairs', []):
                sources.append(f"SG: {sg_ref['GroupId']}")
            for prefix_list in rule.get('PrefixListIds', []):
                sources.append(f"PL: {prefix_list['PrefixListId']}")
            
            inbound_rules.append({
                'protocol': protocol,
                'port_range': f"{from_port}-{to_port}" if from_port != to_port else str(from_port),
                'sources': sources
            })
        
        # Parse outbound rules
        outbound_rules = []
        for rule in sg['IpPermissionsEgress']:
            protocol = rule['IpProtocol']
            from_port = rule.get('FromPort', 'All')
            to_port = rule.get('ToPort', 'All')
            
            destinations = []
            for ip_range in rule.get('IpRanges', []):
                destinations.append(f"CIDR: {ip_range['CidrIp']}")
            for sg_ref in rule.get('UserIdGroupPairs', []):
                destinations.append(f"SG: {sg_ref['GroupId']}")
            for prefix_list in rule.get('PrefixListIds', []):
                destinations.append(f"PL: {prefix_list['PrefixListId']}")
            
            outbound_rules.append({
                'protocol': protocol,
                'port_range': f"{from_port}-{to_port}" if from_port != to_port else str(from_port),
                'destinations': destinations
            })
        
        # Find associated resources
        instances_response = ec2.describe_instances(
            Filters=[{'Name': 'instance.group-id', 'Values': [group_id]}]
        )
        
        associated_instances = []
        for reservation in instances_response['Reservations']:
            for instance in reservation['Instances']:
                associated_instances.append({
                    'instance_id': instance['InstanceId'],
                    'state': instance['State']['Name'],
                    'private_ip': instance.get('PrivateIpAddress', 'N/A')
                })
        
        analysis = {
            'group_id': group_id,
            'group_name': sg['GroupName'],
            'description': sg['Description'],
            'vpc_id': sg['VpcId'],
            'inbound_rules': inbound_rules,
            'outbound_rules': outbound_rules,
            'associated_instances': associated_instances,
            'tags': {tag['Key']: tag['Value'] for tag in sg.get('Tags', [])}
        }
        
        return f"Security Group Analysis for {group_id}:\n" + json.dumps(analysis, indent=2, default=str)
        
    except Exception as e:
        return f"Error analyzing security group: {str(e)}"


@tool
def add_security_group_rule(group_id: str, rule_type: str, protocol: str, 
                           port: int, source_cidr: str, description: str = None) -> str:
    """Add an inbound or outbound rule to a security group."""
    try:
        ec2 = boto3.client('ec2', region_name='us-east-1')
        
        ip_permission = {
            'IpProtocol': protocol.lower(),
            'FromPort': port,
            'ToPort': port,
            'IpRanges': [{'CidrIp': source_cidr}]
        }
        
        if description:
            ip_permission['IpRanges'][0]['Description'] = description
        
        if rule_type.lower() == 'inbound':
            ec2.authorize_security_group_ingress(
                GroupId=group_id,
                IpPermissions=[ip_permission]
            )
            direction = "inbound"
        elif rule_type.lower() == 'outbound':
            ec2.authorize_security_group_egress(
                GroupId=group_id,
                IpPermissions=[ip_permission]
            )
            direction = "outbound"
        else:
            return "Rule type must be 'inbound' or 'outbound'"
        
        return f"Added {direction} rule to security group {group_id}: {protocol.upper()} port {port} from {source_cidr}"
        
    except Exception as e:
        return f"Error adding security group rule: {str(e)}"


# ============================================================================
# Route53 Tools
# ============================================================================

@tool
def analyze_route53_hosted_zone(zone_id: str) -> str:
    """Analyze Route53 hosted zone including records and health checks."""
    try:
        route53 = boto3.client('route53', region_name='us-east-1')
        
        # Get hosted zone details
        zone_response = route53.get_hosted_zone(Id=zone_id)
        zone = zone_response['HostedZone']
        
        # Get DNS records
        records_response = route53.list_resource_record_sets(HostedZoneId=zone_id)
        
        records = []
        for record in records_response['ResourceRecordSets']:
            record_data = {
                'name': record['Name'],
                'type': record['Type'],
                'ttl': record.get('TTL', 'N/A'),
                'values': []
            }
            
            if 'ResourceRecords' in record:
                record_data['values'] = [rr['Value'] for rr in record['ResourceRecords']]
            elif 'AliasTarget' in record:
                record_data['alias_target'] = record['AliasTarget']['DNSName']
                record_data['alias_zone'] = record['AliasTarget']['HostedZoneId']
            
            records.append(record_data)
        
        # Get health checks associated with this zone
        health_checks_response = route53.list_health_checks()
        associated_health_checks = []
        
        for hc in health_checks_response['HealthChecks']:
            if zone['Name'].rstrip('.') in hc.get('HealthCheckConfig', {}).get('FullyQualifiedDomainName', ''):
                associated_health_checks.append({
                    'health_check_id': hc['Id'],
                    'type': hc['HealthCheckConfig']['Type'],
                    'fqdn': hc['HealthCheckConfig'].get('FullyQualifiedDomainName', 'N/A')
                })
        
        analysis = {
            'zone_id': zone_id,
            'name': zone['Name'],
            'record_count': zone['ResourceRecordSetCount'],
            'private_zone': zone.get('Config', {}).get('PrivateZone', False),
            'comment': zone.get('Config', {}).get('Comment', ''),
            'records': records,
            'health_checks': associated_health_checks
        }
        
        return f"Route53 Hosted Zone Analysis for {zone_id}:\n" + json.dumps(analysis, indent=2, default=str)
        
    except Exception as e:
        return f"Error analyzing Route53 hosted zone: {str(e)}"


@tool
def create_route53_record(zone_id: str, record_name: str, record_type: str, 
                         record_value: str, ttl: int = 300) -> str:
    """Create a DNS record in a Route53 hosted zone."""
    try:
        route53 = boto3.client('route53', region_name='us-east-1')
        
        change_batch = {
            'Changes': [{
                'Action': 'CREATE',
                'ResourceRecordSet': {
                    'Name': record_name,
                    'Type': record_type.upper(),
                    'TTL': ttl,
                    'ResourceRecords': [{'Value': record_value}]
                }
            }]
        }
        
        response = route53.change_resource_record_sets(
            HostedZoneId=zone_id,
            ChangeBatch=change_batch
        )
        
        return f"Created {record_type} record {record_name} -> {record_value} in zone {zone_id}. Change ID: {response['ChangeInfo']['Id']}"
        
    except Exception as e:
        return f"Error creating Route53 record: {str(e)}"


# ============================================================================
# CloudFront Tools
# ============================================================================

@tool
def analyze_cloudfront_distribution(distribution_id: str) -> str:
    """Analyze CloudFront distribution including origins, behaviors, and metrics."""
    try:
        cloudfront = boto3.client('cloudfront', region_name='us-east-1')
        cloudwatch = boto3.client('cloudwatch', region_name='us-east-1')
        
        # Get distribution details
        response = cloudfront.get_distribution(Id=distribution_id)
        distribution = response['Distribution']
        config = distribution['DistributionConfig']
        
        # Get origins
        origins = []
        for origin in config['Origins']['Items']:
            origins.append({
                'id': origin['Id'],
                'domain_name': origin['DomainName'],
                'origin_path': origin.get('OriginPath', ''),
                'custom_origin': 'CustomOriginConfig' in origin,
                's3_origin': 'S3OriginConfig' in origin
            })
        
        # Get cache behaviors
        behaviors = []
        
        # Default cache behavior
        default_behavior = config['DefaultCacheBehavior']
        behaviors.append({
            'path_pattern': '*',
            'target_origin': default_behavior['TargetOriginId'],
            'viewer_protocol_policy': default_behavior['ViewerProtocolPolicy'],
            'allowed_methods': default_behavior.get('AllowedMethods', {}).get('Items', []),
            'cached_methods': default_behavior.get('AllowedMethods', {}).get('CachedMethods', {}).get('Items', []),
            'compress': default_behavior.get('Compress', False),
            'ttl_min': default_behavior.get('MinTTL', 0),
            'ttl_default': default_behavior.get('DefaultTTL', 86400),
            'ttl_max': default_behavior.get('MaxTTL', 31536000)
        })
        
        # Additional cache behaviors
        for behavior in config.get('CacheBehaviors', {}).get('Items', []):
            behaviors.append({
                'path_pattern': behavior['PathPattern'],
                'target_origin': behavior['TargetOriginId'],
                'viewer_protocol_policy': behavior['ViewerProtocolPolicy'],
                'allowed_methods': behavior.get('AllowedMethods', {}).get('Items', []),
                'cached_methods': behavior.get('AllowedMethods', {}).get('CachedMethods', {}).get('Items', []),
                'compress': behavior.get('Compress', False),
                'ttl_min': behavior.get('MinTTL', 0),
                'ttl_default': behavior.get('DefaultTTL', 86400),
                'ttl_max': behavior.get('MaxTTL', 31536000)
            })
        
        # Get recent metrics
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=24)
        
        metrics = {}
        metric_names = ['Requests', 'BytesDownloaded', 'BytesUploaded', '4xxErrorRate', '5xxErrorRate']
        
        for metric_name in metric_names:
            try:
                metric_response = cloudwatch.get_metric_statistics(
                    Namespace='AWS/CloudFront',
                    MetricName=metric_name,
                    Dimensions=[{'Name': 'DistributionId', 'Value': distribution_id}],
                    StartTime=start_time,
                    EndTime=end_time,
                    Period=3600,
                    Statistics=['Sum', 'Average']
                )
                
                if metric_response['Datapoints']:
                    total = sum(dp['Sum'] for dp in metric_response['Datapoints'])
                    avg = sum(dp['Average'] for dp in metric_response['Datapoints']) / len(metric_response['Datapoints'])
                    metrics[metric_name] = {'total': total, 'average': avg}
            except:
                metrics[metric_name] = {'total': 0, 'average': 0}
        
        analysis = {
            'distribution_id': distribution_id,
            'domain_name': distribution['DomainName'],
            'status': distribution['Status'],
            'enabled': config['Enabled'],
            'price_class': config['PriceClass'],
            'http_version': config['HttpVersion'],
            'ipv6_enabled': config['IsIPV6Enabled'],
            'default_root_object': config.get('DefaultRootObject', ''),
            'origins': origins,
            'cache_behaviors': behaviors,
            'custom_error_pages': len(config.get('CustomErrorResponses', {}).get('Items', [])),
            'last_modified': distribution['LastModifiedTime'].isoformat(),
            'recent_metrics_24h': metrics
        }
        
        return f"CloudFront Distribution Analysis for {distribution_id}:\n" + json.dumps(analysis, indent=2, default=str)
        
    except Exception as e:
        return f"Error analyzing CloudFront distribution: {str(e)}"


@tool
def invalidate_cloudfront_cache(distribution_id: str, paths: List[str]) -> str:
    """Create a CloudFront cache invalidation for specified paths."""
    try:
        cloudfront = boto3.client('cloudfront', region_name='us-east-1')
        
        invalidation_batch = {
            'Paths': {
                'Quantity': len(paths),
                'Items': paths
            },
            'CallerReference': f"cloudops-sre-{int(datetime.utcnow().timestamp())}"
        }
        
        response = cloudfront.create_invalidation(
            DistributionId=distribution_id,
            InvalidationBatch=invalidation_batch
        )
        
        invalidation_id = response['Invalidation']['Id']
        return f"Created CloudFront invalidation {invalidation_id} for distribution {distribution_id}. Paths: {', '.join(paths)}"
        
    except Exception as e:
        return f"Error creating CloudFront invalidation: {str(e)}"


# ============================================================================
# Load Balancer Tools (Expanded)
# ============================================================================

@tool
def analyze_load_balancer(lb_arn: str) -> str:
    """Comprehensive load balancer analysis including target groups, listeners, and health."""
    try:
        elbv2 = boto3.client('elbv2', region_name='us-east-1')
        cloudwatch = boto3.client('cloudwatch', region_name='us-east-1')
        
        # Get load balancer details
        lb_response = elbv2.describe_load_balancers(LoadBalancerArns=[lb_arn])
        if not lb_response['LoadBalancers']:
            return f"Load balancer {lb_arn} not found"
        
        lb = lb_response['LoadBalancers'][0]
        lb_name = lb['LoadBalancerName']
        
        # Get listeners
        listeners_response = elbv2.describe_listeners(LoadBalancerArn=lb_arn)
        listeners = []
        
        for listener in listeners_response['Listeners']:
            listeners.append({
                'listener_arn': listener['ListenerArn'],
                'port': listener['Port'],
                'protocol': listener['Protocol'],
                'ssl_policy': listener.get('SslPolicy', 'N/A'),
                'certificate_arn': listener.get('Certificates', [{}])[0].get('CertificateArn', 'N/A'),
                'default_actions': [action['Type'] for action in listener['DefaultActions']]
            })
        
        # Get target groups
        target_groups_response = elbv2.describe_target_groups(LoadBalancerArn=lb_arn)
        target_groups = []
        
        for tg in target_groups_response['TargetGroups']:
            # Get target health
            health_response = elbv2.describe_target_health(TargetGroupArn=tg['TargetGroupArn'])
            
            healthy_targets = len([t for t in health_response['TargetHealthDescriptions'] 
                                 if t['TargetHealth']['State'] == 'healthy'])
            total_targets = len(health_response['TargetHealthDescriptions'])
            
            target_groups.append({
                'target_group_arn': tg['TargetGroupArn'],
                'target_group_name': tg['TargetGroupName'],
                'protocol': tg['Protocol'],
                'port': tg['Port'],
                'health_check_path': tg.get('HealthCheckPath', 'N/A'),
                'health_check_interval': tg.get('HealthCheckIntervalSeconds', 'N/A'),
                'healthy_threshold': tg.get('HealthyThresholdCount', 'N/A'),
                'unhealthy_threshold': tg.get('UnhealthyThresholdCount', 'N/A'),
                'healthy_targets': healthy_targets,
                'total_targets': total_targets
            })
        
        # Get CloudWatch metrics
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=1)
        
        metrics = {}
        metric_names = ['RequestCount', 'TargetResponseTime', 'HTTPCode_Target_2XX_Count', 
                       'HTTPCode_Target_4XX_Count', 'HTTPCode_Target_5XX_Count']
        
        for metric_name in metric_names:
            try:
                metric_response = cloudwatch.get_metric_statistics(
                    Namespace='AWS/ApplicationELB',
                    MetricName=metric_name,
                    Dimensions=[{'Name': 'LoadBalancer', 'Value': lb_name}],
                    StartTime=start_time,
                    EndTime=end_time,
                    Period=3600,
                    Statistics=['Sum', 'Average']
                )
                
                if metric_response['Datapoints']:
                    latest = max(metric_response['Datapoints'], key=lambda x: x['Timestamp'])
                    metrics[metric_name] = latest
            except:
                metrics[metric_name] = {'Sum': 0, 'Average': 0}
        
        analysis = {
            'load_balancer_arn': lb_arn,
            'load_balancer_name': lb_name,
            'type': lb['Type'],
            'scheme': lb['Scheme'],
            'state': lb['State']['Code'],
            'vpc_id': lb['VpcId'],
            'availability_zones': [az['ZoneName'] for az in lb['AvailabilityZones']],
            'security_groups': lb.get('SecurityGroups', []),
            'listeners': listeners,
            'target_groups': target_groups,
            'recent_metrics': metrics,
            'created_time': lb['CreatedTime'].isoformat()
        }
        
        return f"Load Balancer Analysis for {lb_name}:\n" + json.dumps(analysis, indent=2, default=str)
        
    except Exception as e:
        return f"Error analyzing load balancer: {str(e)}"


@tool
def modify_target_group_health_check(target_group_arn: str, health_check_path: str = None,
                                   health_check_interval: int = None, healthy_threshold: int = None,
                                   unhealthy_threshold: int = None, timeout: int = None) -> str:
    """Modify target group health check settings."""
    try:
        elbv2 = boto3.client('elbv2', region_name='us-east-1')
        
        modify_params = {'TargetGroupArn': target_group_arn}
        changes = []
        
        if health_check_path:
            modify_params['HealthCheckPath'] = health_check_path
            changes.append(f"path to {health_check_path}")
        
        if health_check_interval:
            modify_params['HealthCheckIntervalSeconds'] = health_check_interval
            changes.append(f"interval to {health_check_interval}s")
        
        if healthy_threshold:
            modify_params['HealthyThresholdCount'] = healthy_threshold
            changes.append(f"healthy threshold to {healthy_threshold}")
        
        if unhealthy_threshold:
            modify_params['UnhealthyThresholdCount'] = unhealthy_threshold
            changes.append(f"unhealthy threshold to {unhealthy_threshold}")
        
        if timeout:
            modify_params['HealthCheckTimeoutSeconds'] = timeout
            changes.append(f"timeout to {timeout}s")
        
        if not changes:
            return "No health check modifications specified"
        
        elbv2.modify_target_group(**modify_params)
        
        return f"Modified target group health check: {', '.join(changes)}"
        
    except Exception as e:
        return f"Error modifying target group health check: {str(e)}"


# ============================================================================
# Network — List tools
# ============================================================================

@tool
def list_vpcs(region: str = 'us-east-1') -> str:
    """List all VPCs in the account."""
    try:
        ec2 = boto3.client('ec2', region_name=region)
        response = ec2.describe_vpcs()
        vpcs = []
        for v in response['Vpcs']:
            name = next((t['Value'] for t in v.get('Tags', []) if t['Key'] == 'Name'), 'Unnamed')
            vpcs.append({
                'vpc_id': v['VpcId'],
                'name': name,
                'cidr': v['CidrBlock'],
                'state': v['State'],
                'default': v['IsDefault'],
            })
        if not vpcs:
            return f"No VPCs found in {region}."
        lines = [f"Found {len(vpcs)} VPC(s) in {region}:\n"]
        for v in vpcs:
            lines.append(
                f"  {v['vpc_id']}  Name={v['name']}  CIDR={v['cidr']}  "
                f"[{v['state']}]  default={v['default']}"
            )
        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing VPCs: {str(e)}"


@tool
def list_security_groups(vpc_id: str = '', region: str = 'us-east-1') -> str:
    """List all security groups, optionally filtered by VPC ID."""
    try:
        ec2 = boto3.client('ec2', region_name=region)
        filters = [{'Name': 'vpc-id', 'Values': [vpc_id]}] if vpc_id else []
        response = ec2.describe_security_groups(Filters=filters)
        groups = []
        for sg in response['SecurityGroups']:
            groups.append({
                'group_id': sg['GroupId'],
                'name': sg['GroupName'],
                'description': sg['Description'][:60],
                'vpc_id': sg['VpcId'],
                'inbound_rules': len(sg['IpPermissions']),
                'outbound_rules': len(sg['IpPermissionsEgress']),
            })
        if not groups:
            return "No security groups found."
        lines = [f"Found {len(groups)} security group(s):\n"]
        for sg in groups:
            lines.append(
                f"  {sg['group_id']}  {sg['name']}  vpc={sg['vpc_id']}  "
                f"inbound={sg['inbound_rules']}  outbound={sg['outbound_rules']}"
            )
        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing security groups: {str(e)}"


@tool
def list_subnets(vpc_id: str = '', region: str = 'us-east-1') -> str:
    """List all subnets, optionally filtered by VPC ID."""
    try:
        ec2 = boto3.client('ec2', region_name=region)
        filters = [{'Name': 'vpc-id', 'Values': [vpc_id]}] if vpc_id else []
        response = ec2.describe_subnets(Filters=filters)
        subnets = []
        for s in response['Subnets']:
            name = next((t['Value'] for t in s.get('Tags', []) if t['Key'] == 'Name'), 'Unnamed')
            subnets.append({
                'subnet_id': s['SubnetId'],
                'name': name,
                'vpc_id': s['VpcId'],
                'cidr': s['CidrBlock'],
                'az': s['AvailabilityZone'],
                'available_ips': s['AvailableIpAddressCount'],
                'public': s['MapPublicIpOnLaunch'],
            })
        if not subnets:
            return "No subnets found."
        lines = [f"Found {len(subnets)} subnet(s):\n"]
        for s in subnets:
            lines.append(
                f"  {s['subnet_id']}  {s['name']}  {s['cidr']}  "
                f"az={s['az']}  public={s['public']}  free_ips={s['available_ips']}"
            )
        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing subnets: {str(e)}"


@tool
def list_internet_gateways(region: str = 'us-east-1') -> str:
    """List all Internet Gateways and their VPC attachments."""
    try:
        ec2 = boto3.client('ec2', region_name=region)
        response = ec2.describe_internet_gateways()
        igws = []
        for igw in response['InternetGateways']:
            name = next((t['Value'] for t in igw.get('Tags', []) if t['Key'] == 'Name'), 'Unnamed')
            attached_vpcs = [a['VpcId'] for a in igw.get('Attachments', []) if a['State'] == 'available']
            igws.append({
                'igw_id': igw['InternetGatewayId'],
                'name': name,
                'attached_vpcs': attached_vpcs,
            })
        if not igws:
            return "No Internet Gateways found."
        lines = [f"Found {len(igws)} Internet Gateway(s):\n"]
        for igw in igws:
            lines.append(f"  {igw['igw_id']}  Name={igw['name']}  vpcs={igw['attached_vpcs']}")
        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing Internet Gateways: {str(e)}"


@tool
def list_nat_gateways(region: str = 'us-east-1') -> str:
    """List all NAT Gateways and their status."""
    try:
        ec2 = boto3.client('ec2', region_name=region)
        response = ec2.describe_nat_gateways()
        gateways = []
        for ngw in response['NatGateways']:
            name = next((t['Value'] for t in ngw.get('Tags', []) if t['Key'] == 'Name'), 'Unnamed')
            gateways.append({
                'nat_gateway_id': ngw['NatGatewayId'],
                'name': name,
                'state': ngw['State'],
                'vpc_id': ngw['VpcId'],
                'subnet_id': ngw['SubnetId'],
                'type': ngw.get('ConnectivityType', 'public'),
            })
        if not gateways:
            return "No NAT Gateways found."
        lines = [f"Found {len(gateways)} NAT Gateway(s):\n"]
        for ngw in gateways:
            lines.append(
                f"  {ngw['nat_gateway_id']}  Name={ngw['name']}  [{ngw['state']}]  "
                f"vpc={ngw['vpc_id']}  type={ngw['type']}"
            )
        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing NAT Gateways: {str(e)}"


@tool
def list_load_balancers(region: str = 'us-east-1') -> str:
    """List all Application, Network, and Gateway Load Balancers."""
    try:
        elbv2 = boto3.client('elbv2', region_name=region)
        response = elbv2.describe_load_balancers()
        lbs = []
        for lb in response['LoadBalancers']:
            lbs.append({
                'name': lb['LoadBalancerName'],
                'arn': lb['LoadBalancerArn'],
                'type': lb['Type'],
                'scheme': lb['Scheme'],
                'state': lb['State']['Code'],
                'dns_name': lb['DNSName'],
                'vpc_id': lb.get('VpcId', 'N/A'),
            })
        if not lbs:
            return "No load balancers found."
        lines = [f"Found {len(lbs)} load balancer(s):\n"]
        for lb in lbs:
            lines.append(
                f"  {lb['name']}  type={lb['type']}  scheme={lb['scheme']}  "
                f"[{lb['state']}]  dns={lb['dns_name']}"
            )
        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing load balancers: {str(e)}"


@tool
def list_cloudfront_distributions(region: str = 'us-east-1') -> str:
    """List all CloudFront distributions."""
    try:
        cf = boto3.client('cloudfront', region_name='us-east-1')
        response = cf.list_distributions()
        dist_list = response.get('DistributionList', {})
        items = dist_list.get('Items', [])
        if not items:
            return "No CloudFront distributions found."
        lines = [f"Found {len(items)} CloudFront distribution(s):\n"]
        for d in items:
            origins = [o['DomainName'] for o in d.get('Origins', {}).get('Items', [])]
            lines.append(
                f"  {d['Id']}  domain={d['DomainName']}  "
                f"status={d['Status']}  enabled={d['Enabled']}  "
                f"origins={origins}"
            )
        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing CloudFront distributions: {str(e)}"


@tool
def list_hosted_zones(region: str = 'us-east-1') -> str:
    """List all Route53 hosted zones."""
    try:
        r53 = boto3.client('route53', region_name='us-east-1')
        response = r53.list_hosted_zones()
        zones = []
        for z in response['HostedZones']:
            zones.append({
                'id': z['Id'].split('/')[-1],
                'name': z['Name'],
                'record_count': z['ResourceRecordSetCount'],
                'private': z['Config']['PrivateZone'],
                'comment': z['Config'].get('Comment', ''),
            })
        if not zones:
            return "No Route53 hosted zones found."
        lines = [f"Found {len(zones)} hosted zone(s):\n"]
        for z in zones:
            lines.append(
                f"  {z['id']}  {z['name']}  records={z['record_count']}  "
                f"private={z['private']}"
            )
        return '\n'.join(lines)
    except Exception as e:
        return f"Error listing hosted zones: {str(e)}"


# ============================================================================
# Security Group — modify rules
# ============================================================================

@tool
def remove_security_group_rule(group_id: str, rule_type: str, protocol: str,
                                port: int, cidr: str) -> str:
    """
    Remove an inbound or outbound rule from a security group.
    rule_type: 'inbound' or 'outbound'
    protocol: 'tcp', 'udp', 'icmp', or '-1' (all)
    port: port number (use -1 for all ports with protocol '-1')
    cidr: e.g. '0.0.0.0/0' or '10.0.0.0/8'
    """
    try:
        ec2 = boto3.client('ec2', region_name='us-east-1')
        ip_permission = {
            'IpProtocol': protocol.lower(),
            'IpRanges': [{'CidrIp': cidr}],
        }
        if protocol.lower() != '-1':
            ip_permission['FromPort'] = port
            ip_permission['ToPort'] = port

        if rule_type.lower() == 'inbound':
            ec2.revoke_security_group_ingress(
                GroupId=group_id,
                IpPermissions=[ip_permission]
            )
        elif rule_type.lower() == 'outbound':
            ec2.revoke_security_group_egress(
                GroupId=group_id,
                IpPermissions=[ip_permission]
            )
        else:
            return "rule_type must be 'inbound' or 'outbound'."

        direction = "inbound" if rule_type.lower() == 'inbound' else "outbound"
        return (f"Removed {direction} rule from security group {group_id}: "
                f"{protocol.upper()} port {port} from {cidr}.")
    except Exception as e:
        return f"Error removing security group rule: {str(e)}"


@tool
def delete_security_group_rule_by_id(group_id: str, rule_id: str) -> str:
    """
    Remove a security group rule by its rule ID (sgr-xxxxxxxx).
    Use analyze_security_group first to find the rule ID.
    rule_type: 'inbound' or 'outbound'
    """
    try:
        ec2 = boto3.client('ec2', region_name='us-east-1')
        # Try inbound first, then outbound
        try:
            ec2.revoke_security_group_ingress(
                GroupId=group_id,
                SecurityGroupRuleIds=[rule_id]
            )
            return f"Removed inbound rule {rule_id} from security group {group_id}."
        except Exception:
            ec2.revoke_security_group_egress(
                GroupId=group_id,
                SecurityGroupRuleIds=[rule_id]
            )
            return f"Removed outbound rule {rule_id} from security group {group_id}."
    except Exception as e:
        return f"Error removing security group rule by ID: {str(e)}"


@tool
def update_security_group_rule(group_id: str, rule_id: str, protocol: str,
                                from_port: int, to_port: int, cidr: str,
                                description: str = '') -> str:
    """
    Update an existing security group rule in-place by rule ID.
    Use analyze_security_group to find the rule ID first.
    """
    try:
        ec2 = boto3.client('ec2', region_name='us-east-1')
        rule = {
            'SecurityGroupRuleId': rule_id,
            'SecurityGroupRule': {
                'IpProtocol': protocol.lower(),
                'FromPort': from_port,
                'ToPort': to_port,
                'CidrIpv4': cidr,
            }
        }
        if description:
            rule['SecurityGroupRule']['Description'] = description

        ec2.modify_security_group_rules(
            GroupId=group_id,
            SecurityGroupRules=[rule]
        )
        return (f"Updated rule {rule_id} on security group {group_id}: "
                f"{protocol.upper()} {from_port}-{to_port} from {cidr}.")
    except Exception as e:
        return f"Error updating security group rule: {str(e)}"


@tool
def delete_security_group(group_id: str) -> str:
    """
    Delete an entire security group. The group must not be attached to any resources.
    Use analyze_security_group first to check associated instances.
    """
    try:
        ec2 = boto3.client('ec2', region_name='us-east-1')
        ec2.delete_security_group(GroupId=group_id)
        return f"Deleted security group {group_id}."
    except Exception as e:
        return f"Error deleting security group: {str(e)}"


@tool
def create_security_group(group_name: str, description: str, vpc_id: str) -> str:
    """Create a new security group in a VPC."""
    try:
        ec2 = boto3.client('ec2', region_name='us-east-1')
        response = ec2.create_security_group(
            GroupName=group_name,
            Description=description,
            VpcId=vpc_id,
            TagSpecifications=[{
                'ResourceType': 'security-group',
                'Tags': [
                    {'Key': 'Name', 'Value': group_name},
                    {'Key': 'CreatedBy', 'Value': 'CloudOps-SRE'},
                ]
            }]
        )
        sg_id = response['GroupId']
        return f"Created security group '{group_name}' ({sg_id}) in VPC {vpc_id}."
    except Exception as e:
        return f"Error creating security group: {str(e)}"
