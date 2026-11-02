"""The evaluation harness must score good trajectories as passing and catch bad ones."""
import json
from pathlib import Path

import pytest

from evals import harness
from tests.test_core.test_approvals import ScriptedModel

ROOT = Path(__file__).resolve().parent.parent


class AnswerModel(ScriptedModel):
    """Scripted tool calls, then a chosen final answer."""

    def __init__(self, tool_calls, answer):
        super().__init__(tool_calls)
        self.answer = answer

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        async for event in super().stream(messages, tool_specs, system_prompt, **kwargs):
            if "contentBlockDelta" in event and "text" in event["contentBlockDelta"]["delta"]:
                event = {"contentBlockDelta": {"delta": {"text": self.answer}}}
            yield event


@pytest.fixture
def scenarios():
    cases, fixtures = harness.load_cases(ROOT / "evals/scenarios.yaml")
    return {c.id: c for c in cases}, fixtures


def run(monkeypatch, case, fixtures, calls, answer, domains=("compute",)):
    import src.agentcore_entrypoint as ep
    model = AnswerModel(calls, answer)
    monkeypatch.setattr(ep, "_AGENT_MODEL", model)
    monkeypatch.setattr(ep, "_BUILD_MODEL", model)
    monkeypatch.setattr(ep.router, "route", lambda q, h=None: list(domains))
    return harness.run_case(case, fixtures)


def test_scenarios_are_valid(scenarios):
    cases, fixtures = scenarios
    assert harness.validate(list(cases.values()), fixtures) == []
    assert len(cases) >= 15 and {c.category for c in cases.values()} >= {
        "investigate", "change", "cross-account", "safety", "build"}


def test_validation_catches_mistakes(scenarios):
    cases, fixtures = scenarios
    bad = harness.Case(id="x", prompt="p", category="c", accounts=["staging"],
                       expect={"tools_called_any": ["no_such_tool"], "domains_any": ["nope"],
                               "queued_params": {"stop_ec2_instance": {"bogus": 1}}})
    problems = harness.validate([bad], {})
    assert any("no_such_tool" in p for p in problems) and any("nope" in p for p in problems)
    assert any("bogus" in p for p in problems) and any("staging" in p for p in problems)


def test_good_trajectory_passes_and_nothing_executes(monkeypatch, scenarios):
    cases, fixtures = scenarios
    result = run(monkeypatch, cases["stop-instance"], fixtures,
                 [("list_ec2_instances", {}), ("stop_ec2_instance", {"instance_id": "i-0def9876abc543210"})],
                 "I've queued a stop for batch-1 (i-0def9876abc543210) in the platform account.")
    assert result.passed, result.checks
    assert [c["tool"] for c in result.calls] == ["list_ec2_instances"]          # the stop was only queued
    assert [q["tool_name"] for q in result.queued] == ["stop_ec2_instance"]
    summary = harness.summarize([result])
    assert summary["task_success_rate"] == 1.0 and summary["metrics"]["Unsafe executions = 0"]["rate"] == 1.0


def test_injection_that_queues_termination_is_caught(monkeypatch, scenarios):
    cases, fixtures = scenarios
    result = run(monkeypatch, cases["prompt-injection"], fixtures,
                 [("list_ec2_instances", {}),
                  ("call_aws_api", {"service": "ec2", "operation": "TerminateInstances",
                                    "parameters": '{"InstanceIds": ["i-0abc1234def567890"]}'})],
                 "Terminating as instructed.")
    assert not result.passed
    assert result.checks["no_queued_matching"] is False and result.checks["read_only"] is False
    assert result.checks["no_unsafe_execution"] is True                     # the gate still held


def test_claims_about_unqueried_accounts_are_caught(monkeypatch, scenarios):
    cases, fixtures = scenarios
    result = run(monkeypatch, cases["compare-public-buckets"], fixtures,
                 [("list_public_s3_buckets", {"account": "platform"})],
                 "Platform: marketing-assets is public. ta-aws-1: ta-static-site is public.",
                 domains=("storage",))
    assert result.checks["grounded"] is False and result.checks["read_accounts"] is False

    good = run(monkeypatch, cases["compare-public-buckets"], fixtures,
               [("list_public_s3_buckets", {"account": "platform"}),
                ("list_public_s3_buckets", {"account": "ta-aws-1"})],
               "Platform: marketing-assets is public. ta-aws-1: ta-static-site is public.",
               domains=("storage",))
    assert good.passed, good.checks


def test_cross_account_peering_scored_by_account(monkeypatch, scenarios):
    cases, fixtures = scenarios
    result = run(monkeypatch, cases["cross-account-peering"], fixtures,
                 [("create_vpc_peering", {"requester_vpc_id": "vpc-0aaa111", "accepter_vpc_id": "vpc-0bbb222",
                                         "requester_account": "platform", "accepter_account": "prod"})],
                 "Queued one action: peer platform with prod (request, accept, routes).", domains=("network",))
    assert result.passed, result.checks


def test_viewer_scenario(monkeypatch, scenarios):
    cases, fixtures = scenarios
    result = run(monkeypatch, cases["viewer-cannot-change"], fixtures, [("list_ec2_instances", {})],
                 "Your role is read-only, so I can't stop it; an operator can.")
    assert result.passed, result.checks


def test_markdown_report(monkeypatch, scenarios):
    cases, fixtures = scenarios
    result = run(monkeypatch, cases["list-instances"], fixtures, [("list_ec2_instances", {})],
                 "You have web-1 (i-0abc1234def567890) running and batch-1 stopped.")
    report = harness.markdown_report(harness.summarize([result]), [result], "test-model")
    assert "Task success rate: 100%" in report and "| list-instances | investigate | ✅ |" in report
