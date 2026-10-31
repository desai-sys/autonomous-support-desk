"""
Tests for DynamoDBService.
All DynamoDB calls are mocked — no real AWS credentials needed.
"""
import pytest
from unittest.mock import MagicMock, patch, call
from botocore.exceptions import ClientError
from src.services.dynamo_service import DynamoDBService


def make_client_error(code="ResourceNotFoundException", message="Table not found"):
    error_response = {"Error": {"Code": code, "Message": message}}
    return ClientError(error_response, "operation")


@pytest.fixture
def mock_table():
    return MagicMock()


@pytest.fixture
def dynamo_service(mock_table):
    with patch("boto3.resource") as mock_resource:
        mock_dynamodb = MagicMock()
        mock_resource.return_value = mock_dynamodb
        mock_dynamodb.Table.return_value = mock_table
        svc = DynamoDBService()
        svc.table = mock_table
        return svc


# ---------------------------------------------------------------------------
# create_ticket
# ---------------------------------------------------------------------------

class TestCreateTicket:

    def test_success(self, dynamo_service, mock_table):
        mock_table.put_item.return_value = {}
        result = dynamo_service.create_ticket(
            ticket_id="abc12345",
            query_text="SSH is down",
            category="EC2",
            urgency="high",
            status="open"
        )
        assert result == {"ticket_id": "abc12345", "result": "created"}
        mock_table.put_item.assert_called_once()

    def test_item_contains_correct_fields(self, dynamo_service, mock_table):
        mock_table.put_item.return_value = {}
        dynamo_service.create_ticket("t1", "query", "Lambda", "critical", "escalated", "some resolution")
        call_kwargs = mock_table.put_item.call_args[1]
        item = call_kwargs["Item"]
        assert item["ticket_id"] == "t1"
        assert item["query_text"] == "query"
        assert item["category"] == "Lambda"
        assert item["urgency"] == "critical"
        assert item["status"] == "escalated"
        assert item["resolution"] == "some resolution"
        assert item["vectorized"] is False
        assert "created_at" in item

    def test_default_resolution_empty_string(self, dynamo_service, mock_table):
        mock_table.put_item.return_value = {}
        dynamo_service.create_ticket("t2", "query", "S3", "low", "open")
        item = mock_table.put_item.call_args[1]["Item"]
        assert item["resolution"] == ""

    def test_client_error_returns_error_dict(self, dynamo_service, mock_table):
        mock_table.put_item.side_effect = make_client_error("ProvisionedThroughputExceededException", "Throttled")
        result = dynamo_service.create_ticket("t3", "q", "EC2", "high", "open")
        assert "error" in result
        assert "Throttled" in result["error"]


# ---------------------------------------------------------------------------
# get_open_tickets
# ---------------------------------------------------------------------------

class TestGetOpenTickets:

    def test_returns_sorted_by_created_at_desc(self, dynamo_service, mock_table):
        mock_table.scan.return_value = {
            "Items": [
                {"ticket_id": "old", "created_at": "2024-01-01T00:00:00", "status": "open"},
                {"ticket_id": "new", "created_at": "2024-06-01T00:00:00", "status": "escalated"},
                {"ticket_id": "mid", "created_at": "2024-03-01T00:00:00", "status": "open"},
            ]
        }
        result = dynamo_service.get_open_tickets()
        assert result[0]["ticket_id"] == "new"
        assert result[1]["ticket_id"] == "mid"
        assert result[2]["ticket_id"] == "old"

    def test_returns_empty_list_on_no_items(self, dynamo_service, mock_table):
        mock_table.scan.return_value = {"Items": []}
        result = dynamo_service.get_open_tickets()
        assert result == []

    def test_returns_empty_list_on_client_error(self, dynamo_service, mock_table):
        mock_table.scan.side_effect = make_client_error()
        result = dynamo_service.get_open_tickets()
        assert result == []

    def test_scan_uses_correct_filter(self, dynamo_service, mock_table):
        mock_table.scan.return_value = {"Items": []}
        dynamo_service.get_open_tickets()
        call_kwargs = mock_table.scan.call_args[1]
        assert ":open" in call_kwargs["ExpressionAttributeValues"]
        assert ":escalated" in call_kwargs["ExpressionAttributeValues"]


# ---------------------------------------------------------------------------
# get_unvectorized_closed_tickets
# ---------------------------------------------------------------------------

class TestGetUnvectorizedClosedTickets:

    def test_returns_items(self, dynamo_service, mock_table):
        mock_table.scan.return_value = {
            "Items": [
                {"ticket_id": "t1", "status": "closed", "vectorized": False, "resolution": "Fixed it"}
            ]
        }
        result = dynamo_service.get_unvectorized_closed_tickets()
        assert len(result) == 1
        assert result[0]["ticket_id"] == "t1"

    def test_returns_empty_list_when_none(self, dynamo_service, mock_table):
        mock_table.scan.return_value = {"Items": []}
        result = dynamo_service.get_unvectorized_closed_tickets()
        assert result == []

    def test_client_error_returns_error_dict(self, dynamo_service, mock_table):
        mock_table.scan.side_effect = make_client_error("InternalServerError", "Server error")
        result = dynamo_service.get_unvectorized_closed_tickets()
        assert "error" in result

    def test_scan_filters_for_closed_and_not_vectorized(self, dynamo_service, mock_table):
        mock_table.scan.return_value = {"Items": []}
        dynamo_service.get_unvectorized_closed_tickets()
        call_kwargs = mock_table.scan.call_args[1]
        assert ":closed" in call_kwargs["ExpressionAttributeValues"]
        assert ":false" in call_kwargs["ExpressionAttributeValues"]
        assert call_kwargs["ExpressionAttributeValues"][":false"] is False


# ---------------------------------------------------------------------------
# mark_ticket_vectorized
# ---------------------------------------------------------------------------

class TestMarkTicketVectorized:

    def test_success(self, dynamo_service, mock_table):
        mock_table.update_item.return_value = {}
        result = dynamo_service.mark_ticket_vectorized("t1")
        assert result == {"ticket_id": "t1", "result": "updated"}

    def test_update_sets_vectorized_true(self, dynamo_service, mock_table):
        mock_table.update_item.return_value = {}
        dynamo_service.mark_ticket_vectorized("t1")
        call_kwargs = mock_table.update_item.call_args[1]
        assert call_kwargs["Key"] == {"ticket_id": "t1"}
        assert call_kwargs["ExpressionAttributeValues"][":true"] is True

    def test_client_error_returns_error_dict(self, dynamo_service, mock_table):
        mock_table.update_item.side_effect = make_client_error("ConditionalCheckFailedException", "Condition failed")
        result = dynamo_service.mark_ticket_vectorized("t1")
        assert "error" in result
