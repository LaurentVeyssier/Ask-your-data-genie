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

"""Service for sharing analysis results and charts via here.now."""

import hashlib
import json
import logging
import os
import tempfile
from typing import Dict, Any, Optional

import requests

logger = logging.getLogger(__name__)

# here.now API details
HERENOW_BASE_URL = "https://here.now"


def generate_share_html(title: str, text_html: str, chart_json: Optional[Dict[str, Any]] = None) -> str:
    """Generates a self-contained, beautifully styled HTML dashboard for the shared analysis.

    Args:
        title: The title of the analysis.
        text_html: Fully rendered HTML markdown response of the agent.
        chart_json: The optional Plotly JSON dict representation.

    Returns:
        The complete HTML string.
    """
    has_chart = chart_json is not None
    chart_data_str = json.dumps(chart_json.get("data", [])) if has_chart else "[]"
    chart_layout_str = json.dumps(chart_json.get("layout", {})) if has_chart else "{}"

    # Standard CDN Plotly Version used in the main app
    plotly_script = '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>' if has_chart else ''

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} - Ask-Your-Data Genie</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    {plotly_script}
    <style>
        :root {{
            --bg-gradient: radial-gradient(circle at top right, #111827, #030712);
            --card-bg: rgba(17, 24, 39, 0.7);
            --card-border: rgba(255, 255, 255, 0.08);
            --accent-cyan: #06b6d4;
            --accent-blue: #3b82f6;
            --text-primary: #f3f4f6;
            --text-secondary: #9ca3af;
            --shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        body {{
            font-family: 'Plus Jakarta Sans', sans-serif;
            background: var(--bg-gradient);
            color: var(--text-primary);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            padding: 2rem 1.5rem;
            line-height: 1.6;
        }}

        header {{
            max-width: 1200px;
            width: 100%;
            margin: 0 auto 2rem auto;
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding-bottom: 1rem;
            border-bottom: 1px solid var(--card-border);
        }}

        .logo {{
            display: flex;
            align-items: center;
            gap: 0.75rem;
            font-weight: 700;
            font-size: 1.25rem;
            background: linear-gradient(135deg, var(--accent-cyan), var(--accent-blue));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}

        .badge {{
            background: rgba(6, 182, 212, 0.15);
            color: var(--accent-cyan);
            border: 1px solid rgba(6, 182, 212, 0.3);
            padding: 0.25rem 0.75rem;
            border-radius: 9999px;
            font-size: 0.85rem;
            font-weight: 500;
        }}

        main {{
            max-width: 1200px;
            width: 100%;
            margin: 0 auto;
            flex: 1;
            display: grid;
            grid-template-columns: { '1fr 1fr' if has_chart else '1fr' };
            gap: 2rem;
        }}

        @media (max-width: 968px) {{
            main {{
                grid-template-columns: 1fr;
            }}
        }}

        .panel {{
            background: var(--card-bg);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 2rem;
            box-shadow: var(--shadow);
            display: flex;
            flex-direction: column;
        }}

        .panel-title {{
            font-size: 1.15rem;
            font-weight: 600;
            margin-bottom: 1.5rem;
            display: flex;
            align-items: center;
            gap: 0.5rem;
            color: var(--text-primary);
            border-bottom: 1px solid rgba(255,255,255,0.05);
            padding-bottom: 0.75rem;
        }}

        .panel-title i {{
            color: var(--accent-cyan);
        }}

        /* Text Content Styling */
        .analysis-text {{
            font-size: 0.95rem;
            color: #d1d5db;
        }}

        .analysis-text p {{
            margin-bottom: 1rem;
        }}

        .analysis-text h1, .analysis-text h2, .analysis-text h3 {{
            margin: 1.5rem 0 0.75rem 0;
            color: var(--text-primary);
            font-weight: 600;
        }}

        .analysis-text ul, .analysis-text ol {{
            margin-left: 1.5rem;
            margin-bottom: 1rem;
        }}

        .analysis-text li {{
            margin-bottom: 0.25rem;
        }}

        .analysis-text code {{
            background: rgba(255, 255, 255, 0.08);
            padding: 0.2rem 0.4rem;
            border-radius: 4px;
            font-family: monospace;
            font-size: 0.9em;
        }}

        .analysis-text pre {{
            background: #0f172a;
            border: 1px solid var(--card-border);
            border-radius: 8px;
            padding: 1rem;
            overflow-x: auto;
            margin: 1rem 0;
        }}

        .analysis-text pre code {{
            background: none;
            padding: 0;
            font-size: 0.85rem;
            color: #e2e8f0;
        }}

        .analysis-text table {{
            width: 100%;
            border-collapse: collapse;
            margin: 1.5rem 0;
            font-size: 0.9rem;
        }}

        .analysis-text th, .analysis-text td {{
            padding: 0.75rem;
            text-align: left;
            border-bottom: 1px solid rgba(255, 255, 255, 0.06);
        }}

        .analysis-text th {{
            color: var(--accent-cyan);
            font-weight: 600;
            background: rgba(255, 255, 255, 0.02);
        }}

        /* Visualization Container */
        .chart-container {{
            width: 100%;
            flex: 1;
            min-height: 450px;
            position: relative;
        }}

        footer {{
            max-width: 1200px;
            width: 100%;
            margin: 3rem auto 0 auto;
            text-align: center;
            font-size: 0.8rem;
            color: var(--text-secondary);
            border-top: 1px solid var(--card-border);
            padding-top: 1.5rem;
        }}

        footer a {{
            color: var(--accent-cyan);
            text-decoration: none;
        }}

        footer a:hover {{
            text-decoration: underline;
        }}
    </style>
</head>
<body>
    <header>
        <div class="logo">
            <i class="fa-solid fa-chart-pie"></i> Ask-Your-Data Genie
        </div>
        <div class="badge">
            <i class="fa-regular fa-clock"></i> Shared Analysis (Expires in 24h)
        </div>
    </header>

    <main>
        <section class="panel">
            <div class="panel-title">
                <i class="fa-solid fa-file-invoice"></i> Analysis Insights
            </div>
            <div class="analysis-text">
                {text_html}
            </div>
        </section>

        {"<section class=\"panel\">" if has_chart else ""}
            {"<div class=\"panel-title\"><i class=\"fa-solid fa-chart-line\"></i> Interactive Graphic</div>" if has_chart else ""}
            {"<div id=\"chart\" class=\"chart-container\"></div>" if has_chart else ""}
        {"</section>" if has_chart else ""}
    </main>

    <footer>
        <p>Generated by <a href="https://github.com/LaurentVeyssier/Ask-your-data-genie" target="_blank">Ask-Your-Data Genie</a> using the <strong>here.now</strong> ephemeral publishing system.</p>
    </footer>

    {f'''<script>
        document.addEventListener("DOMContentLoaded", function() {{
            const data = {chart_data_str};
            const layout = {chart_layout_str};
            
            // Remove hardcoded width and height to force true layout responsiveness
            delete layout.width;
            delete layout.height;
            
            // Format layout for dark theme
            layout.paper_bgcolor = "rgba(0,0,0,0)";
            layout.plot_bgcolor = "rgba(0,0,0,0)";
            layout.font = {{ color: "#f3f4f6", family: "'Plus Jakarta Sans', sans-serif" }};
            
            if (layout.xaxis) {{
                layout.xaxis.gridcolor = "rgba(255,255,255,0.06)";
                layout.xaxis.linecolor = "rgba(255,255,255,0.1)";
            }}
            if (layout.yaxis) {{
                layout.yaxis.gridcolor = "rgba(255,255,255,0.06)";
                layout.yaxis.linecolor = "rgba(255,255,255,0.1)";
            }}
            
            Plotly.newPlot('chart', data, layout, {{ responsive: true }});
        }});
    </script>''' if has_chart else ''}
</body>
</html>
"""
    return html_content


def publish_to_herenow(html_content: str) -> Dict[str, Any]:
    """Publishes the HTML content anonymously to here.now.

    Args:
        html_content: The string containing the full HTML dashboard.

    Returns:
        A dictionary containing the siteUrl and claimUrl.
    """
    temp_file = None
    try:
        # 1. Write the HTML string to a temporary file
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as f:
            f.write(html_content)
            temp_file = f.name

        # Calculate file size and sha256 hash
        file_size = os.path.getsize(temp_file)
        
        sha256 = hashlib.sha256()
        with open(temp_file, "rb") as bf:
            while chunk := bf.read(8192):
                sha256.update(chunk)
        file_hash = sha256.hexdigest()

        # 2. Start the publish session via POST /api/v1/publish
        headers = {
            "Content-Type": "application/json",
            "X-HereNow-Client": "ask-your-data/fastapi",
        }
        
        # Check if HERENOW_API_KEY is available
        api_key = os.environ.get("HERENOW_API_KEY")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        init_body = {
            "files": [
                {
                    "path": "index.html",
                    "size": file_size,
                    "contentType": "text/html; charset=utf-8",
                    "hash": file_hash
                }
            ]
        }

        logger.info("Initializing here.now publish...")
        init_res = requests.post(
            f"{HERENOW_BASE_URL}/api/v1/publish",
            headers=headers,
            json=init_body,
            timeout=30
        )
        
        if not init_res.ok:
            raise ValueError(f"here.now initialization failed: {init_res.text}")
            
        init_data = init_res.json()
        
        slug = init_data["slug"]
        version_id = init_data["upload"]["versionId"]
        finalize_url = init_data["upload"]["finalizeUrl"]
        site_url = init_data["siteUrl"]
        claim_url = init_data.get("claimUrl", "")
        
        upload_info = init_data["upload"]["uploads"][0]
        upload_url = upload_info["url"]

        # 3. Upload the index.html file via PUT to the pre-signed URL
        logger.info("Uploading index.html to here.now...")
        with open(temp_file, "rb") as upload_file:
            upload_headers = {"Content-Type": "text/html; charset=utf-8"}
            upload_res = requests.put(
                upload_url,
                headers=upload_headers,
                data=upload_file,
                timeout=60
            )
            
        if not (200 <= upload_res.status_code < 300):
            raise ValueError(f"File upload failed (HTTP {upload_res.status_code})")

        # 4. Finalize the publish via POST to the finalizeUrl
        logger.info("Finalizing here.now publish...")
        finalize_body = {"versionId": version_id}
        
        fin_headers = {
            "Content-Type": "application/json",
            "X-HereNow-Client": "ask-your-data/fastapi"
        }
        if api_key:
            fin_headers["Authorization"] = f"Bearer {api_key}"

        finalize_res = requests.post(
            finalize_url,
            headers=fin_headers,
            json=finalize_body,
            timeout=30
        )
        
        if not finalize_res.ok:
            raise ValueError(f"here.now finalization failed: {finalize_res.text}")

        logger.info("here.now publish complete! URL: %s", site_url)
        return {
            "siteUrl": site_url,
            "claimUrl": claim_url,
            "success": True
        }

    except Exception as e:
        logger.exception("Error publishing to here.now: %s", e)
        return {
            "success": False,
            "error": str(e)
        }
    finally:
        if temp_file and os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except Exception:
                pass
