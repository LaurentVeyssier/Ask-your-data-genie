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
    os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
except Exception:
    # Fallback if running in a credential-less environment
    os.environ["GOOGLE_CLOUD_PROJECT"] = os.getenv("GOOGLE_CLOUD_PROJECT", "mock-project")

os.environ["GOOGLE_CLOUD_LOCATION"] = os.getenv("GOOGLE_CLOUD_LOCATION", "global")
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

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

2. **Generating Graphics (IMPORTANT)**:
   - If the user asks for a chart, graph, or plot, ALWAYS use the `plotly` library to create beautiful interactive figures.
   - To return the interactive chart to the user, you MUST write the Plotly figure object to a file named `plotly_chart.json` in the current directory using `fig.write_json('plotly_chart.json')` or `plotly.io.to_json(fig)` saved to that file.
   - The platform will automatically capture `plotly_chart.json` as an output artifact and render it as an interactive chart in the web interface.
   
   *Example Plotly Output:*
   ```python
   import pandas as pd
   import plotly.express as px
   
   # 1. Load the data file (use the exact name provided in the chat)
   df = pd.read_csv('data_1_1.csv')
   
   # 2. Perform any computations
   # 3. Create the plotly figure
   fig = px.bar(df, x='Category', y='Sales', title='Sales by Category')
   
   # 4. Save the figure to plotly_chart.json
   fig.write_json('plotly_chart.json')
   print("Saved chart to plotly_chart.json")
   ```

3. **Response Formatting**:
   - Keep your explanations clear, concise, and professional.
   - After executing the code, summarize the findings and insights to the user.
   - Refer to any generated charts in your response.
   
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

# Define the root agent with our custom FileSavingLocalCodeExecutor
root_agent: Agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-3.5-flash",
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
)

# Instantiate the ADK App
app: App = App(
    root_agent=root_agent,
    name="app",
)
