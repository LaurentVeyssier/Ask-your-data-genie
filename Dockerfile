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

# Stage 1: Build virtual environment
FROM python:3.12-slim AS builder

# OPTIMIZATION 1: Instant binary copy instead of pip install
COPY --from=ghcr.io/astral-sh/uv:0.8.13 /uv /uvx /bin/

# OPTIMIZATION 3: Disable bytecode compilation to keep image size small (saves GCP storage)
ENV UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=copy

WORKDIR /code

# Copy package config files
COPY ./pyproject.toml ./uv.lock* ./README.md ./

# OPTIMIZATION 2: Cache mount ensures blazing-fast local iterations
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# Stage 2: Final minimal runtime image
FROM python:3.12-slim AS runner

# Production logging optimization for GCP Cloud Logging
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/code/.venv/bin:$PATH"

WORKDIR /code

# OPTIMIZATION 4: Security Hardening (Non-root user for GCP)
# Create appuser first so we can copy files with correct ownership
RUN useradd -m -u 8888 appuser && chown appuser:appuser /code

# Copy the virtual environment from the builder stage with correct ownership
COPY --chown=appuser:appuser --from=builder /code/.venv /code/.venv

# Copy the application source code with correct ownership
COPY --chown=appuser:appuser ./app ./app

USER appuser

ARG COMMIT_SHA=""
ENV COMMIT_SHA=${COMMIT_SHA}

ARG AGENT_VERSION=0.0.0
ENV AGENT_VERSION=${AGENT_VERSION}

EXPOSE 8080

CMD ["uvicorn", "app.fast_api_app:app", "--host", "0.0.0.0", "--port", "8080"]