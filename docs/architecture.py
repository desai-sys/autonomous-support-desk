"""
Generate docs/architecture.png (requires Graphviz: brew/apt/winget install graphviz).

    python docs/architecture.py
"""

from pathlib import Path

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import ECR, Fargate
from diagrams.aws.database import Dynamodb
from diagrams.aws.devtools import Codebuild
from diagrams.aws.general import Users
from diagrams.aws.integration import EventbridgeScheduler
from diagrams.aws.management import Cloudwatch
from diagrams.aws.ml import Bedrock
from diagrams.aws.network import ALB, CloudFront
from diagrams.aws.security import Cognito, IAMRole, SecretsManager, WAF
from diagrams.aws.storage import S3
from diagrams.aws.compute import EC2, Lambda
from diagrams.aws.database import RDS

OUT = Path(__file__).resolve().parent / "architecture"

graph_attr = {"fontsize": "22", "fontname": "Helvetica", "pad": "0.4", "nodesep": "0.55",
              "ranksep": "0.9", "splines": "spline", "labelloc": "t"}
node_attr = {"fontsize": "12", "fontname": "Helvetica"}
edge_attr = {"fontsize": "10", "fontname": "Helvetica", "color": "#555555"}

with Diagram("CloudOps Command Center — AWS architecture", filename=str(OUT), show=False,
             direction="LR", graph_attr=graph_attr, node_attr=node_attr, edge_attr=edge_attr):
    users = Users("Operators / SREs\n(browser)")

    with Cluster("Platform account"):
        with Cluster("Edge"):
            waf = WAF("AWS WAF\n(us-east-1)")
            cdn = CloudFront("CloudFront\nHTTPS")
        cognito = Cognito("Cognito user pool\ngroups = roles\ntenant attribute")

        with Cluster("VPC (2 AZs)"):
            alb = ALB("ALB\nCloudFront-only")
            with Cluster("ECS Fargate (private subnets)"):
                console = Fargate("Console\nStreamlit")
                loop = Fargate("Learning loop\n(hourly task)")
        scheduler = EventbridgeScheduler("Scheduler")

        with Cluster("Bedrock AgentCore Runtime"):
            agent = Bedrock("CloudOps agent\nStrands · 285 tools\nApproval gate · app builder")
        with Cluster("Amazon Bedrock"):
            router = Bedrock("Nova Micro\ndomain router")
            llm = Bedrock("Nova Pro\nreasoning")
            kb = Bedrock("Knowledge base\n(S3 Vectors)")

        with Cluster("Data"):
            actions = Dynamodb("actions\n(pending approvals)")
            audit = Dynamodb("audit · accounts\ntickets · usage")
            uploads = S3("artifacts\n(code uploads,\nagent package)")
            kbdocs = S3("knowledge\n(resolutions)")
        secrets = SecretsManager("Secrets\n(ExternalId, cookie,\nOIDC client)")
        cw = Cloudwatch("CloudWatch\nlogs · EMF metrics\nalarms · dashboard")

        with Cluster("Build (scripts/deploy.py)"):
            build = Codebuild("CodeBuild\nARM64 image")
            ecr = ECR("ECR")

        platform = EC2("Platform-account\nresources")

    with Cluster("Managed accounts (1..n)"):
        xrole = IAMRole("CloudOpsCrossAccountRole\n(ExternalId)")
        targets = [Lambda("Lambda"), EC2("EC2 / ECS"), RDS("RDS, S3, IAM, ...")]

    users >> Edge(label="HTTPS") >> waf >> cdn >> Edge(label="X-Origin-Verify") >> alb >> console
    users >> Edge(style="dashed", label="sign-in (OIDC)") >> cognito
    console >> Edge(label="invoke (IAM)") >> agent
    console >> Edge(style="dashed") >> [audit, actions, uploads]
    console >> Edge(style="dashed", label="retrieve") >> kb
    agent >> router
    agent >> llm
    agent >> Edge(label="queue / execute") >> actions
    agent >> Edge(style="dashed") >> uploads
    agent >> Edge(label="approved changes") >> platform
    agent >> Edge(label="sts:AssumeRole", color="#d6336c") >> xrole >> targets
    scheduler >> loop >> kbdocs >> kb
    loop >> Edge(style="dashed") >> audit
    build >> ecr >> console
    secrets >> Edge(style="dotted") >> console
    [console, agent] >> Edge(style="dotted") >> cw
