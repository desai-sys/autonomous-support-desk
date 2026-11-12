"""
CloudOps Action Agent — AgentCore runtime entrypoint.

Two request modes:

  chat     Route the prompt to 1-2 tool domains (DomainRouter, Nova Micro), run
           a Strands agent (Nova Pro) over that focused tool set in each target
           account, and return its answer. Mutating tool calls never run here:
           the ApprovalGate queues them as pending actions, returned in
           ``pending_actions`` for a person to approve in the console.
  execute  Run approved pending actions exactly as recorded (no LLM involved).

Deployed by infra/app.yaml (AWS::BedrockAgentCore::Runtime, code from S3)
through agent_main.py.
"""

import json
import re
import time

import structlog
from bedrock_agentcore import BedrockAgentCoreApp
from strands import Agent
from strands.models import BedrockModel

import config
from src.agents.router import DomainRouter
from src.core.account_scope import AccountDirectory, scope_tools
from src.core.accounts import AccountRegistry
from src.core.approvals import ActionStore, ApprovalGate, execute_actions, is_mutating, describe_results
from src.core import conversations, identity, tenancy
from src.core.conversations import ConversationStore, ToolRecorder
from src.core.aws_clients import base_account_id, boto3, use_account
from src.core import request_context
from src.core.monitoring import configure_logging, metrics
from src.core.security import Permission, ROLE_PERMISSIONS, UserRole, security_manager
from src.core.usage import usage_meter

configure_logging("agent")
logger = structlog.get_logger()

_AGENT_MODEL = BedrockModel(model_id=config.AGENT_MODEL_ID, max_tokens=config.AGENT_MAX_TOKENS)
_BUILD_MODEL = (BedrockModel(model_id=config.BUILD_MODEL_ID, max_tokens=config.AGENT_MAX_TOKENS)
                if config.BUILD_MODEL_ID else _AGENT_MODEL)

# ---------------------------------------------------------------------------
# Tools
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

from src.tools.provisioning_tools import launch_ec2_instance
from src.tools.network_workflow_tools import create_vpc_peering
from src.tools.app_builder_tools import (
    delete_generated_app, deploy_generated_app, get_generated_app, prepare_app_bundle,
    prepare_app_file, prepare_app_template,
)
from src.tools.aws_api_tools import (
    call_aws_api, describe_aws_operation, list_aws_operations, query_aws_api,
)

from src.tools.artifact_tools import (
    inspect_uploaded_artifact,
    deploy_artifact_to_lambda,
    deploy_artifact_to_ec2, launch_ec2_for_artifact,
    deploy_artifact_to_ecs,
    deploy_artifact_to_static_site,
)



# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

COMMON_RULES = (
    "RULES:\n"
    "- You CAN act on AWS: never tell the operator to do it themselves in the console. If a "
    "purpose-built tool fits, use it; otherwise use query_aws_api (read-only operations) or "
    "call_aws_api (any change) with the exact boto3 service, operation and parameters. Your "
    "memory of AWS APIs may be out of date: unless you are certain, first find the operation with "
    "list_aws_operations and read its current parameters with describe_aws_operation.\n"
    "- If a change needs details the operator hasn't given, ask for them in ONE short message "
    "and propose sensible defaults (e.g. instance type t3.micro, Amazon Linux 2023, default VPC), "
    "so they can just reply 'yes' or adjust. Look up what you can yourself (VPCs, subnets, key "
    "pairs, existing resources) instead of asking.\n"
    "- Investigate with read-only tools first and base every statement on tool output. "
    "If you search for a resource and cannot find it, say so; never substitute a different "
    "resource or invent identifiers.\n"
    "- Changes are approval-gated by the platform: when a change is needed, call the tool "
    "with the exact parameters. The call is queued (you will see 'NOT EXECUTED YET ... "
    "queued as action <id>'). Then explain precisely what each queued action will do, its "
    "risk and how to roll it back. Never say a change was made unless a tool result shows it.\n"
    "- Only queue changes the operator asked for or clearly needs; one action per change.\n"
    "- When a later change needs an id produced by an earlier one (e.g. a new VPC's id), queue only "
    "the earlier step now and propose the next one after it has run; never guess ids.\n"
    "- Give new resources a Name tag (ask for names or propose sensible ones), and report the names "
    "and ids from the tool results once they exist.\n"
    "- For VPCs that may be connected, pick non-overlapping CIDRs: check existing VPC ranges in the "
    "accounts involved first. Use create_vpc_peering for any VPC peering (it requests, accepts and "
    "adds routes, across accounts, in one action).\n"
    "- If a tool reports errors, unchecked items or an incomplete result, say so plainly and name "
    "what could not be checked; never present a partial result as complete.\n"
    "- Always state the AWS account and region you inspected or will change.\n"
    "- Respond in plain English with short bullet points, not raw JSON.\n"
    "- Chain multiple tools when needed for full context."
)

DOMAIN_INTROS = {
    "compute": (
        "You are an AWS Compute specialist. You manage EC2, Lambda, ECS, EKS, Batch, SageMaker, "
        "App Runner, Lightsail, and CloudWatch metrics/alarms/logs. Use list_ec2_instances to "
        "find instances first and analyze before proposing changes. To create instances use "
        "launch_ec2_instance (defaults: t3.micro, Amazon Linux 2023, default VPC, SSM-managed)."
    ),
    "storage": (
        "You are an AWS Storage specialist. You manage S3 buckets, EBS volumes, EFS, ECR, Backup, "
        "DataSync, and Transfer Family. For S3 use list_s3_buckets first; for security checks use "
        "list_public_s3_buckets. Check versioning and encryption when analyzing buckets."
    ),
    "network": (
        "You are an AWS Network specialist. You manage VPCs, security groups, load balancers, "
        "Route53, CloudFront, API Gateway, SNS, SQS, Kinesis, and MSK. For security groups use "
        "list_security_groups then analyze_security_group; for DNS use list_hosted_zones first. "
        "Flag overly permissive rules (0.0.0.0/0)."
    ),
    "database": (
        "You are an AWS Database specialist. You manage RDS, DynamoDB, ElastiCache, DocumentDB, "
        "Redshift, Neptune, OpenSearch, Glue, Athena, Timestream, QLDB, and DMS. Use list_ tools "
        "first; mention backup status and storage encryption when analyzing databases."
    ),
    "security": (
        "You are an AWS Security specialist. You manage IAM, GuardDuty, WAF, KMS, Secrets Manager, "
        "CloudTrail, Inspector, Macie, Config, Shield, and Access Analyzer. Focus on findings and "
        "compliance; for IAM check MFA and unused credentials; highlight critical findings."
    ),
    "ops": (
        "You are an AWS Operations specialist. You manage costs, budgets, CloudFormation, "
        "CodePipeline, SSM, Step Functions, CloudWatch, health events, Trusted Advisor, and "
        "service quotas. For costs use get_cost_and_usage and break down by service and period."
    ),
    "deployment": (
        "You are an AWS Deployment specialist. You deploy code to Lambda, ECS/ECR, EC2 (via SSM), "
        "App Runner, S3 static sites, and CloudFormation/SAM stacks.\n"
        "WORKFLOW: prepare/inspect the code (prepare_code_payload, detect_code_language, or "
        "inspect_uploaded_artifact for an uploaded zip), present the plan with "
        "create_deployment_plan, then call the deploy tool so it is queued for approval. For "
        "updates say what will change; for rollbacks say which version will be restored.\n"
        "UPLOADED ZIPS: use the deploy_artifact_* tools with the given artifact_uri. If the "
        "operator wants EC2 but has no instance, queue launch_ec2_for_artifact first and tell "
        "them to approve it, then ask them to request the deployment once the instance is "
        "Online in SSM (check_ec2_ssm_status)."
    ),
    "build": (
        "You are an AWS application builder. From the operator's description you write a complete, "
        "working application (code + CloudFormation) and deploy it after their approval.\n"
        "WORKFLOW:\n"
        "1. If essential requirements are unclear (what it does, public or private, data to store), ask "
        "ONE short question with proposed defaults; otherwise just build it.\n"
        "2. Write the app ONE FILE PER CALL with prepare_app_file (path + full content), set the "
        "CloudFormation with prepare_app_template, then call prepare_app_bundle. Never put several "
        "files in one call. Fix any validation errors it returns and call it again.\n"
        "3. Call deploy_generated_app with the returned app_id and version. It is queued: tell the operator "
        "what will be created, the estimated cost profile, and that they can review every file on the "
        "approval card.\n"
        "4. To change an existing app, call get_generated_app, rewrite only the changed files with "
        "prepare_app_file, then prepare_app_bundle and deploy the new version.\n"
        "CONVENTIONS:\n"
        "- Prefer serverless: Lambda (python3.12 or nodejs20.x), API Gateway HTTP API or a Lambda function "
        "URL, DynamoDB (PAY_PER_REQUEST), S3, SQS, EventBridge schedules.\n"
        "- Code goes under src/ and may only use the standard library and the AWS SDK (boto3 / "
        "@aws-sdk v3) because no dependencies are installed. src/app.py -> Handler: app.handler.\n"
        "- The template must declare `ArtifactBucket` and `ArtifactKey` String parameters and use "
        "`Code: {S3Bucket: !Ref ArtifactBucket, S3Key: !Ref ArtifactKey}` (or SAM `CodeUri: {Bucket: "
        "!Ref ArtifactBucket, Key: !Ref ArtifactKey}`).\n"
        "- Give the function its own execution role with only the permissions it needs (no '*' "
        "actions, no admin policies, no IAM users). Pass table/bucket names via environment variables.\n"
        "- Output every URL (e.g. ApiUrl) so it is smoke-tested after deployment; make GET / return "
        "a useful response.\n"
        "- Web pages, games and other browser UIs: one Lambda whose GET / returns a single "
        "self-contained HTML page (inline CSS and JavaScript, Content-Type text/html; all game/UI "
        "logic runs in the browser), exposed with AWS::Lambda::Url (AuthType NONE, which makes it "
        "public — say so in the plan) plus the matching AWS::Lambda::Permission "
        "(lambda:InvokeFunctionUrl, Principal '*', FunctionUrlAuthType NONE). Output the URL as AppUrl.\n"
        "- Include a README.md explaining endpoints and usage. Keep apps small and readable."
    ),
}

DOMAIN_PROMPTS = {d: f"{intro}\n\n{COMMON_RULES}" for d, intro in DOMAIN_INTROS.items()}

# Nova models sometimes emit their private reasoning; never show it to operators.
_THINKING_RE = re.compile(r"<thinking>.*?(</thinking>|$)\s*", re.DOTALL | re.IGNORECASE)


# ---------------------------------------------------------------------------
# Domain tool groups (~30-60 tools each)
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


# Every domain can fall back to the generic AWS API tools; EC2 launches are
# useful wherever compute or deployments come up.
TOOLS_COMPUTE.append(launch_ec2_instance)
TOOLS_NETWORK.append(create_vpc_peering)
TOOLS_DEPLOYMENT.append(launch_ec2_instance)
for _group in (TOOLS_COMPUTE, TOOLS_STORAGE, TOOLS_NETWORK, TOOLS_DATABASE,
               TOOLS_SECURITY, TOOLS_COST_OPS, TOOLS_DEPLOYMENT):
    _group.extend([list_aws_operations, describe_aws_operation, query_aws_api, call_aws_api])


TOOLS_BUILD = [
    prepare_app_file, prepare_app_template, prepare_app_bundle, get_generated_app,
    deploy_generated_app, delete_generated_app,
    validate_cfn_template, list_aws_operations, describe_aws_operation, query_aws_api, call_aws_api,
    search_resource_by_name, describe_agent_capabilities,
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
    "build": TOOLS_BUILD,
}

# Every tool by name, for executing approved actions.
TOOL_REGISTRY = {t.tool_name: t for group in DOMAIN_TOOLS.values() for t in group}

router = DomainRouter(region=config.AWS_REGION)
action_store = ActionStore()
conversation_store = ConversationStore()
account_registry = AccountRegistry()

_ACCOUNT_ID_RE = re.compile(r"\b(\d{12})\b")
# Viewers get the purpose-built read tools only, not open-ended API access.
_NOT_FOR_VIEWERS = frozenset({"query_aws_api"})
_MISSING_TOOL_PHRASES = (
    "don't have a tool", "not available in the current tools", "cannot find a tool",
    "i don't have the capability", "not covered by my tools", "no appropriate tool", "escalate",
)


# ---------------------------------------------------------------------------
# AgentCore app
# ---------------------------------------------------------------------------

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload: dict) -> dict:
    """
    Request:
        {
          "mode": "chat" | "execute",
          "session_id": "<conversation id>", "tenant_id": "...",
          "user": {"id": "...", "email": "...", "role": "viewer|operator|sre|admin"},
          # chat
          "prompt": "<history + message>", "user_message": "<latest message>",
          "history": [...], "account_ids": [...], "region": "...", "artifact": {...},
          # execute
          "action_ids": [...]
        }
    """
    try:
        identity.verify(payload)
    except identity.IdentityError as e:
        logger.warning("request_rejected", reason=str(e))
        return {"response": f"Request rejected: {e}", "error": "Unauthorized",
                "session_id": payload.get("session_id", "")}
    caller = _caller(payload)
    structlog.contextvars.bind_contextvars(
        tenant_id=caller["tenant_id"], user_id=caller["user_id"],
        session_id=payload.get("session_id", ""),
    )
    start = time.time()
    mode = payload.get("mode", "chat")
    try:
        with request_context.bind(caller["tenant_id"], caller["user_id"],
                                  payload.get("session_id", "")):
            result = _dispatch(mode, payload, caller)
        ok = "error" not in result
    except Exception as e:
        logger.exception("invoke_failed")
        result, ok = {"response": f"Agent error: {e}", "error": type(e).__name__}, False
    duration_ms = (time.time() - start) * 1000
    metrics.request(f"agent-{mode}", duration_ms, ok)
    result["execution_time_ms"] = round(duration_ms, 2)
    result["session_id"] = payload.get("session_id", "")
    return result


def _dispatch(mode: str, payload: dict, caller: dict) -> dict:
    # The planner runtime (model, read-only credentials) never executes changes and
    # the executor runtime (write credentials) never runs a model.
    allowed = {"planner": ("chat", "check_account"), "executor": ("execute", "check_account")}.get(
        config.RUNTIME_MODE, ("chat", "execute", "check_account"))
    if mode in ("chat", "execute", "check_account") and mode not in allowed:
        return {"response": f"This runtime ({config.RUNTIME_MODE}) does not handle '{mode}' requests.",
                "error": "WrongRuntime"}
    if mode == "execute":
        return _execute(payload, caller)
    if mode == "check_account":
        return _check_account(payload, caller)
    if mode == "chat":
        return _chat(payload, caller)
    return {"response": f"Unknown mode '{mode}'.", "error": "BadRequest"}


def _caller(payload: dict) -> dict:
    user = payload.get("user") or {}
    try:
        role = UserRole(user.get("role", "viewer"))
    except ValueError:
        role = UserRole.VIEWER
    return {
        "user_id": str(user.get("id") or user.get("email") or "unknown"),
        "role": role,
        "tenant_id": str(payload.get("tenant_id") or config.DEFAULT_TENANT_ID),
    }


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

def _chat(payload: dict, caller: dict) -> dict:
    prompt = payload.get("prompt", "")
    history = payload.get("history", [])
    session_id = payload.get("session_id") or "default"
    region = payload.get("region") or None
    artifact = payload.get("artifact") if isinstance(payload.get("artifact"), dict) else None
    can_propose = Permission.PROPOSE_CHANGES in ROLE_PERMISSIONS[caller["role"]]

    if not prompt.strip():
        return {"response": "Empty prompt.", "error": "BadRequest"}
    if not usage_meter.allow_request(caller["tenant_id"]):
        return {"response": "Your organisation has reached today's request limit.",
                "error": "RateLimited"}

    # One agent run works across accounts: every tool takes an `account` argument,
    # resolved against the registered accounts (see src/core/account_scope.py).
    registered = {a: info.get("name") or a
                  for a, info in account_registry.list(caller["tenant_id"]).items()}
    selected, refused = _selected_accounts(payload, set(registered))
    directory = AccountDirectory(base_account_id(), registered,
                                 default=selected[0] if len(selected) == 1 else None, region=region,
                                 platform_allowed=tenancy.platform_allowed(caller["tenant_id"]))
    refused += [a for a in directory.unregistered_ids_in(payload.get("user_message") or prompt)
                if a not in refused]

    notes = _build_context_notes(artifact, region, read_only=not can_propose)
    notes.append(directory.prompt_note())
    if len(selected) > 1:
        notes.append("SELECTED ACCOUNTS: the operator selected "
                     + ", ".join(directory.label(a) for a in selected)
                     + ". Unless they say otherwise, do the request in each of them and report per account.")
    if refused:
        notes.append("UNREGISTERED ACCOUNTS: " + ", ".join(refused) + " are not registered, so you "
                     "cannot act there. Tell the operator an admin must deploy the cross-account role "
                     "there and register it on the Accounts page.")

    gate = ApprovalGate(action_store, tenant_id=caller["tenant_id"], session_id=session_id,
                        requested_by=caller["user_id"], account_id=None, region=region,
                        directory=directory)
    # Earlier turns come from the platform's own records (with tool results and what
    # approved actions returned), not from the text the browser sent, when available.
    user_message = payload.get("user_message") or prompt
    records = conversation_store.recent(caller["tenant_id"], payload.get("session_id", ""))
    remembered = conversations.render(records)
    if remembered:
        prompt = f"{remembered}\n\nUser: {user_message}"
        history = conversations.history_lines(records)
    recorder = ToolRecorder()
    gate.companions = [recorder]            # hooks that run alongside the gate in this turn
    domains, tools = ["unknown"], 0
    try:
        response, domains, tools = _resolve_query(
            prompt, payload.get("domain", "auto"), history, notes, gate,
            has_artifact=bool(artifact), read_only=not can_propose, directory=directory,
            route_text=user_message if remembered else None,
        )
    except Exception as e:
        logger.exception("chat_run_failed")
        response = (f"The request failed: {e}\n\nNothing was changed. Try again, or split the "
                    f"request into smaller steps.")

    conversation_store.record_turn(caller["tenant_id"], payload.get("session_id", ""), user_message,
                                   response, recorder.calls, gate.queued)
    for action in gate.queued:
        security_manager.log_audit_event(
            caller["user_id"], caller["tenant_id"], "action_proposed", action["summary"], "PENDING",
            details={"action_id": action["action_id"], "account_id": action["account_id"],
                     "region": action["region"]},
        )

    return {
        "response": response,
        "domain": "+".join(domains),
        "tools_available": tools,
        "accounts": [label for label in directory.used.values()],
        "refused_accounts": refused,
        "pending_actions": gate.queued,
        "escalate": "ESCALATE" in response.upper() and not gate.queued,
        "usage": request_context.usage(),
    }


def _selected_accounts(payload: dict, registered: set):
    """
    Accounts the operator picked as targets: (registered ids, unregistered ids).

    Reads the "account_ids"/"accounts"/"account_id"/"account" payload keys. The
    platform account (or an empty selection) means no explicit target. Accounts
    named in the message itself are handled by the agent through each tool's
    `account` argument; conversation history is never scanned.
    """
    raw = []
    for key in ("accounts", "account_ids"):
        val = payload.get(key)
        if isinstance(val, (list, tuple)):
            raw.extend(str(a).strip() for a in val)
        elif isinstance(val, str):
            raw.extend(p for p in re.split(r"[,\s]+", val) if p)
    for key in ("account_id", "account"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            raw.append(val.strip())

    base = base_account_id()
    selected, refused = [], []
    for account_id in dict.fromkeys(a for a in raw if _ACCOUNT_ID_RE.fullmatch(a)):
        if account_id == base:
            continue
        (selected if account_id in registered else refused).append(account_id)
    return selected, refused


def _build_context_notes(artifact, region, read_only: bool = False) -> list:
    """System-prompt notes describing the operator's upload, region and permissions."""
    notes = []
    if read_only:
        notes.append(
            "READ-ONLY SESSION: The operator's role cannot change resources and no mutating "
            "tools are loaded. Investigate and explain; if they ask for a change, describe what "
            "would be needed and say that an operator, SRE or admin must request it."
        )
    if region:
        notes.append(
            f"REGION CONTEXT: The operator selected region {region}. Pass region='{region}' "
            f"to every tool that takes a region unless the operator names another one."
        )
    if artifact and artifact.get("uri"):
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
            "Call inspect_uploaded_artifact first, then present the plan and call the matching "
            "deploy_artifact_* tool so it is queued for approval. Never use a different URI."
        )
        notes.append("\n".join(lines))
    return notes


def _resolve_query(prompt: str, domain: str, history: list, context_notes: list,
                   gate: ApprovalGate, has_artifact: bool = False, read_only: bool = False,
                   directory: AccountDirectory = None, route_text: str = None):
    """
    Route the prompt to 1-2 domains, run the agent, and retry once with an extra
    domain if the agent signals it lacked the right tool.

    Returns (response_text, domains_used, tool_count).
    """
    if domain == "auto" or domain not in DOMAIN_TOOLS:
        domains = router.route(route_text or prompt, history)
    else:
        domains = [domain]

    # An attached upload always needs the deployment tools loaded.
    if has_artifact and "deployment" not in domains:
        domains = domains[:1] + ["deployment"]

    text, final_domains, tool_count = _run_agent_with_domains(
        prompt, domains, context_notes, gate, read_only, directory
    )

    needs_retry = any(p in text.lower() for p in _MISSING_TOOL_PHRASES)
    if needs_retry and not gate.queued and len(final_domains) < 3:
        tried = set(final_domains)
        remaining = [d for d in DOMAIN_TOOLS if d not in tried]
        retry_prompt = (
            f"The query '{prompt[-1500:]}' could not be handled by {'+'.join(tried)} tools. "
            f"Which additional domain is needed? Options: {' '.join(remaining)}"
        )
        extra = [d for d in router.route(retry_prompt) if d not in tried]
        if extra:
            text, final_domains, tool_count = _run_agent_with_domains(
                prompt, final_domains + extra[:1], context_notes, gate, read_only, directory
            )
    return text, final_domains, tool_count


def _run_agent_with_domains(prompt: str, domains: list, context_notes: list,
                            gate: ApprovalGate, read_only: bool = False,
                            directory: AccountDirectory = None):
    """Run one agent over the merged tools of ``domains``. Returns (text, domains, tool_count)."""
    seen, tools = set(), []
    for d in domains:
        for tool_fn in DOMAIN_TOOLS.get(d, []):
            if id(tool_fn) in seen or (read_only and (is_mutating(tool_fn.tool_name)
                                                      or tool_fn.tool_name in _NOT_FOR_VIEWERS)):
                continue
            seen.add(id(tool_fn))
            tools.append(tool_fn)

    system_prompt = DOMAIN_PROMPTS[domains[0]]
    if len(domains) > 1:
        system_prompt += (
            f"\n\nCross-service context: tools from {', '.join(domains)} are loaded. "
            f"Use tools from any loaded domain as needed."
        )

    for note in context_notes:
        system_prompt += f"\n\n{note}"
    system_prompt += (
        "\n\nIf you truly cannot find a matching tool for what the operator needs, respond with "
        "exactly: ESCALATE - [brief reason why]"
    )

    if directory is not None:
        tools = scope_tools(tools, directory)
    model = _BUILD_MODEL if "build" in domains else _AGENT_MODEL
    hooks = [h for h in [gate, *getattr(gate, "companions", [])] if h is not None]
    try:
        result = Agent(model=model, system_prompt=system_prompt, tools=tools,
                       hooks=hooks, callback_handler=None)(prompt)
    except Exception as e:
        # Models occasionally emit a malformed tool call when its input is large. Retry once
        # with a fresh agent and a nudge towards smaller calls.
        if "ToolUse" not in str(e) and "invalid sequence" not in str(e):
            raise
        logger.warning("tool_use_stream_error_retry", error=str(e))
        result = Agent(model=model, tools=tools, hooks=hooks, callback_handler=None,
                       system_prompt=system_prompt + (
                           "\n\nYour previous attempt failed because a tool call was malformed. "
                           "Keep every tool call small: one file per call, plain text content."))(prompt)
    try:
        tokens = result.metrics.accumulated_usage
        request_context.record_usage(tokens.get("inputTokens", 0), tokens.get("outputTokens", 0))
        metrics.put("ModelInputTokens", tokens.get("inputTokens", 0), "Count", {"Model": model.config["model_id"]})
        metrics.put("ModelOutputTokens", tokens.get("outputTokens", 0), "Count", {"Model": model.config["model_id"]})
    except Exception:  # usage is best-effort telemetry
        pass
    text = _THINKING_RE.sub("", str(result)).strip()
    return text, domains, len(tools)


# ---------------------------------------------------------------------------
# Execute approved actions
# ---------------------------------------------------------------------------

def _execute(payload: dict, caller: dict) -> dict:
    action_ids = [str(a) for a in payload.get("action_ids") or []][:20]
    if not action_ids:
        return {"response": "No actions given.", "error": "BadRequest"}

    def audit(**kwargs):
        security_manager.log_audit_event(caller["user_id"], caller["tenant_id"], **kwargs)

    # The account must still be this tenant's when the change runs, not only when it was proposed.
    registered = set(account_registry.list(caller["tenant_id"]))

    def account_allowed(account_id) -> bool:
        return account_id in registered if account_id else tenancy.platform_allowed(caller["tenant_id"])

    results = execute_actions(
        action_ids, tenant_id=caller["tenant_id"], approver=caller["user_id"],
        approver_role=caller["role"].value, tools=TOOL_REGISTRY, store=action_store, audit=audit,
        stop_on_failure=bool(payload.get("stop_on_failure")), account_allowed=account_allowed,
    )
    for r in results:
        metrics.action(r["status"])
    conversation_store.record_execution(caller["tenant_id"], payload.get("session_id", ""),
                                        caller["user_id"], results)
    return {"response": describe_results(results, lambda a: _account_name(a, caller["tenant_id"])),
            "results": results}


def _check_account(payload: dict, caller: dict) -> dict:
    """Admin check that the cross-account role in an account can be assumed."""
    if Permission.MANAGE_ACCOUNTS not in ROLE_PERMISSIONS[caller["role"]]:
        return {"ok": False, "response": "Only admins can test accounts.", "error": "Forbidden"}
    account_id = str(payload.get("account_id", ""))
    if not _ACCOUNT_ID_RE.fullmatch(account_id):
        return {"ok": False, "response": "Invalid account id.", "error": "BadRequest"}
    if not account_registry.is_allowed(account_id, caller["tenant_id"]):
        return {"ok": False, "response": "Register the account before testing it.", "error": "BadRequest"}
    try:
        with use_account(account_id):
            arn = boto3.client("sts").get_caller_identity()["Arn"]
        return {"ok": True, "response": f"Connected to {account_id} as {arn}."}
    except Exception as e:
        return {"ok": False, "response": f"Cannot assume {config.CROSS_ACCOUNT_ROLE_NAME} in "
                                         f"{account_id}: {e}"}


def _account_name(account_id, tenant_id=None) -> str:
    if not account_id:
        return "the platform account"
    try:
        name = account_registry.name_of(account_id, tenant_id)
    except Exception:
        name = None
    return f"{name} ({account_id})" if name and name != account_id else account_id


if __name__ == "__main__":
    app.run()
