"""
Guard against `except client.exceptions.X` where X isn't an exception botocore
models for that service. Python only evaluates the except clause when an error
occurs, so such code fails at the worst moment (this broke the S3 public-access
scan: NoSuchBucketPolicy is an error code, not a modeled S3 exception).
"""
import pathlib
import re

import botocore.session

ROOT = pathlib.Path(__file__).resolve().parents[2]
_ALWAYS = {"ClientError"}   # every client exposes this


def _modeled(service, cache={}):
    if service not in cache:
        model = botocore.session.get_session().get_service_model(service)
        cache[service] = {n for n in model.shape_names if model.shape_for(n).metadata.get("exception")}
    return cache[service]


def test_every_client_exception_used_in_code_is_modeled():
    problems = []
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text()
        clients = dict(re.findall(r"(\w+)\s*=\s*boto3\.client\(\s*['\"]([\w-]+)['\"]", text))
        for var, exc in re.findall(r"(\w+)\.exceptions\.(\w+)", text):
            service = clients.get(var)
            if service and exc not in _ALWAYS and exc not in _modeled(service):
                problems.append(f"{path.relative_to(ROOT)}: {service} has no modeled exception {exc}")
    assert not problems, "Use the error code instead (e.response['Error']['Code']):\n" + "\n".join(sorted(set(problems)))
