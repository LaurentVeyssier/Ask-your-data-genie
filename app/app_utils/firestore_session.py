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

    async def _save_session(self, session: Session) -> None:
        """Serializes and writes the Session object to Firestore.

        Args:
            session: The Session instance to save.
        """
        doc_ref = self.db.collection(self.collection_name).document(session.id)
        # Using model_dump_json() ensures all nested fields serialize perfectly to JSON.
        # This bypasses Firestore type matching constraints for complex objects.
        session_json = session.model_dump_json()
        await doc_ref.set({
            "id": session.id,
            "app_name": session.app_name,
            "user_id": session.user_id,
            "last_update_time": session.last_update_time,
            "session_json": session_json
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
        # Delete Firestore document
        doc_ref = self.db.collection(self.collection_name).document(session_id)
        await doc_ref.delete()

        # Delete GCS artifacts
        if self.bucket_name:
            try:
                import asyncio
                def _purge():
                    client = storage.Client()
                    bucket = client.bucket(self.bucket_name)
                    # GcsArtifactService formats GCS blob paths like: {app_name}/{user_id}/{session_id}/
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
