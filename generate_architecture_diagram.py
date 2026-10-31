"""
Architecture Diagram Generator — Autonomous CloudOps Command Center
Uses the `diagrams` library with official AWS icons.

Prerequisites:
  1. Install Graphviz:
     Windows: winget install graphviz  (then restart terminal)
     Mac:     brew install graphviz
     Linux:   sudo apt-get install graphviz
  2. pip install diagrams  (already done)

Run:
  python generate_architecture_diagram.py

Output:
  cloudops_architecture.png
"""

from diagrams import Diagram, Cluster, Edge
from diagrams.aws.compute import EC2, ECS, ECR
from diagrams.aws.database import Dynamodb
from diagrams.aws.storage import S3
from diagrams.aws.ml import Bedrock
from diagrams.aws.security import IAM
from diagrams.aws.network import ELB
from diagrams.aws.management import Cloudwatch
from diagrams.aws.devtools import Codebuild
from diagrams.onprem.client import User as HumanUser

graph_attr = {
    "fontsize": "13",
    "bgcolor": "#0e1117",
    "fontcolor": "#c9d1d9",
    "pad": "0.8",
    "splines": "ortho",
    "nodesep": "0.7",
    "ranksep": "1.2",
}

node_attr = {
    "fontsize": "11",
    "fontcolor": "#c9d1d9",
}

with Diagram(
    "Autonomous CloudOps Command Center",
    filename="cloudops_architecture",
    outformat="png",
    show=False,
    direction="TB",
    graph_attr=graph_attr,
    node_attr=node_attr,
):

    # ── Users ─────────────────────────────────────────────────────────────────
    with Cluster("Users", graph_attr={"bgcolor": "#161b22", "fontcolor": "#58a6ff"}):
        operator = HumanUser("Operator")
        sre_user = HumanUser("SRE Engineer")

    # ── EC2: Streamlit Apps + Orchestrator ────────────────────────────────────
    # The orchestrator is a Python class that runs inside the Streamlit process —
    # NOT a separate Lambda or service.
    with Cluster(
        "EC2 t3.medium  (Streamlit Apps)",
        graph_attr={"bgcolor": "#161b22", "fontcolor": "#58a6ff"},
    ):
        cmd_center = EC2("app.py :8501\nCommand Center")
        sre_portal = EC2("sre_portal.py :8502\nSRE Portal")

        with Cluster(
            "AdvancedOrchestrator  (Python class, same process)",
            graph_attr={"bgcolor": "#0d1117", "fontcolor": "#c9d1d9"},
        ):
            orchestrator = EC2("route_query()\n_invoke_agent()\nboto3 → AgentCore")

    operator >> cmd_center
    sre_user >> sre_portal
    cmd_center >> orchestrator

    # ── AgentCore Container ───────────────────────────────────────────────────
    # ARM64 Docker container running on Bedrock AgentCore runtime.
    # Nova Micro domain classifier and Nova Pro agent both live INSIDE this container.
    with Cluster(
        "AWS Bedrock AgentCore Runtime  (ARM64 Container)",
        graph_attr={"bgcolor": "#0d1117", "fontcolor": "#39ff14"},
    ):
        with Cluster(
            "agentcore_entrypoint.py",
            graph_attr={"bgcolor": "#161b22", "fontcolor": "#c9d1d9"},
        ):
            nova_micro = Bedrock(
                "Nova Micro\nDomain Router (router.py)\nroute() picks 1-2 tool groups"
            )
            nova_pro = Bedrock(
                "Nova Pro Agent\nReasons + selects tools\n275+ tools / 7 domains"
            )

        nova_micro >> Edge(label="loads tool subset", color="#c9d1d9") >> nova_pro

    orchestrator >> Edge(label="A2A invoke\n(invoke_agent_runtime)", color="#39ff14") >> nova_micro

    # ── Tool Domains (called by Nova Pro via boto3) ───────────────────────────
    with Cluster(
        "AWS Services  (boto3 tool calls from Nova Pro)",
        graph_attr={"bgcolor": "#161b22", "fontcolor": "#58a6ff"},
    ):
        with Cluster("Compute  (~62 tools)"):
            ec2_t = EC2("EC2 / Lambda\nECS / EKS\nSageMaker")

        with Cluster("Storage  (~33 tools)"):
            s3_t = S3("S3 / EBS\nEFS / ECR\nBackup")

        with Cluster("Network  (~41 tools)"):
            net_t = ELB("VPC / ALB\nRoute53 / CF\nAPI GW / SQS")

        with Cluster("Database  (~40 tools)"):
            db_t = Dynamodb("DynamoDB / RDS\nElastiCache\nRedshift / OpenSearch")

        with Cluster("Security  (~35 tools)"):
            sec_t = IAM("IAM / GuardDuty\nWAF / KMS\nCloudTrail / Inspector")

        with Cluster("Cost & Ops  (~36 tools)"):
            ops_t = Cloudwatch("CloudWatch\nCloudFormation\nCost Explorer / SSM")

        with Cluster("Deployment  (~15 tools)"):
            deploy_t = Codebuild("Lambda / ECS / EC2\nApp Runner / S3 site\nCloudFormation / SAM")

    nova_pro >> [ec2_t, s3_t, net_t, db_t, sec_t, ops_t, deploy_t]

    # ── RAG Fallback (only when escalate=True) ────────────────────────────────
    with Cluster(
        "RAG Fallback  (escalate=True only)",
        graph_attr={"bgcolor": "#161b22", "fontcolor": "#58a6ff"},
    ):
        bedrock_kb = Bedrock("Bedrock KB\nVector Index")
        kb_s3 = S3("S3 Bucket\nresolutions/")

    orchestrator >> Edge(
        label="rag_service.resolve()\n(fallback only)",
        style="dashed",
        color="#ff9a5c",
    ) >> bedrock_kb

    # ── Persistence ───────────────────────────────────────────────────────────
    with Cluster(
        "Persistence  (DynamoDB on-demand)",
        graph_attr={"bgcolor": "#161b22", "fontcolor": "#58a6ff"},
    ):
        tickets_db = Dynamodb("CloudOpsTickets")
        users_db = Dynamodb("CloudOpsUsers\nCloudOpsTenants\nCloudOpsUsage\nCloudOpsAuditLog")

    orchestrator >> Edge(label="create ticket\n(on escalate)", color="#ff6b35") >> tickets_db
    sre_portal >> Edge(label="view / resolve", color="#58a6ff") >> tickets_db

    # ── Continuous Learning Loop ──────────────────────────────────────────────
    # learning_loop.py is a standalone Python script — NOT a Lambda.
    with Cluster(
        "Continuous Learning  (learning_loop.py script)",
        graph_attr={"bgcolor": "#161b22", "fontcolor": "#58a6ff"},
    ):
        learning_script = EC2("learning_loop.py\nstandalone script\n(manual / admin trigger)")

    tickets_db >> Edge(
        label="scan unvectorized\nclosed tickets", color="#c9d1d9"
    ) >> learning_script
    learning_script >> Edge(label="upload resolutions", color="#c9d1d9") >> kb_s3
    kb_s3 >> Edge(label="start_ingestion_job()", color="#c9d1d9") >> bedrock_kb

    # ── CI/CD Build Pipeline ──────────────────────────────────────────────────
    with Cluster(
        "CI/CD Build Pipeline  (deploy only, not request path)",
        graph_attr={"bgcolor": "#161b22", "fontcolor": "#58a6ff"},
    ):
        cb = Codebuild("CodeBuild\nARM64 build\n(agentcore deploy)")
        ecr_repo = ECR("ECR\nContainer Registry")

    cb >> Edge(label="push image", color="#8b949e") >> ecr_repo
    ecr_repo >> Edge(label="pull on deploy", style="dashed", color="#8b949e") >> nova_micro

    # ── Observability ─────────────────────────────────────────────────────────
    with Cluster(
        "Observability",
        graph_attr={"bgcolor": "#161b22", "fontcolor": "#58a6ff"},
    ):
        cw_logs = Cloudwatch("CloudWatch Logs\n+ X-Ray Traces")

    nova_pro >> Edge(label="metrics / logs", color="#8b949e") >> cw_logs
    orchestrator >> Edge(label="audit events", color="#8b949e") >> users_db


print()
print("=" * 60)
print("  Architecture diagram saved: cloudops_architecture.png")
print("=" * 60)
