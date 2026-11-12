"""
Generate the architecture diagram in the AWS Architecture Icons style:
docs/architecture.svg and docs/architecture.png.

    python docs/architecture.py

Icons come from the `diagrams` package (pip install diagrams), which bundles the
official AWS Architecture Icons. The PNG is rendered with Playwright/Chromium.
Layout is explicit (coordinates below) so the diagram stays clean and stable.
"""

import base64
import html
from pathlib import Path

import diagrams  # pip install diagrams (bundles the official AWS icons)

DOCS = Path(__file__).resolve().parent
ICONS = Path(diagrams.__file__).resolve().parent.parent / "resources" / "aws"

INK = "#232F3E"
LINE = "#545B64"
FONT = "'Amazon Ember', 'Helvetica Neue', Arial, sans-serif"
ICON = 56

# Group styles from the AWS Architecture Icons deck.
STYLE = {
    "cloud":   dict(stroke=INK, dash="", fill="none", label=INK),
    "account": dict(stroke="#E7157B", dash="", fill="none", label="#E7157B"),
    "region":  dict(stroke="#00A4A6", dash="6 4", fill="none", label="#00A4A6"),
    "vpc":     dict(stroke="#8C4FFF", dash="", fill="none", label="#8C4FFF"),
    "az":      dict(stroke="#00A4A6", dash="6 4", fill="none", label="#00A4A6"),
    "public":  dict(stroke="none", dash="", fill="#F2F6E8", label="#7AA116"),
    "private": dict(stroke="none", dash="", fill="#E6F6F7", label="#00A4A6"),
    "generic": dict(stroke="#7D8998", dash="6 4", fill="none", label="#545B64"),
    "service": dict(stroke="#7D8998", dash="", fill="#FFFFFF", label=INK),
}

out = []


def icon_data(rel: str) -> str:
    return "data:image/png;base64," + base64.b64encode((ICONS / rel).read_bytes()).decode()


def text(x, y, s, size=13, anchor="middle", weight="normal", color=INK, italic=False):
    style = ' font-style="italic"' if italic else ""
    out.append(f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" fill="{color}" '
               f'text-anchor="{anchor}" font-weight="{weight}"{style}>{html.escape(s)}</text>')


def group(x1, y1, x2, y2, label, kind, badge=None):
    st = STYLE[kind]
    dash = f' stroke-dasharray="{st["dash"]}"' if st["dash"] else ""
    stroke = f'stroke="{st["stroke"]}" stroke-width="1.6"' if st["stroke"] != "none" else 'stroke="none"'
    out.append(f'<rect x="{x1}" y="{y1}" width="{x2 - x1}" height="{y2 - y1}" fill="{st["fill"]}" {stroke}{dash}/>')
    tx = x1 + 10
    if badge:
        bw, bh = (34, 23) if badge == "aws.png" else (24, 24)
        if badge != "aws.png":
            out.append(f'<rect x="{x1}" y="{y1}" width="30" height="30" fill="{st["stroke"]}"/>')
            out.append(f'<image href="{icon_data(badge)}" x="{x1 + 3}" y="{y1 + 3}" width="24" height="24"/>')
        else:
            out.append(f'<rect x="{x1}" y="{y1}" width="40" height="30" fill="{INK}"/>')
            out.append(f'<image href="{icon_data(badge)}" x="{x1 + 4}" y="{y1 + 4}" width="{bw}" height="{bh}"/>')
        tx = x1 + (48 if badge == "aws.png" else 38)
    text(tx, y1 + 20, label, size=13.5, anchor="start", weight="bold", color=st["label"])


def node(cx, cy, rel, *labels, size=ICON):
    out.append(f'<image href="{icon_data(rel)}" x="{cx - size / 2}" y="{cy - size / 2}" width="{size}" height="{size}"/>')
    for i, s in enumerate(labels):
        text(cx, cy + size / 2 + 16 + i * 15, s, size=12.5 if i == 0 else 11.5,
             weight="bold" if i == 0 else "normal", color=INK if i == 0 else "#545B64")


def chip(x1, y, x2, s):
    out.append(f'<rect x="{x1}" y="{y}" width="{x2 - x1}" height="34" rx="5" fill="#F3F0FF" stroke="#8C4FFF" stroke-width="1"/>')
    text((x1 + x2) / 2, y + 22, s, size=12, color=INK)


def path(points, dashed=False, arrow=True, color=LINE):
    d = "M" + " L".join(f"{x},{y}" for x, y in points)
    dash = ' stroke-dasharray="5 4"' if dashed else ""
    marker = ' marker-end="url(#arrow)"' if arrow else ""
    out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.8"{dash}{marker}/>')


def step(cx, cy, n):
    out.append(f'<circle cx="{cx}" cy="{cy}" r="12" fill="{INK}"/>')
    text(cx, cy + 4.5, str(n), size=12.5, weight="bold", color="#FFFFFF")


STEPS = [
    "Operators sign in with Amazon Cognito managed login (OIDC, optional MFA). Cognito groups map to roles "
    "(viewer, operator, SRE, admin) and a protected attribute sets the tenant.",
    "The browser connects over HTTPS to Amazon CloudFront, where AWS WAF applies managed rule groups and rate limiting.",
    "CloudFront reaches an internal Application Load Balancer through a VPC origin. The load balancer has no "
    "public address and also requires a secret origin header.",
    "The ALB routes to the console (React + FastAPI) on Amazon ECS on AWS Fargate in private subnets across two "
    "Availability Zones. Each chat turn runs as a background job with a heartbeat.",
    "The console signs every request. Chat goes to the planner runtime on Amazon Bedrock AgentCore, the only "
    "component that talks to a model; its IAM role is read-only.",
    "Amazon Nova Micro routes the request to one or two tool domains; Amazon Nova Pro reasons and calls tools. "
    "Read-only calls run immediately; listings are complete or flagged as incomplete.",
    "Every call that would change something is intercepted by the approval gate and stored as a pending "
    "action in Amazon DynamoDB. Nothing changes, and the planner's role could not change it anyway.",
    "A second person approves the card. The executor runtime, which has no model, replays the stored call at "
    "most once and records the result in the audit log and conversation memory.",
    "In member accounts the planner assumes a read-only role and the executor a change role, each with an "
    "ExternalId. Only the tenant's registered accounts are allowed.",
    "Every hour, Amazon EventBridge Scheduler runs the learning-loop task, which writes resolved tickets to "
    "Amazon S3 and syncs the Bedrock knowledge base (S3 Vectors) used for future escalations.",
    "Logs and embedded metrics go to Amazon CloudWatch (dashboard and alarms); alarms notify by email "
    "through Amazon SNS.",
    "scripts/deploy.py, or the GitHub pipeline (staging, evaluation gate, approval, production), deploys two "
    "AWS CloudFormation stacks; AWS CodeBuild builds the ARM64 console image into Amazon ECR.",
]


def build() -> str:
    out.clear()
    W, H = 2010, 1660
    out.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">')
    out.append('<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
               'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#545B64"/>'
               '</marker></defs>')
    out.append(f'<rect width="{W}" height="{H}" fill="#FFFFFF"/>')
    text(30, 42, "CloudOps Command Center — reference architecture", size=24, anchor="start", weight="bold")
    text(30, 66, "Approval-gated AI operations agent on Amazon Bedrock AgentCore, operating across AWS accounts",
         size=14, anchor="start", color="#545B64")
    out.append('<g transform="translate(0,70)">')

    # ── Groups ──────────────────────────────────────────────────────────────
    group(200, 20, 1985, 1205, "AWS Cloud", "cloud", "aws.png")
    group(220, 60, 1515, 1190, "Platform account", "account", "management/organizations-account.png")
    group(245, 250, 445, 600, "Edge (global)", "generic")
    group(470, 100, 1495, 1172, "Region (e.g. us-east-1)", "region")
    group(500, 160, 1010, 720, "VPC  10.40.0.0/16", "vpc", "network/vpc.png")
    for x1, x2, n in ((518, 755, 1), (765, 995, 2)):
        group(x1, 200, x2, 705, f"Availability Zone {n}", "az")
        group(x1 + 8, 232, x2 - 8, 362, "Public subnet", "public")
        group(x1 + 8, 374, x2 - 8, 695, "Private subnet", "private")
    group(1040, 160, 1295, 720, "Amazon Bedrock AgentCore", "service")
    group(1052, 190, 1283, 462, "Planner · read-only role", "generic")
    group(1052, 474, 1283, 708, "Executor · change role", "generic")
    group(1320, 160, 1480, 530, "Amazon Bedrock", "service")
    group(500, 760, 1010, 960, "Data", "generic")
    group(1040, 760, 1480, 960, "Operations", "generic")
    group(500, 980, 1010, 1160, "Identity and security", "generic")
    group(1040, 980, 1480, 1160, "Build and deploy", "generic")
    group(1550, 60, 1965, 1190, "Member accounts (1..n)", "account", "management/organizations-account.png")
    group(1740, 300, 1945, 830, "Managed resources", "generic")

    # ── Nodes ───────────────────────────────────────────────────────────────
    node(100, 330, "general/users.png", "Operators / SREs", "(browser)")
    node(345, 330, "network/cloudfront.png", "Amazon CloudFront")
    node(345, 490, "security/waf.png", "AWS WAF", "managed rules, rate limit")
    node(880, 160, "network/internet-gateway.png", size=40)
    text(908, 150, "Internet gateway", size=11.5, anchor="start", color="#545B64")
    node(636, 290, "network/nat-gateway.png", "NAT gateway", size=48)
    node(880, 290, "network/nat-gateway.png", "NAT gateway", size=48)
    node(760, 440, "network/elb-application-load-balancer.png", "Application Load", "Balancer (internal)")
    text(600, 429, "CloudFront VPC origin", size=11.5, color="#545B64", italic=True)
    node(590, 596, "compute/fargate.png", "Console task", "React + FastAPI")
    node(695, 596, "compute/fargate.png", "Learning loop", "task (hourly)")
    node(880, 596, "compute/fargate.png", "Console task", "React + FastAPI")
    text(760, 690, "Amazon ECS on AWS Fargate (ARM64) · auto scaling", size=11.5, color="#545B64", italic=True)

    for i, s in enumerate(["Domain router", "Strands agent · 285 tools", "Approval gate · memory"]):
        chip(1066, 218 + i * 42, 1269, s)
    node(1167, 386, "ml/bedrock.png", "Planner runtime", "talks to the model")
    node(1167, 552, "ml/bedrock.png", "Executor runtime", "no model")
    chip(1066, 652, 1269, "Replays approved calls")

    node(1400, 230, "ml/bedrock.png", "Amazon Nova Micro", "domain router")
    node(1400, 345, "ml/bedrock.png", "Amazon Nova Pro", "reasoning, tool use")
    node(1400, 460, "ml/bedrock.png", "Knowledge Bases", "on S3 Vectors")

    node(578, 835, "database/dynamodb.png", "Amazon DynamoDB", "actions · audit · jobs ·", )
    text(578, 906, "tickets · accounts · usage ·", size=11.5, color="#545B64")
    text(578, 921, "conversations", size=11.5, color="#545B64")
    node(703, 835, "storage/simple-storage-service-s3.png", "Amazon S3", "artifacts, uploads")
    node(828, 835, "storage/simple-storage-service-s3.png", "Amazon S3", "knowledge docs")
    node(948, 835, "storage/simple-storage-service-s3.png", "Amazon S3 Vectors", "embeddings")

    node(1140, 840, "integration/eventbridge-scheduler.png", "EventBridge", "Scheduler")
    node(1275, 840, "management/cloudwatch.png", "Amazon CloudWatch", "logs · metrics · alarms")
    node(1400, 840, "integration/simple-notification-service-sns.png", "Amazon SNS", "alarm email")

    node(578, 1050, "security/cognito.png", "Amazon Cognito", "OIDC · MFA · groups")
    node(718, 1050, "security/secrets-manager.png", "AWS Secrets Manager", "ExternalId, signing key")
    node(868, 1050, "security/identity-and-access-management-iam.png", "AWS IAM", "split roles, deny rules")

    node(1120, 1050, "management/cloudformation.png", "AWS CloudFormation", "foundation + app stacks")
    node(1260, 1050, "devtools/codebuild.png", "AWS CodeBuild", "ARM64 image")
    node(1400, 1050, "compute/ec2-container-registry.png", "Amazon ECR", "console image")

    node(1640, 400, "security/identity-and-access-management-iam-role.png", "Read role", "(ReadOnlyAccess)")
    node(1640, 560, "security/identity-and-access-management-iam-role.png", "Change role", "(approved changes)")
    text(1640, 650, "both require an ExternalId", size=11.5, color="#545B64", italic=True)
    for (cx, cy), (rel, lab) in zip(
            [(1795, 400), (1895, 400), (1795, 560), (1895, 560), (1795, 720), (1895, 720)],
            [("compute/ec2.png", "Amazon EC2"), ("compute/lambda.png", "AWS Lambda"),
             ("compute/elastic-container-service.png", "Amazon ECS"), ("database/rds.png", "Amazon RDS"),
             ("storage/simple-storage-service-s3.png", "Amazon S3"), ("network/vpc.png", "Amazon VPC")]):
        node(cx, cy, rel, lab, size=48)
    text(1842, 800, "…and any AWS API", size=11.5, color="#545B64", italic=True)

    # ── Connections ────────────────────────────────────────────────────────
    path([(132, 330), (313, 330)])                                   # users → CloudFront
    path([(345, 395), (345, 458)], arrow=False)                      # CloudFront ↔ WAF
    path([(377, 330), (457, 330), (457, 440), (728, 440)])           # CloudFront → VPC origin → ALB
    path([(760, 508), (760, 530), (590, 530), (590, 564)])           # ALB → console AZ1
    path([(760, 530), (880, 530), (880, 564)])                       # ALB → console AZ2
    path([(912, 596), (1025, 596), (1025, 386), (1135, 386)])        # console → planner (chat)
    path([(1025, 552), (1135, 552)])                                 # console → executor (approvals)
    path([(1199, 386), (1305, 386), (1305, 230), (1368, 230)])       # planner → Nova Micro
    path([(1305, 345), (1368, 345)])                                 # planner → Nova Pro
    path([(1100, 720), (1100, 735), (578, 735), (578, 803)])         # runtimes → actions table
    path([(1262, 190), (1262, 135), (1640, 135), (1640, 368)], color="#00A4A6")   # planner → read role
    path([(1199, 552), (1606, 552)], color="#E7157B")                # executor → change role
    path([(1674, 400), (1736, 400)], color="#00A4A6")                # read role → resources
    path([(1674, 560), (1736, 560)], color="#E7157B")                # change role → resources
    path([(1140, 808), (1140, 752), (695, 752), (695, 662)], dashed=True)  # scheduler → loop
    path([(100, 375), (100, 1050), (546, 1050)], dashed=True)        # users → Cognito

    for (cx, cy), n in zip([(100, 720), (190, 330), (560, 440), (675, 530), (1025, 470), (1305, 290),
                            (870, 735), (1082, 552), (1450, 135), (1176, 806), (1311, 806), (1154, 1016)],
                           range(1, 13)):
        step(cx, cy, n)
    step(1520, 552, 9)

    out.append("</g>")

    # ── Legend ─────────────────────────────────────────────────────────────
    y0 = 1300
    out.append(f'<line x1="30" y1="{y0 - 22}" x2="{W - 30}" y2="{y0 - 22}" stroke="#D5DBDB"/>')
    col_w = (W - 90) // 2
    for i, s in enumerate(STEPS):
        col, row = divmod(i, 6)
        x = 40 + col * (col_w + 30)
        y = y0 + row * 58
        out.append(f'<circle cx="{x + 12}" cy="{y + 2}" r="12" fill="{INK}"/>')
        text(x + 12, y + 6.5, str(i + 1), size=12.5, weight="bold", color="#FFFFFF")
        out.append(f'<foreignObject x="{x + 34}" y="{y - 12}" width="{col_w - 40}" height="56">'
                   f'<div xmlns="http://www.w3.org/1999/xhtml" style="font-family:{FONT};font-size:14px;'
                   f'line-height:19px;color:{INK}">{html.escape(s)}</div></foreignObject>')
    out.append("</svg>")
    return "\n".join(out)


def main():
    svg = build()
    (DOCS / "architecture.svg").write_text(svg, encoding="utf-8")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("wrote docs/architecture.svg (pip install playwright to also render the PNG)")
        return
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 2010, "height": 1660}, device_scale_factor=1.5)
        page.set_content(f"<html><body style='margin:0'>{svg}</body></html>")
        page.locator("svg").screenshot(path=str(DOCS / "architecture.png"))
        browser.close()
    print("wrote docs/architecture.svg and docs/architecture.png")


if __name__ == "__main__":
    main()
