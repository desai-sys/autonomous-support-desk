"""
AWS Bedrock AgentCore Entrypoint - Multi-Agent Architecture.

Production-grade design: instead of one 256-tool agent, the router classifies
the query and loads ONLY the relevant domain's tools (~30-50 per specialist).

Deploy with:
    agentcore configure --entrypoint src/agentcore_entrypoint.py --non-interactive
    agentcore deploy
"""

import os
import re
import time
import json
import boto3
from datetime import datetime
from bedrock_agentcore import BedrockAgentCoreApp
from strands import Agent, tool
from strands.models import BedrockModel

# Account-aware client context (cross-account / multi-account support)
from src.core.aws_clients import use_account, get_active_account

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

# Router uses Nova Micro (ultra-fast classification, ~50ms)
# Specialist agents use Nova Pro (powerful reasoning with focused tool set)
_AGENT_MODEL = BedrockModel(
    model_id="us.amazon.nova-pro-v1:0",
    max_tokens=8192,
)

# ---------------------------------------------------------------------------
# Import tool modules (unchanged)
# ---------------------------------------------------------------------------

from src.tools.compute_tools import (
    list_ec2_instances,
    analyze_ec2_instance, start_ec2_instance, stop_ec2_instance, reboot_ec2_instance,
    modify_ec2_instance_type, create_ec2_snapshot, analyze_lambda_function,
    update_lambda_memory, update_lambda_timeout, invoke_lambda_function,
    analyze_auto_scaling_group, create_scaling_policy, analyze_ecs_cluster,
    scale_ecs_service, restart_ecs_service,
    list_lambda_functions, enable_ec2_monitoring, get_ec2_metrics
)

from src.tools.storage_tools import (
    list_s3_buckets, analyze_s3_bucket, list_s3_bucket_objects, enable_s3_versioning,
    create_s3_lifecycle_policy, enable_s3_server_side_encryption,
    list_public_s3_buckets, enable_s3_public_access_block, get_s3_bucket_public_access_status,
    analyze_ebs_volume, modify_ebs_volume, create_ebs_snapshot,
    list_ebs_snapshots, analyze_efs_filesystem, create_efs_access_point,
    list_ebs_volumes, list_efs_filesystems
)

from src.tools.network_tools import (
    list_vpcs, list_security_groups, list_subnets, list_internet_gateways,
    list_nat_gateways, list_load_balancers, list_cloudfront_distributions,
    list_hosted_zones,
    analyze_vpc, create_vpc_subnet, analyze_security_group, add_security_group_rule,
    remove_security_group_rule, delete_security_group_rule_by_id,
    update_security_group_rule, delete_security_group, create_security_group,
    analyze_route53_hosted_zone, create_route53_record, analyze_cloudfront_distribution,
    invalidate_cloudfront_cache, analyze_load_balancer, modify_target_group_health_check
)

from src.tools.database_tools import (
    list_rds_instances, list_dynamodb_tables, list_elasticache_clusters,
    list_documentdb_clusters,
    analyze_rds_instance, create_rds_snapshot, modify_rds_instance,
    analyze_dynamodb_table, update_dynamodb_capacity, enable_dynamodb_point_in_time_recovery,
    analyze_elasticache_cluster, create_elasticache_snapshot,
    analyze_documentdb_cluster, create_documentdb_snapshot
)

from src.tools.security_tools import (
    analyze_iam_user, analyze_iam_role, list_iam_users_without_mfa, rotate_iam_access_key,
    analyze_secrets_manager_secret, rotate_secrets_manager_secret,
    analyze_kms_key, create_kms_key, analyze_waf_web_acl, create_waf_ip_block_rule,
    analyze_guardduty_findings, archive_guardduty_finding,
    list_iam_users, list_iam_roles, list_secrets, list_kms_keys
)

from src.tools.aws_tools import (
    list_cloudwatch_alarms, create_cloudwatch_alarm, get_cloudwatch_metrics,
    list_log_groups, get_log_events, create_log_metric_filter,
    list_sns_topics, create_sns_topic, publish_sns_message,
    list_sns_subscriptions, subscribe_sns_topic,
    list_sqs_queues, analyze_sqs_queue, purge_sqs_queue,
    send_sqs_message, get_sqs_queue_attributes,
    list_api_gateways, analyze_api_gateway, create_api_gateway_deployment,
    get_api_gateway_stages,
    list_cloudformation_stacks, analyze_cloudformation_stack,
    get_stack_events, detect_stack_drift,
    list_pipelines, get_pipeline_state, retry_pipeline_stage, start_pipeline_execution,
    list_ecr_repositories, analyze_ecr_repository, get_ecr_image_scan_results,
    delete_ecr_images,
    list_kinesis_streams, analyze_kinesis_stream, get_kinesis_shard_iterator,
    list_state_machines, analyze_state_machine,
    start_state_machine_execution, stop_state_machine_execution,
    list_event_buses, list_event_rules, enable_event_rule,
    disable_event_rule, put_event,
    list_ssm_parameters, get_ssm_parameter, put_ssm_parameter,
    run_ssm_command, get_ssm_command_output, list_ssm_patch_baselines,
    get_cost_and_usage, get_cost_forecast, get_rightsizing_recommendations,
    list_backup_plans, list_backup_jobs, start_backup_job, get_backup_job_status,
    list_glue_jobs, analyze_glue_job, start_glue_job, stop_glue_job, get_glue_job_run,
    list_athena_workgroups, start_athena_query, get_athena_query_results,
    list_opensearch_domains, analyze_opensearch_domain,
    list_opensearch_serverless_collections, analyze_opensearch_serverless_collection,
    list_opensearch_serverless_indexes, list_opensearch_serverless_access_policies,
    create_opensearch_serverless_collection,
    list_msk_clusters, analyze_msk_cluster, reboot_msk_broker,
    list_appsync_apis, analyze_appsync_api,
    list_cognito_user_pools, analyze_cognito_user_pool, list_cognito_users,
    get_ses_sending_stats, list_ses_identities, check_ses_sending_quota,
    list_cloudtrail_trails, analyze_cloudtrail_trail, lookup_cloudtrail_events,
    list_config_rules, get_config_compliance_summary, get_noncompliant_resources,
    get_trusted_advisor_checks, get_trusted_advisor_check_result,
    describe_aws_health_events, describe_affected_resources,
    list_redshift_clusters, analyze_redshift_cluster,
    pause_redshift_cluster, resume_redshift_cluster,
    list_neptune_clusters, analyze_neptune_cluster,
    list_timestream_databases, list_timestream_tables,
    list_qldb_ledgers, analyze_qldb_ledger,
    list_dms_tasks, analyze_dms_task, start_dms_task, stop_dms_task,
    list_transfer_servers, analyze_transfer_server,
    list_datasync_tasks, analyze_datasync_task, start_datasync_task,
    list_waf_rules,
    get_shield_protection_status, list_shield_protections,
    get_macie_status, list_macie_findings,
    list_inspector_findings, get_inspector_coverage,
    get_security_hub_findings, get_security_hub_summary, enable_security_hub_standard,
    list_detective_graphs,
    list_access_analyzers, list_access_analyzer_findings,
    list_ram_resources, list_ram_resource_shares,
    list_service_quotas, request_quota_increase,
    list_budgets, create_budget_alert,
    list_org_accounts, describe_org_policy,
    list_landing_zones,
    list_eks_clusters, analyze_eks_cluster, update_eks_nodegroup,
    list_ecs_task_definitions, deregister_task_definition,
    list_batch_job_queues, analyze_batch_job_queue, submit_batch_job, cancel_batch_job,
    list_sagemaker_endpoints, analyze_sagemaker_endpoint, list_sagemaker_training_jobs,
    list_sagemaker_domains, analyze_sagemaker_domain, list_sagemaker_user_profiles,
    list_sagemaker_apps, list_sagemaker_models, list_sagemaker_notebooks,
    start_sagemaker_notebook, stop_sagemaker_notebook,
    list_sagemaker_pipelines, list_sagemaker_experiments,
    list_sagemaker_feature_groups, list_sagemaker_model_packages,
    stop_sagemaker_training_job, delete_sagemaker_endpoint,
    list_bedrock_models, list_bedrock_knowledge_bases, analyze_bedrock_knowledge_base,
    list_apprunner_services, analyze_apprunner_service,
    pause_apprunner_service, resume_apprunner_service,
    list_lightsail_instances, analyze_lightsail_instance,
    describe_agent_capabilities,
    search_resource_by_name,
)

from src.tools.deployment_tools import (
    deploy_lambda_function, rollback_lambda_function,
    deploy_ecs_service, rollback_ecs_service,
    deploy_to_ec2, check_ec2_ssm_status,
    deploy_apprunner_service, deploy_apprunner_source,
    deploy_static_site,
    validate_cfn_template, deploy_cfn_stack, rollback_cfn_stack,
    prepare_code_payload, detect_code_language,
    create_deployment_plan,
)

from src.tools.artifact_tools import (
    inspect_uploaded_artifact,
    deploy_artifact_to_lambda,
    deploy_artifact_to_ec2, launch_ec2_for_artifact,
    deploy_artifact_to_ecs,
    deploy_artifact_to_static_site,
)

# Import enterprise core modules
from src.core.monitoring import monitoring_manager, monitor_performance
from src.core.security import security_manager
from src.core.multi_tenant import tenant_manager

# Import the domain router
from src.agents.router import DomainRouter


# ---------------------------------------------------------------------------
# Domain Router instance
# ---------------------------------------------------------------------------

router = DomainRouter(region="us-east-1")


# ---------------------------------------------------------------------------
# Domain-specific system prompts
# ---------------------------------------------------------------------------

DOMAIN_PROMPTS = {
    "compute": (
        "You are an AWS Compute specialist. You manage EC2, Lambda, ECS, EKS, Batch, "
        "SageMaker, App Runner, Lightsail, and CloudWatch metrics/alarms/logs.\n\n"
        "Always use list_ec2_instances to find instances first, analyze before acting, "
        "confirm before changes.\n\n"
        "RULES:\n"
        "- If you search for a resource and cannot find it, say so clearly. "
        "Never substitute a different resource.\n"
        "- For read operations, execute immediately. For write/delete operations, "
        "describe and ask for confirmation.\n"
        "- Respond in plain English with bullet points, not raw JSON.\n"
        "- When the user says 'yes', 'go ahead', 'do it', 'confirm' -- execute the "
        "previously proposed action.\n"
        "- Chain multiple tools when needed for full context."
    ),

    "storage": (
        "You are an AWS Storage specialist. You manage S3 buckets, EBS volumes, EFS, "
        "ECR, Backup, DataSync, and Transfer Family.\n\n"
        "For S3, use list_s3_buckets first. For security checks, use list_public_s3_buckets.\n\n"
        "RULES:\n"
        "- If you search for a resource and cannot find it, say so clearly. "
        "Never substitute a different resource.\n"
        "- For read operations, execute immediately. For write/delete operations, "
        "describe and ask for confirmation.\n"
        "- Respond in plain English with bullet points, not raw JSON.\n"
        "- When the user says 'yes', 'go ahead', 'do it', 'confirm' -- execute the "
        "previously proposed action.\n"
        "- Always check versioning and encryption status when analyzing buckets."
    ),

    "network": (
        "You are an AWS Network specialist. You manage VPCs, security groups, load balancers, "
        "Route53, CloudFront, API Gateway, SNS, SQS, Kinesis, and MSK.\n\n"
        "For security groups use list_security_groups then analyze_security_group. "
        "For DNS use list_hosted_zones first.\n\n"
        "RULES:\n"
        "- If you search for a resource and cannot find it, say so clearly. "
        "Never substitute a different resource.\n"
        "- For read operations, execute immediately. For write/delete operations, "
        "describe and ask for confirmation.\n"
        "- Respond in plain English with bullet points, not raw JSON.\n"
        "- When the user says 'yes', 'go ahead', 'do it', 'confirm' -- execute the "
        "previously proposed action.\n"
        "- Flag overly permissive rules (0.0.0.0/0) as security concerns."
    ),

    "database": (
        "You are an AWS Database specialist. You manage RDS, DynamoDB, ElastiCache, "
        "DocumentDB, Redshift, Neptune, OpenSearch, Glue, Athena, Timestream, QLDB, and DMS.\n\n"
        "Use list_ tools first to discover resources. For RDS, check status and endpoints.\n\n"
        "RULES:\n"
        "- If you search for a resource and cannot find it, say so clearly. "
        "Never substitute a different resource.\n"
        "- For read operations, execute immediately. For write/delete operations, "
        "describe and ask for confirmation.\n"
        "- Respond in plain English with bullet points, not raw JSON.\n"
        "- When the user says 'yes', 'go ahead', 'do it', 'confirm' -- execute the "
        "previously proposed action.\n"
        "- Always mention backup status and storage encryption when analyzing databases."
    ),

    "security": (
        "You are an AWS Security specialist. You manage IAM, GuardDuty, WAF, KMS, "
        "Secrets Manager, CloudTrail, Inspector, Macie, Config, Shield, and Access Analyzer.\n\n"
        "Focus on findings and compliance. For IAM, check MFA and unused credentials.\n\n"
        "RULES:\n"
        "- If you search for a resource and cannot find it, say so clearly. "
        "Never substitute a different resource.\n"
        "- For read operations, execute immediately. For write/delete operations, "
        "describe and ask for confirmation.\n"
        "- Respond in plain English with bullet points, not raw JSON.\n"
        "- When the user says 'yes', 'go ahead', 'do it', 'confirm' -- execute the "
        "previously proposed action.\n"
        "- Always highlight critical findings and recommend remediation steps."
    ),

    "ops": (
        "You are an AWS Operations specialist. You manage costs, budgets, CloudFormation, "
        "CodePipeline, SSM, Step Functions, CloudWatch, health events, Trusted Advisor, "
        "and service quotas.\n\n"
        "For costs use get_cost_and_usage. For deployments use list_cloudformation_stacks.\n\n"
        "RULES:\n"
        "- If you search for a resource and cannot find it, say so clearly. "
        "Never substitute a different resource.\n"
        "- For read operations, execute immediately. For write/delete operations, "
        "describe and ask for confirmation.\n"
        "- Respond in plain English with bullet points, not raw JSON.\n"
        "- When the user says 'yes', 'go ahead', 'do it', 'confirm' -- execute the "
        "previously proposed action.\n"
        "- For cost queries, always show breakdown by service and time period."
    ),

    "deployment": (
        "You are an AWS Deployment specialist. You deploy code to Lambda, ECS/ECR, EC2 (via SSM), "
        "App Runner, S3 static sites, and CloudFormation/SAM stacks.\n\n"
        "WORKFLOW:\n"
        "1. Use prepare_code_payload to extract/validate the code.\n"
        "2. Use detect_code_language if the runtime is not explicit.\n"
        "3. Use create_deployment_plan to present the plan to the operator.\n"
        "4. WAIT for operator confirmation before executing any deployment.\n"
        "5. Execute the appropriate deploy_* tool.\n"
        "6. Return the deployment manifest with resource identifiers.\n\n"
        "UPLOADED ZIP WORKFLOW (when an UPLOADED ARTIFACT note is present):\n"
        "1. Call inspect_uploaded_artifact first.\n"
        "2. Pick the target the operator asked for (lambda, ec2, ecs, s3 static site); "
        "if they did not say, recommend one from recommended_targets and ask.\n"
        "3. Present a plan via create_deployment_plan with account, region, resource name, "
        "runtime/handler or run command, and any IAM roles or instances that will be created.\n"
        "4. After confirmation call deploy_artifact_to_lambda, deploy_artifact_to_ec2 "
        "(launch_ec2_for_artifact first if they have no instance), deploy_artifact_to_ecs, "
        "or deploy_artifact_to_static_site with the artifact_uri.\n\n"
        "RULES:\n"
        "- NEVER deploy without presenting a plan and receiving confirmation.\n"
        "- Always include the AWS account ID and region in the deployment plan.\n"
        "- For updates to existing resources, mention what will change.\n"
        "- For rollbacks, confirm which version/revision will be restored.\n"
        "- If a deployment fails, return the error and suggest corrective actions.\n"
        "- Respond in plain English with bullet points, not raw JSON.\n"
        "- When the user says 'yes', 'go ahead', 'do it', 'confirm' -- execute the deployment."
    ),
}


# ---------------------------------------------------------------------------
# Domain tool groups (~30-50 tools each)
# ---------------------------------------------------------------------------

TOOLS_COMPUTE = [
    list_ec2_instances, analyze_ec2_instance, start_ec2_instance, stop_ec2_instance,
    reboot_ec2_instance, modify_ec2_instance_type, create_ec2_snapshot,
    enable_ec2_monitoring, get_ec2_metrics,
    list_lambda_functions, analyze_lambda_function, update_lambda_memory,
    update_lambda_timeout, invoke_lambda_function,
    analyze_auto_scaling_group, create_scaling_policy,
    analyze_ecs_cluster, scale_ecs_service, restart_ecs_service,
    list_ecs_task_definitions, deregister_task_definition,
    list_eks_clusters, analyze_eks_cluster, update_eks_nodegroup,
    list_batch_job_queues, analyze_batch_job_queue, submit_batch_job, cancel_batch_job,
    list_cloudwatch_alarms, get_cloudwatch_metrics, list_log_groups, get_log_events,
    run_ssm_command, get_ssm_command_output,
    list_apprunner_services, analyze_apprunner_service,
    pause_apprunner_service, resume_apprunner_service,
    list_lightsail_instances, analyze_lightsail_instance,
    list_sagemaker_endpoints, analyze_sagemaker_endpoint, list_sagemaker_training_jobs,
    list_sagemaker_domains, analyze_sagemaker_domain, list_sagemaker_user_profiles,
    list_sagemaker_apps, list_sagemaker_models, list_sagemaker_notebooks,
    start_sagemaker_notebook, stop_sagemaker_notebook,
    list_sagemaker_pipelines, list_sagemaker_experiments,
    list_sagemaker_feature_groups, list_sagemaker_model_packages,
    stop_sagemaker_training_job, delete_sagemaker_endpoint,
    search_resource_by_name,
    describe_agent_capabilities,
]

TOOLS_STORAGE = [
    list_s3_buckets, analyze_s3_bucket, list_s3_bucket_objects,
    list_public_s3_buckets, enable_s3_public_access_block, get_s3_bucket_public_access_status,
    enable_s3_versioning, create_s3_lifecycle_policy, enable_s3_server_side_encryption,
    list_ebs_volumes, analyze_ebs_volume, modify_ebs_volume, create_ebs_snapshot,
    list_ebs_snapshots,
    list_efs_filesystems, analyze_efs_filesystem, create_efs_access_point,
    list_backup_plans, list_backup_jobs, start_backup_job, get_backup_job_status,
    list_datasync_tasks, analyze_datasync_task, start_datasync_task,
    list_transfer_servers, analyze_transfer_server,
    list_ecr_repositories, analyze_ecr_repository, get_ecr_image_scan_results,
    delete_ecr_images,
    list_bedrock_knowledge_bases, analyze_bedrock_knowledge_base,
    search_resource_by_name,
    describe_agent_capabilities,
]

TOOLS_NETWORK = [
    list_vpcs, analyze_vpc, create_vpc_subnet,
    list_security_groups, analyze_security_group,
    add_security_group_rule, remove_security_group_rule,
    delete_security_group_rule_by_id, update_security_group_rule,
    delete_security_group, create_security_group,
    list_subnets, list_internet_gateways, list_nat_gateways,
    list_load_balancers, analyze_load_balancer, modify_target_group_health_check,
    list_cloudfront_distributions, analyze_cloudfront_distribution, invalidate_cloudfront_cache,
    list_hosted_zones, analyze_route53_hosted_zone, create_route53_record,
    list_api_gateways, analyze_api_gateway, create_api_gateway_deployment, get_api_gateway_stages,
    list_event_buses, list_event_rules, enable_event_rule, disable_event_rule, put_event,
    list_kinesis_streams, analyze_kinesis_stream,
    list_msk_clusters, analyze_msk_cluster, reboot_msk_broker,
    list_sns_topics, create_sns_topic, publish_sns_message, subscribe_sns_topic,
    list_sqs_queues, analyze_sqs_queue, purge_sqs_queue, send_sqs_message,
    search_resource_by_name,
    describe_agent_capabilities,
]

TOOLS_DATABASE = [
    list_rds_instances, analyze_rds_instance, create_rds_snapshot, modify_rds_instance,
    list_dynamodb_tables, analyze_dynamodb_table, update_dynamodb_capacity,
    enable_dynamodb_point_in_time_recovery,
    list_elasticache_clusters, analyze_elasticache_cluster, create_elasticache_snapshot,
    list_documentdb_clusters, analyze_documentdb_cluster, create_documentdb_snapshot,
    list_redshift_clusters, analyze_redshift_cluster, pause_redshift_cluster, resume_redshift_cluster,
    list_neptune_clusters, analyze_neptune_cluster,
    list_opensearch_domains, analyze_opensearch_domain,
    list_opensearch_serverless_collections, analyze_opensearch_serverless_collection,
    list_opensearch_serverless_indexes, list_opensearch_serverless_access_policies,
    create_opensearch_serverless_collection,
    list_glue_jobs, analyze_glue_job, start_glue_job, stop_glue_job, get_glue_job_run,
    list_athena_workgroups, start_athena_query, get_athena_query_results,
    list_timestream_databases, list_timestream_tables,
    list_qldb_ledgers, analyze_qldb_ledger,
    list_dms_tasks, analyze_dms_task, start_dms_task, stop_dms_task,
    search_resource_by_name,
    describe_agent_capabilities,
]

TOOLS_SECURITY = [
    list_iam_users, list_iam_roles, list_iam_users_without_mfa,
    analyze_iam_user, analyze_iam_role, rotate_iam_access_key,
    list_secrets, analyze_secrets_manager_secret, rotate_secrets_manager_secret,
    list_kms_keys, analyze_kms_key, create_kms_key,
    analyze_waf_web_acl, create_waf_ip_block_rule, list_waf_rules,
    analyze_guardduty_findings, archive_guardduty_finding,
    get_security_hub_findings, get_security_hub_summary, enable_security_hub_standard,
    list_inspector_findings, get_inspector_coverage,
    get_macie_status, list_macie_findings,
    get_shield_protection_status, list_shield_protections,
    list_cloudtrail_trails, analyze_cloudtrail_trail, lookup_cloudtrail_events,
    list_config_rules, get_config_compliance_summary, get_noncompliant_resources,
    list_access_analyzers, list_access_analyzer_findings,
    list_detective_graphs,
    list_cognito_user_pools, analyze_cognito_user_pool, list_cognito_users,
    search_resource_by_name,
    describe_agent_capabilities,
]

TOOLS_COST_OPS = [
    get_cost_and_usage, get_cost_forecast, get_rightsizing_recommendations,
    list_budgets, create_budget_alert,
    list_service_quotas, request_quota_increase,
    list_org_accounts, describe_org_policy, list_landing_zones,
    get_trusted_advisor_checks, get_trusted_advisor_check_result,
    describe_aws_health_events, describe_affected_resources,
    list_cloudwatch_alarms, create_cloudwatch_alarm, get_cloudwatch_metrics,
    list_log_groups, get_log_events, create_log_metric_filter,
    list_cloudformation_stacks, analyze_cloudformation_stack, get_stack_events, detect_stack_drift,
    list_pipelines, get_pipeline_state, retry_pipeline_stage, start_pipeline_execution,
    list_ssm_parameters, get_ssm_parameter, put_ssm_parameter, list_ssm_patch_baselines,
    list_state_machines, analyze_state_machine, start_state_machine_execution, stop_state_machine_execution,
    list_appsync_apis, analyze_appsync_api,
    get_ses_sending_stats, list_ses_identities, check_ses_sending_quota,
    list_bedrock_models,
    list_ram_resources, list_ram_resource_shares,
    list_backup_plans, list_backup_jobs,
    search_resource_by_name,
    describe_agent_capabilities,
]

TOOLS_DEPLOYMENT = [
    deploy_lambda_function, rollback_lambda_function,
    deploy_ecs_service, rollback_ecs_service,
    deploy_to_ec2, check_ec2_ssm_status,
    deploy_apprunner_service, deploy_apprunner_source,
    deploy_static_site,
    validate_cfn_template, deploy_cfn_stack, rollback_cfn_stack,
    prepare_code_payload, detect_code_language,
    create_deployment_plan,
    inspect_uploaded_artifact,
    deploy_artifact_to_lambda,
    deploy_artifact_to_ec2, launch_ec2_for_artifact, get_ssm_command_output,
    deploy_artifact_to_ecs,
    deploy_artifact_to_static_site,
    list_ec2_instances, list_lambda_functions,
    search_resource_by_name,
    describe_agent_capabilities,
]


# ---------------------------------------------------------------------------
# Domain tool map
# ---------------------------------------------------------------------------

DOMAIN_TOOLS = {
    "compute": TOOLS_COMPUTE,
    "storage": TOOLS_STORAGE,
    "network": TOOLS_NETWORK,
    "database": TOOLS_DATABASE,
    "security": TOOLS_SECURITY,
    "ops": TOOLS_COST_OPS,
    "deployment": TOOLS_DEPLOYMENT,
}


# ---------------------------------------------------------------------------
# AgentCore app
# ---------------------------------------------------------------------------

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload: dict) -> dict:
    """
    Multi-agent entrypoint. Routes query to the correct specialist.

    Accepts:
        {
            "prompt": "list my EC2 instances",
            "domain": "auto" | "compute" | "storage" | ... ,
            "session_id": "customer-123",
            "tenant_id": "tenant-456",
            "history": [...]
        }

    Returns:
        {
            "response": "...",
            "domain": "compute",
            "session_id": "...",
            "tools_available": 52,
            "execution_time_ms": 1234.56
        }
    """
    start_time = time.time()

    prompt = payload.get("prompt", "")
    domain = payload.get("domain", "auto")
    session_id = payload.get("session_id", "default")
    tenant_id = payload.get("tenant_id", "default")
    history = payload.get("history", [])
    region = payload.get("region") or None
    context_notes = _build_context_notes(payload.get("artifact"), region)

    # Non-fatal telemetry
    try:
        tenant_manager.record_usage(tenant_id, "tool_executions")
    except Exception:
        pass

    try:
        if not tenant_manager.check_usage_limits(tenant_id, "execute_tool"):
            return {
                "response": "Tool execution limit exceeded. Please upgrade your plan.",
                "domain": domain,
                "session_id": session_id,
                "error": "RATE_LIMITED",
            }
    except Exception:
        pass

    # Determine which account(s) this request should act on. Zero/one account
    # behaves exactly like before; multiple accounts fan out.
    target_accounts = _extract_target_accounts(payload, prompt)

    try:
        if len(target_accounts) <= 1:
            # Single-account (or base-account) path.
            account_id = target_accounts[0] if target_accounts else None
            with use_account(account_id):
                result_text, final_domains, tool_count = _resolve_query(
                    prompt, domain, history, context_notes, bool(payload.get("artifact"))
                )

            duration_ms = (time.time() - start_time) * 1000
            domain_str = "+".join(final_domains)

            try:
                monitoring_manager.record_tool_execution(
                    tool_name="agent_invocation",
                    duration_ms=duration_ms,
                    success=True,
                    tenant_id=tenant_id,
                )
            except Exception:
                pass

            resp = {
                "response": result_text,
                "domain": domain_str,
                "session_id": session_id,
                "tenant_id": tenant_id,
                "execution_time_ms": round(duration_ms, 2),
                "tools_available": tool_count,
            }
            if account_id:
                resp["accounts"] = [account_id]
            return resp

        # --- Multi-account fan-out ---
        # Run the same query against every specified account, each under its own
        # assumed-role credentials, and aggregate the results into one report.
        per_account = []
        domains_seen = set()
        max_tools = 0
        for account_id in target_accounts:
            try:
                with use_account(account_id):
                    a_text, a_domains, a_tools = _resolve_query(
                        prompt, domain, history, context_notes, bool(payload.get("artifact"))
                    )
                domains_seen.update(a_domains)
                max_tools = max(max_tools, a_tools)
                per_account.append({
                    "account_id": account_id,
                    "response": a_text,
                    "domain": "+".join(a_domains),
                })
            except Exception as acc_err:  # one account failing shouldn't abort the rest
                per_account.append({
                    "account_id": account_id,
                    "response": f"Account error: {str(acc_err)}",
                    "domain": "unknown",
                    "error": type(acc_err).__name__,
                })

        combined = "\n\n".join(
            f"=== Account {r['account_id']} ===\n{r['response']}" for r in per_account
        )

        duration_ms = (time.time() - start_time) * 1000

        try:
            monitoring_manager.record_tool_execution(
                tool_name="agent_invocation",
                duration_ms=duration_ms,
                success=True,
                tenant_id=tenant_id,
            )
        except Exception:
            pass

        return {
            "response": combined,
            "domain": "+".join(sorted(domains_seen)) if domains_seen else "unknown",
            "session_id": session_id,
            "tenant_id": tenant_id,
            "execution_time_ms": round(duration_ms, 2),
            "tools_available": max_tools,
            "accounts": target_accounts,
            "per_account": per_account,
        }

    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000

        # Non-fatal monitoring
        try:
            monitoring_manager.record_tool_execution(
                tool_name="agent_invocation",
                duration_ms=duration_ms,
                success=False,
                tenant_id=tenant_id,
                error_type=type(e).__name__,
            )
        except Exception:
            pass

        return {
            "response": f"Agent error: {str(e)}",
            "domain": "unknown",
            "session_id": session_id,
            "error": type(e).__name__,
            "execution_time_ms": round(duration_ms, 2),
        }


_ACCOUNT_ID_RE = re.compile(r"\b(\d{12})\b")


def _extract_target_accounts(payload: dict, prompt: str) -> list:
    """
    Determine which AWS account(s) the request targets.

    Precedence:
      1. Explicit payload fields: "accounts" / "account_ids" (list) or
         "account_id" / "account" (single). If any of these keys is present the
         caller has chosen explicitly, and an empty value means the base account.
      2. 12-digit account numbers mentioned in the latest user message
         ("user_message", falling back to the prompt). Conversation history is
         not scanned, so ARNs echoed in earlier replies never trigger a fan-out.
      3. Empty list -> act on the runtime's own (base) account.

    Returns a de-duplicated, order-preserving list of 12-digit account id strings.
    """
    raw = []
    explicit = any(
        key in payload for key in ("accounts", "account_ids", "account_id", "account")
    )

    for key in ("accounts", "account_ids"):
        val = payload.get(key)
        if isinstance(val, (list, tuple)):
            raw.extend(str(a).strip() for a in val)
        elif isinstance(val, str):
            raw.extend(p.strip() for p in re.split(r"[,\s]+", val) if p.strip())

    for key in ("account_id", "account"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            raw.append(val.strip())

    # Fall back to account numbers named in the latest message itself.
    if not explicit:
        message = payload.get("user_message") or prompt or ""
        raw.extend(_ACCOUNT_ID_RE.findall(message))

    # Keep only valid 12-digit ids, de-duplicated, order preserved.
    seen = set()
    accounts = []
    for a in raw:
        if _ACCOUNT_ID_RE.fullmatch(a) and a not in seen:
            seen.add(a)
            accounts.append(a)
    return accounts


def _build_context_notes(artifact, region) -> list:
    """System-prompt notes describing the operator's upload and chosen region."""
    notes = []
    if region:
        notes.append(
            f"REGION CONTEXT: The operator selected region {region}. Pass region='{region}' "
            f"to every tool that takes a region unless the operator names another one."
        )
    if isinstance(artifact, dict) and artifact.get("uri"):
        lines = [
            "UPLOADED ARTIFACT: The operator uploaded a code zip for deployment.",
            f"- artifact_uri: {artifact['uri']}",
            f"- filename: {artifact.get('filename', 'unknown')}",
            f"- size_bytes: {artifact.get('size_bytes', 'unknown')}",
            f"- sha256: {artifact.get('sha256', 'unknown')}",
        ]
        if artifact.get("target"):
            lines.append(f"- requested target: {artifact['target']}")
        if artifact.get("options"):
            lines.append(f"- operator options: {json.dumps(artifact['options'])}")
        lines.append(
            "Use inspect_uploaded_artifact, then present a deployment plan and wait for "
            "confirmation before calling any deploy_artifact_* tool. Never invent a different URI."
        )
        notes.append("\n".join(lines))
    return notes


def _resolve_query(prompt: str, domain: str, history: list,
                   context_notes: list = None, has_artifact: bool = False):
    """
    Route the prompt to 1-2 domains, run the agent, and auto-retry with an
    expanded domain set if the agent signals it lacked the right tool.

    Runs under whatever account context is currently active (see aws_clients),
    so it works for both the base account and any assumed cross-account role.

    Returns (response_text, domains_used, tool_count).
    """
    # Route to correct domain(s) - supports cross-service queries (1-2 domains)
    if domain == "auto" or domain not in DOMAIN_TOOLS:
        domains = router.route(prompt, history)
    else:
        domains = [domain]

    # An attached upload always needs the deployment tools loaded.
    if has_artifact and "deployment" not in domains:
        domains = domains[:1] + ["deployment"]

    result_text, final_domains, tool_count = _run_agent_with_domains(
        prompt, domains, history, context_notes
    )

    # Auto-retry if agent indicates missing tools
    needs_retry = any(phrase in result_text.lower() for phrase in [
        "don't have a tool",
        "not available in the current tools",
        "cannot find a tool",
        "i don't have the capability",
        "not covered by my tools",
        "no appropriate tool",
        "escalate",
    ])

    if needs_retry and len(final_domains) < 3:
        tried = set(final_domains)
        remaining = [d for d in DOMAIN_TOOLS.keys() if d not in tried]
        if remaining:
            retry_prompt = (
                f"The query '{prompt}' could not be handled by {'+'.join(tried)} tools. "
                f"Which additional domain is needed? Options: {' '.join(remaining)}"
            )
            extra_domains = router.route(retry_prompt)
            new_domains_to_add = [d for d in extra_domains if d not in tried]
            if new_domains_to_add:
                expanded = final_domains + new_domains_to_add[:1]  # max 3 total
                result_text, final_domains, tool_count = _run_agent_with_domains(
                    prompt, expanded, history, context_notes
                )

    return result_text, final_domains, tool_count


def _run_agent_with_domains(prompt: str, domains: list, history: list = None,
                            context_notes: list = None):
    """
    Create and run an agent with merged tools from the specified domains.
    Returns (response_text, domains_used, tool_count).
    """
    # Merge tools from all selected domains (deduplicated)
    seen_ids = set()
    tools = []
    for d in domains:
        for tool_fn in DOMAIN_TOOLS.get(d, []):
            if id(tool_fn) not in seen_ids:
                seen_ids.add(id(tool_fn))
                tools.append(tool_fn)

    # Build system prompt - primary domain's prompt + cross-domain note
    primary_domain = domains[0]
    system_prompt = DOMAIN_PROMPTS[primary_domain]

    if len(domains) > 1:
        others = ", ".join(domains[1:])
        system_prompt += (
            f"\n\nCross-service context: This query involves multiple domains. "
            f"You have tools from {primary_domain} AND {others} loaded. "
            f"Use tools from any loaded domain as needed."
        )

    # Tell the agent which account it is operating on, so it reports accurately.
    active_account = get_active_account()
    if active_account:
        system_prompt += (
            f"\n\nACCOUNT CONTEXT: All tool calls in this turn run against AWS account "
            f"{active_account} (via an assumed cross-account role). State this account id "
            f"when you report results, and do not claim to act on any other account."
        )

    for note in context_notes or []:
        system_prompt += f"\n\n{note}"

    # Add universal safety rules
    system_prompt += (
        "\n\nIMPORTANT: If you truly cannot find a matching tool for what the user needs, "
        "respond with exactly: ESCALATE - [brief reason why]"
    )

    # Create and run agent
    agent = Agent(model=_AGENT_MODEL, system_prompt=system_prompt, tools=tools)
    response = agent(prompt)

    return str(response), domains, len(tools)


if __name__ == "__main__":
    app.run()
