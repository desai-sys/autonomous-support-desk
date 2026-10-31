#!/usr/bin/env python3
"""
Quick setup script for Bedrock Knowledge Base with S3 vector storage.
"""

import boto3
import json
import time
import os

def create_knowledge_base_with_s3():
    """Create a Bedrock Knowledge Base using S3 for vector storage."""
    
    bedrock_agent = boto3.client('bedrock-agent', region_name='us-east-1')
    iam = boto3.client('iam', region_name='us-east-1')
    
    # Get bucket name from environment or prompt
    bucket_name = os.environ.get('KB_BUCKET_NAME')
    if not bucket_name:
        bucket_name = input("Enter your S3 bucket name: ")
    
    print(f"Setting up Knowledge Base with S3 bucket: {bucket_name}")
    
    # 1. Create IAM role for Knowledge Base
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {
                    "Service": "bedrock.amazonaws.com"
                },
                "Action": "sts:AssumeRole"
            }
        ]
    }
    
    role_policy = {
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
                    f"arn:aws:s3:::{bucket_name}",
                    f"arn:aws:s3:::{bucket_name}/*"
                ]
            },
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel"
                ],
                "Resource": "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v1"
            }
        ]
    }
    
    role_name = "CloudOpsKnowledgeBaseRole"
    
    try:
        # Create role
        iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Role for CloudOps Knowledge Base"
        )
        
        # Attach policy
        iam.put_role_policy(
            RoleName=role_name,
            PolicyName="CloudOpsKBPolicy",
            PolicyDocument=json.dumps(role_policy)
        )
        
        print("✅ Created IAM role")
        
        # Wait for role to propagate
        time.sleep(10)
        
    except iam.exceptions.EntityAlreadyExistsException:
        print("ℹ️ IAM role already exists")
    
    # Get role ARN
    role_arn = iam.get_role(RoleName=role_name)['Role']['Arn']
    
    # 2. Create Knowledge Base with S3 vector storage
    try:
        kb_response = bedrock_agent.create_knowledge_base(
            name="CloudOpsKnowledgeBase",
            description="Knowledge base for CloudOps SRE resolutions",
            roleArn=role_arn,
            knowledgeBaseConfiguration={
                'type': 'VECTOR',
                'vectorKnowledgeBaseConfiguration': {
                    'embeddingModelArn': 'arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v1'
                }
            },
            storageConfiguration={
                'type': 'S3',
                's3Configuration': {
                    'bucketArn': f"arn:aws:s3:::{bucket_name}",
                    'keyPrefix': 'knowledge-base-vectors/'
                }
            }
        )
        
        kb_id = kb_response['knowledgeBase']['knowledgeBaseId']
        print(f"✅ Created Knowledge Base: {kb_id}")
        
        # 3. Create data source
        ds_response = bedrock_agent.create_data_source(
            knowledgeBaseId=kb_id,
            name="CloudOpsResolutions",
            description="SRE resolution documents",
            dataSourceConfiguration={
                'type': 'S3',
                's3Configuration': {
                    'bucketArn': f"arn:aws:s3:::{bucket_name}",
                    'inclusionPrefixes': ['resolutions/']
                }
            }
        )
        
        ds_id = ds_response['dataSource']['dataSourceId']
        print(f"✅ Created Data Source: {ds_id}")
        
        # 4. Create initial sample document
        s3 = boto3.client('s3')
        sample_content = """Issue: EC2 instance SSH connection timeout
Resolution: Check security group rules. Use analyze_ec2_instance tool to inspect security groups, then use add_security_group_rule to open port 22 if needed. Verify the instance is running and has a public IP if accessing from internet."""
        
        s3.put_object(
            Bucket=bucket_name,
            Key='resolutions/sample-ssh-issue.txt',
            Body=sample_content
        )
        print("✅ Created sample resolution document")
        
        # 5. Start ingestion job
        bedrock_agent.start_ingestion_job(
            knowledgeBaseId=kb_id,
            dataSourceId=ds_id
        )
        print("✅ Started ingestion job")
        
        # Output configuration
        print("\n" + "="*50)
        print("CONFIGURATION VALUES:")
        print("="*50)
        print(f"S3_BUCKET_NAME = '{bucket_name}'")
        print(f"KNOWLEDGE_BASE_ID = '{kb_id}'")
        print(f"DATA_SOURCE_ID = '{ds_id}'")
        print("\nUpdate these values in your configuration files:")
        print("- src/services/rag_service.py")
        print("- learning_loop.py")
        
        return kb_id, ds_id, bucket_name
        
    except Exception as e:
        print(f"❌ Error creating Knowledge Base: {str(e)}")
        return None, None, None

if __name__ == "__main__":
    create_knowledge_base_with_s3()