"""
Learning loop tests — S3, DynamoDB, and Bedrock all mocked.
Run with: python -m pytest tests/test_learning_loop.py -v
"""
import sys
import unittest.mock as mock
import pytest

# Mock AWS before any imports
mock_boto3 = mock.MagicMock()
sys.modules['boto3'] = mock_boto3
sys.modules['botocore'] = mock.MagicMock()
sys.modules['botocore.exceptions'] = mock.MagicMock()

mock_dynamo_cls = mock.MagicMock()
mock_dynamo_inst = mock.MagicMock()
mock_dynamo_cls.return_value = mock_dynamo_inst

# Patch DynamoDBService at the module level without clobbering 'src' package
with mock.patch.dict('sys.modules', {
    'src.services.dynamo_service': mock.MagicMock(DynamoDBService=mock_dynamo_cls)
}):
    import importlib
    import learning_loop
    importlib.reload(learning_loop)


@pytest.fixture(autouse=True)
def reset_mocks():
    mock_dynamo_inst.reset_mock()
    mock_boto3.reset_mock()
    yield


def _make_clients():
    mock_s3 = mock.MagicMock()
    mock_bedrock = mock.MagicMock()
    mock_boto3.client.side_effect = lambda svc, **kw: mock_s3 if svc == 's3' else mock_bedrock
    return mock_s3, mock_bedrock


class TestSyncClosedTickets:
    def test_uploads_each_ticket_to_s3(self):
        mock_s3, mock_bedrock = _make_clients()
        mock_dynamo_inst.get_unvectorized_closed_tickets.return_value = [
            {'ticket_id': 'T1', 'query_text': 'EC2 down', 'resolution': 'Rebooted',
             'category': 'Compute', 'updated_at': '2024-01-01'},
            {'ticket_id': 'T2', 'query_text': 'S3 public', 'resolution': 'Blocked',
             'category': 'Storage', 'updated_at': '2024-01-02'},
        ]
        learning_loop.sync_closed_tickets()
        assert mock_s3.put_object.call_count == 2
        keys = [c[1]['Key'] for c in mock_s3.put_object.call_args_list]
        assert 'resolutions/T1.txt' in keys
        assert 'resolutions/T2.txt' in keys

    def test_s3_content_has_issue_and_resolution(self):
        mock_s3, _ = _make_clients()
        mock_dynamo_inst.get_unvectorized_closed_tickets.return_value = [
            {'ticket_id': 'T1', 'query_text': 'EC2 down', 'resolution': 'Rebooted',
             'category': 'Compute', 'updated_at': '2024-01-01'},
        ]
        learning_loop.sync_closed_tickets()
        body = mock_s3.put_object.call_args[1]['Body'].decode('utf-8')
        assert 'Issue:' in body
        assert 'Resolution:' in body
        assert 'EC2 down' in body
        assert 'Rebooted' in body

    def test_marks_each_ticket_vectorized(self):
        mock_s3, _ = _make_clients()
        mock_dynamo_inst.get_unvectorized_closed_tickets.return_value = [
            {'ticket_id': 'T1', 'query_text': 'q', 'resolution': 'r',
             'category': 'c', 'updated_at': 't'},
            {'ticket_id': 'T2', 'query_text': 'q', 'resolution': 'r',
             'category': 'c', 'updated_at': 't'},
        ]
        learning_loop.sync_closed_tickets()
        assert mock_dynamo_inst.mark_ticket_vectorized.call_count == 2
        ids = [c[0][0] for c in mock_dynamo_inst.mark_ticket_vectorized.call_args_list]
        assert 'T1' in ids
        assert 'T2' in ids

    def test_triggers_bedrock_ingestion_once(self):
        mock_s3, mock_bedrock = _make_clients()
        mock_dynamo_inst.get_unvectorized_closed_tickets.return_value = [
            {'ticket_id': 'T1', 'query_text': 'q', 'resolution': 'r',
             'category': 'c', 'updated_at': 't'},
        ]
        learning_loop.sync_closed_tickets()
        mock_bedrock.start_ingestion_job.assert_called_once()

    def test_no_op_when_no_tickets(self):
        mock_s3, mock_bedrock = _make_clients()
        mock_dynamo_inst.get_unvectorized_closed_tickets.return_value = []
        learning_loop.sync_closed_tickets()
        mock_s3.put_object.assert_not_called()
        mock_bedrock.start_ingestion_job.assert_not_called()

    def test_s3_failure_does_not_crash_loop(self):
        mock_s3, mock_bedrock = _make_clients()
        mock_s3.put_object.side_effect = Exception('S3 access denied')
        mock_dynamo_inst.get_unvectorized_closed_tickets.return_value = [
            {'ticket_id': 'T1', 'query_text': 'q', 'resolution': 'r',
             'category': 'c', 'updated_at': 't'},
        ]
        # Should not raise
        learning_loop.sync_closed_tickets()
        # Ingestion should NOT be triggered if nothing uploaded
        mock_bedrock.start_ingestion_job.assert_not_called()
