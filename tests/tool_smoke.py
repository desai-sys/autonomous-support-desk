"""
Run every tool once against simulated AWS and report programming errors.

Tools catch their own exceptions and return an error string, so a test that
only checks "it returned something" proves nothing. This harness watches the
exceptions actually raised inside the tool code while it runs (sys.monitoring)
and classifies them: a KeyError, TypeError, AttributeError, NameError or a
botocore parameter-validation error is a bug in the tool (botocore validates
parameters before any request is sent, so that check is exact even where the
simulator doesn't implement the service); AWS errors such as "not found" are
expected for the made-up inputs.

Used by tests/test_tools/test_all_tools_smoke.py; also runnable directly:
    python tests/tool_smoke.py [tool_name ...]
"""

import inspect
import io
import json
import os
import signal
import sys
import zipfile
from typing import Dict, List

BUG_TYPES = ("KeyError", "TypeError", "AttributeError", "NameError", "UnboundLocalError", "IndexError",
             "ParamValidationError", "ZeroDivisionError", "ImportError", "ModuleNotFoundError",
             "UnknownServiceError", "RecursionError")
TOOL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "tools")


def seed() -> Dict[str, str]:
    """Create one of each common resource so tools have something real to look at."""
    from src.core.aws_clients import boto3
    ids: Dict[str, str] = {}
    ec2 = boto3.client("ec2", region_name="us-east-1")
    vpc = ec2.create_vpc(CidrBlock="10.50.0.0/16")["Vpc"]["VpcId"]
    subnet = ec2.create_subnet(VpcId=vpc, CidrBlock="10.50.1.0/24", AvailabilityZone="us-east-1a")["Subnet"]["SubnetId"]
    subnet2 = ec2.create_subnet(VpcId=vpc, CidrBlock="10.50.2.0/24", AvailabilityZone="us-east-1b")["Subnet"]["SubnetId"]
    sg = ec2.create_security_group(GroupName="smoke-sg", Description="smoke", VpcId=vpc)["GroupId"]
    instance = ec2.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.micro",
                                 SubnetId=subnet, SecurityGroupIds=[sg])["Instances"][0]
    volume = ec2.create_volume(AvailabilityZone="us-east-1a", Size=8)["VolumeId"]
    snapshot = ec2.create_snapshot(VolumeId=volume)["SnapshotId"]
    eip = ec2.allocate_address(Domain="vpc")["AllocationId"]
    ec2.create_key_pair(KeyName="smoke-key")
    route_table = ec2.create_route_table(VpcId=vpc)["RouteTable"]["RouteTableId"]
    ids.update(vpc=vpc, subnet=subnet, subnet2=subnet2, sg=sg, instance=instance["InstanceId"], volume=volume,
               snapshot=snapshot, eip=eip, ami=instance["ImageId"], route_table=route_table)

    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="smoke-bucket")
    s3.put_object(Bucket="smoke-bucket", Key="index.html", Body=b"<html></html>")
    s3.create_bucket(Bucket=os.environ.get("ARTIFACT_BUCKET", "cloudops-artifacts-test"))
    ids["bucket"] = "smoke-bucket"

    iam = boto3.client("iam")
    trust = json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {
        "Service": ["lambda.amazonaws.com", "ecs-tasks.amazonaws.com"]}, "Action": "sts:AssumeRole"}]})
    role = iam.create_role(RoleName="smoke-role", AssumeRolePolicyDocument=trust)["Role"]["Arn"]
    iam.create_user(UserName="smoke-user")
    iam.create_group(GroupName="smoke-group")
    policy = iam.create_policy(PolicyName="smoke-policy", PolicyDocument=json.dumps({
        "Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "s3:ListBucket", "Resource": "*"}]}))
    ids.update(role="smoke-role", role_arn=role, user="smoke-user", policy_arn=policy["Policy"]["Arn"])

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("lambda_function.py", "def lambda_handler(event, context):\n    return {'ok': True}\n")
    boto3.client("lambda", region_name="us-east-1").create_function(
        FunctionName="smoke-fn", Runtime="python3.12", Role=role, Handler="lambda_function.lambda_handler",
        Code={"ZipFile": buf.getvalue()})
    ids["function"] = "smoke-fn"

    boto3.client("dynamodb", region_name="us-east-1").create_table(
        TableName="smoke-table", BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}])
    ids["table"] = "smoke-table"

    boto3.client("rds", region_name="us-east-1").create_db_instance(
        DBInstanceIdentifier="smoke-db", DBInstanceClass="db.t3.micro", Engine="postgres",
        MasterUsername="admin", MasterUserPassword="Passw0rd-long", AllocatedStorage=20)
    ids["db"] = "smoke-db"

    ecs = boto3.client("ecs", region_name="us-east-1")
    ecs.create_cluster(clusterName="smoke-cluster")
    ecs.register_task_definition(family="smoke-task", networkMode="awsvpc", requiresCompatibilities=["FARGATE"],
                                 cpu="256", memory="512",
                                 containerDefinitions=[{"name": "app", "image": "nginx", "essential": True}])
    ecs.create_service(cluster="smoke-cluster", serviceName="smoke-service", taskDefinition="smoke-task",
                       desiredCount=1, launchType="FARGATE",
                       networkConfiguration={"awsvpcConfiguration": {"subnets": [subnet], "securityGroups": [sg]}})
    ids.update(cluster="smoke-cluster", service="smoke-service", task_definition="smoke-task")

    logs = boto3.client("logs", region_name="us-east-1")
    logs.create_log_group(logGroupName="/smoke/group")
    logs.create_log_stream(logGroupName="/smoke/group", logStreamName="stream")
    boto3.client("cloudwatch", region_name="us-east-1").put_metric_alarm(
        AlarmName="smoke-alarm", MetricName="CPUUtilization", Namespace="AWS/EC2", Statistic="Average",
        Period=300, EvaluationPeriods=1, Threshold=80, ComparisonOperator="GreaterThanThreshold")
    ids.update(log_group="/smoke/group", log_stream="stream", alarm="smoke-alarm")

    ids["topic_arn"] = boto3.client("sns", region_name="us-east-1").create_topic(Name="smoke-topic")["TopicArn"]
    ids["queue_url"] = boto3.client("sqs", region_name="us-east-1").create_queue(QueueName="smoke-queue")["QueueUrl"]
    ids["key_id"] = boto3.client("kms", region_name="us-east-1").create_key()["KeyMetadata"]["KeyId"]
    boto3.client("secretsmanager", region_name="us-east-1").create_secret(Name="smoke-secret", SecretString="x")
    boto3.client("ssm", region_name="us-east-1").put_parameter(Name="/smoke/param", Value="v", Type="String")
    boto3.client("ecr", region_name="us-east-1").create_repository(repositoryName="smoke-repo")
    elb = boto3.client("elbv2", region_name="us-east-1")
    lb = elb.create_load_balancer(Name="smoke-lb", Subnets=[subnet, subnet2], SecurityGroups=[sg])["LoadBalancers"][0]
    tg = elb.create_target_group(Name="smoke-tg", Protocol="HTTP", Port=80, VpcId=vpc)["TargetGroups"][0]
    ids.update(lb_arn=lb["LoadBalancerArn"], lb_name="smoke-lb", tg_arn=tg["TargetGroupArn"])
    boto3.client("autoscaling", region_name="us-east-1").create_launch_configuration(
        LaunchConfigurationName="smoke-lc", ImageId="ami-12345678", InstanceType="t3.micro")
    boto3.client("autoscaling", region_name="us-east-1").create_auto_scaling_group(
        AutoScalingGroupName="smoke-asg", LaunchConfigurationName="smoke-lc", MinSize=0, MaxSize=1,
        VPCZoneIdentifier=subnet)
    boto3.client("cloudformation", region_name="us-east-1").create_stack(
        StackName="smoke-stack", TemplateBody=json.dumps({"Resources": {"T": {"Type": "AWS::SNS::Topic"}}}))
    zone = boto3.client("route53").create_hosted_zone(Name="smoke.example.com", CallerReference="smoke")
    ids.update(asg="smoke-asg", stack="smoke-stack", zone=zone["HostedZone"]["Id"].split("/")[-1],
               secret="smoke-secret", parameter="/smoke/param", repository="smoke-repo")
    return ids


# Parameter name (substring match, first hit wins) -> seeded value key or literal.
_BY_NAME = [
    ("region", "=us-east-1"), ("availability_zone", "=us-east-1a"),
    ("instance_ids", "instance"), ("db_instance", "db"), ("db_identifier", "db"), ("db_name", "db"),
    ("instance_id", "instance"), ("instance_type", "=t3.micro"), ("instance_class", "=db.t3.micro"),
    ("bucket", "bucket"), ("function", "function"), ("table", "table"), ("vpc", "vpc"),
    ("subnet_ids", "subnet"), ("subnet", "subnet"), ("security_group", "sg"), ("group_id", "sg"), ("sg_id", "sg"),
    ("volume", "volume"), ("snapshot", "snapshot"), ("allocation", "eip"), ("image_id", "ami"), ("ami", "ami"),
    ("route_table", "route_table"), ("role_arn", "role_arn"), ("role", "role"), ("user", "user"),
    ("policy_arn", "policy_arn"), ("cluster", "cluster"), ("service_name", "service"), ("service", "service"),
    ("task_def", "task_definition"), ("log_group", "log_group"), ("log_stream", "log_stream"),
    ("alarm", "alarm"), ("topic", "topic_arn"), ("queue", "queue_url"), ("key_id", "key_id"),
    ("secret", "secret"), ("parameter_name", "parameter"), ("repository", "repository"),
    ("load_balancer_arn", "lb_arn"), ("lb_arn", "lb_arn"), ("target_group", "tg_arn"),
    ("load_balancer", "lb_name"), ("auto_scaling", "asg"), ("asg", "asg"), ("stack", "stack"),
    ("hosted_zone", "zone"), ("zone_id", "zone"), ("cidr", "=10.60.0.0/16"), ("port", "=443"),
    ("protocol", "=tcp"), ("runtime", "=python3.12"), ("handler", "=lambda_function.lambda_handler"),
    ("namespace", "=AWS/EC2"), ("metric", "=CPUUtilization"), ("email", "=ops@example.com"),
    ("account_id", "=123456789012"), ("artifact_uri", "=s3://cloudops-artifacts-test/uploads/t/a/app.zip"),
    ("start_date", "=2026-01-01"), ("end_date", "=2026-01-08"), ("date", "=2026-01-01"),
    ("days", "=7"), ("hours", "=1"), ("minutes", "=30"), ("limit", "=10"), ("count", "=1"),
    ("size", "=8"), ("memory", "=256"), ("timeout", "=30"), ("threshold", "=80"), ("capacity", "=1"),
    ("tags", "={}"), ("json", "={}"), ("policy_document", '={"Version": "2012-10-17", "Statement": []}'),
    ("payload", "={}"), ("dimensions", "=[]"), ("environment", "={}"), ("name", "=smoke-new"),
]


def arguments(tool, ids: Dict[str, str]) -> dict:
    schema = tool.tool_spec["inputSchema"]["json"]
    kwargs = {}
    for name in schema.get("required", []):
        spec = schema["properties"].get(name, {})
        kind = spec.get("type", "string")
        value = None
        for needle, source in _BY_NAME:
            if needle in name.lower():
                value = source[1:] if source.startswith("=") else ids.get(source, "smoke")
                break
        if value is None:
            value = "smoke"
        if kind == "integer":
            value = int(value) if str(value).lstrip("-").isdigit() else 1
        elif kind == "number":
            value = float(value) if str(value).replace(".", "", 1).isdigit() else 1.0
        elif kind == "boolean":
            value = False
        elif kind == "array":
            value = [value]
        elif kind == "object":
            value = {}
        kwargs[name] = value
    return kwargs


class _Timeout(Exception):
    pass


def _raised_inside_simulator(exc: BaseException) -> bool:
    """True if the exception started inside moto (a simulator gap, not the tool's code)."""
    tb, origin = exc.__traceback__, ""
    while tb is not None:
        origin = tb.tb_frame.f_code.co_filename
        tb = tb.tb_next
    return f"{os.sep}moto{os.sep}" in origin


def run_tool(tool, kwargs: dict, seconds: int = 20) -> dict:
    """Call one tool; returns {'output', 'raised': [(type, message, file:line)], 'escaped'}."""
    raised: List[tuple] = []
    mon = sys.monitoring
    tool_id = mon.DEBUGGER_ID

    def on_raise(code, offset, exc):
        name = type(exc).__name__
        if name not in BUG_TYPES:
            return
        if name == "ParamValidationError":
            # Wrong parameter names or types are tool bugs; a made-up value that is too
            # short or badly formatted is just this harness's input.
            if not any(m in str(exc) for m in ("Unknown parameter", "Missing required parameter",
                                                "Invalid type for parameter")):
                return
        elif not code.co_filename.startswith(TOOL_DIR) or _raised_inside_simulator(exc):
            return
        entry = (name, str(exc)[:200], f"{os.path.basename(code.co_filename)}:{code.co_name}")
        if not raised or raised[-1][:2] != entry[:2]:
            raised.append(entry)

    def on_alarm(*_):
        raise _Timeout()

    mon.use_tool_id(tool_id, "tool-smoke")
    mon.register_callback(tool_id, mon.events.RAISE, on_raise)
    mon.set_events(tool_id, mon.events.RAISE)
    previous = signal.signal(signal.SIGALRM, on_alarm)
    signal.alarm(seconds)
    escaped = None
    output = ""
    try:
        output = tool(**kwargs)
    except _Timeout:
        escaped = f"timed out after {seconds}s"
    except Exception as e:                      # a tool must report errors, not raise them
        escaped = f"{type(e).__name__}: {e}"
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
        mon.set_events(tool_id, 0)
        mon.register_callback(tool_id, mon.events.RAISE, None)
        mon.free_tool_id(tool_id)
    return {"output": output if isinstance(output, str) else json.dumps(output, default=str),
            "raised": raised, "escaped": escaped}


def run_all(names=None) -> Dict[str, dict]:
    import time
    from unittest import mock
    from moto import mock_aws
    os.environ.update(AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing",
                      AWS_DEFAULT_REGION="us-east-1")
    os.environ.setdefault("ARTIFACT_BUCKET", "cloudops-artifacts-test")
    import src.agentcore_entrypoint as ep
    report = {}
    with mock_aws(config={"iam": {"load_aws_managed_policies": True}}), mock.patch.object(time, "sleep", lambda s: None):
        ids = seed()
        for name, tool in sorted(ep.TOOL_REGISTRY.items()):
            if names and name not in names:
                continue
            kwargs = arguments(tool, ids)
            report[name] = {"kwargs": kwargs, **run_tool(tool, kwargs)}
    return report


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    results = run_all(set(sys.argv[1:]) or None)
    bad = {n: r for n, r in results.items() if r["raised"] or r["escaped"]}
    for name, r in bad.items():
        print(f"\n{name}({r['kwargs']})")
        if r["escaped"]:
            print("   ESCAPED:", r["escaped"])
        for kind, message, where in r["raised"]:
            print(f"   {kind} in {where}: {message}")
    print(f"\n{len(results)} tools run, {len(bad)} with problems")
