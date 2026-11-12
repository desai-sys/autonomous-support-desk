"""
Every tool is run once against simulated AWS.

Most tools had never been executed by any test, and several could not have
worked at all (a method that doesn't exist, a wrong parameter name, `self`
outside a class). This test fails if any tool raises a programming error or
sends AWS a parameter the API doesn't have; see tests/tool_smoke.py for how
that is detected. It does not prove a tool's answer is right, only that the
tool runs.
"""
import logging

import pytest

from tests import tool_smoke


@pytest.fixture(scope="module")
def report():
    logging.disable(logging.CRITICAL)
    try:
        return tool_smoke.run_all()
    finally:
        logging.disable(logging.NOTSET)


def test_every_registered_tool_was_run(report):
    import src.agentcore_entrypoint as ep
    assert set(report) == set(ep.TOOL_REGISTRY) and len(report) > 280


def test_no_tool_has_a_programming_error(report):
    problems = {name: r["raised"] for name, r in report.items() if r["raised"]}
    assert problems == {}, "\n".join(f"{n}: {kind} in {where}: {msg}"
                                     for n, errs in problems.items() for kind, msg, where in errs)


def test_tools_report_errors_instead_of_raising(report):
    escaped = {name: r["escaped"] for name, r in report.items() if r["escaped"]}
    assert escaped == {}


def test_most_tools_complete_against_the_simulator(report):
    """Guards the harness itself: if seeding breaks, almost everything would 'pass' by failing early."""
    completed = [n for n, r in report.items()
                 if not r["output"].startswith(("Error", "Failed")) and '"status": "failed"' not in r["output"][:200]]
    assert len(completed) >= 150
