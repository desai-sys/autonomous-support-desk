#!/usr/bin/env python3
"""
Teardown Script — Removes all AWS resources created by the CloudOps platform.

DANGER: This is destructive and irreversible.
Run with --confirm to actually delete resources.

Usage:
    python scripts/teardown_aws_infrastructure.py           # Dry run (shows what would be deleted)
    python scripts/teardown_aws_infrastructure.py --confirm # Actually delete everything
"""

import boto3
import sys
import time
import json
from datetime import datetime


class AWSInfrastructureTeardown:
    def __init__(self, region='us-east-1', dry_run=True):
        self.region = region
        self.dry_run = dry_run
        self.session = boto3.Session(region_name=region)
        self.dynamodb = self.session.client('dynamodb')
        self.s3 = self.session.client('s3')
        self.iam = self.session.client('iam')
        self.ec2 = self.session.client('ec2')

        self.mode = "DRY RUN" if dry_run else "LIVE DELETE"

    def _action(self, description, fn):
        """Execute or simulate an action."""
        if self.dry_run:
            print(f"  [DRY RUN] Would: {description}")
        else:
            try:
                fn()
                print(f"  ✅ {description}")
            except Exception as e:
                print(f"  ⚠️  {description} — {str(e)[:80]}")

    def delete_dynamodb_tables(self):
        """Delete all CloudOps DynamoDB tables."""
        print("\n🗑️  DynamoDB Tables")
        tables = [
            'CloudOpsTickets', 'CloudOpsUsers', 'CloudOpsTenants',
            'CloudOpsUsage', 'CloudOpsAuditLog'
        ]
        for table in tables:
            try:
                self.dynamodb.describe_table(TableName=table)
                self._action(
                    f"Delete table {table}",
                    lambda t=table: self.dynamodb.delete_table(TableName=t)
                )
            except self.dynamodb.exceptions.ResourceNotFoundException:
                print(f"  ⏭️  Table {table} not found (already deleted)")

    def delete_s3_bucket(self):
        """Delete the CloudOps S3 bucket and all objects."""
        print("\n🗑️  S3 Bucket")
        # Find CloudOps buckets
        try:
            buckets = self.s3.list_buckets()['Buckets']
            cloudops_buckets = [b['Name'] for b in buckets
                               if 'cloudops' in b['Name'].lower() or
                                  'bedrock-agents-knowledgebase' in b['Name'].lower()]

            for bucket_name in cloudops_buckets:
                # Must empty bucket before deleting
                self._action(
                    f"Empty and delete bucket {bucket_name}",
                    lambda bn=bucket_name: self._empty_and_delete_bucket(bn)
                )

            if not cloudops_buckets:
                print("  ⏭️  No CloudOps S3 buckets found")
        except Exception as e:
            print(f"  ⚠️  Error listing buckets: {e}")

    def _empty_and_delete_bucket(self, bucket_name):
        """Empty all objects from a bucket, then delete it."""
        # Delete all objects
        paginator = self.s3.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=bucket_name):
            objects = page.get('Contents', [])
            if objects:
                delete_keys = [{'Key': obj['Key']} for obj in objects]
                self.s3.delete_objects(
                    Bucket=bucket_name,
                    Delete={'Objects': delete_keys}
                )

        # Delete all versions (if versioning enabled)
        try:
            paginator = self.s3.get_paginator('list_object_versions')
            for page in paginator.paginate(Bucket=bucket_name):
                versions = page.get('Versions', []) + page.get('DeleteMarkers', [])
                if versions:
                    delete_keys = [{'Key': v['Key'], 'VersionId': v['VersionId']} for v in versions]
                    self.s3.delete_objects(
                        Bucket=bucket_name,
                        Delete={'Objects': delete_keys}
                    )
        except Exception:
            pass

        # Delete bucket
        self.s3.delete_bucket(Bucket=bucket_name)

    def delete_iam_roles(self):
        """Delete CloudOps IAM roles and policies."""
        print("\n🗑️  IAM Roles")
        try:
            roles = self.iam.list_roles()['Roles']
            cloudops_roles = [r for r in roles
                             if 'CloudOps' in r['RoleName'] or
                                'cloudops' in r['RoleName'].lower()]

            for role in cloudops_roles:
                role_name = role['RoleName']
                self._action(
                    f"Delete role {role_name}",
                    lambda rn=role_name: self._delete_role(rn)
                )

            if not cloudops_roles:
                print("  ⏭️  No CloudOps IAM roles found")
        except Exception as e:
            print(f"  ⚠️  Error listing roles: {e}")

    def _delete_role(self, role_name):
        """Detach all policies, delete inline policies, then delete the role."""
        # Detach managed policies
        attached = self.iam.list_attached_role_policies(RoleName=role_name)
        for policy in attached['AttachedPolicies']:
            self.iam.detach_role_policy(RoleName=role_name, PolicyArn=policy['PolicyArn'])

        # Delete inline policies
        inline = self.iam.list_role_policies(RoleName=role_name)
        for policy_name in inline['PolicyNames']:
            self.iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)

        # Delete instance profiles
        profiles = self.iam.list_instance_profiles_for_role(RoleName=role_name)
        for profile in profiles['InstanceProfiles']:
            self.iam.remove_role_from_instance_profile(
                InstanceProfileName=profile['InstanceProfileName'],
                RoleName=role_name
            )

        # Delete the role
        self.iam.delete_role(RoleName=role_name)

    def delete_security_groups(self):
        """Delete CloudOps security groups."""
        print("\n🗑️  Security Groups")
        try:
            sgs = self.ec2.describe_security_groups(
                Filters=[{'Name': 'group-name', 'Values': ['CloudOps-*']}]
            )['SecurityGroups']

            for sg in sgs:
                self._action(
                    f"Delete security group {sg['GroupId']} ({sg['GroupName']})",
                    lambda gid=sg['GroupId']: self.ec2.delete_security_group(GroupId=gid)
                )

            if not sgs:
                print("  ⏭️  No CloudOps security groups found")
        except Exception as e:
            print(f"  ⚠️  Error listing security groups: {e}")

    def delete_bedrock_knowledge_base(self):
        """Delete the Bedrock Knowledge Base."""
        print("\n🗑️  Bedrock Knowledge Base")
        try:
            bedrock = self.session.client('bedrock-agent')
            kbs = bedrock.list_knowledge_bases()['knowledgeBaseSummaries']
            cloudops_kbs = [kb for kb in kbs if 'cloudops' in kb['name'].lower() or
                           'support' in kb['name'].lower()]

            for kb in cloudops_kbs:
                kb_id = kb['knowledgeBaseId']
                # Delete data sources first
                try:
                    data_sources = bedrock.list_data_sources(knowledgeBaseId=kb_id)
                    for ds in data_sources['dataSourceSummaries']:
                        self._action(
                            f"Delete data source {ds['dataSourceId']}",
                            lambda kid=kb_id, did=ds['dataSourceId']:
                                bedrock.delete_data_source(knowledgeBaseId=kid, dataSourceId=did)
                        )
                except Exception:
                    pass

                self._action(
                    f"Delete knowledge base {kb['name']} ({kb_id})",
                    lambda kid=kb_id: bedrock.delete_knowledge_base(knowledgeBaseId=kid)
                )

            if not cloudops_kbs:
                print("  ⏭️  No CloudOps Knowledge Bases found")
        except Exception as e:
            print(f"  ⚠️  Error: {e}")

    def delete_agentcore_agent(self):
        """Delete the AgentCore agent (via CLI is preferred, this is backup)."""
        print("\n🗑️  AgentCore Agent")
        print("  ℹ️  Run: agentcore destroy")
        print("  ℹ️  Or delete from AWS Console → Bedrock → AgentCore → Runtimes")

    def delete_ec2_instance(self):
        """Terminate the Streamlit EC2 instance."""
        print("\n🗑️  EC2 Instance (Streamlit Apps)")
        try:
            instances = self.ec2.describe_instances(
                Filters=[{'Name': 'tag:Name', 'Values': ['cloudops*', 'CloudOps*']}]
            )
            for r in instances['Reservations']:
                for i in r['Instances']:
                    if i['State']['Name'] not in ('terminated', 'shutting-down'):
                        self._action(
                            f"Terminate EC2 {i['InstanceId']} ({i['State']['Name']})",
                            lambda iid=i['InstanceId']:
                                self.ec2.terminate_instances(InstanceIds=[iid])
                        )
        except Exception as e:
            print(f"  ⚠️  Error: {e}")

    def run_teardown(self):
        """Run the full teardown."""
        print("=" * 60)
        print(f"  CloudOps Infrastructure Teardown — {self.mode}")
        print(f"  Region: {self.region}")
        print(f"  Time: {datetime.now().isoformat()}")
        print("=" * 60)

        if not self.dry_run:
            print("\n⚠️  THIS WILL PERMANENTLY DELETE ALL CLOUDOPS RESOURCES!")
            print("    Press Ctrl+C within 5 seconds to abort...")
            time.sleep(5)

        self.delete_dynamodb_tables()
        self.delete_s3_bucket()
        self.delete_bedrock_knowledge_base()
        self.delete_agentcore_agent()
        self.delete_ec2_instance()
        self.delete_security_groups()
        self.delete_iam_roles()

        print("\n" + "=" * 60)
        if self.dry_run:
            print("  DRY RUN COMPLETE — no resources were deleted.")
            print("  Run with --confirm to actually delete everything:")
            print("    python scripts/teardown_aws_infrastructure.py --confirm")
        else:
            print("  ✅ TEARDOWN COMPLETE")
            print("  Remaining manual cleanup:")
            print("    1. Run: agentcore destroy")
            print("    2. Delete ECR repository in AWS Console")
            print("    3. Delete CodeBuild project in AWS Console")
        print("=" * 60)


def main():
    region = 'us-east-1'
    dry_run = '--confirm' not in sys.argv

    if '--region' in sys.argv:
        idx = sys.argv.index('--region')
        region = sys.argv[idx + 1]

    teardown = AWSInfrastructureTeardown(region=region, dry_run=dry_run)
    teardown.run_teardown()


if __name__ == "__main__":
    main()
