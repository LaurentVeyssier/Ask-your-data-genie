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

"""FastAPI application that serves the agent API and mounting static files."""

import base64
import json
import logging
import os
from typing import Dict, Any, Optional

import dotenv
# Load .env variables, forcing override
dotenv.load_dotenv(override=True)

import google.auth
from fastapi import FastAPI, HTTPException, Response, Depends, Security, BackgroundTasks
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from google.adk.artifacts import InMemoryArtifactService
from google.adk.cli.fast_api import get_fast_api_app
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.cloud import logging as google_cloud_logging
from google.genai import types
from pydantic import BaseModel

from app.agent import root_agent
from app.app_utils.telemetry import setup_telemetry
from app.app_utils.typing import Feedback
from app.app_utils.auth_service import (
    UserStore,
    hash_password,
    verify_password,
    create_jwt_token,
    verify_jwt_token,
)

# Setup telemetry
setup_telemetry()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ask_your_data")


def serialize_event(obj: Any) -> Any:
    """Recursively converts bytes and sets to serializable formats.

    Args:
        obj: The object to convert.

    Returns:
        The serializable object.
    """
    if isinstance(obj, dict):
        return {k: serialize_event(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [serialize_event(v) for v in obj]
    elif isinstance(obj, set):
        return [serialize_event(v) for v in obj]
    elif isinstance(obj, bytes):
        return base64.b64encode(obj).decode("utf-8")
    return obj


allow_origins = (
    os.getenv("ALLOW_ORIGINS", "").split(",") if os.getenv("ALLOW_ORIGINS") else None
)

logs_bucket_name = os.environ.get("LOGS_BUCKET_NAME")
AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
session_service_uri = None
artifact_service_uri = f"gs://{logs_bucket_name}" if logs_bucket_name else None

# Initialize the default ADK Fast API app
app: FastAPI = get_fast_api_app(
    agents_dir=AGENT_DIR,
    web=False,
    artifact_service_uri=artifact_service_uri,
    allow_origins=allow_origins,
    session_service_uri=session_service_uri,
    otel_to_cloud=True,
)
app.title = "ask-your-data"
app.description = "API for interacting with the Agent ask-your-data"

# Setup CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Determine environment
IS_DEPLOYED = bool(os.getenv("K_SERVICE") or os.getenv("ENVIRONMENT") == "production")

# Initialize database / user store
if os.getenv("INTEGRATION_TEST") == "TRUE":
    user_store = UserStore(is_deployed=IS_DEPLOYED, db_path="test_users.db")
else:
    user_store = UserStore(is_deployed=IS_DEPLOYED)

# Initialize Session and Artifact services based on environment
if IS_DEPLOYED:
    from google.cloud.firestore import AsyncClient
    from app.app_utils.firestore_session import FirestoreSessionService
    from google.adk.artifacts import GcsArtifactService

    firestore_client = AsyncClient()
    session_service = FirestoreSessionService(
        firestore_client=firestore_client,
        bucket_name=logs_bucket_name,
    )
    if logs_bucket_name:
        artifact_service = GcsArtifactService(bucket_name=logs_bucket_name)
    else:
        artifact_service = InMemoryArtifactService()
else:
    session_service = InMemorySessionService()
    artifact_service = InMemoryArtifactService()


# Security Bearer scheme
security_scheme = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(security_scheme),
) -> str:
    """FastAPI dependency to secure routes and extract authenticated user ID.

    Args:
        credentials: The HTTP Authorization credentials.

    Returns:
        The authenticated user email.

    Raises:
        HTTPException: If token is missing or invalid.
    """
    token = credentials.credentials
    email = verify_jwt_token(token)
    if not email:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return email


async def get_current_admin(
    current_user: str = Depends(get_current_user),
) -> str:
    """Security guard to check if the authenticated user has Admin permissions.

    Args:
        current_user: The authenticated user's ID/email.

    Returns:
        The authenticated user's ID/email if they are an admin.

    Raises:
        HTTPException: 403 Forbidden if not an admin.
    """
    user = await user_store.get_user(current_user)
    if not user or not user.get("is_admin", False):
        raise HTTPException(
            status_code=403,
            detail="Administrative privileges required to access this resource",
        )
    return current_user


async def run_session_cleanup() -> None:
    """Asynchronously runs the 7-day session and artifact cleanup in production."""
    if IS_DEPLOYED:
        try:
            # FirestoreSessionService clean_old_sessions
            if hasattr(session_service, "clean_old_sessions"):
                cleaned = await session_service.clean_old_sessions(max_age_days=7)
                if cleaned > 0:
                    logger.info("Automatically cleaned up %d expired sessions.", cleaned)
        except Exception as e:
            logger.error("Failed to run automatic session cleanup: %s", e)


class UserCredentials(BaseModel):
    """Pydantic model representing user login/register credentials."""
    email: str
    password: str


class FilePayload(BaseModel):
    """Pydantic model representing an uploaded file."""
    name: str
    type: str
    data: str  # Base64 encoded string of file content


class ChatRequest(BaseModel):
    """Pydantic model representing the chat request."""
    message: str
    sessionId: Optional[str] = None
    file: Optional[FilePayload] = None


@app.post("/api/auth/register")
async def register(
    creds: UserCredentials,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Registers a new user and schedules automatic cleanup."""
    if not creds.email or not creds.password:
        raise HTTPException(status_code=400, detail="Email and password are required")
    if len(creds.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    existing = await user_store.get_user(creds.email)
    if existing:
        raise HTTPException(status_code=400, detail="User already exists")

    pwd_hash = hash_password(creds.password)
    user = await user_store.create_user(creds.email, pwd_hash)

    # Trigger automatic cleanup in background
    background_tasks.add_task(run_session_cleanup)

    token = create_jwt_token(creds.email)
    return {
        "status": "success",
        "token": token,
        "email": creds.email,
        "is_admin": user.get("is_admin", False),
    }


@app.post("/api/auth/login")
async def login(
    creds: UserCredentials,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Authenticates a user, returns JWT and triggers automatic cleanup."""
    if not creds.email or not creds.password:
        raise HTTPException(status_code=400, detail="Email and password are required")

    user = await user_store.get_user(creds.email)
    if not user or not verify_password(creds.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect email or password")

    # Trigger automatic cleanup in background
    background_tasks.add_task(run_session_cleanup)

    token = create_jwt_token(creds.email)
    return {
        "status": "success",
        "token": token,
        "email": creds.email,
        "is_admin": user.get("is_admin", False),
    }


@app.get("/api/auth/me")
async def get_me(current_user: str = Depends(get_current_user)) -> dict[str, Any]:
    """Validates token and returns current user details."""
    user = await user_store.get_user(current_user)
    is_admin = user.get("is_admin", False) if user else False
    return {"email": current_user, "is_admin": is_admin}


@app.get("/api/sessions")
async def list_user_sessions(
    current_user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Lists all the sessions belonging to the authenticated user."""
    try:
        response = await session_service.list_sessions(
            app_name="app", user_id=current_user
        )
        sessions_list = []
        for s in response.sessions:
            sessions_list.append({
                "id": s.id,
                "app_name": s.app_name,
                "user_id": s.user_id,
                "last_update_time": s.last_update_time,
                "state": s.state,
            })
        return {"sessions": sessions_list}
    except Exception as e:
        logger.error("Error listing sessions: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


def format_session_history(session: Any) -> list[dict[str, Any]]:
    """Translates ADK session history events into a clean history turns format.

    Args:
        session: The Session instance containing the events.

    Returns:
        A list of turn dictionaries (role, text, file, code, etc.).
    """
    turns = []
    current_turn = None

    for event in session.events:
        if event.content:
            role = event.content.role
            if role == "user":
                text = ""
                file_name = None
                for part in event.content.parts:
                    if part.text:
                        text += part.text
                    elif part.inline_data:
                        file_name = part.inline_data.display_name or "uploaded_file.csv"
                current_turn = {
                    "role": "user",
                    "text": text,
                    "file": file_name,
                }
                turns.append(current_turn)
                current_turn = None
            elif role in ("model", "assistant"):
                if current_turn is None or current_turn["role"] != "assistant":
                    current_turn = {
                        "role": "assistant",
                        "text": "",
                        "code": "",
                        "code_output": "",
                        "code_outcome": None,
                        "artifacts": [],
                    }
                    turns.append(current_turn)

                for part in event.content.parts:
                    if part.text:
                        current_turn["text"] += part.text
                    elif part.executable_code:
                        current_turn["code"] += part.executable_code.code
                    elif part.code_execution_result:
                        current_turn["code_output"] += part.code_execution_result.output
                        current_turn["code_outcome"] = part.code_execution_result.outcome

        if event.actions and event.actions.artifact_delta:
            if current_turn and current_turn["role"] == "assistant":
                for filename in event.actions.artifact_delta.keys():
                    if filename not in current_turn["artifacts"]:
                        current_turn["artifacts"].append(filename)

    return turns


@app.get("/api/sessions/{session_id}")
async def get_session_history(
    session_id: str,
    current_user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Retrieves session details and builds conversation turns for recovery."""
    session = await session_service.get_session(
        app_name="app", user_id=current_user, session_id=session_id
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    history = format_session_history(session)
    return {
        "id": session.id,
        "app_name": session.app_name,
        "user_id": session.user_id,
        "last_update_time": session.last_update_time,
        "state": session.state,
        "history": history,
    }


@app.delete("/api/sessions/{session_id}")
async def delete_user_session(
    session_id: str,
    current_user: str = Depends(get_current_user),
) -> dict[str, str]:
    """Deletes a session and its artifacts."""
    session = await session_service.get_session(
        app_name="app", user_id=current_user, session_id=session_id
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    await session_service.delete_session(
        app_name="app", user_id=current_user, session_id=session_id
    )
    return {"status": "success"}


# ==============================================================================
# ADMIN ENDPOINTS
# ==============================================================================

@app.get("/api/admin/users")
async def admin_list_users(
    current_admin: str = Depends(get_current_admin),
) -> dict[str, Any]:
    """Lists all registered users in the database (Admin only)."""
    users = await user_store.list_users()
    for u in users:
        u["is_primary_admin"] = (u["email"] == user_store.admin_email)
    return {"users": users}


@app.post("/api/admin/users/{email}/toggle-admin")
async def admin_toggle_role(
    email: str,
    current_admin: str = Depends(get_current_admin),
) -> dict[str, Any]:
    """Toggles administrative privileges for a user (Admin only)."""
    try:
        new_state = await user_store.toggle_admin(email)
        return {"status": "success", "email": email, "is_admin": new_state}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/admin/sessions")
async def admin_list_sessions(
    current_admin: str = Depends(get_current_admin),
) -> dict[str, Any]:
    """Lists all sessions across all users in the system (Admin only)."""
    try:
        # Passing user_id=None gets sessions for all users
        response = await session_service.list_sessions(
            app_name="app", user_id=None
        )
        sessions_list = []
        for s in response.sessions:
            sessions_list.append({
                "id": s.id,
                "app_name": s.app_name,
                "user_id": s.user_id,
                "last_update_time": s.last_update_time,
                "state": s.state,
            })
        return {"sessions": sessions_list}
    except Exception as e:
        logger.error("Error in admin list sessions: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/admin/sessions/{user_id}/{session_id}")
async def admin_delete_session(
    user_id: str,
    session_id: str,
    current_admin: str = Depends(get_current_admin),
) -> dict[str, str]:
    """Force deletes a user session and all its GCS/local artifacts (Admin only)."""
    session = await session_service.get_session(
        app_name="app", user_id=user_id, session_id=session_id
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    await session_service.delete_session(
        app_name="app", user_id=user_id, session_id=session_id
    )
    return {"status": "success"}


@app.get("/api/admin/stats")
async def admin_get_stats(
    current_admin: str = Depends(get_current_admin),
) -> dict[str, Any]:
    """Aggregates system statistics for the Admin dashboard (Admin only)."""
    try:
        users = await user_store.list_users()
        sessions_response = await session_service.list_sessions(
            app_name="app", user_id=None
        )

        total_users = len(users)
        total_sessions = len(sessions_response.sessions)
        admin_count = sum(1 for u in users if u.get("is_admin", False))

        return {
            "total_users": total_users,
            "total_sessions": total_sessions,
            "admin_users": admin_count,
            "db_type": "Firestore" if IS_DEPLOYED else "SQLite",
            "environment": "production" if IS_DEPLOYED else "local"
        }
    except Exception as e:
        logger.error("Error aggregating admin stats: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/admin/cleanup")
async def admin_trigger_cleanup(
    background_tasks: BackgroundTasks,
    current_admin: str = Depends(get_current_admin),
) -> dict[str, str]:
    """Manually triggers GCS and Firestore cleanup in the background (Admin only)."""
    background_tasks.add_task(run_session_cleanup)
    return {"status": "success", "message": "Cleanup job triggered in background"}


# ==============================================================================
# MAIN & UTILITY ENDPOINTS
# ==============================================================================

@app.post("/api/chat")
async def chat_endpoint(
    request: ChatRequest,
    current_user: str = Depends(get_current_user),
) -> StreamingResponse:
    """Streams agent execution events for the user question and optional CSV file.

    Args:
        request: The ChatRequest data containing the prompt, session, and file.
        current_user: The authenticated user's ID.

    Returns:
        A StreamingResponse emitting server-sent events.
    """
    session_id: str = request.sessionId or "default-session"

    # Create session if it doesn't exist
    try:
        await session_service.create_session(
            app_name="app", user_id=current_user, session_id=session_id
        )
    except Exception:
        # Ignore if session already exists
        pass

    # Build the prompt parts
    parts = []
    if request.file:
        try:
            file_bytes = base64.b64decode(request.file.data.encode("utf-8"))
            parts.append(
                types.Part.from_bytes(
                    data=file_bytes,
                    mime_type=request.file.type,
                )
            )
        except Exception as e:
            raise HTTPException(
                status_code=400, detail=f"Failed to decode base64 file: {e}"
            )

    parts.append(types.Part.from_text(text=request.message))
    new_message = types.Content(role="user", parts=parts)

    # Initialize the local Runner
    runner = Runner(
        agent=root_agent,
        app_name="app",
        session_service=session_service,
        artifact_service=artifact_service,
    )

    async def event_generator():
        try:
            async for event in runner.run_async(
                user_id=current_user,
                session_id=session_id,
                new_message=new_message,
            ):
                event_dict = event.model_dump(by_alias=True)
                event_serializable = serialize_event(event_dict)
                yield f"data: {json.dumps(event_serializable)}\n\n"
        except Exception as err:
            logger.error("Error during runner execution: %s", err, exc_info=True)
            # Stream the error event
            yield f"data: {json.dumps({'error': str(err)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/artifacts/{session_id}/{filename}")
async def get_artifact(
    session_id: str,
    filename: str,
    current_user: str = Depends(get_current_user),
) -> Response:
    """Retrieves a generated artifact file by filename.

    Args:
        session_id: The ID of the current session.
        filename: The name of the artifact file to load.
        current_user: The authenticated user's ID.

    Returns:
        A Response containing the raw file data.
    """
    try:
        artifact = await artifact_service.load_artifact(
            app_name="app",
            user_id=current_user,
            session_id=session_id,
            filename=filename,
        )
        if not artifact:
            raise HTTPException(status_code=404, detail="Artifact not found")

        if artifact.inline_data is not None:
            data_bytes = artifact.inline_data.data
            mime_type = artifact.inline_data.mime_type
        elif artifact.text is not None:
            data_bytes = artifact.text.encode("utf-8")
            mime_type = "text/plain"
        else:
            raise HTTPException(status_code=404, detail="Unsupported artifact format")

        return Response(content=data_bytes, media_type=mime_type)
    except Exception as e:
        raise HTTPException(
            status_code=404, detail=f"Failed to load artifact {filename}: {e}"
        )


@app.get("/")
def read_index() -> HTMLResponse:
    """Serves the static index.html frontend page.

    Returns:
        An HTMLResponse containing index.html content.
    """
    index_path = os.path.join(AGENT_DIR, "app", "static", "index.html")
    if not os.path.exists(index_path):
        # Fallback if UI is not created yet
        return HTMLResponse(
            content="<h1>Ask Your Data UI is loading...</h1>", status_code=200
        )
    with open(index_path, "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content, status_code=200)


# Serve other static files (CSS, JS) from app/static
app.mount("/static", StaticFiles(directory=os.path.join(AGENT_DIR, "app", "static")), name="static")


@app.post("/feedback")
def collect_feedback(feedback: Feedback) -> dict[str, str]:
    """Collect and log feedback."""
    try:
        logger.log_struct(feedback.model_dump(), severity="INFO")
    except Exception:
        pass
    return {"status": "success"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
