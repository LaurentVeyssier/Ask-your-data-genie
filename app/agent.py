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

"""Agent definition for the Ask Your Data application."""

import os
from typing import Dict, Any

import google.auth
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from app.local_executor import FileSavingLocalCodeExecutor

# Set up standard Google Cloud credentials and parameters
try:
    _, project_id = google.auth.default()
    if "GOOGLE_CLOUD_PROJECT" not in os.environ:
        os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
except Exception:
    # Fallback if running in a credential-less environment
    if "GOOGLE_CLOUD_PROJECT" not in os.environ:
        os.environ["GOOGLE_CLOUD_PROJECT"] = "mock-project"

os.environ["GOOGLE_CLOUD_LOCATION"] = os.getenv("GOOGLE_CLOUD_LOCATION", "global")

# Configure whether to run using the enterprise Vertex AI backend or Gemini Developer API (using API Key)
# Default to "True" (Vertex AI) if not specified. Supports both legacy/new SDK settings.
# This mapping handles both the legacy GOOGLE_GENAI_USE_VERTEXAI and the newer GOOGLE_GENAI_USE_ENTERPRISE environment variables seamlessly, preventing deprecation warnings
use_vertex = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", os.getenv("GOOGLE_GENAI_USE_ENTERPRISE", "True"))
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = use_vertex
os.environ["GOOGLE_GENAI_USE_ENTERPRISE"] = use_vertex

# Avoid conflict between Vertex AI and Gemini API Key
# Force client to use Application Default Credentials (ADC) when backend switched to Enterprise Vertex AI
if use_vertex == "True":
    os.environ.pop("GOOGLE_API_KEY", None)
    os.environ.pop("GEMINI_API_KEY", None)
else:
    # Cleanse trailing newlines, whitespaces, and Byte Order Marks (\ufeff) from API keys (common side effect in GCP Secret Manager from Windows files)
    if "GEMINI_API_KEY" in os.environ:
        os.environ["GEMINI_API_KEY"] = os.environ["GEMINI_API_KEY"].strip().lstrip('\ufeff')
    if "GOOGLE_API_KEY" in os.environ:
        os.environ["GOOGLE_API_KEY"] = os.environ["GOOGLE_API_KEY"].strip().lstrip('\ufeff')



# System instruction guiding the model on how to write code and generate Plotly charts
SYSTEM_INSTRUCTION: str = """You are a highly capable Data Science AI Assistant.
Your goal is to help users analyze and visualize their CSV data.

When a user uploads a CSV file, it is automatically made available in the current working directory.
The file name will be provided to you (e.g., `data_1_1.csv` or similar).

### Rules for Analysis:
1. **Writing Code**:
   - Write Python code blocks to inspect, clean, and analyze the data.
   - Use standard python libraries like `pandas`, `numpy`, and `plotly`.
   - Your code is executed in a clean subprocess locally.
   - ALWAYS output your code in python markdown blocks like:
     ```python
     # your python code here
     ```
   - Print text answers, statistics, or metrics using `print()`.

2. **Generating Graphics (CRITICAL)**:
   - **Value Check**: Do NOT generate a visualization for basic lookups, schema listings, or simple questions (e.g., "what are the columns?", "show me the first 5 rows", "what is the average age?"). Only generate a chart if the user explicitly asks for one (e.g., "plot", "graph", "visualize") OR if the query involves complex comparisons, trends, correlations, or distributions where a visual aid adds significant cognitive value.
   - **Chart Selection Logic**:
     - **Trends over Time**: Use Line Charts (`px.line`) for temporal data (dates/times on the x-axis).
     - **Comparisons**: Use Bar Charts (`px.bar`) to compare discrete categories. Use horizontal bar charts if there are many categories (>10) or long labels.
     - **Distributions**: Use Histograms (`px.histogram`) or Box Plots (`px.box`) to show the distribution of numeric values, identify outliers, or view density.
     - **Relationships / Correlations**: Use Scatter Plots (`px.scatter`) to show relationships between two numeric columns. Use Heatmaps (`px.imshow`) for correlation matrices.
     - **Compositions**: Avoid pie charts unless comparing a very small number of categories (<=5) summing to 100%. Otherwise, default to a sorted bar chart.

    - **Enhanced Chart Design Rules (For Efficient Analysis)**:
     To ensure graphics maximize analytical efficiency and minimize cognitive load, enforce the following:
     - **Aggregation First**: Do not plot massive, raw datasets directly onto bar or line charts. Aggregate the data first (e.g., `.groupby()`, `.resample()`) so the visualization highlights macro-trends, not noisy data points.
     - **Logical Sorting**: Always sort bar charts by value (descending or ascending) rather than leaving them in random or alphabetical order, unless there is a natural order (like months).
     - **Readability & High-Cardinality**: If a categorical variable has more than 10 categories, use a *horizontal* bar chart (`orientation='h'`) and consider grouping long-tail categories into an "Other" bucket to avoid unreadable axis labels.
     - **Trend Clarity**: When using line charts with high-frequency noise (e.g., daily data over years), plot both the raw data (faded/thin) and a rolling average line (bold) to make the trend immediately clear.
     - **Color & Contrast**: Use color intentionally to represent a variable (dimension) or a specific metric threshold—never use random, multi-colored bars for a single metric. For heatmaps, ensure a diverging color scale (like `RdBu` or `Viridis`) is used with an explicit contrast range.
     - **Clarity**: Always explicitly label your axes (with units if applicable), add appropriate legends, and give the figure a descriptive title that states the core insight (e.g., "Distribution of Revenue" instead of just "Revenue").

   - **Plotly Integration**:
     - To render the interactive chart in the web UI, write the figure object to `plotly_chart.json` in the current directory using `fig.write_json('plotly_chart.json')`.
     - Always label your axes clearly, add appropriate legends, and give the figure a descriptive title.
     - Include interesting descriptive statistics info such as median, standard deviation or count if it helps in understanding the data.
     - If no chart is needed to answer the user's query, do not create or write a Plotly JSON file.

   *Example Plotly Output:*
   ```python
   import pandas as pd
   import plotly.express as px

   # 1. Load and aggregate data for efficient visual analysis
   df = pd.read_csv('data_1_1.csv')
   df_grouped = df.groupby('Category')['Sales'].sum().reset_index().sort_values(by='Sales', ascending=False)

   # 2. Create optimized figure
   fig = px.bar(df_grouped, x='Sales', y='Category', orientation='h',
                title='Top Categories by Total Sales (Descending)',
                labels={'Sales': 'Total Sales ($)', 'Category': 'Product Category'})
   
   fig.update_layout(yaxis={'categoryorder':'total ascending'}) # Ensures strict visual sorting

   # 3. Save the figure
   fig.write_json('plotly_chart.json')
   print("Saved analysis chart to plotly_chart.json")
   ```

3. **Response Formatting**:
   - Keep your explanations clear, concise, and professional.
   - Summarize the data insights first, and then refer to any generated charts in your response.
   - No need to mention the graph has been saved to `plotly_chart.json` as this is for internal use only, not for end user

4. **Tool and Function Calling Restrictions (CRITICAL)**:
   - NEVER attempt to generate or invoke any native function calls or tools.
   - Only output standard text and python markdown blocks.
"""

async def clean_history_callback(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> None:
    """Sanitizes llm_request.contents to convert code execution parts to text,
    ensuring alternating roles and preventing malformed function call errors.
    """
    # Debug log the outgoing request to scratch directory
    import json
    try:
        debug_data = {
            "model": llm_request.model,
            "contents": [c.model_dump() for c in llm_request.contents],
            "config": llm_request.config.model_dump() if llm_request.config else None,
        }
        os.makedirs("scratch", exist_ok=True)
        with open("scratch/llm_request_debug.json", "w", encoding="utf-8") as f:
            json.dump(debug_data, f, indent=2)
    except Exception as e:
        pass

    cleaned_contents = []
    for content in llm_request.contents:
        new_parts = []
        role = content.role or "user"
        
        for part in content.parts:
            if part.executable_code:
                # Convert executable code to python code block text
                code_text = f"\n```python\n{part.executable_code.code}\n```\n"
                new_parts.append(types.Part.from_text(text=code_text))
            elif part.code_execution_result:
                # Convert code execution result to a text block
                result_text = f"\n```\nCode execution result:\n{part.code_execution_result.output}\n```\n"
                new_parts.append(types.Part.from_text(text=result_text))
                role = "user"
            elif getattr(part, "thought", None) or getattr(part, "thought_signature", None):
                # Skip thought and thought_signature parts in the sanitized history
                # because we are mutating the conversation to plain text blocks,
                # which corrupts any cryptographic thought signatures.
                continue
            elif part.text:
                new_parts.append(part)
            else:
                new_parts.append(part)
        
        if new_parts:
            cleaned_contents.append(types.Content(role=role, parts=new_parts))
            
    # Merge consecutive contents with the same role
    merged_contents = []
    for content in cleaned_contents:
        if not merged_contents:
            merged_contents.append(content)
        else:
            last_content = merged_contents[-1]
            if last_content.role == content.role:
                last_content.parts.extend(content.parts)
            else:
                merged_contents.append(content)
                
    llm_request.contents = merged_contents

async def init_agent_callback(callback_context: CallbackContext) -> None:
    """Ensures that the artifact service is initialized during agent runs.
    This is especially required for offline/eval runs (e.g. `agents-cli eval generate`)
    where the ADK framework executes the agent without initializing the artifact service,
    which would otherwise crash the local code executor post-processor with a ValueError.
    """
    inv_ctx = getattr(callback_context, "_invocation_context", None)
    if inv_ctx and getattr(inv_ctx, "artifact_service", None) is None:
        from google.adk.artifacts import InMemoryArtifactService
        inv_ctx.artifact_service = InMemoryArtifactService()


# Define the root agent with our custom FileSavingLocalCodeExecutor
root_agent: Agent = Agent(
    name="root_agent",
    model=Gemini(
        model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash"),
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    generate_content_config=types.GenerateContentConfig(
        temperature=0.0,
    ),
    instruction=SYSTEM_INSTRUCTION,
    code_executor=FileSavingLocalCodeExecutor(
        timeout_seconds=30.0,
    ),
    before_model_callback=clean_history_callback,
    before_agent_callback=init_agent_callback,
)

# Instantiate the ADK App
app: App = App(
    root_agent=root_agent,
    name="app",
)
