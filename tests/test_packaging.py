"""The agent's dependency lock must match requirements/agent.txt and be host-independent."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _pins(path):
    pins = {}
    for line in (ROOT / path).read_text().splitlines():
        m = re.match(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)", line)
        if m:
            pins[m.group(1).lower().replace("_", "-")] = m.group(2)
    return pins


def test_lock_contains_every_direct_pin_at_the_same_version():
    lock = _pins("requirements/agent.lock.txt")
    for name, version in _pins("requirements/agent.txt").items():
        assert lock.get(name) == version, (
            f"{name}=={version} is not in requirements/agent.lock.txt; regenerate it "
            "(python scripts/update_deps.py --lock-only)")


def test_lock_is_resolved_for_the_target_not_the_host():
    text = (ROOT / "requirements/agent.lock.txt").read_text()
    requirement_lines = [l for l in text.splitlines() if l and not l.startswith((" ", "#"))]
    assert not [l for l in requirement_lines if ";" in l], "lock must not contain environment markers"
    assert "pywin32" not in text
    assert all("--hash=sha256:" in text.split(l, 1)[1].split("\n", 3)[1] for l in requirement_lines)


def test_agent_and_console_pin_the_same_aws_sdk():
    agent, console = _pins("requirements/agent.txt"), _pins("requirements/console.txt")
    for name in ("boto3", "botocore"):
        assert agent[name] == console[name], f"{name} differs between agent.txt and console.txt"


def test_update_deps_reads_pypi_and_rewrites_pins():
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import update_deps

    fake = {"info": {"version": "1.50.2", "requires_dist": [
        "botocore<1.51.0,>=1.50.2", "jmespath<2.0.0,>=0.7.1", "s3transfer<0.20.0,>=0.19.0"]}}
    assert update_deps.latest_sdk_versions(lambda url: fake) == {"boto3": "1.50.2", "botocore": "1.50.2"}
    text = "strands-agents==1.0\nboto3==1.43.123\nbotocore==1.43.123\n"
    assert update_deps.current_pins(text) == {"boto3": "1.43.123", "botocore": "1.43.123"}
    assert update_deps.apply_pins(text, {"boto3": "1.50.2", "botocore": "1.50.2"}) == \
        "strands-agents==1.0\nboto3==1.50.2\nbotocore==1.50.2\n"
