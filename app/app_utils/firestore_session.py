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

"""Custom Firestore session service for ADK sessions persistence."""

import time
import logging
import json
import asyncio
from typing import Optional, Any
from google.cloud.firestore import AsyncClient
from google.cloud import storage
from google.adk.sessions import BaseSessionService, Session
from google.adk.sessions.base_session_service import ListSessionsResponse, GetSessionConfig
from google.adk.events import Event

logger = logging.getLogger("ask_your_data.firestore_session")


class FirestoreSessionService(BaseSessionService):
    """A persistent SessionService implementation using Firestore and Cloud Storage."""

    def __init__(
        self,
        firestore_client: AsyncClient,
        bucket_name: Optional[str] = None,
        collection_name: str = "sessions",
    ) -> None:
        """Initializes the FirestoreSessionService.

        Args:
            firestore_client: The async Firestore client instance.
            bucket_name: Optional GCS bucket name to clean up artifacts when sessions are deleted.
            collection_name: The Firestore collection name for storing sessions.
        """
        self.db = firestore_client
        self.bucket_name = bucket_name
        self.collection_name = collection_name

    async def _save_events_to_gcs(self, session_id: str, events: list[Event]) -> None:
        """Serializes and writes the events list to GCS.

        Args:
            session_id: The session ID.
            events: The list of Event instances.
        """
        if not self.bucket_name:
            return
        events_json = json.dumps([e.model_dump_json(by_alias=True) for e in events])
        
        def _upload():
            client = storage.Client()
            bucket = client.bucket(self.bucket_name)
            blob_path = f"sessions/{session_id}/events.json"
            blob = bucket.blob(blob_path)
            blob.upload_from_string(events_json, content_type="application/json")
            logger.info("Saved %d events to GCS path %s", len(events), blob_path)
            
        await asyncio.to_thread(_upload)

    async def _load_events_from_gcs(self, session_id: str) -> Optional[list[Event]]:
        """Downloads and deserializes the events list from GCS.

        Args:
            session_id: The session ID.

        Returns:
            The list of Event instances, or None if not found/error.
        """
        if not self.bucket_name:
            return None
        def _download():
            client = storage.Client()
            bucket = client.bucket(self.bucket_name)
            blob_path = f"sessions/{session_id}/events.json"
            blob = bucket.blob(blob_path)
            if not blob.exists():
                return None
            return blob.download_as_text()
            
        try:
            events_str = await asyncio.to_thread(_download)
            if events_str is None:
                return None
            
            events_list = json.loads(events_str)
            return [Event.model_validate_json(e_str) for e_str in events_list]
        except Exception as e:
            logger.error("Failed to load events from GCS for %s: %s", session_id, e)
            return None

    async def _save_state_keys(self, session_id: str, large_state: dict[str, Any]) -> None:
        """Saves large state keys to GCS or Firestore subcollection.

        Args:
            session_id: The session ID.
            large_state: The dictionary of large state keys.
        """
        if self.bucket_name:
            state_json = json.dumps(large_state)
            def _upload():
                client = storage.Client()
                bucket = client.bucket(self.bucket_name)
                blob_path = f"sessions/{session_id}/state_large.json"
                blob = bucket.blob(blob_path)
                blob.upload_from_string(state_json, content_type="application/json")
                logger.info("Saved large state keys to GCS path %s", blob_path)
            await asyncio.to_thread(_upload)
        else:
            doc_ref = self.db.collection(self.collection_name).document(session_id)
            for key, val in large_state.items():
                key_ref = doc_ref.collection("state_large").document(key)
                await key_ref.set({
                    "val_json": json.dumps(val)
                })

    async def _load_state_keys(self, session_id: str) -> dict[str, Any]:
        """Loads large state keys from GCS or Firestore subcollection.

        Args:
            session_id: The session ID.

        Returns:
            A dictionary containing the large state keys.
        """
        large_state = {}
        if self.bucket_name:
            def _download():
                client = storage.Client()
                bucket = client.bucket(self.bucket_name)
                blob_path = f"sessions/{session_id}/state_large.json"
                blob = bucket.blob(blob_path)
                if not blob.exists():
                    return None
                return blob.download_as_text()
            try:
                state_str = await asyncio.to_thread(_download)
                if state_str:
                    parsed = json.loads(state_str)
                    if isinstance(parsed, dict):
                        large_state = parsed
            except Exception as e:
                logger.error("Failed to load large state from GCS for %s: %s", session_id, e)
        else:
            try:
                doc_ref = self.db.collection(self.collection_name).document(session_id)
                docs = doc_ref.collection("state_large").stream()
                async for doc in docs:
                    data = doc.to_dict()
                    if data and "val_json" in data:
                        try:
                            large_state[doc.id] = json.loads(data["val_json"])
                        except Exception:
                            pass
            except Exception as e:
                logger.error("Failed to load large state from Firestore subcollection for %s: %s", session_id, e)
        return large_state

    async def _save_session(self, session: Session) -> None:
        """Serializes and writes the Session object to Firestore.

        Args:
            session: The Session instance to save.
        """
        doc_ref = self.db.collection(self.collection_name).document(session.id)
        
        # Save events list separately to prevent exceeding Firestore's 1MB document limit
        events = session.events
        session.events = []
        
        # Extract large state keys to store separately (avoids Firestore's 1MB limit for CSV uploads in state)
        large_state = {}
        large_keys = ["_code_executor_input_files", "_code_execution_results"]
        for key in large_keys:
            if key in session.state:
                large_state[key] = session.state.pop(key)

        try:
            session_json = session.model_dump_json()
        finally:
            # Restore events and state on the in-memory object
            session.events = events
            for key, val in large_state.items():
                session.state[key] = val

        await doc_ref.set({
            "id": session.id,
            "app_name": session.app_name,
            "user_id": session.user_id,
            "last_update_time": session.last_update_time,
            "session_json": session_json
        })

        if large_state:
            await self._save_state_keys(session.id, large_state)

        if self.bucket_name:
            await self._save_events_to_gcs(session.id, events)
        else:
            # Save each event to the events subcollection
            for idx, event in enumerate(events):
                event_ref = doc_ref.collection("events").document(str(idx))
                await event_ref.set({
                    "event_json": event.model_dump_json(),
                    "timestamp": event.timestamp,
                    "idx": idx
                })

    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        state: Optional[dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> Session:
        """Creates a new session and persists it in Firestore.

        Args:
            app_name: The application name.
            user_id: The authenticated user's ID.
            state: The initial state dictionary.
            session_id: Optional client-provided session ID.

        Returns:
            The created Session object.
        """
        import uuid
        session_id = (
            session_id.strip()
            if session_id and session_id.strip()
            else str(uuid.uuid4())
        )

        # Check if session already exists
        existing = await self.get_session(app_name=app_name, user_id=user_id, session_id=session_id)
        if existing:
            return existing

        session = Session(
            app_name=app_name,
            user_id=user_id,
            id=session_id,
            state=state or {},
            last_update_time=time.time(),
        )
        await self._save_session(session)
        return session

    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        config: Optional[GetSessionConfig] = None,
    ) -> Optional[Session]:
        """Retrieves and deserializes a Session object from Firestore.

        Args:
            app_name: The application name.
            user_id: The user's ID.
            session_id: The session ID.
            config: Optional filter configuration.

        Returns:
            The Session object if found and matches parameters, None otherwise.
        """
        doc_ref = self.db.collection(self.collection_name).document(session_id)
        doc = await doc_ref.get()
        if not doc.exists:
            return None

        data = doc.to_dict()
        if not data or data.get("app_name") != app_name or data.get("user_id") != user_id:
            return None

        try:
            session = Session.model_validate_json(data["session_json"])
        except Exception as e:
            logger.error("Failed to validate Session JSON for %s: %s", session_id, e)
            return None

        # Load large state keys and merge back into session.state
        large_state = await self._load_state_keys(session_id)
        for key, val in large_state.items():
            session.state[key] = val

        # Load events list from GCS (or fall back to Firestore events subcollection)
        events = None
        if self.bucket_name:
            events = await self._load_events_from_gcs(session_id)

        if events is None:
            try:
                events_ref = doc_ref.collection("events").order_by("idx")
                events_docs = events_ref.stream()
                events = []
                async for event_doc in events_docs:
                    event_data = event_doc.to_dict()
                    if event_data and "event_json" in event_data:
                        events.append(Event.model_validate_json(event_data["event_json"]))
            except Exception as e:
                logger.error("Failed to load events subcollection for %s: %s", session_id, e)
                return None

        # Fallback: only overwrite session.events if loaded events are not None or empty,
        # or if they are empty but the deserialized session also has no events.
        # (Maintains compatibility with legacy single-document sessions).
        if events or not session.events:
            session.events = events

        # Filter events if config is provided
        if config:
            if config.num_recent_events is not None:
                if config.num_recent_events == 0:
                    session.events = []
                else:
                    session.events = session.events[-config.num_recent_events:]
            if config.after_timestamp:
                session.events = [e for e in session.events if e.timestamp >= config.after_timestamp]

        return session

    async def list_sessions(
        self, *, app_name: str, user_id: Optional[str] = None
    ) -> ListSessionsResponse:
        """Lists sessions for the given app and user.

        Args:
            app_name: The application name.
            user_id: Optional user ID to filter by.

        Returns:
            ListSessionsResponse containing matching sessions (events omitted).
        """
        query = self.db.collection(self.collection_name).where("app_name", "==", app_name)
        if user_id is not None:
            query = query.where("user_id", "==", user_id)

        docs = query.stream()
        sessions = []
        async for doc in docs:
            data = doc.to_dict()
            if not data or "session_json" not in data:
                continue
            try:
                session = Session.model_validate_json(data["session_json"])
                session.events = []  # List sessions returns them without events
                sessions.append(session)
            except Exception as e:
                logger.error("Failed to parse list session document: %s", e)

        # Sort descending by last update time
        sessions.sort(key=lambda s: s.last_update_time, reverse=True)
        return ListSessionsResponse(sessions=sessions)

    async def delete_session(
        self, *, app_name: str, user_id: str, session_id: str
    ) -> None:
        """Deletes a session and clean up GCS artifacts if a bucket is configured.

        Args:
            app_name: The application name.
            user_id: The user ID.
            session_id: The session ID.
        """
        # Delete Firestore document and its events/state subcollections
        doc_ref = self.db.collection(self.collection_name).document(session_id)
        try:
            async for event_doc in doc_ref.collection("events").stream():
                await event_doc.reference.delete()
        except Exception as e:
            logger.error("Failed to delete events subcollection for %s: %s", session_id, e)
            
        try:
            async for state_doc in doc_ref.collection("state_large").stream():
                await state_doc.reference.delete()
        except Exception as e:
            logger.error("Failed to delete state_large subcollection for %s: %s", session_id, e)

        await doc_ref.delete()

        # Delete GCS artifacts and the events.json file
        if self.bucket_name:
            try:
                import asyncio
                def _purge():
                    client = storage.Client()
                    bucket = client.bucket(self.bucket_name)
                    # 1. Delete events file under sessions/{session_id}/
                    events_prefix = f"sessions/{session_id}/"
                    events_blobs = list(bucket.list_blobs(prefix=events_prefix))
                    if events_blobs:
                        bucket.delete_blobs(events_blobs)
                        logger.info("Purged %d GCS events files for session %s", len(events_blobs), session_id)
                        
                    # 2. GcsArtifactService formats GCS blob paths like: {app_name}/{user_id}/{session_id}/
                    prefix = f"{app_name}/{user_id}/{session_id}/"
                    blobs = list(bucket.list_blobs(prefix=prefix))
                    if blobs:
                        bucket.delete_blobs(blobs)
                        logger.info("Purged %d GCS artifacts for session %s", len(blobs), session_id)
                await asyncio.to_thread(_purge)
            except Exception as e:
                logger.error("Failed to purge GCS artifacts for session %s: %s", session_id, e)

    async def append_event(self, session: Session, event: Event) -> Event:
        """Appends an event to the session and updates Firestore.

        Args:
            session: The Session instance.
            event: The Event instance to append.

        Returns:
            The Event instance.
        """
        if event.partial:
            return event

        # Standard state resolution (handled by superclass)
        await super().append_event(session=session, event=event)
        session.last_update_time = event.timestamp

        # Persist updated session
        await self._save_session(session)
        return event

    async def clean_old_sessions(self, max_age_days: int = 7) -> int:
        """Cleans up sessions and their GCS artifacts older than max_age_days.

        Args:
            max_age_days: Cutoff threshold in days.

        Returns:
            The number of cleaned up sessions.
        """
        cutoff_time = time.time() - (max_age_days * 24 * 3600)
        query = self.db.collection(self.collection_name).where("last_update_time", "<", cutoff_time)
        docs = query.stream()

        count = 0
        async for doc in docs:
            data = doc.to_dict()
            if data:
                session_id = data.get("id")
                user_id = data.get("user_id")
                app_name = data.get("app_name", "app")
                if session_id and user_id:
                    logger.info("Cleaning up old session: %s (User: %s)", session_id, user_id)
                    await self.delete_session(app_name=app_name, user_id=user_id, session_id=session_id)
                    count += 1
        return count
