"""
DynamoDB service tests — all AWS calls mocked.
Run with: python -m pytest tests/test_dynamo_service.py -v
"""
import sys
import unittest.mock as mock
import pytest
from datetime import datetime

# Mock boto3 before import
mock_table = mock.MagicMock()
mock_resource = mock.MagicMock()
mock_resource.Table.return_value = mock_table
mock_boto3 = mock.MagicMock()
mock_boto3.resource.return_value = mock_resource
sys.modules['boto3'] = mock_boto3

mock_botocore = mock.MagicMock()
mock_botocore.exceptions.ClientError = Exception
sys.modules['botocore'] = mock_botocore
sys.modules['botocore.exceptions'] = mock_botocore.exceptions

from src.services.dynamo_service import DynamoDBService


@pytest.fixture
def svc():
    mock_table.reset_mock()
    mock_table.put_item.side_effect = None   # clear any side_effect from prior test
    mock_table.scan.side_effect = None
    mock_table.update_item.side_effect = None
    return DynamoDBService()


class TestCreateTicket:
    def test_creates_item_with_correct_fields(self, svc):
        mock_table.put_item.return_value = {}
        result = svc.create_ticket('TKT-001', 'EC2 down', 'Compute', 'high', 'open')
        assert result == {'ticket_id': 'TKT-001', 'result': 'created'}
        item = mock_table.put_item.call_args[1]['Item']
        assert item['ticket_id'] == 'TKT-001'
        assert item['query_text'] == 'EC2 down'
        assert item['category'] == 'Compute'
        assert item['urgency'] == 'high'
        assert item['status'] == 'open'
        assert item['vectorized'] is False
        assert 'created_at' in item

    def test_returns_error_on_exception(self, svc):
        mock_table.put_item.side_effect = Exception('Table not found')
        result = svc.create_ticket('T1', 'q', 'c', 'h', 'open')
        assert 'error' in result

    def test_default_resolution_is_empty_string(self, svc):
        mock_table.put_item.return_value = {}
        svc.create_ticket('T1', 'q', 'c', 'h', 'open')
        item = mock_table.put_item.call_args[1]['Item']
        assert item['resolution'] == ''


class TestGetOpenTickets:
    def test_returns_sorted_by_created_at_descending(self, svc):
        mock_table.scan.return_value = {'Items': [
            {'ticket_id': 'T1', 'status': 'open', 'created_at': '2024-01-01T10:00:00'},
            {'ticket_id': 'T2', 'status': 'escalated', 'created_at': '2024-01-03T10:00:00'},
            {'ticket_id': 'T3', 'status': 'open', 'created_at': '2024-01-02T10:00:00'},
        ]}
        tickets = svc.get_open_tickets()
        assert tickets[0]['ticket_id'] == 'T2'
        assert tickets[1]['ticket_id'] == 'T3'
        assert tickets[2]['ticket_id'] == 'T1'

    def test_returns_empty_list_on_exception(self, svc):
        mock_table.scan.side_effect = Exception('DynamoDB error')
        result = svc.get_open_tickets()
        assert result == []

    def test_returns_empty_list_when_no_tickets(self, svc):
        mock_table.scan.return_value = {'Items': []}
        result = svc.get_open_tickets()
        assert result == []


class TestGetUnvectorizedClosedTickets:
    def test_returns_unvectorized_tickets(self, svc):
        mock_table.scan.return_value = {'Items': [
            {'ticket_id': 'T1', 'status': 'closed', 'vectorized': False, 'resolution': 'Fixed'}
        ]}
        result = svc.get_unvectorized_closed_tickets()
        assert len(result) == 1
        assert result[0]['ticket_id'] == 'T1'

    def test_returns_empty_on_exception(self, svc):
        mock_table.scan.side_effect = Exception('Error')
        result = svc.get_unvectorized_closed_tickets()
        assert result == []


class TestMarkTicketVectorized:
    def test_sets_vectorized_true(self, svc):
        mock_table.update_item.return_value = {}
        result = svc.mark_ticket_vectorized('TKT-001')
        assert result == {'ticket_id': 'TKT-001', 'result': 'updated'}
        call = mock_table.update_item.call_args[1]
        assert call['Key'] == {'ticket_id': 'TKT-001'}
        assert call['ExpressionAttributeValues'][':true'] is True

    def test_returns_error_on_exception(self, svc):
        mock_table.update_item.side_effect = Exception('Conditional check failed')
        result = svc.mark_ticket_vectorized('TKT-001')
        assert 'error' in result
