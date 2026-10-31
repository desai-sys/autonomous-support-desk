"""
Provisioning tools: create new resources with sensible defaults.

Like every mutating tool, these are queued for operator approval by the
ApprovalGate and only run once approved.
"""

import json
import re
import time

from strands import tool
import structlog

from src.core.aws_clients import boto3
from src.tools.artifact_tools import EC2_SSM_ROLE_NAME, _ensure_ec2_ssm_profile
from src.tools.deployment_tools import _build_manifest, _generate_default_name

logger = structlog.get_logger()

# Friendly OS names -> public SSM parameters that always point at the latest AMI.
# {arch} is "x86_64"/"amd64" or "arm64" depending on the instance type.
AMI_ALIASES = {
    "amazon-linux-2023": ("/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-{arch}",
                          {"x86": "x86_64", "arm": "arm64"}),
    "ubuntu-24.04": ("/aws/service/canonical/ubuntu/server/24.04/stable/current/{arch}/hvm/ebs-gp3/ami-id",
                     {"x86": "amd64", "arm": "arm64"}),
    "ubuntu-22.04": ("/aws/service/canonical/ubuntu/server/22.04/stable/current/{arch}/hvm/ebs-gp2/ami-id",
                     {"x86": "amd64", "arm": "arm64"}),
    "windows-2022": ("/aws/service/ami-windows-latest/Windows_Server-2022-English-Full-Base",
                     {"x86": "", "arm": None}),
}


def _is_arm(instance_type: str) -> bool:
    return bool(re.match(r"^[a-z]+\d+g", instance_type.split(".", 1)[0]))


def _resolve_ami(ami: str, instance_type: str, region: str) -> str:
    ami = (ami or "amazon-linux-2023").strip()
    if ami.startswith("ami-"):
        return ami
    if ami not in AMI_ALIASES:
        raise ValueError(f"Unknown AMI '{ami}'. Use an ami-... id or one of: {', '.join(AMI_ALIASES)}.")
    template, archs = AMI_ALIASES[ami]
    arch = archs["arm" if _is_arm(instance_type) else "x86"]
    if arch is None:
        raise ValueError(f"{ami} has no ARM image; choose an x86 instance type such as t3.micro.")
    name = template.format(arch=arch)
    return boto3.client("ssm", region_name=region).get_parameter(Name=name)["Parameter"]["Value"]


@tool
def launch_ec2_instance(
    name: str = '',
    instance_type: str = 't3.micro',
    ami: str = 'amazon-linux-2023',
    count: int = 1,
    subnet_id: str = '',
    security_group_ids: str = '',
    key_name: str = '',
    volume_size_gb: int = 0,
    enable_ssm: bool = True,
    user_data: str = '',
    region: str = 'us-east-1',
) -> str:
    """Launch new EC2 instance(s). Every parameter has a sensible default, so ask the operator only for what matters to them.

    Args:
        name: Name tag. If empty, a default name is generated.
        instance_type: e.g. t3.micro (default), t3.small, m7i.large, t4g.small (ARM).
        ami: An ami-... id, or one of amazon-linux-2023 (default), ubuntu-24.04, ubuntu-22.04, windows-2022.
        count: Number of identical instances (1-10).
        subnet_id: Subnet to launch into. Empty = default VPC.
        security_group_ids: Comma-separated security group ids. Empty = the VPC's default group.
        key_name: EC2 key pair name for SSH/RDP. Empty = no key pair (use SSM Session Manager instead).
        volume_size_gb: Root volume size in GiB (gp3). 0 = the AMI's default size.
        enable_ssm: Attach the CloudOpsEC2SSMRole instance profile so the instance is manageable through SSM.
        user_data: Optional startup script (plain text).
        region: AWS region.

    Returns:
        JSON deployment manifest with the instance ids, or error details.
    """
    try:
        count = max(1, min(int(count), 10))
        name = name or _generate_default_name()
        ec2 = boto3.client("ec2", region_name=region)
        ami_id = _resolve_ami(ami, instance_type, region)

        params = {
            "ImageId": ami_id,
            "InstanceType": instance_type,
            "MinCount": count,
            "MaxCount": count,
            "MetadataOptions": {"HttpTokens": "required", "HttpEndpoint": "enabled"},
            "TagSpecifications": [
                {"ResourceType": "instance",
                 "Tags": [{"Key": "Name", "Value": name}, {"Key": "ManagedBy", "Value": "CloudOps"}]},
                {"ResourceType": "volume",
                 "Tags": [{"Key": "Name", "Value": name}, {"Key": "ManagedBy", "Value": "CloudOps"}]},
            ],
        }
        if subnet_id:
            params["SubnetId"] = subnet_id.strip()
        groups = [g.strip() for g in security_group_ids.split(",") if g.strip()]
        if groups:
            params["SecurityGroupIds"] = groups
        if key_name:
            params["KeyName"] = key_name.strip()
        if user_data:
            params["UserData"] = user_data
        if volume_size_gb:
            root = ec2.describe_images(ImageIds=[ami_id])["Images"][0]["RootDeviceName"]
            params["BlockDeviceMappings"] = [{"DeviceName": root, "Ebs": {
                "VolumeSize": int(volume_size_gb), "VolumeType": "gp3",
                "DeleteOnTermination": True, "Encrypted": True}}]

        profile_created = False
        if enable_ssm:
            profile_created = _ensure_ec2_ssm_profile()
            params["IamInstanceProfile"] = {"Name": EC2_SSM_ROLE_NAME}

        # A new instance profile takes a few seconds before EC2 accepts it.
        attempts = 8 if profile_created else 1
        for attempt in range(attempts):
            try:
                instances = ec2.run_instances(**params)["Instances"]
                break
            except Exception as e:
                if "instance profile" not in str(e).lower() or attempt == attempts - 1:
                    raise
                time.sleep(5)

        ids = [i["InstanceId"] for i in instances]
        try:
            ec2.get_waiter("instance_running").wait(InstanceIds=ids,
                                                    WaiterConfig={"Delay": 5, "MaxAttempts": 36})
            state = "running"
        except Exception:
            state = "pending"
        described = ec2.describe_instances(InstanceIds=ids)["Reservations"]
        details = [
            {"instance_id": i["InstanceId"], "state": i["State"]["Name"],
             "private_ip": i.get("PrivateIpAddress"), "public_ip": i.get("PublicIpAddress"),
             "subnet_id": i.get("SubnetId"), "availability_zone": i["Placement"]["AvailabilityZone"]}
            for r in described for i in r["Instances"]
        ]
        account_id = boto3.client("sts", region_name=region).get_caller_identity()["Account"]
        return _build_manifest(
            target_type="ec2",
            status="success",
            resource_arn=f"arn:aws:ec2:{region}:{account_id}:instance/{ids[0]}",
            region=region,
            details={"action": "launched", "name": name, "instance_type": instance_type,
                     "ami_id": ami_id, "state": state, "instances": details,
                     "ssm_managed": enable_ssm, "key_name": key_name or None},
            rollback_info={"instance_ids": ids, "note": "Terminate the instances to roll back."},
        )
    except ValueError as e:
        return json.dumps({"status": "failed", "error_type": "invalid_parameters",
                           "error_message": str(e), "suggestion": "Ask the operator to choose a valid value."})
    except Exception as e:
        logger.error("launch_ec2_instance_failed", error=str(e))
        return json.dumps({
            "status": "failed", "error_type": "aws_api_error",
            "error_message": f"Failed to launch EC2 instance: {e}",
            "suggestion": "Check the subnet/security group/key pair exist in this region and account, "
                          "the instance type is offered in the AZ, and service quotas allow more vCPUs.",
        })
