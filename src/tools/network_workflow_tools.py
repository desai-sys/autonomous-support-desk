"""
Multi-step network workflows that must happen together, possibly across accounts.

A VPC peering is "request in one account, accept in the other". The accept step
needs the connection id that only exists after the request, so queuing the two
steps separately can't work. ``create_vpc_peering`` does the whole workflow as
one approval-gated action, assuming the right role for each step.
"""

import ipaddress
import json
import time
from typing import Optional

from strands import tool
import structlog

from src.core.account_scope import UnknownAccount, resolve_account_ref
from src.core.aws_clients import base_account_id, boto3, use_account

logger = structlog.get_logger()


def _fail(error_type: str, message: str, suggestion: str = "", **extra) -> str:
    return json.dumps({"status": "failed", "error_type": error_type, "error_message": message,
                       "suggestion": suggestion, **extra})


def _vpc(account: Optional[str], region: str, vpc_id: str) -> dict:
    with use_account(account, region):
        vpcs = boto3.client("ec2", region_name=region).describe_vpcs(VpcIds=[vpc_id])["Vpcs"]
    return vpcs[0]


def _cidrs(vpc: dict) -> list:
    blocks = [a["CidrBlock"] for a in vpc.get("CidrBlockAssociationSet", [])
              if a.get("CidrBlockState", {}).get("State", "associated") == "associated"]
    return blocks or [vpc["CidrBlock"]]


def _peering_status(account: Optional[str], region: str, pcx_id: str) -> str:
    with use_account(account, region):
        conns = boto3.client("ec2", region_name=region).describe_vpc_peering_connections(
            VpcPeeringConnectionIds=[pcx_id])["VpcPeeringConnections"]
    return conns[0]["Status"]["Code"] if conns else "missing"


def _wait_for(account, region, pcx_id, wanted: set, attempts: int = 20) -> str:
    status = "unknown"
    for _ in range(attempts):
        status = _peering_status(account, region, pcx_id)
        if status in wanted or status in ("failed", "rejected", "deleted", "expired"):
            return status
        time.sleep(3)
    return status


def _add_routes(account, region, vpc_id, destination_cidr, pcx_id) -> list:
    """Route the peer CIDR through the peering in every route table of the VPC."""
    updated = []
    with use_account(account, region):
        ec2 = boto3.client("ec2", region_name=region)
        tables = ec2.describe_route_tables(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["RouteTables"]
        for table in tables:
            try:
                ec2.create_route(RouteTableId=table["RouteTableId"], DestinationCidrBlock=destination_cidr,
                                 VpcPeeringConnectionId=pcx_id)
                updated.append(table["RouteTableId"])
            except Exception as e:
                if "RouteAlreadyExists" not in str(e):
                    raise
    return updated


@tool
def create_vpc_peering(
    requester_vpc_id: str,
    accepter_vpc_id: str,
    requester_account: str = 'platform',
    accepter_account: str = 'platform',
    requester_region: str = 'us-east-1',
    accepter_region: str = '',
    name: str = '',
    add_routes: bool = True,
) -> str:
    """Peer two VPCs, in the same or different accounts/regions, as ONE action: request, accept, and add routes.

    Use this for every VPC peering instead of separate create/accept calls (the accept step needs
    the connection id the request returns). Fails up front if the VPC CIDRs overlap.

    Args:
        requester_vpc_id: VPC that requests the peering.
        accepter_vpc_id: VPC that accepts it.
        requester_account: 'platform' or a registered account's name or id owning the requester VPC.
        accepter_account: 'platform' or a registered account's name or id owning the accepter VPC.
        requester_region: Region of the requester VPC.
        accepter_region: Region of the accepter VPC (defaults to the requester's).
        name: Name tag for the peering connection.
        add_routes: Add routes for the peer CIDR to every route table in both VPCs.

    Returns:
        JSON with the peering connection id, its status, and the route tables updated.
    """
    accepter_region = accepter_region or requester_region
    try:
        requester = resolve_account_ref(requester_account)
        accepter = resolve_account_ref(accepter_account)
    except UnknownAccount as e:
        return _fail("unknown_account", str(e))
    platform = base_account_id()
    accepter_id = accepter or platform

    try:
        req_vpc = _vpc(requester, requester_region, requester_vpc_id)
    except Exception as e:
        return _fail("vpc_not_found", f"{requester_vpc_id} not found in {requester_account} ({requester_region}): {e}",
                     "Check the VPC id, account and region.")
    try:
        acc_vpc = _vpc(accepter, accepter_region, accepter_vpc_id)
    except Exception as e:
        return _fail("vpc_not_found", f"{accepter_vpc_id} not found in {accepter_account} ({accepter_region}): {e}",
                     "Check the VPC id, account and region.")

    overlaps = [(a, b) for a in _cidrs(req_vpc) for b in _cidrs(acc_vpc)
                if ipaddress.ip_network(a).overlaps(ipaddress.ip_network(b))]
    if overlaps:
        return _fail("overlapping_cidrs",
                     "VPC CIDRs overlap, and AWS cannot peer overlapping VPCs: "
                     + ", ".join(f"{a} ↔ {b}" for a, b in overlaps),
                     "Recreate one VPC with a non-overlapping range (e.g. 10.1.0.0/16), then peer again.")

    name = name or f"{requester_vpc_id}-to-{accepter_vpc_id}"
    try:
        with use_account(requester, requester_region):
            params = {
                "VpcId": requester_vpc_id, "PeerVpcId": accepter_vpc_id,
                "TagSpecifications": [{"ResourceType": "vpc-peering-connection",
                                       "Tags": [{"Key": "Name", "Value": name},
                                                {"Key": "ManagedBy", "Value": "CloudOps"}]}],
            }
            if accepter_id != (requester or platform):
                params["PeerOwnerId"] = accepter_id
            if accepter_region != requester_region:
                params["PeerRegion"] = accepter_region
            pcx_id = boto3.client("ec2", region_name=requester_region).create_vpc_peering_connection(
                **params)["VpcPeeringConnection"]["VpcPeeringConnectionId"]
    except Exception as e:
        return _fail("request_failed", f"Requesting the peering from {requester_account} failed: {e}")

    try:
        _wait_for(accepter, accepter_region, pcx_id, {"pending-acceptance", "active"})
        with use_account(accepter, accepter_region):
            boto3.client("ec2", region_name=accepter_region).accept_vpc_peering_connection(
                VpcPeeringConnectionId=pcx_id)
        status = _wait_for(accepter, accepter_region, pcx_id, {"active"})
    except Exception as e:
        return _fail("accept_failed", f"Peering {pcx_id} was requested but accepting it in {accepter_account} "
                     f"failed: {e}", f"Accept it manually, or delete {pcx_id} and try again.",
                     vpc_peering_connection_id=pcx_id)

    routes = {"requester": [], "accepter": []}
    route_error = None
    if add_routes and status == "active":
        try:
            routes["requester"] = _add_routes(requester, requester_region, requester_vpc_id,
                                              _cidrs(acc_vpc)[0], pcx_id)
            routes["accepter"] = _add_routes(accepter, accepter_region, accepter_vpc_id,
                                             _cidrs(req_vpc)[0], pcx_id)
        except Exception as e:
            route_error = f"Peering is active but adding routes failed: {e}"

    return json.dumps({
        "status": "success" if status == "active" and not route_error else "partial",
        "vpc_peering_connection_id": pcx_id,
        "name": name,
        "peering_status": status,
        "requester": {"account": requester_account, "account_id": requester or platform,
                      "region": requester_region, "vpc_id": requester_vpc_id, "cidrs": _cidrs(req_vpc)},
        "accepter": {"account": accepter_account, "account_id": accepter_id,
                     "region": accepter_region, "vpc_id": accepter_vpc_id, "cidrs": _cidrs(acc_vpc)},
        "route_tables_updated": routes,
        "error": route_error,
        "rollback": f"Delete peering connection {pcx_id} (this also removes its routes' target).",
    })
