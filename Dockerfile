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

# Install uv package manager
RUN pip install --no-cache-dir uv==0.8.13

WORKDIR /code

# Copy package config files
COPY ./pyproject.toml ./uv.lock* ./README.md ./

# Sync dependencies (excluding dev tools)
RUN uv sync --frozen --no-dev --no-editable

# Stage 2: Final minimal runtime image
FROM python:3.12-slim AS runner

WORKDIR /code

# Copy the virtual environment from the builder stage
COPY --from=builder /code/.venv /code/.venv

# Copy the application source code
COPY ./app ./app

# Add virtual environment to PATH
ENV PATH="/code/.venv/bin:$PATH"

ARG COMMIT_SHA=""
ENV COMMIT_SHA=${COMMIT_SHA}

ARG AGENT_VERSION=0.0.0
ENV AGENT_VERSION=${AGENT_VERSION}

EXPOSE 8080

# Run uvicorn directly from the virtual environment
CMD ["uvicorn", "app.fast_api_app:app", "--host", "0.0.0.0", "--port", "8080"]