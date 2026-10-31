# Requirements Document

## Introduction

This feature adds code deployment capabilities to the Autonomous CloudOps Command Center. Operators will be able to provide code (inline, as file references, or as archive content) through the Streamlit chat UI and instruct the agent to deploy it to various AWS compute targets — Lambda, EC2, ECS/ECR, App Runner, S3 static sites, and CloudFormation/SAM stacks. The agent will handle packaging, uploading, and deploying the code using the existing Strands SDK `@tool` pattern and the agent's PowerUserAccess runtime role.

## Glossary

- **Deployment_Agent**: The specialist agent within the CloudOps Command Center responsible for executing code deployment operations on AWS
- **Operator**: A user of the Streamlit Command Center chat UI who submits deployment requests
- **Deployment_Target**: An AWS service where code is deployed (Lambda, EC2, ECS, App Runner, S3, CloudFormation)
- **Code_Payload**: The source code, archive, or template content provided by the Operator for deployment
- **Deployment_Manifest**: A structured summary returned to the Operator describing what was deployed, where, and the resulting resource identifiers
- **Packaging_Engine**: The internal component that prepares code for deployment by zipping, building Docker images, or bundling assets
- **Rollback_Action**: An operation that reverts a deployment to the previous known-good state
- **Domain_Router**: The existing Nova Micro classifier that routes queries to specialist agent domains
- **Deployment_Tools_Module**: The new `src/tools/deployment_tools.py` file containing all deployment-related `@tool` functions

## Requirements

### Requirement 1: Lambda Function Deployment

**User Story:** As an Operator, I want to provide Python or Node.js code and deploy it as a Lambda function, so that I can quickly ship serverless workloads without leaving the chat interface.

#### Acceptance Criteria

1. WHEN the Operator provides code and specifies Lambda as the deployment target, THE Deployment_Agent SHALL package the code into a zip archive and deploy it to AWS Lambda
2. WHEN no function name is specified, THE Deployment_Agent SHALL generate a unique function name using the pattern `cloudops-deploy-{timestamp}`
3. WHEN the specified Lambda function already exists, THE Deployment_Agent SHALL update the function code and publish a new version
4. WHEN the specified Lambda function does not exist, THE Deployment_Agent SHALL create a new Lambda function with the provided code, a default execution role, 256MB memory, and 30-second timeout
5. WHEN deployment completes, THE Deployment_Agent SHALL return a Deployment_Manifest containing the function ARN, version, runtime, and code size
6. IF the code package exceeds 50MB (direct upload limit), THEN THE Deployment_Agent SHALL upload the package to S3 first and deploy from the S3 location
7. IF the Lambda deployment fails, THEN THE Deployment_Agent SHALL return the error message from AWS and suggest corrective actions

### Requirement 2: ECS/ECR Container Deployment

**User Story:** As an Operator, I want to provide a Dockerfile and application code and have the agent build and deploy it as a container to ECS, so that I can run containerized workloads through the chat interface.

#### Acceptance Criteria

1. WHEN the Operator provides a Dockerfile and code for ECS deployment, THE Deployment_Agent SHALL create an ECR repository if one does not exist, build the Docker image using AWS CodeBuild, push the image to ECR, and update the ECS service
2. WHEN the target ECS cluster or service does not exist, THE Deployment_Agent SHALL create a new Fargate service with the deployed image, default vCPU of 0.25, and 512MB memory
3. WHEN the target ECS service already exists, THE Deployment_Agent SHALL register a new task definition revision with the updated image URI and trigger a rolling deployment
4. WHEN the container deployment completes, THE Deployment_Agent SHALL return a Deployment_Manifest containing the ECR image URI, task definition ARN, service ARN, and desired count
5. IF the CodeBuild image build fails, THEN THE Deployment_Agent SHALL return the build logs and the specific failure reason

### Requirement 3: EC2 Code Deployment

**User Story:** As an Operator, I want to deploy application code to a running EC2 instance, so that I can update server-based workloads without SSH access.

#### Acceptance Criteria

1. WHEN the Operator provides code and specifies an EC2 instance as the deployment target, THE Deployment_Agent SHALL upload the code to S3 and execute an SSM Run Command on the target instance to pull and deploy the code
2. WHEN a deployment path is not specified, THE Deployment_Agent SHALL deploy to `/opt/cloudops/deployments/{timestamp}/`
3. WHEN the deployment completes, THE Deployment_Agent SHALL return a Deployment_Manifest containing the instance ID, S3 artifact location, deployment path, and SSM command ID
4. IF the target EC2 instance does not have the SSM agent running, THEN THE Deployment_Agent SHALL report that the instance is unreachable via SSM and suggest enabling the SSM agent
5. IF the SSM command execution fails, THEN THE Deployment_Agent SHALL return the command output, exit code, and error details

### Requirement 4: App Runner Deployment

**User Story:** As an Operator, I want to deploy container-based or source-based applications to App Runner, so that I can get a fully managed HTTPS endpoint without configuring load balancers.

#### Acceptance Criteria

1. WHEN the Operator provides code and specifies App Runner as the deployment target, THE Deployment_Agent SHALL build the container image via CodeBuild, push to ECR, and create or update an App Runner service
2. WHEN a source-based deployment is requested with a supported runtime (Python 3, Node.js 16+), THE Deployment_Agent SHALL configure an App Runner source-code deployment using the provided build and start commands
3. WHEN the App Runner deployment completes, THE Deployment_Agent SHALL return a Deployment_Manifest containing the service ARN, service URL, status, and deployed image or source configuration
4. IF the App Runner service creation fails, THEN THE Deployment_Agent SHALL return the failure reason and the CloudWatch log group for debugging

### Requirement 5: S3 Static Site Deployment

**User Story:** As an Operator, I want to deploy static website files (HTML, CSS, JS) to an S3 bucket with optional CloudFront distribution, so that I can host frontend applications through the chat interface.

#### Acceptance Criteria

1. WHEN the Operator provides static files and specifies S3 hosting as the deployment target, THE Deployment_Agent SHALL upload all files to the specified S3 bucket with appropriate content-type headers
2. WHEN the target S3 bucket does not exist, THE Deployment_Agent SHALL create the bucket with static website hosting enabled and a default index.html document configuration
3. WHEN the Operator requests CloudFront integration, THE Deployment_Agent SHALL create or update a CloudFront distribution pointing to the S3 bucket origin
4. WHEN the static site deployment completes, THE Deployment_Agent SHALL return a Deployment_Manifest containing the bucket name, website endpoint URL, and CloudFront domain if applicable
5. IF the S3 upload fails for any file, THEN THE Deployment_Agent SHALL report which files failed and which succeeded

### Requirement 6: CloudFormation and SAM Template Deployment

**User Story:** As an Operator, I want to deploy CloudFormation or SAM templates through the chat, so that I can provision full infrastructure stacks conversationally.

#### Acceptance Criteria

1. WHEN the Operator provides a CloudFormation or SAM template, THE Deployment_Agent SHALL validate the template syntax before deployment
2. WHEN the template is valid and the stack does not exist, THE Deployment_Agent SHALL create a new CloudFormation stack with the provided template and parameters
3. WHEN the template is valid and the stack already exists, THE Deployment_Agent SHALL create a change set, present the changes to the Operator for confirmation, and execute the change set upon approval
4. WHEN the stack deployment completes, THE Deployment_Agent SHALL return a Deployment_Manifest containing the stack name, stack ID, outputs, and resources created
5. IF the template validation fails, THEN THE Deployment_Agent SHALL return the validation errors with line references
6. IF the stack creation or update fails, THEN THE Deployment_Agent SHALL return the stack events showing the failure cause and offer to initiate a rollback

### Requirement 7: Deployment Safety and Confirmation

**User Story:** As an Operator, I want all destructive deployment actions to require explicit confirmation, so that accidental deployments are prevented.

#### Acceptance Criteria

1. THE Deployment_Agent SHALL present a deployment plan to the Operator before executing any deployment action
2. WHEN the Operator confirms the deployment plan, THE Deployment_Agent SHALL proceed with the deployment
3. WHEN the Operator rejects or modifies the deployment plan, THE Deployment_Agent SHALL revise the plan according to the Operator's feedback and present it again
4. THE Deployment_Agent SHALL include the target AWS account ID and region in every deployment plan
5. WHILE a deployment is in progress, THE Deployment_Agent SHALL stream status updates to the Operator through the chat interface

### Requirement 8: Deployment Rollback

**User Story:** As an Operator, I want to roll back a failed or unwanted deployment to the previous state, so that I can recover from bad deployments quickly.

#### Acceptance Criteria

1. WHEN the Operator requests a rollback for a Lambda deployment, THE Deployment_Agent SHALL revert the function code to the previous published version
2. WHEN the Operator requests a rollback for an ECS deployment, THE Deployment_Agent SHALL update the service to use the previous task definition revision
3. WHEN the Operator requests a rollback for a CloudFormation stack, THE Deployment_Agent SHALL initiate a stack rollback to the last successful state
4. WHEN a rollback completes, THE Deployment_Agent SHALL return a Deployment_Manifest confirming the rolled-back resource state
5. IF a rollback cannot be performed because no previous version exists, THEN THE Deployment_Agent SHALL inform the Operator and suggest alternative recovery actions

### Requirement 9: Domain Router Integration

**User Story:** As an Operator, I want deployment-related queries to be automatically routed to the deployment specialist, so that the agent understands deployment intent without explicit domain selection.

#### Acceptance Criteria

1. WHEN the Operator submits a query containing deployment-related intent (deploy, ship, push, release, launch code), THE Domain_Router SHALL classify the query as the `deployment` domain
2. WHEN a deployment query also references another domain's resources (deploy Lambda, deploy to EC2), THE Domain_Router SHALL return both `deployment` and the relevant second domain
3. THE Deployment_Tools_Module SHALL be registered in the AgentCore entrypoint following the same pattern as existing tool modules

### Requirement 10: Code Payload Handling

**User Story:** As an Operator, I want to provide code in multiple ways (pasted inline, as a file path reference, or as base64-encoded archive), so that I have flexibility in how I submit code for deployment.

#### Acceptance Criteria

1. WHEN the Operator pastes code inline in the chat message, THE Deployment_Agent SHALL extract the code content and use it as the Code_Payload
2. WHEN the Operator provides a base64-encoded zip archive in the chat, THE Deployment_Agent SHALL decode the archive and use the contents as the Code_Payload
3. WHEN the Operator references an S3 object path as the code source, THE Deployment_Agent SHALL retrieve the object from S3 and use it as the Code_Payload
4. THE Deployment_Agent SHALL detect the programming language or framework from the Code_Payload content when not explicitly specified by the Operator
5. IF the Code_Payload is empty or cannot be parsed, THEN THE Deployment_Agent SHALL inform the Operator and request a valid code submission
