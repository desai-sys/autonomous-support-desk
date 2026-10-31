#!/usr/bin/env python3
"""
Remove a CloudOps deployment.

    python scripts/destroy.py --region us-east-1               # keeps tickets + audit tables
    python scripts/destroy.py --region us-east-1 --delete-data # deletes everything

The tickets and audit tables are retained by default (DeletionPolicy: Retain).
Re-deploying with the same --app-name needs them gone, so either pass
--delete-data or redeploy under a new app name. Resources the agent created in
your accounts (Lambda functions, instances, ...) are not touched.
"""

import argparse
import sys

import boto3
from botocore.exceptions import ClientError


def empty_bucket(s3, bucket: str) -> None:
    try:
        paginator = s3.get_paginator("list_object_versions")
        for page in paginator.paginate(Bucket=bucket):
            objects = [{"Key": v["Key"], "VersionId": v["VersionId"]}
                       for v in page.get("Versions", []) + page.get("DeleteMarkers", [])]
            for i in range(0, len(objects), 1000):
                s3.delete_objects(Bucket=bucket, Delete={"Objects": objects[i:i + 1000], "Quiet": True})
        print(f"Emptied s3://{bucket}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchBucket":
            raise


def stack_outputs(cfn, name: str):
    """Outputs of a stack, or None if it does not exist."""
    try:
        stack = cfn.describe_stacks(StackName=name)["Stacks"][0]
    except ClientError:
        print(f"{name}: not found")
        return None
    return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}


def wait_delete(cfn, name: str) -> None:
    print(f"Deleting {name}...")
    cfn.delete_stack(StackName=name)
    cfn.get_waiter("stack_delete_complete").wait(StackName=name, WaiterConfig={"Delay": 15, "MaxAttempts": 240})
    print(f"{name}: deleted")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--region", required=True)
    p.add_argument("--profile")
    p.add_argument("--app-name", default="cloudops")
    p.add_argument("--delete-data", action="store_true", help="Also delete the tickets and audit tables")
    p.add_argument("--yes", action="store_true", help="Do not ask for confirmation")
    args = p.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    account = session.client("sts").get_caller_identity()["Account"]
    if not args.yes:
        answer = input(f"Delete CloudOps '{args.app_name}' from account {account} in {args.region}? [y/N] ")
        if answer.strip().lower() != "y":
            sys.exit("Aborted.")

    cfn, s3 = session.client("cloudformation"), session.client("s3")
    app_stack, foundation_stack = f"{args.app_name}-app", f"{args.app_name}-foundation"

    if stack_outputs(cfn, app_stack) is not None:
        wait_delete(cfn, app_stack)

    outputs = stack_outputs(cfn, foundation_stack)
    if outputs is not None:
        for key in ("ArtifactBucketName", "KnowledgeBucketName"):
            if outputs.get(key):
                empty_bucket(s3, outputs[key])
        if outputs.get("UserPoolId"):
            try:
                session.client("cognito-idp").update_user_pool(
                    UserPoolId=outputs["UserPoolId"], DeletionProtection="INACTIVE")
            except ClientError as e:
                print(f"Could not disable user pool deletion protection: {e}")
        wait_delete(cfn, foundation_stack)

    sm = session.client("secretsmanager")
    for name in ("cross-account-external-id", "origin-verify", "console-cookie-secret",
                 "console-oidc-client-secret"):
        try:
            sm.delete_secret(SecretId=f"{args.app_name}/{name}", ForceDeleteWithoutRecovery=True)
        except ClientError:
            pass

    ddb = session.client("dynamodb")
    for table in (f"{args.app_name}-tickets", f"{args.app_name}-audit"):
        if args.delete_data:
            try:
                ddb.delete_table(TableName=table)
                print(f"Deleted table {table}")
            except ClientError:
                pass
        else:
            print(f"Kept table {table} (use --delete-data to remove it)")
    print("Done.")


if __name__ == "__main__":
    main()
