# Implementation Plan: Code Deployment Capability

## Overview

Add a `deployment` domain to the Autonomous CloudOps Command Center enabling operators to deploy code to Lambda, ECS/ECR, EC2, App Runner, S3 static sites, and CloudFormation/SAM stacks through the chat interface. The implementation follows the existing `@tool` decorator pattern and integrates with the DomainRouter multi-agent architecture.

## Tasks

- [x] 1. Create deployment tools module with payload handling and utility functions
  - [x] 1.1 Create `src/tools/deployment_tools.py` with helper functions and payload handling tools
    - Implement `prepare_code_payload` tool (inline, base64, S3 source extraction)
    - Implement `detect_code_language` tool (pattern-based language detection)
    - Implement `create_deployment_plan` tool (structured plan generation for operator confirmation)
    - Implement internal helper: `_package_code_to_zip(code_content: str, filename: str) -> bytes`
    - Implement internal helper: `_generate_default_name() -> str` returning `cloudops-deploy-{timestamp}`
    - Implement internal helper: `_get_content_type(filename: str) -> str` for MIME type mapping
    - Implement internal helper: `_upload_to_s3(bucket: str, key: str, body: bytes) -> str`
    - Implement internal helper: `_build_manifest(target_type, status, resource_arn, region, details, error=None, rollback_info=None) -> str`
    - All tools use `@tool` decorator from Strands SDK, return JSON strings, handle errors gracefully
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 7.1, 7.4_

  - [ ]* 1.2 Write property tests for payload handling and utility functions
    - **Property 1: Code packaging produces valid zip archive**
    - **Property 2: Default resource names match expected patterns**
    - **Property 4: Content-type detection for static files**
    - **Property 9: Inline code payload passthrough**
    - **Property 10: Base64 payload round-trip**
    - **Property 11: Language detection from code markers**
    - **Validates: Requirements 1.1, 1.2, 5.1, 10.1, 10.2, 10.4**

- [x] 2. Implement Lambda deployment and rollback tools
  - [x] 2.1 Implement `deploy_lambda_function` tool in `src/tools/deployment_tools.py`
    - Package code into zip via `_package_code_to_zip`
    - If function exists: update code with `update_function_code`, publish version
    - If function does not exist: create with `create_function` (256MB, 30s timeout, default role)
    - If zip exceeds 50MB: upload to S3 first, deploy from S3 location
    - Return deployment manifest with function_arn, version, runtime, code_size
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7_

  - [x] 2.2 Implement `rollback_lambda_function` tool in `src/tools/deployment_tools.py`
    - List function versions, identify previous published version
    - Update function code to the previous version's code
    - If no previous version exists, return informative error with suggestions
    - Return deployment manifest confirming rolled-back state
    - _Requirements: 8.1, 8.4, 8.5_

  - [ ]* 2.3 Write unit tests for Lambda deployment tools
    - Test create new function with defaults
    - Test update existing function
    - Test S3 fallback for large packages
    - Test rollback to previous version
    - Test rollback with no previous version
    - Test error handling for deployment failures
    - _Requirements: 1.1, 1.3, 1.4, 1.6, 1.7, 8.1, 8.5_

- [x] 3. Implement ECS/ECR deployment and rollback tools
  - [x] 3.1 Implement `deploy_ecs_service` tool in `src/tools/deployment_tools.py`
    - Create ECR repository if it does not exist
    - Upload Dockerfile and app code to S3 staging
    - Trigger CodeBuild to build image and push to ECR
    - If service exists: register new task definition revision, update service with rolling deployment
    - If service does not exist: create Fargate service (0.25 vCPU, 512MB)
    - Return manifest with ecr_image_uri, task_definition_arn, service_arn, desired_count
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [x] 3.2 Implement `rollback_ecs_service` tool in `src/tools/deployment_tools.py`
    - List task definition revisions, identify previous revision
    - Update service to use previous task definition
    - Return manifest confirming rolled-back state
    - _Requirements: 8.2, 8.4, 8.5_

  - [ ]* 3.3 Write unit tests for ECS/ECR deployment tools
    - Test ECR repo creation when missing
    - Test update to existing service
    - Test CodeBuild failure handling
    - Test rollback to previous task definition
    - _Requirements: 2.1, 2.3, 2.5, 8.2_

- [ ] 4. Implement EC2 deployment tools
  - [x] 4.1 Implement `deploy_to_ec2` tool in `src/tools/deployment_tools.py`
    - Upload code to S3 staging bucket
    - Execute SSM Run Command to pull from S3 and deploy to target path
    - Default path: `/opt/cloudops/deployments/{timestamp}/`
    - Return manifest with instance_id, s3_artifact_location, deployment_path, ssm_command_id
    - _Requirements: 3.1, 3.2, 3.3, 3.5_

  - [x] 4.2 Implement `check_ec2_ssm_status` tool in `src/tools/deployment_tools.py`
    - Query SSM for instance information and ping status
    - Return online/offline status with actionable message
    - _Requirements: 3.4_

  - [ ]* 4.3 Write unit tests for EC2 deployment tools
    - Test successful deployment flow
    - Test SSM agent not available
    - Test SSM command failure
    - Test default path generation
    - _Requirements: 3.1, 3.2, 3.4, 3.5_

- [x] 5. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Implement App Runner deployment tools
  - [x] 6.1 Implement `deploy_apprunner_service` tool in `src/tools/deployment_tools.py`
    - Build container via CodeBuild, push to ECR
    - Create or update App Runner service with ECR image source
    - Return manifest with service_arn, service_url, status, image_uri
    - _Requirements: 4.1, 4.3, 4.4_

  - [x] 6.2 Implement `deploy_apprunner_source` tool in `src/tools/deployment_tools.py`
    - Configure source-based deployment with runtime, build_command, start_command
    - Support Python 3 and Node.js 16+ runtimes
    - Return manifest with service_arn, service_url, status, source config
    - _Requirements: 4.2, 4.3_

  - [ ]* 6.3 Write unit tests for App Runner deployment tools
    - Test container-based deployment
    - Test source-based deployment
    - Test service creation failure handling
    - _Requirements: 4.1, 4.2, 4.4_

- [x] 7. Implement S3 static site deployment tool
  - [x] 7.1 Implement `deploy_static_site` tool in `src/tools/deployment_tools.py`
    - Parse files_content (JSON map of filename→content)
    - Create bucket with website hosting if it does not exist
    - Upload all files with correct content-type headers via `_get_content_type`
    - Optionally create/update CloudFront distribution
    - Report partial failures (which files succeeded/failed)
    - Return manifest with bucket_name, website_endpoint, cloudfront_domain, files_uploaded
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [ ]* 7.2 Write property test for partial upload failure reporting
    - **Property 5: Partial upload failure reporting preserves file accounting**
    - **Validates: Requirements 5.5**

  - [ ]* 7.3 Write unit tests for S3 static site deployment
    - Test bucket creation with hosting config
    - Test file upload with correct content types
    - Test CloudFront distribution creation
    - Test partial failure reporting
    - _Requirements: 5.1, 5.2, 5.3, 5.5_

- [ ] 8. Implement CloudFormation/SAM deployment and rollback tools
  - [x] 8.1 Implement `validate_cfn_template` tool in `src/tools/deployment_tools.py`
    - Call CloudFormation validate_template API
    - Return validation result or error with details
    - _Requirements: 6.1, 6.5_

  - [x] 8.2 Implement `deploy_cfn_stack` tool in `src/tools/deployment_tools.py`
    - Validate template before deployment
    - If stack does not exist: create stack with parameters
    - If stack exists: create change set, present changes, execute on confirmation
    - Return manifest with stack_name, stack_id, outputs, resources_created
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.6_

  - [x] 8.3 Implement `rollback_cfn_stack` tool in `src/tools/deployment_tools.py`
    - Initiate stack rollback to last successful state
    - Return manifest confirming rollback status
    - _Requirements: 8.3, 8.4, 8.5_

  - [ ]* 8.4 Write unit tests for CloudFormation deployment tools
    - Test template validation success and failure
    - Test new stack creation
    - Test change set for existing stack
    - Test stack rollback
    - Test deployment failure with stack events
    - _Requirements: 6.1, 6.2, 6.3, 6.5, 6.6, 8.3_

- [x] 9. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 10. Integrate deployment domain into router and entrypoint
  - [x] 10.1 Update `src/agents/router.py` to add deployment domain
    - Add `"deployment"` to `VALID_DOMAINS` set
    - Update `CLASSIFIER_PROMPT` to include deployment keywords (deploy, ship, push, release, launch code, rollback, update function, deploy container, static site, CloudFormation stack deploy, SAM deploy)
    - _Requirements: 9.1, 9.2_

  - [x] 10.2 Update `src/agentcore_entrypoint.py` to register deployment tools
    - Add import block for all deployment tools from `src/tools/deployment_tools.py`
    - Define `TOOLS_DEPLOYMENT` list with all deployment tools plus `search_resource_by_name` and `describe_agent_capabilities`
    - Add `"deployment": TOOLS_DEPLOYMENT` to `DOMAIN_TOOLS` dict
    - Add deployment system prompt to `DOMAIN_PROMPTS` dict
    - _Requirements: 9.3_

  - [ ]* 10.3 Write property tests for domain router deployment classification
    - **Property 7: Domain router classifies deployment intent**
    - **Property 8: Domain router returns cross-domain pairs for combined queries**
    - **Validates: Requirements 9.1, 9.2**

  - [ ]* 10.4 Write unit tests for deployment integration
    - Test router classifies "deploy my Lambda" as deployment+compute
    - Test tool merging from deployment and compute domains
    - Test deployment system prompt is used
    - _Requirements: 9.1, 9.2, 9.3_

- [x] 11. Add deployment plan confirmation flow and manifest completeness
  - [x] 11.1 Enhance `create_deployment_plan` tool with full confirmation workflow
    - Ensure plan always contains account_id and region
    - Include target type, action (create/update/rollback), resource name, changes summary, estimated impact
    - Set `requires_confirmation: true` for all deployment actions
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

  - [ ]* 11.2 Write property test for deployment plan completeness
    - **Property 6: Deployment plan always includes account and region**
    - **Validates: Requirements 7.4**

  - [ ]* 11.3 Write property test for manifest completeness
    - **Property 3: Deployment manifest completeness by target type**
    - **Validates: Requirements 1.5, 2.4, 3.3, 4.3, 5.4, 6.4, 8.4**

- [x] 12. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- All AWS calls in tests should be mocked via `unittest.mock.patch` or `moto`
- The Hypothesis library is used for property-based testing (compatible with pytest)
- All deployment tools follow the existing `@tool` decorator pattern from Strands SDK

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "2.1", "4.1", "4.2"] },
    { "id": 2, "tasks": ["2.2", "2.3", "3.1", "4.3", "6.1", "6.2", "7.1", "8.1"] },
    { "id": 3, "tasks": ["3.2", "3.3", "6.3", "7.2", "7.3", "8.2"] },
    { "id": 4, "tasks": ["8.3", "8.4"] },
    { "id": 5, "tasks": ["10.1", "10.2", "11.1"] },
    { "id": 6, "tasks": ["10.3", "10.4", "11.2", "11.3"] }
  ]
}
```
