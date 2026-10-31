#!/usr/bin/env python3
"""
Complete AWS infrastructure setup script.
This script creates all necessary AWS resources for the CloudOps platform.
"""

import boto3
import json
import time
import uuid
from datetime import datetime

class AWSInfrastructureSetup:
    def __init__(self, region='us-east-1'):
        self.region = region
        self.session = boto3.Session(region_name=region)
        self.dynamodb = self.session.client('dynamodb')
        self.s3 = self.session.client('s3')
        self.iam = self.session.client('iam')
        self.bedrock_agent = self.session.client('bedrock-agent')
        self.ec2 = self.session.client('ec2')
        
        # Generate unique identifiers
        self.unique_id = str(uuid.uuid4())[:8]
        self.bucket_name = f"cloudops-kb-{self.unique_id}"
        
        # Store configuration
        self.config = {}
    
    def create_dynamodb_tables(self):
        """Create all required DynamoDB tables."""
        print("🔄 Creating DynamoDB tables...")
        
        tables = [
            {
                'name': 'CloudOpsTickets',
                'key': 'ticket_id'
            },
            {
                'name': 'CloudOpsUsers',
                'key': 'user_id'
            },
            {
                'name': 'CloudOpsTenants',
                'key': 'tenant_id'
            },
            {
                'name': 'CloudOpsUsage',
                'key': 'tenant_id',
                'sort_key': 'period'
            },
            {
                'name': 'CloudOpsAuditLog',
                'key': 'event_id',
                'sort_key': 'timestamp'
            }
        ]
        
        for table in tables:
            try:
                # Check if table exists
                self.dynamodb.describe_table(TableName=table['name'])
                print(f"  ✅ Table {table['name']} already exists")
            except self.dynamodb.exceptions.ResourceNotFoundException:
                # Create table
                key_schema = [{'AttributeName': table['key'], 'KeyType': 'HASH'}]
                attribute_definitions = [{'AttributeName': table['key'], 'AttributeType': 'S'}]
                
                if 'sort_key' in table:
                    key_schema.append({'AttributeName': table['sort_key'], 'KeyType': 'RANGE'})
                    attribute_definitions.append({'AttributeName': table['sort_key'], 'AttributeType': 'S'})
                
                self.dynamodb.create_table(
                    TableName=table['name'],
                    KeySchema=key_schema,
                    AttributeDefinitions=attribute_definitions,
                    BillingMode='PAY_PER_REQUEST'
                )
                print(f"  ✅ Created table {table['name']}")
        
        # Wait for tables to be active
        print("  ⏳ Waiting for tables to be active...")
        time.sleep(10)
    
    def create_s3_bucket(self):
        """Create S3 bucket for knowledge base."""
        print("🔄 Creating S3 bucket...")
        
        try:
            # Check if bucket exists
            self.s3.head_bucket(Bucket=self.bucket_name)
            print(f"  ✅ Bucket {self.bucket_name} already exists")
        except:
            # Create bucket
            if self.region == 'us-east-1':
                self.s3.create_bucket(Bucket=self.bucket_name)
            else:
                self.s3.create_bucket(
                    Bucket=self.bucket_name,
                    CreateBucketConfiguration={'LocationConstraint': self.region}
                )
            
            # Enable versioning
            self.s3.put_bucket_versioning(
                Bucket=self.bucket_name,
                VersioningConfiguration={'Status': 'Enabled'}
            )
            
            # Block public access
            self.s3.put_public_access_block(
                Bucket=self.bucket_name,
                PublicAccessBlockConfiguration={
                    'BlockPublicAcls': True,
                    'IgnorePublicAcls': True,
                    'BlockPublicPolicy': True,
                    'RestrictPublicBuckets': True
                }
            )
            
            print(f"  ✅ Created bucket {self.bucket_name}")
        
        self.config['S3_BUCKET_NAME'] = self.bucket_name
    
    def create_iam_roles(self):
        """Create IAM roles for the platform."""
        print("🔄 Creating IAM roles...")
        
        # Knowledge Base role
        kb_role_name = f"CloudOpsKnowledgeBaseRole-{self.unique_id}"
        
        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "bedrock.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }
            ]
        }
        
        kb_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "s3:GetObject",
                        "s3:ListBucket",
                        "s3:PutObject",
                        "s3:DeleteObject"
                    ],
                    "Resource": [
                        f"arn:aws:s3:::{self.bucket_name}",
                        f"arn:aws:s3:::{self.bucket_name}/*"
                    ]
                },
                {
                    "Effect": "Allow",
                    "Action": ["bedrock:InvokeModel"],
                    "Resource": "arn:aws:bedrock:*::foundation-model/amazon.titan-embed-text-v1"
                }
            ]
        }
        
        try:
            # Create role
            self.iam.create_role(
                RoleName=kb_role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description="Role for CloudOps Knowledge Base"
            )
            
            # Attach policy
            self.iam.put_role_policy(
                RoleName=kb_role_name,
                PolicyName="CloudOpsKBPolicy",
                PolicyDocument=json.dumps(kb_policy)
            )
            
            print(f"  ✅ Created Knowledge Base role: {kb_role_name}")
            
        except self.iam.exceptions.EntityAlreadyExistsException:
            print(f"  ✅ Knowledge Base role already exists: {kb_role_name}")
        
        # Get role ARN
        role_arn = self.iam.get_role(RoleName=kb_role_name)['Role']['Arn']
        self.config['KB_ROLE_ARN'] = role_arn
        
        # Wait for role propagation
        time.sleep(10)
        
        # AgentCore execution role
        agent_role_name = f"CloudOpsAgentCoreRole-{self.unique_id}"
        
        agent_trust_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "bedrock.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }
            ]
        }
        
        try:
            self.iam.create_role(
                RoleName=agent_role_name,
                AssumeRolePolicyDocument=json.dumps(agent_trust_policy),
                Description="Role for CloudOps AgentCore execution"
            )
            
            # Attach a single broad policy instead of hitting the 10-policy limit
            policies = [
                'arn:aws:iam::aws:policy/PowerUserAccess',
                'arn:aws:iam::aws:policy/IAMReadOnlyAccess',
            ]
            
            for policy_arn in policies:
                try:
                    self.iam.attach_role_policy(
                        RoleName=agent_role_name,
                        PolicyArn=policy_arn
                    )
                except Exception as e:
                    print(f"    Warning: Could not attach {policy_arn}: {str(e)}")
            
            print(f"  ✅ Created AgentCore role: {agent_role_name}")
            
        except self.iam.exceptions.EntityAlreadyExistsException:
            print(f"  ✅ AgentCore role already exists: {agent_role_name}")
        
        agent_role_arn = self.iam.get_role(RoleName=agent_role_name)['Role']['Arn']
        self.config['AGENT_ROLE_ARN'] = agent_role_arn
    
    def create_knowledge_base(self):
        """Create Bedrock Knowledge Base with S3 vector storage."""
        print("🔄 Creating Bedrock Knowledge Base...")
        
        try:
            kb_response = self.bedrock_agent.create_knowledge_base(
                name=f"CloudOpsKnowledgeBase-{self.unique_id}",
                description="Knowledge base for CloudOps SRE resolutions",
                roleArn=self.config['KB_ROLE_ARN'],
                knowledgeBaseConfiguration={
                    'type': 'VECTOR',
                    'vectorKnowledgeBaseConfiguration': {
                        'embeddingModelArn': 'arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v1'
                    }
                },
                storageConfiguration={
                    'type': 'S3_VECTORS',
                    's3VectorsConfiguration': {
                        'vectorBucketArn': f"arn:aws:s3:::{self.bucket_name}",
                    }
                }
            )
            
            kb_id = kb_response['knowledgeBase']['knowledgeBaseId']
            print(f"  ✅ Created Knowledge Base: {kb_id}")
            
            # Create data source
            ds_response = self.bedrock_agent.create_data_source(
                knowledgeBaseId=kb_id,
                name="CloudOpsResolutions",
                description="SRE resolution documents",
                dataSourceConfiguration={
                    'type': 'S3',
                    's3Configuration': {
                        'bucketArn': f"arn:aws:s3:::{self.bucket_name}",
                        'inclusionPrefixes': ['resolutions/']
                    }
                }
            )
            
            ds_id = ds_response['dataSource']['dataSourceId']
            print(f"  ✅ Created Data Source: {ds_id}")
            
            # Create sample resolution document
            sample_content = """Issue: EC2 instance SSH connection timeout
Resolution: Check security group rules using analyze_ec2_instance tool. If port 22 is not open, use add_security_group_rule to allow SSH access. Verify the instance is running and has a public IP if accessing from internet.

Issue: S3 bucket security concerns
Resolution: Use analyze_s3_bucket tool to check public access settings. If bucket is public, use secure_s3_bucket tool to apply public access block. Enable encryption if not already enabled.

Issue: RDS database connection issues
Resolution: Use analyze_rds_instance tool to check database status and endpoint. Verify security groups allow database port access. Check if database is in correct VPC and subnet group."""
            
            self.s3.put_object(
                Bucket=self.bucket_name,
                Key='resolutions/sample-resolutions.txt',
                Body=sample_content
            )
            print("  ✅ Created sample resolution documents")
            
            # Start ingestion job
            self.bedrock_agent.start_ingestion_job(
                knowledgeBaseId=kb_id,
                dataSourceId=ds_id
            )
            print("  ✅ Started ingestion job")
            
            self.config['KNOWLEDGE_BASE_ID'] = kb_id
            self.config['DATA_SOURCE_ID'] = ds_id
            
        except Exception as e:
            print(f"  ❌ Error creating Knowledge Base: {str(e)}")
            # Continue with deployment even if KB creation fails
            self.config['KNOWLEDGE_BASE_ID'] = 'PLACEHOLDER'
            self.config['DATA_SOURCE_ID'] = 'PLACEHOLDER'
    
    def create_ec2_security_group(self):
        """Create security group for EC2 instances."""
        print("🔄 Creating EC2 security group...")
        
        try:
            # Get default VPC
            vpcs = self.ec2.describe_vpcs(Filters=[{'Name': 'is-default', 'Values': ['true']}])
            if not vpcs['Vpcs']:
                print("  ⚠️ No default VPC found, skipping security group creation")
                return
            
            vpc_id = vpcs['Vpcs'][0]['VpcId']
            
            # Create security group
            sg_response = self.ec2.create_security_group(
                GroupName=f'CloudOps-SG-{self.unique_id}',
                Description='Security group for CloudOps platform',
                VpcId=vpc_id
            )
            
            sg_id = sg_response['GroupId']
            
            # Add rules for Streamlit apps
            self.ec2.authorize_security_group_ingress(
                GroupId=sg_id,
                IpPermissions=[
                    {
                        'IpProtocol': 'tcp',
                        'FromPort': 8501,
                        'ToPort': 8501,
                        'IpRanges': [{'CidrIp': '0.0.0.0/0', 'Description': 'Streamlit Command Center'}]
                    },
                    {
                        'IpProtocol': 'tcp',
                        'FromPort': 8502,
                        'ToPort': 8502,
                        'IpRanges': [{'CidrIp': '0.0.0.0/0', 'Description': 'Streamlit SRE Portal'}]
                    },
                    {
                        'IpProtocol': 'tcp',
                        'FromPort': 22,
                        'ToPort': 22,
                        'IpRanges': [{'CidrIp': '0.0.0.0/0', 'Description': 'SSH access'}]
                    }
                ]
            )
            
            print(f"  ✅ Created security group: {sg_id}")
            self.config['SECURITY_GROUP_ID'] = sg_id
            
        except Exception as e:
            print(f"  ⚠️ Error creating security group: {str(e)}")
    
    def save_configuration(self):
        """Save configuration to files."""
        print("🔄 Saving configuration...")
        
        # Save to config.py
        config_content = f'''"""
CloudOps Platform Configuration
Generated on: {datetime.now().isoformat()}
"""

# AWS Configuration
AWS_REGION = "{self.region}"
UNIQUE_ID = "{self.unique_id}"

# S3 Configuration
S3_BUCKET_NAME = "{self.config.get('S3_BUCKET_NAME', 'PLACEHOLDER')}"

# Knowledge Base Configuration
KNOWLEDGE_BASE_ID = "{self.config.get('KNOWLEDGE_BASE_ID', 'PLACEHOLDER')}"
DATA_SOURCE_ID = "{self.config.get('DATA_SOURCE_ID', 'PLACEHOLDER')}"

# IAM Roles
KB_ROLE_ARN = "{self.config.get('KB_ROLE_ARN', 'PLACEHOLDER')}"
AGENT_ROLE_ARN = "{self.config.get('AGENT_ROLE_ARN', 'PLACEHOLDER')}"

# EC2 Configuration
SECURITY_GROUP_ID = "{self.config.get('SECURITY_GROUP_ID', 'PLACEHOLDER')}"

# DynamoDB Tables
TICKETS_TABLE = "CloudOpsTickets"
USERS_TABLE = "CloudOpsUsers"
TENANTS_TABLE = "CloudOpsTenants"
USAGE_TABLE = "CloudOpsUsage"
AUDIT_TABLE = "CloudOpsAuditLog"

# Default Configuration
DEFAULT_TENANT_ID = "demo-tenant-001"
DEFAULT_USER_ID = "demo-user"

# Agent Configuration (update after deployment)
AGENT_ARN = "UPDATE_AFTER_AGENT_DEPLOYMENT"

# Application Configuration
STREAMLIT_PORT_COMMAND_CENTER = 8501
STREAMLIT_PORT_SRE_PORTAL = 8502
'''
        
        with open('config.py', 'w') as f:
            f.write(config_content)
        
        # Save deployment info
        deployment_info = {
            'deployment_date': datetime.now().isoformat(),
            'region': self.region,
            'unique_id': self.unique_id,
            'resources': self.config
        }
        
        with open('deployment_info.json', 'w') as f:
            json.dump(deployment_info, f, indent=2)
        
        print("  ✅ Configuration saved to config.py and deployment_info.json")
    
    def run_setup(self):
        """Run the complete setup process."""
        print("🚀 Starting AWS infrastructure setup...")
        print(f"Region: {self.region}")
        print(f"Unique ID: {self.unique_id}")
        print("-" * 50)
        
        try:
            self.create_dynamodb_tables()
            self.create_s3_bucket()
            self.create_iam_roles()
            self.create_knowledge_base()
            self.create_ec2_security_group()
            self.save_configuration()
            
            print("\n" + "="*50)
            print("✅ AWS INFRASTRUCTURE SETUP COMPLETE!")
            print("="*50)
            print(f"S3 Bucket: {self.config.get('S3_BUCKET_NAME')}")
            print(f"Knowledge Base ID: {self.config.get('KNOWLEDGE_BASE_ID')}")
            print(f"Security Group ID: {self.config.get('SECURITY_GROUP_ID')}")
            print("\nNext steps:")
            print("1. Deploy the AgentCore agent")
            print("2. Update AGENT_ARN in config.py")
            print("3. Deploy Streamlit apps to EC2")
            print("\nConfiguration saved to config.py")
            
        except Exception as e:
            print(f"\n❌ Setup failed: {str(e)}")
            raise

def main():
    """Main function."""
    import sys
    
    region = 'us-east-1'
    if len(sys.argv) > 1:
        region = sys.argv[1]
    
    setup = AWSInfrastructureSetup(region)
    setup.run_setup()

if __name__ == "__main__":
    main()