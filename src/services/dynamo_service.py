"""
DynamoDB service for CloudOps platform.
"""

import boto3
from datetime import datetime, timezone
from botocore.exceptions import ClientError
from config import TICKETS_TABLE, AWS_REGION


class DynamoDBService:
    """Service for DynamoDB operations."""

    def __init__(self):
        self.dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
        self.table = self.dynamodb.Table(TICKETS_TABLE)

    def create_ticket(self, ticket_id, query_text, category, urgency, status, resolution=""):
        """Create a new ticket."""
        try:
            self.table.put_item(Item={
                'ticket_id': ticket_id,
                'query_text': query_text,
                'category': category,
                'urgency': urgency,
                'status': status,
                'resolution': resolution,
                'vectorized': False,
                'created_at': datetime.now(timezone.utc).isoformat()
            })
            return {'ticket_id': ticket_id, 'result': 'created'}
        except Exception as e:
            msg = e.response['Error']['Message'] if hasattr(e, 'response') else str(e)
            return {'error': msg}

    def get_open_tickets(self):
        """Get all open or escalated tickets, sorted newest first."""
        try:
            response = self.table.scan(
                FilterExpression='#s IN (:open, :escalated)',
                ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={':open': 'open', ':escalated': 'escalated'}
            )
            items = response.get('Items', [])
            return sorted(items, key=lambda x: x.get('created_at', ''), reverse=True)
        except Exception:
            return []

    def get_unvectorized_closed_tickets(self):
        """Get closed tickets that haven't been vectorized yet."""
        try:
            response = self.table.scan(
                FilterExpression='#s = :closed AND vectorized = :false',
                ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={':closed': 'closed', ':false': False}
            )
            return response.get('Items', [])
        except Exception:
            return []

    def mark_ticket_vectorized(self, ticket_id):
        """Mark a ticket as vectorized=True."""
        try:
            self.table.update_item(
                Key={'ticket_id': ticket_id},
                UpdateExpression='SET vectorized = :true',
                ExpressionAttributeValues={':true': True}
            )
            return {'ticket_id': ticket_id, 'result': 'updated'}
        except Exception as e:
            msg = e.response['Error']['Message'] if hasattr(e, 'response') else str(e)
            return {'error': msg}

    def close_ticket(self, ticket_id, resolution):
        """Close a ticket with a resolution text."""
        try:
            self.table.update_item(
                Key={'ticket_id': ticket_id},
                UpdateExpression='SET #s = :closed, resolution = :res, updated_at = :ts, vectorized = :false',
                ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={
                    ':closed': 'closed',
                    ':res': resolution.strip(),
                    ':ts': datetime.now(timezone.utc).isoformat(),
                    ':false': False
                }
            )
            return {'ticket_id': ticket_id, 'result': 'closed'}
        except Exception as e:
            msg = e.response['Error']['Message'] if hasattr(e, 'response') else str(e)
            return {'error': msg}
