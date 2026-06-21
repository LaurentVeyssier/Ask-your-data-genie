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

import google.auth
from fastapi import FastAPI, HTTPException, Response
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
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

# Persistent In-Memory Services for the /api/chat route
session_service = InMemorySessionService()
artifact_service = InMemoryArtifactService()


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


@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest) -> StreamingResponse:
    """Streams agent execution events for the user question and optional CSV file.

    Args:
        request: The ChatRequest data containing the prompt, session, and file.

    Returns:
        A StreamingResponse emitting server-sent events.
    """
    session_id: str = request.sessionId or "default-session"

    # Create session if it doesn't exist
    try:
        await session_service.create_session(
            app_name="app", user_id="user", session_id=session_id
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
                user_id="user",
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
async def get_artifact(session_id: str, filename: str) -> Response:
    """Retrieves a generated artifact file by filename.

    Args:
        session_id: The ID of the current session.
        filename: The name of the artifact file to load.

    Returns:
        A Response containing the raw file data.
    """
    try:
        artifact = await artifact_service.load_artifact(
            app_name="app",
            user_id="user",
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
