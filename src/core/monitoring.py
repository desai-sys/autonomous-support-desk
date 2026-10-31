"""
Enterprise Monitoring & Observability Module
Provides CloudWatch integration, X-Ray tracing, and performance metrics.
"""

import boto3
import time
import json
import traceback
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable
from functools import wraps
from contextlib import contextmanager
import structlog
from botocore.exceptions import ClientError
from pydantic import BaseModel

logger = structlog.get_logger()


class MetricData(BaseModel):
    """CloudWatch metric data model."""
    metric_name: str
    value: float
    unit: str = "Count"
    dimensions: Dict[str, str] = {}
    timestamp: datetime = None


class PerformanceMetrics(BaseModel):
    """Performance tracking model."""
    operation: str
    duration_ms: float
    success: bool
    error_type: Optional[str] = None
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    metadata: Dict[str, Any] = {}


class MonitoringManager:
    """Centralized monitoring and observability."""
    
    def __init__(self):
        self.cloudwatch = boto3.client('cloudwatch', region_name='us-east-1')
        self.xray = boto3.client('xray', region_name='us-east-1')
        self.logs = boto3.client('logs', region_name='us-east-1')
        
        # Metric namespace
        self.namespace = "CloudOps/SRE"
        
        # Performance tracking
        self.performance_buffer = []
        self.buffer_size = 100
        
        # SLA thresholds
        self.sla_thresholds = {
            'resolution_time_p95': 300000,  # 5 minutes in ms
            'resolution_time_p99': 600000,  # 10 minutes in ms
            'success_rate': 0.95,  # 95%
            'availability': 0.999  # 99.9%
        }

    def record_metric(self, metric_name: str, value: float, 
                     unit: str = "Count", dimensions: Dict[str, str] = None) -> None:
        """Record a CloudWatch metric."""
        try:
            metric_data = MetricData(
                metric_name=metric_name,
                value=value,
                unit=unit,
                dimensions=dimensions or {},
                timestamp=datetime.utcnow()
            )
            
            self._send_metric_to_cloudwatch(metric_data)
            
            logger.info("metric_recorded", 
                       metric=metric_name, 
                       value=value, 
                       dimensions=dimensions)
                       
        except Exception as e:
            logger.error("metric_recording_failed", 
                        metric=metric_name, 
                        error=str(e))

    def record_resolution_time(self, duration_ms: float, resolution_type: str, 
                              tenant_id: str, success: bool = True) -> None:
        """Record resolution time metrics."""
        dimensions = {
            'ResolutionType': resolution_type,
            'TenantId': tenant_id,
            'Success': str(success)
        }
        
        self.record_metric('ResolutionTime', duration_ms, 'Milliseconds', dimensions)
        self.record_metric('ResolutionCount', 1, 'Count', dimensions)
        
        # Check SLA compliance
        self._check_sla_compliance('resolution_time', duration_ms, dimensions)

    def record_tool_execution(self, tool_name: str, duration_ms: float, 
                             success: bool, tenant_id: str, 
                             error_type: Optional[str] = None) -> None:
        """Record tool execution metrics."""
        dimensions = {
            'ToolName': tool_name,
            'TenantId': tenant_id,
            'Success': str(success)
        }
        
        if error_type:
            dimensions['ErrorType'] = error_type
        
        self.record_metric('ToolExecutionTime', duration_ms, 'Milliseconds', dimensions)
        self.record_metric('ToolExecutionCount', 1, 'Count', dimensions)
        
        if not success:
            self.record_metric('ToolExecutionErrors', 1, 'Count', dimensions)

    def record_api_request(self, endpoint: str, method: str, status_code: int, 
                          duration_ms: float, tenant_id: Optional[str] = None) -> None:
        """Record API request metrics."""
        dimensions = {
            'Endpoint': endpoint,
            'Method': method,
            'StatusCode': str(status_code)
        }
        
        if tenant_id:
            dimensions['TenantId'] = tenant_id
        
        self.record_metric('APIRequestTime', duration_ms, 'Milliseconds', dimensions)
        self.record_metric('APIRequestCount', 1, 'Count', dimensions)
        
        if status_code >= 400:
            self.record_metric('APIErrors', 1, 'Count', dimensions)

    def record_escalation(self, reason: str, tenant_id: str, 
                         resolution_type: str = "escalated") -> None:
        """Record escalation metrics."""
        dimensions = {
            'Reason': reason,
            'TenantId': tenant_id,
            'ResolutionType': resolution_type
        }
        
        self.record_metric('EscalationCount', 1, 'Count', dimensions)

    def record_learning_loop_metrics(self, tickets_processed: int, 
                                   ingestion_duration_ms: float, 
                                   success: bool) -> None:
        """Record learning loop performance."""
        dimensions = {'Success': str(success)}
        
        self.record_metric('LearningLoopTickets', tickets_processed, 'Count', dimensions)
        self.record_metric('LearningLoopDuration', ingestion_duration_ms, 'Milliseconds', dimensions)
        
        if not success:
            self.record_metric('LearningLoopErrors', 1, 'Count', dimensions)

    def get_sla_metrics(self, tenant_id: Optional[str] = None, 
                       hours: int = 24) -> Dict[str, Any]:
        """Get SLA compliance metrics."""
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=hours)
        
        try:
            # Resolution time percentiles
            resolution_times = self._get_metric_statistics(
                'ResolutionTime', start_time, end_time, 
                tenant_id, ['Average', 'Maximum']
            )
            
            # Success rate
            success_count = self._get_metric_statistics(
                'ResolutionCount', start_time, end_time,
                tenant_id, ['Sum'], {'Success': 'True'}
            )
            
            total_count = self._get_metric_statistics(
                'ResolutionCount', start_time, end_time,
                tenant_id, ['Sum']
            )
            
            success_rate = 0.0
            if total_count and total_count.get('Sum', 0) > 0:
                success_rate = success_count.get('Sum', 0) / total_count.get('Sum', 1)
            
            # API availability
            api_errors = self._get_metric_statistics(
                'APIErrors', start_time, end_time,
                tenant_id, ['Sum']
            )
            
            api_total = self._get_metric_statistics(
                'APIRequestCount', start_time, end_time,
                tenant_id, ['Sum']
            )
            
            availability = 1.0
            if api_total and api_total.get('Sum', 0) > 0:
                error_rate = api_errors.get('Sum', 0) / api_total.get('Sum', 1)
                availability = 1.0 - error_rate
            
            return {
                'resolution_time_avg': resolution_times.get('Average', 0),
                'resolution_time_max': resolution_times.get('Maximum', 0),
                'success_rate': success_rate,
                'availability': availability,
                'sla_compliance': {
                    'resolution_time': resolution_times.get('Average', 0) < self.sla_thresholds['resolution_time_p95'],
                    'success_rate': success_rate >= self.sla_thresholds['success_rate'],
                    'availability': availability >= self.sla_thresholds['availability']
                }
            }
            
        except Exception as e:
            logger.error("sla_metrics_retrieval_failed", error=str(e))
            return {}

    def create_dashboard(self, tenant_id: Optional[str] = None) -> str:
        """Create CloudWatch dashboard."""
        dashboard_name = f"CloudOps-SRE-{tenant_id}" if tenant_id else "CloudOps-SRE-Global"
        
        dashboard_body = {
            "widgets": [
                {
                    "type": "metric",
                    "properties": {
                        "metrics": [
                            [self.namespace, "ResolutionTime", "TenantId", tenant_id or "ALL"],
                            [self.namespace, "ToolExecutionTime", "TenantId", tenant_id or "ALL"]
                        ],
                        "period": 300,
                        "stat": "Average",
                        "region": "us-east-1",
                        "title": "Resolution & Tool Execution Times"
                    }
                },
                {
                    "type": "metric",
                    "properties": {
                        "metrics": [
                            [self.namespace, "ResolutionCount", "Success", "True"],
                            [self.namespace, "ResolutionCount", "Success", "False"],
                            [self.namespace, "EscalationCount"]
                        ],
                        "period": 300,
                        "stat": "Sum",
                        "region": "us-east-1",
                        "title": "Resolution Success & Escalations"
                    }
                },
                {
                    "type": "metric",
                    "properties": {
                        "metrics": [
                            [self.namespace, "APIRequestCount"],
                            [self.namespace, "APIErrors"]
                        ],
                        "period": 300,
                        "stat": "Sum",
                        "region": "us-east-1",
                        "title": "API Health"
                    }
                }
            ]
        }
        
        try:
            self.cloudwatch.put_dashboard(
                DashboardName=dashboard_name,
                DashboardBody=json.dumps(dashboard_body)
            )
            
            logger.info("dashboard_created", dashboard_name=dashboard_name)
            return dashboard_name
            
        except ClientError as e:
            logger.error("dashboard_creation_failed", error=str(e))
            return ""

    def _send_metric_to_cloudwatch(self, metric: MetricData) -> None:
        """Send metric data to CloudWatch."""
        try:
            metric_data = {
                'MetricName': metric.metric_name,
                'Value': metric.value,
                'Unit': metric.unit,
                'Timestamp': metric.timestamp or datetime.utcnow()
            }
            
            if metric.dimensions:
                metric_data['Dimensions'] = [
                    {'Name': k, 'Value': v} 
                    for k, v in metric.dimensions.items()
                ]
            
            self.cloudwatch.put_metric_data(
                Namespace=self.namespace,
                MetricData=[metric_data]
            )
            
        except ClientError as e:
            logger.error("cloudwatch_metric_failed", error=str(e))

    def _get_metric_statistics(self, metric_name: str, start_time: datetime, 
                              end_time: datetime, tenant_id: Optional[str] = None,
                              statistics: List[str] = ['Average'],
                              additional_dimensions: Dict[str, str] = None) -> Dict[str, float]:
        """Get CloudWatch metric statistics."""
        try:
            dimensions = []
            if tenant_id:
                dimensions.append({'Name': 'TenantId', 'Value': tenant_id})
            
            if additional_dimensions:
                dimensions.extend([
                    {'Name': k, 'Value': v} 
                    for k, v in additional_dimensions.items()
                ])
            
            response = self.cloudwatch.get_metric_statistics(
                Namespace=self.namespace,
                MetricName=metric_name,
                Dimensions=dimensions,
                StartTime=start_time,
                EndTime=end_time,
                Period=3600,  # 1 hour
                Statistics=statistics
            )
            
            if response['Datapoints']:
                latest = max(response['Datapoints'], key=lambda x: x['Timestamp'])
                return {stat: latest.get(stat, 0) for stat in statistics}
            
            return {stat: 0 for stat in statistics}
            
        except ClientError as e:
            logger.error("metric_statistics_failed", error=str(e))
            return {stat: 0 for stat in statistics}

    def _check_sla_compliance(self, metric_type: str, value: float, 
                             dimensions: Dict[str, str]) -> None:
        """Check SLA compliance and alert if needed."""
        if metric_type == 'resolution_time':
            if value > self.sla_thresholds['resolution_time_p95']:
                self._send_sla_alert('Resolution time SLA breach', 
                                   f"Resolution took {value}ms", dimensions)

    def _send_sla_alert(self, title: str, message: str, 
                       dimensions: Dict[str, str]) -> None:
        """Send SLA breach alert."""
        logger.warning("sla_breach", 
                      title=title, 
                      message=message, 
                      dimensions=dimensions)
        
        # TODO: Integrate with SNS for real alerts
        self.record_metric('SLABreach', 1, 'Count', dimensions)


# Decorators for automatic monitoring
def monitor_performance(operation_name: str):
    """Decorator to monitor function performance."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            success = True
            error_type = None
            
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                success = False
                error_type = type(e).__name__
                logger.error("monitored_operation_failed", 
                           operation=operation_name, 
                           error=str(e),
                           traceback=traceback.format_exc())
                raise
            finally:
                duration_ms = (time.time() - start_time) * 1000
                
                # Extract tenant_id from args/kwargs if available
                tenant_id = None
                for arg in args:
                    if hasattr(arg, 'tenant_id'):
                        tenant_id = arg.tenant_id
                        break
                
                if not tenant_id and 'tenant_id' in kwargs:
                    tenant_id = kwargs['tenant_id']
                
                monitoring_manager.record_metric(
                    f'{operation_name}Duration', 
                    duration_ms, 
                    'Milliseconds',
                    {'Success': str(success), 'TenantId': tenant_id or 'unknown'}
                )
                
                if not success:
                    monitoring_manager.record_metric(
                        f'{operation_name}Errors', 
                        1, 
                        'Count',
                        {'ErrorType': error_type, 'TenantId': tenant_id or 'unknown'}
                    )
        
        return wrapper
    return decorator


@contextmanager
def trace_operation(operation_name: str, metadata: Dict[str, Any] = None):
    """Context manager for X-Ray tracing."""
    # TODO: Implement X-Ray tracing when available
    start_time = time.time()
    
    try:
        logger.info("operation_started", operation=operation_name, metadata=metadata)
        yield
        logger.info("operation_completed", operation=operation_name)
    except Exception as e:
        logger.error("operation_failed", operation=operation_name, error=str(e))
        raise
    finally:
        duration_ms = (time.time() - start_time) * 1000
        logger.info("operation_duration", operation=operation_name, duration_ms=duration_ms)


# Global monitoring manager instance
monitoring_manager = MonitoringManager()