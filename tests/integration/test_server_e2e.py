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
import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from typing import Any

import dotenv
dotenv.load_dotenv()

import pytest
import requests
from requests.exceptions import RequestException

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_URL = "http://127.0.0.1:8001"
STREAM_URL = BASE_URL + "/run_sse"
FEEDBACK_URL = BASE_URL + "/feedback"

HEADERS = {"Content-Type": "application/json"}


def log_output(pipe: Any, log_func: Any) -> None:
    """Log the output from the given pipe."""
    for line in iter(pipe.readline, ""):
        log_func(line.strip())


def start_server() -> subprocess.Popen[str]:
    """Start the FastAPI server using subprocess and log its output."""
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.fast_api_app:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8001",
    ]
    env = os.environ.copy()
    env["INTEGRATION_TEST"] = "TRUE"
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
    )

    # Start threads to log stdout and stderr in real-time
    threading.Thread(
        target=log_output, args=(process.stdout, logger.info), daemon=True
    ).start()
    threading.Thread(
        target=log_output, args=(process.stderr, logger.error), daemon=True
    ).start()

    return process


def wait_for_server(timeout: int = 90, interval: int = 1) -> bool:
    """Wait for the server to be ready."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            response = requests.get("http://127.0.0.1:8001/docs", timeout=10)
            if response.status_code == 200:
                logger.info("Server is ready")
                return True
        except RequestException:
            pass
        time.sleep(interval)
    logger.error(f"Server did not become ready within {timeout} seconds")
    return False


@pytest.fixture(scope="session")
def server_fixture(request: Any) -> Iterator[subprocess.Popen[str]]:
    """Pytest fixture to start and stop the server for testing."""
    # Clean up test database if it exists
    if os.path.exists("test_users.db"):
        try:
            os.remove("test_users.db")
        except Exception:
            pass

    logger.info("Starting server process")
    server_process = start_server()
    if not wait_for_server():
        pytest.fail("Server failed to start")
    logger.info("Server process started")

    def stop_server() -> None:
        logger.info("Stopping server process")
        server_process.terminate()
        server_process.wait()
        logger.info("Server process stopped")
        # Clean up test database after stop
        if os.path.exists("test_users.db"):
            try:
                os.remove("test_users.db")
            except Exception:
                pass

    request.addfinalizer(stop_server)
    yield server_process


def test_chat_stream(server_fixture: subprocess.Popen[str]) -> None:
    """Test the chat stream functionality."""
    logger.info("Starting chat stream test")
    # Create session first
    user_id = "test_user_123"
    session_data = {"state": {"preferred_language": "English", "visit_count": 1}}

    session_url = f"{BASE_URL}/apps/app/users/{user_id}/sessions"
    session_response = requests.post(
        session_url,
        headers=HEADERS,
        json=session_data,
        timeout=60,
    )
    assert session_response.status_code == 200
    logger.info(f"Session creation response: {session_response.json()}")
    session_id = session_response.json()["id"]

    # Then send chat message
    data = {
        "app_name": "app",
        "user_id": user_id,
        "session_id": session_id,
        "new_message": {
            "role": "user",
            "parts": [{"text": "Hi!"}],
        },
        "streaming": True,
    }
    response = requests.post(
        STREAM_URL, headers=HEADERS, json=data, stream=True, timeout=60
    )
    assert response.status_code == 200

    # Parse SSE events from response
    events = []
    for line in response.iter_lines():
        if line:
            # SSE format is "data: {json}"
            line_str = line.decode("utf-8")
            if line_str.startswith("data: "):
                event_json = line_str[6:]  # Remove "data: " prefix
                event = json.loads(event_json)
                events.append(event)

    assert events, "No events received from stream"
    # Check for valid content in the response
    has_text_content = False
    for event in events:
        content = event.get("content")
        if (
            content is not None
            and content.get("parts")
            and any(part.get("text") for part in content["parts"])
        ):
            has_text_content = True
            break

    assert has_text_content, "Expected at least one event with text content"


def test_chat_stream_error_handling(server_fixture: subprocess.Popen[str]) -> None:
    """Test the chat stream error handling."""
    logger.info("Starting chat stream error handling test")
    data = {
        "input": {"messages": [{"type": "invalid_type", "content": "Cause an error"}]}
    }
    response = requests.post(
        STREAM_URL, headers=HEADERS, json=data, stream=True, timeout=10
    )

    assert response.status_code == 422, (
        f"Expected status code 422, got {response.status_code}"
    )
    logger.info("Error handling test completed successfully")


def test_collect_feedback(server_fixture: subprocess.Popen[str]) -> None:
    """
    Test the feedback collection endpoint (/feedback) to ensure it properly
    logs the received feedback.
    """
    # Create sample feedback data
    feedback_data = {
        "score": 4,
        "user_id": "test-user-456",
        "session_id": "test-session-456",
        "text": "Great response!",
    }

    response = requests.post(
        FEEDBACK_URL, json=feedback_data, headers=HEADERS, timeout=10
    )
    assert response.status_code == 200


def test_admin_flow(server_fixture: subprocess.Popen[str]) -> None:
    """Test the admin RBAC permissions, listing users/stats, role toggling, and cleanup."""
    import uuid
    unique_suffix = uuid.uuid4().hex[:6]
    reg_email = f"user_{unique_suffix}@example.com"
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com").strip().lower()
    password = "password123"

    # 1. Register regular user
    reg_res = requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": reg_email, "password": password},
        headers=HEADERS,
        timeout=10
    )
    assert reg_res.status_code == 200
    reg_token = reg_res.json()["token"]
    assert reg_res.json()["is_admin"] is False

    # 2. Register/Login admin user
    admin_res = requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": admin_email, "password": password},
        headers=HEADERS,
        timeout=10
    )
    if admin_res.status_code == 400:  # Already exists
        admin_res = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": admin_email, "password": password},
            headers=HEADERS,
            timeout=10
        )
    assert admin_res.status_code == 200
    admin_token = admin_res.json()["token"]
    assert admin_res.json()["is_admin"] is True

    # 3. Regular user tries to access admin stats -> 403
    reg_headers = {"Authorization": f"Bearer {reg_token}", "Content-Type": "application/json"}
    stats_res = requests.get(f"{BASE_URL}/api/admin/stats", headers=reg_headers, timeout=10)
    assert stats_res.status_code == 403

    # 4. Admin accesses stats -> 200
    admin_headers = {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}
    stats_res = requests.get(f"{BASE_URL}/api/admin/stats", headers=admin_headers, timeout=10)
    assert stats_res.status_code == 200
    stats_data = stats_res.json()
    assert stats_data["total_users"] >= 2
    assert stats_data["admin_users"] >= 1

    # 5. Admin lists users
    users_res = requests.get(f"{BASE_URL}/api/admin/users", headers=admin_headers, timeout=10)
    assert users_res.status_code == 200
    users_list = users_res.json()["users"]
    emails = [u["email"] for u in users_list]
    assert reg_email in emails
    assert admin_email in emails

    # 6. Admin promotes regular user
    promote_res = requests.post(
        f"{BASE_URL}/api/admin/users/{reg_email}/toggle-admin",
        headers=admin_headers,
        timeout=10
    )
    assert promote_res.status_code == 200
    assert promote_res.json()["is_admin"] is True

    # 7. Check that the promoted user is now admin
    me_res = requests.get(f"{BASE_URL}/api/auth/me", headers=reg_headers, timeout=10)
    assert me_res.status_code == 200
    assert me_res.json()["is_admin"] is True

    # 8. Attempt to demote primary admin -> should still be True (ignored/disallowed)
    demote_primary_res = requests.post(
        f"{BASE_URL}/api/admin/users/{admin_email}/toggle-admin",
        headers=reg_headers,
        timeout=10
    )
    assert demote_primary_res.status_code == 200
    assert demote_primary_res.json()["is_admin"] is True

    # 9. Original admin demotes the promoted user back to regular user
    demote_res = requests.post(
        f"{BASE_URL}/api/admin/users/{reg_email}/toggle-admin",
        headers=admin_headers,
        timeout=10
    )
    assert demote_res.status_code == 200
    assert demote_res.json()["is_admin"] is False

    # 10. Admin triggers cleanup -> 200
    cleanup_res = requests.post(f"{BASE_URL}/api/admin/cleanup", headers=admin_headers, timeout=10)
    assert cleanup_res.status_code == 200
    assert cleanup_res.json()["status"] == "success"


def test_share_here_now(server_fixture: subprocess.Popen[str]) -> None:
    """Test the here.now sharing functionality."""
    logger.info("Starting share here-now test")

    # 1. Register a user to get token
    import uuid
    unique_suffix = uuid.uuid4().hex[:6]
    email = f"share_user_{unique_suffix}@example.com"
    password = "password123"

    reg_res = requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": password},
        headers=HEADERS,
        timeout=10
    )
    assert reg_res.status_code == 200
    token = reg_res.json()["token"]

    # 2. Call share endpoint
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {
        "title": "Test Share",
        "textHtml": "<h3>Sales Analysis</h3><p>Total sales of widgets: <b>$1,500</b></p>",
        "chartJson": {
            "data": [{"x": ["widgets", "gadgets"], "y": [1500, 800], "type": "bar"}],
            "layout": {"title": "Sales by Product"}
        }
    }

    share_res = requests.post(
        f"{BASE_URL}/api/share/here-now",
        json=payload,
        headers=headers,
        timeout=30
    )

    assert share_res.status_code == 200
    share_data = share_res.json()
    assert "siteUrl" in share_data
    assert share_data["siteUrl"].startswith("https://")
    assert "claimUrl" in share_data


