# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.adk.events import Event
from google.adk.sessions import Session

from app.app_utils.firestore_session import FirestoreSessionService


@pytest.mark.asyncio
async def test_save_session_without_gcs():
    """Verify that _save_session uses Firestore subcollection when bucket_name is not configured."""
    mock_db = MagicMock()
    mock_collection = MagicMock()
    mock_doc = MagicMock()
    mock_sub_collection = MagicMock()
    mock_sub_doc = MagicMock()

    mock_db.collection.return_value = mock_collection
    mock_collection.document.return_value = mock_doc
    mock_doc.set = AsyncMock()
    mock_doc.collection.return_value = mock_sub_collection
    mock_sub_collection.document.return_value = mock_sub_doc
    mock_sub_doc.set = AsyncMock()

    service = FirestoreSessionService(firestore_client=mock_db, bucket_name=None)
    session = Session(app_name="app", user_id="user123", id="session123")
    
    # Add a mock event
    event = Event(author="user", content={"parts": [{"text": "Hello"}]})
    session.events.append(event)

    await service._save_session(session)

    # Verify Firestore document was written
    mock_db.collection.assert_called_with("sessions")
    mock_collection.document.assert_called_with("session123")
    mock_doc.set.assert_called_once()
    
    # Verify events subcollection was written
    mock_doc.collection.assert_called_with("events")
    mock_sub_collection.document.assert_called_with("0")
    mock_sub_doc.set.assert_called_once()


@pytest.mark.asyncio
@patch("google.cloud.storage.Client")
async def test_save_session_with_gcs(mock_storage_client_cls):
    """Verify that _save_session uploads to GCS when bucket_name is configured."""
    mock_db = MagicMock()
    mock_collection = MagicMock()
    mock_doc = MagicMock()

    mock_db.collection.return_value = mock_collection
    mock_collection.document.return_value = mock_doc
    mock_doc.set = AsyncMock()

    # Setup GCS mock
    mock_storage_client = MagicMock()
    mock_storage_client_cls.return_value = mock_storage_client
    mock_bucket = MagicMock()
    mock_storage_client.bucket.return_value = mock_bucket
    mock_blob = MagicMock()
    mock_bucket.blob.return_value = mock_blob

    service = FirestoreSessionService(firestore_client=mock_db, bucket_name="my-test-bucket")
    session = Session(app_name="app", user_id="user123", id="session123")
    
    event = Event(author="user", content={"parts": [{"text": "Hello"}]})
    session.events.append(event)

    await service._save_session(session)

    # Verify Firestore document was written
    mock_doc.set.assert_called_once()
    
    # Verify events subcollection was NOT written
    mock_doc.collection.assert_not_called()

    # Verify GCS write
    mock_storage_client_cls.assert_called_once()
    mock_storage_client.bucket.assert_called_with("my-test-bucket")
    mock_bucket.blob.assert_called_with("sessions/session123/events.json")
    mock_blob.upload_from_string.assert_called_once()


@pytest.mark.asyncio
@patch("google.cloud.storage.Client")
async def test_get_session_gcs_and_fallback(mock_storage_client_cls):
    """Verify get_session loads events from GCS, and falls back to subcollection."""
    mock_db = MagicMock()
    mock_collection = MagicMock()
    mock_doc = MagicMock()
    mock_doc_get = AsyncMock()

    mock_db.collection.return_value = mock_collection
    mock_collection.document.return_value = mock_doc
    mock_doc.get = mock_doc_get

    # Mock doc exists with data
    mock_doc_snapshot = MagicMock()
    mock_doc_snapshot.exists = True
    session_stub = Session(app_name="app", user_id="user123", id="session123")
    mock_doc_snapshot.to_dict.return_value = {
        "app_name": "app",
        "user_id": "user123",
        "id": "session123",
        "session_json": session_stub.model_dump_json()
    }
    mock_doc_get.return_value = mock_doc_snapshot

    # Setup GCS mock: first test GCS success
    mock_storage_client = MagicMock()
    mock_storage_client_cls.return_value = mock_storage_client
    mock_bucket = MagicMock()
    mock_storage_client.bucket.return_value = mock_bucket
    mock_blob = MagicMock()
    mock_bucket.blob.return_value = mock_blob
    
    mock_blob.exists.return_value = True
    event = Event(author="user", content={"parts": [{"text": "Hello GCS"}]})
    mock_blob.download_as_text.return_value = json.dumps([event.model_dump_json(by_alias=True)])

    service = FirestoreSessionService(firestore_client=mock_db, bucket_name="my-test-bucket")
    
    # 1. GCS success flow
    session = await service.get_session(app_name="app", user_id="user123", session_id="session123")
    assert session is not None
    assert len(session.events) == 1
    assert session.events[0].content.parts[0].text == "Hello GCS"
    
    # 2. GCS missing flow - should fall back to subcollection
    mock_blob.exists.return_value = False
    
    mock_sub_collection = MagicMock()
    mock_doc.collection.return_value = mock_sub_collection
    mock_sub_ref = MagicMock()
    mock_sub_collection.order_by.return_value = mock_sub_ref
    
    # Mock firestore async stream generator
    async def mock_stream():
        sub_doc = MagicMock()
        sub_event = Event(author="user", content={"parts": [{"text": "Hello Subcollection"}]})
        sub_doc.to_dict.return_value = {
            "event_json": sub_event.model_dump_json(),
            "timestamp": sub_event.timestamp,
            "idx": 0
        }
        yield sub_doc

    mock_sub_ref.stream = mock_stream

    session_fallback = await service.get_session(app_name="app", user_id="user123", session_id="session123")
    assert session_fallback is not None
    assert len(session_fallback.events) == 1
    assert session_fallback.events[0].content.parts[0].text == "Hello Subcollection"
