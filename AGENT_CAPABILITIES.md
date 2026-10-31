# CloudOps Agent — Full Capabilities Reference

Example requests for the Command Center (your console URL from `scripts/deploy.py`).

Read-only requests (list, analyze, get, describe, ...) run immediately. Requests that change
something (start, stop, modify, create, delete, deploy, rollback, ...) are investigated and then
queued as **changes awaiting approval**: each shows the exact tool call, account and region, and
runs only when an operator, SRE or admin clicks **Approve**. Viewers get read-only answers.

If details are missing (for example the instance type for a new EC2 instance), the agent asks
once and proposes defaults. Anything without a dedicated tool is still possible: the agent uses
`query_aws_api` for read-only AWS operations and `call_aws_api` for any change (approval-gated).

| Action | Sample Query |
|--------|-------------|
| Launch EC2 instances | "create an EC2 instance" · "launch 2 t3.small Ubuntu 24.04 servers named api in subnet-0abc" |
| Anything else | "create a VPC 10.20.0.0/16 with a tag env=dev" · "list my Route 53 health checks" |

---

## Compute (~62 tools)

| Action | Sample Query |
|--------|-------------|
| List EC2 instances | "list my EC2 instances" |
| Analyze EC2 instance | "analyze EC2 instance i-0abc123" |
| Start EC2 instance | "start instance i-0abc123" |
| Stop EC2 instance | "stop instance i-0abc123" |
| Reboot EC2 instance | "reboot instance i-0abc123" |
| Modify instance type | "change i-0abc123 to t3.large" |
| Create EC2 snapshot | "create a snapshot of i-0abc123" |
| Enable detailed monitoring | "enable monitoring on i-0abc123" |
| Get EC2 metrics | "show CPU metrics for i-0abc123" |
| List Lambda functions | "list my Lambda functions" |
| Analyze Lambda function | "analyze Lambda function my-func" |
| Update Lambda memory | "increase my-func memory to 512MB" |
| Update Lambda timeout | "set my-func timeout to 30 seconds" |
| Invoke Lambda function | "invoke Lambda my-func with payload {}" |
| Analyze Auto Scaling group | "analyze ASG my-asg" |
| Create scaling policy | "create a scaling policy for my-asg" |
| Analyze ECS cluster | "analyze ECS cluster my-cluster" |
| Scale ECS service | "scale my-service to 3 tasks" |
| Restart ECS service | "restart ECS service my-service" |
| List ECS task definitions | "list ECS task definitions" |
| Deregister task definition | "deregister task definition my-task:3" |
| List EKS clusters | "list my EKS clusters" |
| Analyze EKS cluster | "analyze EKS cluster my-cluster" |
| Update EKS nodegroup | "update nodegroup scaling in my-cluster" |
| List Batch job queues | "list Batch job queues" |
| Analyze Batch job queue | "analyze Batch queue my-queue" |
| Submit Batch job | "submit a Batch job to my-queue" |
| Cancel Batch job | "cancel Batch job abc-123" |
| List CloudWatch alarms | "list my CloudWatch alarms" |
| Get CloudWatch metrics | "get CPU metrics for my instance" |
| List log groups | "list CloudWatch log groups" |
| Get log events | "show recent logs from /aws/lambda/my-func" |
| Run SSM command | "run 'uptime' on instance i-0abc123 via SSM" |
| Get SSM command output | "get output of SSM command abc-123" |
| List App Runner services | "list my App Runner services" |
| Analyze App Runner service | "analyze App Runner service my-app" |
| Pause App Runner service | "pause App Runner service my-app" |
| Resume App Runner service | "resume App Runner service my-app" |
| List Lightsail instances | "list Lightsail instances" |
| Analyze Lightsail instance | "analyze Lightsail instance my-server" |
| List SageMaker endpoints | "list SageMaker endpoints" |
| Analyze SageMaker endpoint | "analyze endpoint my-model-endpoint" |
| List SageMaker training jobs | "list SageMaker training jobs" |
| List SageMaker domains | "list SageMaker domains" |
| Analyze SageMaker domain | "analyze SageMaker domain my-domain" |
| List SageMaker notebooks | "list SageMaker notebooks" |
| Start SageMaker notebook | "start notebook my-notebook" |
| Stop SageMaker notebook | "stop notebook my-notebook" |
| List SageMaker pipelines | "list SageMaker pipelines" |
| List SageMaker experiments | "list SageMaker experiments" |
| List SageMaker feature groups | "list SageMaker feature groups" |
| List SageMaker model packages | "list SageMaker model packages" |
| Stop training job | "stop SageMaker training job my-job" |
| Delete SageMaker endpoint | "delete endpoint my-old-endpoint" |
| Search resource by name | "find resource named production-server" |
| Describe agent capabilities | "what can you do?" |

---

## Storage (~33 tools)

| Action | Sample Query |
|--------|-------------|
| List S3 buckets | "list my S3 buckets" |
| Analyze S3 bucket | "analyze bucket my-data-bucket" |
| List S3 bucket objects | "list objects in my-data-bucket" |
| List public S3 buckets | "which of my S3 buckets are public?" |
| Enable public access block | "block public access on my-bucket" |
| Get bucket public access status | "is my-bucket publicly accessible?" |
| Enable S3 versioning | "enable versioning on my-bucket" |
| Create lifecycle policy | "add a lifecycle policy to my-bucket" |
| Enable S3 encryption | "enable encryption on my-bucket" |
| List EBS volumes | "list my EBS volumes" |
| Analyze EBS volume | "analyze volume vol-0abc123" |
| Modify EBS volume | "resize vol-0abc123 to 100GB" |
| Create EBS snapshot | "create snapshot of vol-0abc123" |
| List EBS snapshots | "list my EBS snapshots" |
| List EFS filesystems | "list my EFS file systems" |
| Analyze EFS filesystem | "analyze EFS fs-0abc123" |
| Create EFS access point | "create access point for fs-0abc123" |
| List backup plans | "list my AWS Backup plans" |
| List backup jobs | "list recent backup jobs" |
| Start backup job | "start a backup of my-resource" |
| Get backup job status | "check status of backup job abc-123" |
| List DataSync tasks | "list DataSync tasks" |
| Analyze DataSync task | "analyze DataSync task my-task" |
| Start DataSync task | "start DataSync task my-task" |
| List Transfer servers | "list Transfer Family servers" |
| Analyze Transfer server | "analyze Transfer server s-0abc123" |
| List ECR repositories | "list my ECR repositories" |
| Analyze ECR repository | "analyze ECR repo my-app" |
| Get ECR scan results | "show scan results for my-app:latest" |
| Delete ECR images | "delete untagged images from my-app repo" |
| List Bedrock knowledge bases | "list my Bedrock knowledge bases" |
| Analyze Bedrock KB | "analyze knowledge base kb-0abc123" |

---

## Network (~41 tools)

| Action | Sample Query |
|--------|-------------|
| List VPCs | "list my VPCs" |
| Analyze VPC | "analyze VPC vpc-0abc123" |
| Create VPC subnet | "create a subnet in vpc-0abc123" |
| List security groups | "list my security groups" |
| Analyze security group | "analyze security group sg-0abc123" |
| Add security group rule | "allow port 443 inbound on sg-0abc123" |
| Remove security group rule | "remove the 0.0.0.0/0 rule from sg-0abc123" |
| Delete security group rule by ID | "delete rule sgr-abc123" |
| Update security group rule | "update rule on sg-0abc123 to restrict CIDR" |
| Delete security group | "delete security group sg-old123" |
| Create security group | "create a new security group for my web app" |
| List subnets | "list subnets in vpc-0abc123" |
| List internet gateways | "list my internet gateways" |
| List NAT gateways | "list NAT gateways" |
| List load balancers | "list my load balancers" |
| Analyze load balancer | "analyze ALB my-alb" |
| Modify target group health check | "change health check path to /health" |
| List CloudFront distributions | "list my CloudFront distributions" |
| Analyze CloudFront distribution | "analyze CloudFront E1ABC2DEF3" |
| Invalidate CloudFront cache | "invalidate CloudFront cache for /*" |
| List Route53 hosted zones | "list my DNS hosted zones" |
| Analyze Route53 hosted zone | "analyze hosted zone Z0ABC123" |
| Create Route53 record | "create DNS record api.example.com" |
| List API Gateways | "list my API Gateways" |
| Analyze API Gateway | "analyze API Gateway my-api" |
| Create API Gateway deployment | "deploy API Gateway my-api to prod" |
| Get API Gateway stages | "list stages for API my-api" |
| List EventBridge buses | "list event buses" |
| List EventBridge rules | "list event rules" |
| Enable event rule | "enable event rule my-rule" |
| Disable event rule | "disable event rule my-rule" |
| Put event | "send a test event to default bus" |
| List Kinesis streams | "list my Kinesis streams" |
| Analyze Kinesis stream | "analyze stream my-stream" |
| List MSK clusters | "list my Kafka clusters" |
| Analyze MSK cluster | "analyze MSK cluster my-kafka" |
| Reboot MSK broker | "reboot broker 1 in my-kafka" |
| List SNS topics | "list my SNS topics" |
| Create SNS topic | "create SNS topic alerts-topic" |
| Publish SNS message | "send a test message to alerts-topic" |
| Subscribe to SNS topic | "subscribe email@example.com to alerts-topic" |
| List SQS queues | "list my SQS queues" |
| Analyze SQS queue | "analyze queue my-queue" |
| Purge SQS queue | "purge all messages from my-queue" |
| Send SQS message | "send a test message to my-queue" |

---

## Database (~40 tools)

| Action | Sample Query |
|--------|-------------|
| List RDS instances | "list my RDS instances" |
| Analyze RDS instance | "analyze RDS instance my-db" |
| Create RDS snapshot | "create snapshot of RDS my-db" |
| Modify RDS instance | "scale my-db to db.r5.large" |
| List DynamoDB tables | "list my DynamoDB tables" |
| Analyze DynamoDB table | "analyze DynamoDB table Users" |
| Update DynamoDB capacity | "increase read capacity on Users table" |
| Enable DynamoDB PITR | "enable point-in-time recovery on Users" |
| List ElastiCache clusters | "list my ElastiCache clusters" |
| Analyze ElastiCache cluster | "analyze ElastiCache my-redis" |
| Create ElastiCache snapshot | "create snapshot of my-redis" |
| List DocumentDB clusters | "list DocumentDB clusters" |
| Analyze DocumentDB cluster | "analyze DocumentDB my-docdb" |
| Create DocumentDB snapshot | "snapshot my DocumentDB cluster" |
| List Redshift clusters | "list Redshift clusters" |
| Analyze Redshift cluster | "analyze Redshift my-warehouse" |
| Pause Redshift cluster | "pause Redshift cluster my-warehouse" |
| Resume Redshift cluster | "resume Redshift cluster my-warehouse" |
| List Neptune clusters | "list Neptune graph databases" |
| Analyze Neptune cluster | "analyze Neptune my-graph" |
| List OpenSearch domains | "list my OpenSearch domains" |
| Analyze OpenSearch domain | "analyze OpenSearch my-search" |
| List OpenSearch Serverless collections | "list OpenSearch Serverless collections" |
| Analyze OpenSearch Serverless collection | "analyze serverless collection my-col" |
| Create OpenSearch Serverless collection | "create OpenSearch Serverless collection" |
| List Glue jobs | "list my Glue ETL jobs" |
| Analyze Glue job | "analyze Glue job my-etl" |
| Start Glue job | "start Glue job my-etl" |
| Stop Glue job | "stop Glue job my-etl" |
| Get Glue job run | "get status of Glue job run jr-abc123" |
| List Athena workgroups | "list Athena workgroups" |
| Start Athena query | "run Athena query: SELECT * FROM logs LIMIT 10" |
| Get Athena query results | "get results of Athena query abc-123" |
| List Timestream databases | "list Timestream databases" |
| List Timestream tables | "list tables in Timestream db my-ts" |
| List QLDB ledgers | "list QLDB ledgers" |
| Analyze QLDB ledger | "analyze QLDB ledger my-ledger" |
| List DMS tasks | "list DMS replication tasks" |
| Analyze DMS task | "analyze DMS task my-migration" |
| Start DMS task | "start DMS task my-migration" |
| Stop DMS task | "stop DMS task my-migration" |

---

## Security (~35 tools)

| Action | Sample Query |
|--------|-------------|
| List IAM users | "list IAM users" |
| List IAM roles | "list IAM roles" |
| List IAM users without MFA | "which IAM users don't have MFA?" |
| Analyze IAM user | "analyze IAM user john.doe" |
| Analyze IAM role | "analyze IAM role my-lambda-role" |
| Rotate IAM access key | "rotate access key for user john.doe" |
| List secrets | "list Secrets Manager secrets" |
| Analyze secret | "analyze secret prod/db-password" |
| Rotate secret | "rotate secret prod/db-password" |
| List KMS keys | "list my KMS keys" |
| Analyze KMS key | "analyze KMS key abc-123" |
| Create KMS key | "create a new KMS key for encryption" |
| Analyze WAF web ACL | "analyze WAF web ACL my-acl" |
| Create WAF IP block rule | "block IP 1.2.3.4 in WAF" |
| List WAF rules | "list WAF rules" |
| Analyze GuardDuty findings | "show GuardDuty findings" |
| Archive GuardDuty finding | "archive GuardDuty finding abc-123" |
| Get Security Hub findings | "show Security Hub findings" |
| Get Security Hub summary | "Security Hub compliance summary" |
| Enable Security Hub standard | "enable CIS benchmark in Security Hub" |
| List Inspector findings | "list Inspector vulnerability findings" |
| Get Inspector coverage | "show Inspector coverage status" |
| Get Macie status | "is Macie enabled?" |
| List Macie findings | "show Macie sensitive data findings" |
| Get Shield protection status | "is Shield protecting my resources?" |
| List Shield protections | "list Shield protected resources" |
| List CloudTrail trails | "list CloudTrail trails" |
| Analyze CloudTrail trail | "analyze trail my-trail" |
| Lookup CloudTrail events | "look up events for user john.doe" |
| List Config rules | "list AWS Config rules" |
| Get Config compliance summary | "show Config compliance summary" |
| Get noncompliant resources | "which resources are noncompliant?" |
| List Access Analyzers | "list IAM Access Analyzers" |
| List Access Analyzer findings | "show Access Analyzer findings" |
| List Cognito user pools | "list Cognito user pools" |
| Analyze Cognito user pool | "analyze Cognito pool my-pool" |
| List Cognito users | "list users in Cognito pool my-pool" |

---

## Cost & Ops (~36 tools)

| Action | Sample Query |
|--------|-------------|
| Get cost and usage | "show my AWS costs for last 7 days" |
| Get cost forecast | "forecast my AWS spending for this month" |
| Get rightsizing recommendations | "show EC2 rightsizing recommendations" |
| List budgets | "list my AWS budgets" |
| Create budget alert | "create a $500/month budget alert" |
| List service quotas | "list service quotas for EC2" |
| Request quota increase | "request increase for EC2 instance limit" |
| List organization accounts | "list accounts in my organization" |
| Describe organization policy | "describe org policy my-scp" |
| List landing zones | "list Control Tower landing zones" |
| Get Trusted Advisor checks | "run Trusted Advisor checks" |
| Get Trusted Advisor check result | "show Trusted Advisor cost optimization" |
| Describe AWS Health events | "any ongoing AWS health events?" |
| Describe affected resources | "which of my resources are affected?" |
| List CloudWatch alarms | "list CloudWatch alarms in ALARM state" |
| Create CloudWatch alarm | "create CPU alarm for i-0abc123" |
| Get CloudWatch metrics | "get Lambda invocation metrics" |
| List log groups | "list CloudWatch log groups" |
| Get log events | "show last 50 log events from my-group" |
| Create log metric filter | "create metric filter for ERROR in logs" |
| List CloudFormation stacks | "list my CloudFormation stacks" |
| Analyze CloudFormation stack | "analyze stack my-app-stack" |
| Get stack events | "show events for stack my-app-stack" |
| Detect stack drift | "check if my-app-stack has drifted" |
| List CodePipeline pipelines | "list my pipelines" |
| Get pipeline state | "what's the status of pipeline my-pipe?" |
| Retry pipeline stage | "retry the deploy stage in my-pipe" |
| Start pipeline execution | "start pipeline my-pipe" |
| List SSM parameters | "list SSM Parameter Store parameters" |
| Get SSM parameter | "get value of /app/db-host" |
| Put SSM parameter | "set /app/version to 2.0.1" |
| List Step Functions state machines | "list my Step Functions" |
| Analyze state machine | "analyze state machine my-workflow" |
| Start state machine execution | "start execution of my-workflow" |
| Stop state machine execution | "stop execution abc-123" |
| List SES identities | "list SES verified identities" |
| Get SES sending stats | "show SES sending statistics" |
| Check SES sending quota | "check my SES sending limits" |
| List Bedrock models | "list available Bedrock models" |

---

## Deployment (~15 tools)

| Action | Sample Query |
|--------|-------------|
| Prepare code payload (inline) | "deploy this Python code to Lambda: def handler..." |
| Prepare code payload (S3) | "deploy the code from s3://my-bucket/app.zip to Lambda" |
| Detect code language | "what language is this code?" |
| Create deployment plan | "plan a deployment of my-func to Lambda" |
| Deploy Lambda function | "deploy my Python function to Lambda as my-func" |
| Rollback Lambda function | "rollback Lambda function my-func to previous version" |
| Deploy ECS service | "deploy my container to ECS service my-app" |
| Rollback ECS service | "rollback ECS service my-app to previous task definition" |
| Deploy to EC2 | "deploy this code to EC2 instance i-0abc123" |
| Check EC2 SSM status | "is instance i-0abc123 ready for deployment?" |
| Deploy App Runner (container) | "deploy my container to App Runner" |
| Deploy App Runner (source) | "deploy my Python app to App Runner from source" |
| Deploy static site to S3 | "deploy my static website to S3" |
| Validate CloudFormation template | "validate this CloudFormation template" |
| Deploy CloudFormation stack | "deploy this CloudFormation template as my-stack" |
| Rollback CloudFormation stack | "rollback CloudFormation stack my-stack" |

---

## Meta

| Action | Sample Query |
|--------|-------------|
| Describe capabilities | "what can you do?" or "help" |
| Search resource by name | "find resource named my-production-db" |

---

## Targeting a Specific Account

By default every request acts on the platform account (where the agent runs). In the
Command Center, choose accounts in the sidebar's **Target accounts** picker, or type a
registered 12-digit account ID in the message; it is added to the picker so follow-ups
stay on that account. Only accounts an admin registered on the **Accounts** page can be
targeted. The agent assumes `CloudOpsCrossAccountRole` there and states which account it
acted on. Pick several accounts to fan out across all of them.

| Action | Sample Query |
|--------|-------------|
| Act on another account | "list EC2 instances in account 123456789012" |
| Act on multiple accounts | "check for public S3 buckets in accounts 111122223333 and 444455556666" |
| Fan-out audit | "list IAM users without MFA across accounts 111122223333, 444455556666" |

---

## Deploying an Uploaded Zip

Use **📦 Deploy code (.zip)** above the chat to upload a zip and pick a target; the
agent inspects it, shows a plan, and queues the deployment for your approval.

| Action | Sample Query (after uploading) |
|--------|-------------|
| Get a recommendation | "inspect the uploaded zip and tell me where it should run" |
| Lambda | "deploy the uploaded zip to Lambda as orders-api" |
| Existing EC2 | "deploy it to i-0abc123def4567890 and run `python3 app.py` in the background" |
| New EC2 | "launch a t3.small and run the uploaded code with `npm install && npm start`" |
| ECS Fargate | "build the Dockerfile in the upload and run it on ECS as web-frontend" |
| Static site | "publish the uploaded zip as a static website" |
| Another account | pick the account in **Target Accounts**, then "deploy the upload to Lambda" |

---

## Quick Test Queries (copy-paste these)

```
list my EC2 instances
list my S3 buckets
which of my S3 buckets are public?
list my RDS instances
list my Lambda functions
show my AWS costs for last 7 days
list IAM users without MFA
list my security groups
list my DynamoDB tables
list CloudWatch alarms
deploy this Python code to Lambda: def handler(event, context): return {"statusCode": 200}
rollback Lambda function my-func
deploy my static site to S3
list EC2 instances in account 123456789012
what can you do?
```
