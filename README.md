# Ask-Your-Data Analyst Genie 📊🤖

An intelligent, secure, and feature-rich data analysis web application powered by a Gemini ReAct agent. Users can upload CSV files, perform complex mathematical or statistical operations, generate premium interactive Plotly visualizations, and chat with their files in natural language.

The application implements full user authentication (email + password), persistent multi-device sessions, automatic GCS/Firestore cleanups, and a glassmorphic Administrative Control Panel for user and system management.

---

## ✨ Features & Architecture

### 1. 🤖 ReAct Analyst Agent
- **Powered by GenAI SDK**: Implements the modern `google-genai` client for prompt processing and tools invocation.
- **Secure Sandbox Execution**: Executes generated Python code inside a localized, secure sandbox (`FileSavingLocalCodeExecutor`).
- **Interactive Visualizations**: Generates rich dynamic graphics, exporting output directly as interactive [Plotly](https://plotly.com/javascript/) charts rendered seamlessly on the frontend.

### 2. 🔐 User Authentication & Security
- **Secure Credentials**: Hashes and verifies passwords securely using **bcrypt**.
- **JWT Session Tokens**: Signs and validates session states using **PyJWT** (HS256 algorithm) stored locally in browser storage.
- **Glassmorphic UI**: Beautiful signup and login overlays styled using modern dark-theme glassmorphism and subtle animations.

### 3. 💾 Hybrid Session Persistence & Recovery
- **Local Developer Mode**: Zero-configuration runs utilizing **SQLite** (`local_users.db`) for user credentials and an in-memory session engine.
- **Production Mode**: Persistent user document records in **GCP Firestore** and session history, while file uploads, outputs, and generated Plotly chart configurations are stored securely in **Google Cloud Storage (GCS)**.
- **Complete Workspace Recovery**: Instantly reconstructs chat history turns, collapsible executed Python code accordions, and fully interactive Plotly graphics when returning to past sessions.

### 4. 🧹 Automatic & Manual 7-Day Purging
- Background FastAPI tasks automatically identify and clean up sessions, artifacts, and GCS storage objects older than 7 days.
- Admins can trigger manual sweeping runs on-demand.

### 5. 🛡️ Admin Control Panel (RBAC)
Designate a primary administrator via environment variables to gain access to a dedicated dashboard modal containing:
- **Users Portal**: Lists all registered accounts, shows registration dates, and supports role promotion or demotion.
- **Active Sessions Monitor**: Real-time listing of active sessions across the entire system. Allows admins to force-terminate and delete storage artifacts for any session.
- **Stats & System Control**: Glowing analytics cards (counters for total users, active sessions, admins), database backend details, and manual broom triggers.

---

## 📁 Project Structure

```
ask-your-data/
├── app/                     # Core application source
│   ├── app_utils/           # Helpers and database models
│   │   ├── auth_service.py      # BCrypt hashing, JWTs, SQLite/Firestore UserStore
│   │   ├── firestore_session.py # Firestore session engine & GCS purging
│   │   └── telemetry.py         # Google Cloud Trace and metrics setup
│   ├── static/              # Frontend web application assets
│   │   ├── app.js               # UI controller, Plotly renderer, Admin API clients
│   │   ├── index.html           # Glassmorphic layout, chat panels, admin portal
│   │   └── style.css            # Custom CSS tokens, animation keyframes, scrollbars
│   ├── agent.py             # Agent prompt logic and tools registry
│   ├── fast_api_app.py      # FastAPI routing, security dependencies, and admin endpoints
│   └── local_executor.py    # Python code execution sandbox
├── tests/                   # Automated validation suite
│   ├── integration/         # Server and Agent end-to-end tests
│   └── unit/                # Core unit logic tests
├── .env.example             # Template for developer configuration
├── pyproject.toml           # Package declarations and dependencies
└── GEMINI.md                # Development workflows
```

---

## ⚙️ Configuration & Environment

Copy [.env.example](.env.example) to `.env` and adjust the variables:

```bash
cp .env.example .env
```

| Key | Description | Default |
|-----|-------------|---------|
| `ENVIRONMENT` | `'local'` (uses SQLite/in-memory) or `'production'` (uses GCS/Firestore) | `local` |
| `JWT_SECRET` | Secret key used to sign JWT authentication tokens | *Auto-generated* |
| `ADMIN_EMAIL` | Email address automatically promoted to Administrator role | `admin@example.com` |
| `GEMINI_MODEL` | Gemini model name used for processing chat and analysis | `gemini-3.5-flash` |
| `GOOGLE_CLOUD_PROJECT` | GCP Project ID (required for Firestore and Vertex AI in production) | `your-gcp-project-id` |
| `GOOGLE_CLOUD_LOCATION` | Region location for Vertex API calls | `global` |
| `LOGS_BUCKET_NAME` | GCP Storage Bucket name for session file artifacts | `your-gcs-bucket-name` |

---

## 🚀 Quick Start

### 1. Requirements
Ensure you have the following installed:
- [uv](https://docs.astral.sh/uv/getting-started/installation/): Fast Python package manager.
- [Google Cloud SDK](https://cloud.google.com/sdk/docs/install): For authenticating with Google Cloud services.

### 2. Dependency Setup
Install project dependencies:
```bash
uv sync
```

### 3. Running Locally

#### Option A: Direct Host Execution (Standard local development)
Run the FastAPI development server:
```bash
uv run uvicorn app.fast_api_app:app --reload --host 127.0.0.1 --port 8000
```
Open your browser and navigate to `http://127.0.0.1:8000`.

- Register a new account.
- If your credentials match `ADMIN_EMAIL`, the violet **Admin Panel** button will become visible in the header.
- You can set the admin email in the `.env` file with the `ADMIN_EMAIL` variable on your first time running the application.

#### Option B: Secure Containerized Execution (Isolated Sandbox)
To isolate code execution and protect your host machine from untrusted AI-generated Python code:
1. Ensure **Docker Desktop** is running.
2. Authenticate Google Cloud SDK locally to generate Application Default Credentials (ADC):
   ```bash
   gcloud auth application-default login
   ```
3. Launch the container stack:
   ```bash
   docker compose up --build
   ```
4. Access the interface at `http://localhost:8000`. The application and all Python code executed by the agent will run isolated inside the container. Your host machine files and processes are fully protected.

---

## 🧪 Testing

The codebase includes comprehensive unit and integration tests (validating session life cycles, security parameters, and role-based access).

Run the tests locally:
```bash
uv run pytest
```

---

## ☁️ Deployment

Deploy the application to Google Cloud Run:

```bash
gcloud config set project <YOUR_PROJECT_ID>
agents-cli deploy
```

For setting up full infrastructure pipelines (Terraform) or continuous integration pipelines, you can run:
```bash
agents-cli scaffold enhance
```
