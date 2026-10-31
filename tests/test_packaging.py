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
            "(see the comment at the top of requirements/agent.txt)")


def test_lock_is_resolved_for_the_target_not_the_host():
    text = (ROOT / "requirements/agent.lock.txt").read_text()
    requirement_lines = [l for l in text.splitlines() if l and not l.startswith((" ", "#"))]
    assert not [l for l in requirement_lines if ";" in l], "lock must not contain environment markers"
    assert "pywin32" not in text
    assert all("--hash=sha256:" in text.split(l, 1)[1].split("\n", 3)[1] for l in requirement_lines)
