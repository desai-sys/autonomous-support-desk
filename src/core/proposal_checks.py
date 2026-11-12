"""
Checks on a mutating tool call before it is queued for approval.

The prompt asks the model to use the right tool and sensible inputs, but models
don't always follow it. These checks refuse proposals that are known to fail or
to cause trouble later, with an instruction the model can act on in the same
turn. Each returns a message (refuse) or None (queue it).
"""

import ipaddress
import json
from typing import Callable, List, Optional

_PEERING_OPS = {"createvpcpeeringconnection", "acceptvpcpeeringconnection"}


def _api_call(name: str, tool_input: dict):
    """(service, normalized operation, params) for call_aws_api, else None."""
    if name != "call_aws_api":
        return None
    params = tool_input.get("parameters") or {}
    if isinstance(params, str):
        try:
            params = json.loads(params or "{}")
        except ValueError:
            params = {}
    op = str(tool_input.get("operation", "")).replace("_", "").replace("-", "").lower()
    return str(tool_input.get("service", "")).lower(), op, params if isinstance(params, dict) else {}


def peering_uses_workflow_tool(name: str, tool_input: dict, run: "ProposalChecks") -> Optional[str]:
    call = _api_call(name, tool_input)
    if call and call[0] == "ec2" and call[1] in _PEERING_OPS:
        return ("Use create_vpc_peering instead of call_aws_api for VPC peering. It requests the "
                "peering in the requester account, accepts it in the accepter account and adds routes, "
                "as one approval. Pass requester_vpc_id, accepter_vpc_id, requester_account and "
                "accepter_account.")
    return None


def vpc_cidrs_do_not_overlap(name: str, tool_input: dict, run: "ProposalChecks") -> Optional[str]:
    call = _api_call(name, tool_input)
    if not (call and call[0] == "ec2" and call[1] == "createvpc"):
        return None
    cidr = call[2].get("CidrBlock")
    try:
        net = ipaddress.ip_network(cidr)
    except (TypeError, ValueError):
        return None
    for other in run.vpc_cidrs:
        if net.overlaps(other):
            used = ", ".join(str(n) for n in run.vpc_cidrs)
            return (f"{cidr} overlaps {other}, already proposed for another VPC in this request. "
                    f"Overlapping VPCs can never be peered. Choose a range that doesn't overlap "
                    f"{used} (e.g. 10.{max(n.network_address.packed[1] for n in run.vpc_cidrs) + 1}.0.0/16) "
                    f"and propose it again.")
    run.vpc_cidrs.append(net)
    return None


def platform_is_off_limits(name: str, tool_input: dict, run: "ProposalChecks") -> Optional[str]:
    """The agent may not change the platform that controls it (users, audit, its own runtime)."""
    from src.tools.aws_api_tools import platform_resource_in
    internal = platform_resource_in(json.dumps(tool_input, default=str))
    if internal:
        return (f"{internal} belongs to the CloudOps platform itself (its users, audit trail, approvals "
                f"or runtime). The agent cannot change it; an administrator must do that directly in AWS.")
    return None


DEFAULT_CHECKS: List[Callable] = [platform_is_off_limits, peering_uses_workflow_tool,
                                  vpc_cidrs_do_not_overlap]


class ProposalChecks:
    """Per-run state for the checks (e.g. VPC ranges proposed so far in this run)."""

    def __init__(self, checks: Optional[List[Callable]] = None):
        self.checks = DEFAULT_CHECKS if checks is None else checks
        self.vpc_cidrs: list = []

    def problem(self, name: str, tool_input: dict) -> Optional[str]:
        for check in self.checks:
            message = check(name, tool_input, self)
            if message:
                return message
        return None
