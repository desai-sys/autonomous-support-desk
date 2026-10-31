"""
Structured logging and metrics.

Metrics are written as CloudWatch Embedded Metric Format (EMF) log lines, so
emitting one costs no API call: the AgentCore runtime and the ECS console both
ship stdout to CloudWatch Logs, which turns EMF lines into metrics in the
``CloudOps`` namespace. The dashboard and alarms in infra/app.yaml read them.
"""

import json
import logging
import time
from typing import Dict, Optional

import structlog

import config

NAMESPACE = "CloudOps"


def configure_logging(service: str) -> None:
    """JSON logs on stdout, tagged with the service name."""
    # Libraries (e.g. bedrock_agentcore) attach their own handlers; only set the level
    # here so their records aren't printed twice.
    logging.getLogger().setLevel(getattr(logging, config.LOG_LEVEL.upper(), logging.INFO))
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.bind_contextvars(service=service, env=config.ENVIRONMENT)


class Metrics:
    """Emit CloudWatch metrics via EMF."""

    def put(self, name: str, value: float, unit: str = "Count",
            dimensions: Optional[Dict[str, str]] = None) -> None:
        dims = {"Environment": config.ENVIRONMENT, **(dimensions or {})}
        record = {
            "_aws": {
                "Timestamp": int(time.time() * 1000),
                "CloudWatchMetrics": [{
                    "Namespace": NAMESPACE,
                    "Dimensions": [list(dims)],
                    "Metrics": [{"Name": name, "Unit": unit}],
                }],
            },
            name: value,
            **dims,
        }
        print(json.dumps(record), flush=True)

    def request(self, service: str, duration_ms: float, success: bool) -> None:
        self.put("RequestLatency", duration_ms, "Milliseconds", {"Service": service})
        self.put("Requests", 1, "Count", {"Service": service})
        if not success:
            self.put("RequestErrors", 1, "Count", {"Service": service})

    def action(self, status: str) -> None:
        self.put("Actions", 1, "Count", {"Status": status})


metrics = Metrics()
