// Global Application State
let sessionId = "";
let selectedFile = null;
let isGenerating = false;
let currentChartData = null;

// Initialize
document.addEventListener("DOMContentLoaded", () => {
    generateNewSession();
    setupEventListeners();
});

// Generate Session ID
function generateNewSession() {
    sessionId = "session_" + Math.random().toString(36).substring(2, 15);
    document.getElementById("session-id-display").textContent = sessionId;
    document.getElementById("plotly-chart").innerHTML = "";
    document.getElementById("plotly-chart").classList.add("hidden");
    document.getElementById("viz-empty-state").classList.remove("hidden");
    document.getElementById("export-chart-btn").classList.add("hidden");
    document.getElementById("artifacts-log").classList.add("hidden");
    document.getElementById("artifacts-list").innerHTML = "";
    currentChartData = null;
}

// Setup Event Listeners
function setupEventListeners() {
    const chatInput = document.getElementById("chat-input");
    const sendBtn = document.getElementById("send-btn");
    const fileInput = document.getElementById("file-input");
    const dropzone = document.getElementById("dropzone");
    const removeFileBtn = document.getElementById("remove-file-btn");
    const newChatBtn = document.getElementById("new-chat-btn");
    const exportBtn = document.getElementById("export-chart-btn");

    // Text Input Auto-resize & Keybinds
    chatInput.addEventListener("input", () => {
        chatInput.style.height = "auto";
        chatInput.style.height = (chatInput.scrollHeight) + "px";
        toggleSendButton();
    });

    chatInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    sendBtn.addEventListener("click", sendMessage);

    // File Upload Drag & Drop
    fileInput.addEventListener("change", handleFileSelect);

    dropzone.addEventListener("dragover", (e) => {
        e.preventDefault();
        dropzone.classList.add("dragover");
    });

    dropzone.addEventListener("dragleave", () => {
        dropzone.classList.remove("dragover");
    });

    dropzone.addEventListener("drop", (e) => {
        e.preventDefault();
        dropzone.classList.remove("dragover");
        if (e.dataTransfer.files.length > 0) {
            fileInput.files = e.dataTransfer.files;
            handleFileSelect({ target: fileInput });
        }
    });

    // Remove File
    removeFileBtn.addEventListener("click", () => {
        selectedFile = null;
        fileInput.value = "";
        document.getElementById("file-status").classList.add("hidden");
        document.getElementById("dropzone").classList.remove("hidden");
        toggleSendButton();
    });

    // Reset Chat
    newChatBtn.addEventListener("click", () => {
        if (confirm("Are you sure you want to reset the session? All history and charts will be cleared.")) {
            generateNewSession();
            // Clear chat log
            const history = document.getElementById("chat-history");
            history.innerHTML = `
                <div class="message system-message">
                    <div class="message-avatar"><i class="fa-solid fa-robot"></i></div>
                    <div class="message-content">
                        <p>Hello! I am your AI Data Analyst. Upload a CSV file and ask me to analyze it, compute statistics, or generate charts using natural language.</p>
                    </div>
                </div>
            `;
            // Clear files
            selectedFile = null;
            fileInput.value = "";
            document.getElementById("file-status").classList.add("hidden");
            document.getElementById("dropzone").classList.remove("hidden");
            chatInput.value = "";
            chatInput.style.height = "auto";
            toggleSendButton();
        }
    });

    // Export Chart (HTML)
    exportBtn.addEventListener("click", () => {
        if (currentChartData) {
            const chartHtml = `
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Plotly Chart Export</title>
                    <script src="https://cdn.plot.ly/plotly-2.35.0.min.js"></script>
                </head>
                <body style="background-color: #0b0f19; margin: 0; padding: 20px;">
                    <div id="chart" style="width: 100%; height: 95vh;"></div>
                    <script>
                        Plotly.newPlot('chart', ${JSON.stringify(currentChartData.data)}, ${JSON.stringify(currentChartData.layout)});
                    </script>
                </body>
                </html>
            `;
            const blob = new Blob([chartHtml], { type: "text/html" });
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = `chart_${sessionId}.html`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        }
    });
}

// Enable/Disable Send Button
function toggleSendButton() {
    const chatInput = document.getElementById("chat-input");
    const sendBtn = document.getElementById("send-btn");
    const hasText = chatInput.value.trim().length > 0;
    
    // Enable send if there is text AND no generation is in progress
    sendBtn.disabled = !hasText || isGenerating;
}

// Read CSV File and convert to base64
function handleFileSelect(e) {
    const file = e.target.files[0];
    if (!file) return;

    if (!file.name.endsWith(".csv")) {
        alert("Please upload a valid CSV file (.csv)");
        e.target.value = "";
        return;
    }

    const reader = new FileReader();
    reader.onload = function(event) {
        const rawContent = event.target.result;
        // Convert to base64
        const base64Data = btoa(unescape(encodeURIComponent(rawContent)));
        
        selectedFile = {
            name: file.name,
            type: "text/csv",
            data: base64Data
        };

        // Update File Status badge
        document.getElementById("file-name").textContent = file.name;
        document.getElementById("file-size").textContent = formatBytes(file.size);
        document.getElementById("dropzone").classList.add("hidden");
        document.getElementById("file-status").classList.remove("hidden");
        
        toggleSendButton();
    };
    reader.readAsText(file);
}

// Format Bytes to human readable
function formatBytes(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const dm = 2;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

// Stream and Send Message to Backend
async function sendMessage() {
    const chatInput = document.getElementById("chat-input");
    const messageText = chatInput.value.trim();
    if (!messageText || isGenerating) return;

    isGenerating = true;
    toggleSendButton();

    // 1. Append User Message to Chat Log
    appendUserMessage(messageText, selectedFile ? selectedFile.name : null);

    // Clear input box
    chatInput.value = "";
    chatInput.style.height = "auto";

    // 2. Append Assistant message card for streaming
    const assistantCard = appendAssistantMessagePlaceholder();
    const contentTextElement = assistantCard.querySelector(".message-text-content");
    const statusDot = assistantCard.querySelector(".typing-indicator");

    try {
        const runtimeMode = document.getElementById("runtime-mode").value;
        const payload = {
            message: messageText,
            sessionId: sessionId,
            file: selectedFile
        };

        // Once the CSV is uploaded in the first turn, we don't need to re-send it
        // in subsequent turns within the same session.
        selectedFile = null; 
        document.getElementById("dropzone").classList.add("hidden");
        
        const response = await fetch("/api/chat", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify(payload)
        });

        if (!response.ok) {
            throw new Error(`Server returned HTTP ${response.status}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        // Parsed content tracking
        let textAccumulator = "";
        let codeAccumulator = "";
        let codeOutputAccumulator = "";
        let currentAccordion = null;
        let isCodeOpen = false;

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n\n");
            buffer = lines.pop(); // keep partial line

            for (const line of lines) {
                if (line.startsWith("data: ")) {
                    const dataStr = line.substring(6).trim();
                    if (!dataStr) continue;

                    try {
                        const event = JSON.parse(dataStr);
                        
                        if (event.error) {
                            throw new Error(event.error);
                        }

                        if (event.errorCode) {
                            throw new Error(`Agent execution error [${event.errorCode}]: ${event.errorMessage || 'The model was interrupted.'}`);
                        }

                        // Parse the event content
                        if (event.content && event.content.parts) {
                            for (const part of event.content.parts) {
                                // 1. Handle code block generated by agent
                                if (part.executableCode) {
                                    const code = part.executableCode.code;
                                    if (code) {
                                        if (!currentAccordion) {
                                            // Reset code accumulators when creating a new accordion block
                                            codeAccumulator = "";
                                            codeOutputAccumulator = "";
                                            currentAccordion = createCodeAccordion(assistantCard);
                                        }
                                        codeAccumulator += code;
                                        currentAccordion.querySelector("pre").textContent = codeAccumulator;
                                        currentAccordion.classList.remove("hidden");
                                    }
                                }

                                // 2. Handle code execution result
                                if (part.codeExecutionResult) {
                                    const executionOutput = part.codeExecutionResult.output;
                                    if (executionOutput) {
                                        if (!currentAccordion) {
                                            codeOutputAccumulator = "";
                                            currentAccordion = createCodeAccordion(assistantCard);
                                        }
                                        codeOutputAccumulator += executionOutput;
                                        let outputDiv = currentAccordion.querySelector(".code-output");
                                        if (!outputDiv) {
                                            outputDiv = document.createElement("div");
                                            outputDiv.className = "code-output";
                                            currentAccordion.querySelector(".code-block-wrapper").appendChild(outputDiv);
                                        }
                                        
                                        // Check if outcome failed to apply error styling
                                        if (part.codeExecutionResult.outcome === "OUTCOME_FAILED") {
                                            outputDiv.classList.add("error");
                                        }
                                        outputDiv.textContent = codeOutputAccumulator;

                                        // Once the execution result is rendered, clear the current accordion pointer
                                        // so that subsequent code blocks start in a new accordion.
                                        currentAccordion = null;
                                    }
                                }

                                // 3. Handle standard text parts
                                if (part.text) {
                                    textAccumulator += part.text;
                                    // Parse markdown bullet points and bolding very simply
                                    contentTextElement.innerHTML = formatMarkdown(textAccumulator);
                                }
                            }
                        }

                        // 4. Handle newly created artifacts (like Plotly chart json)
                        if (event.actions && event.actions.artifactDelta) {
                            const delta = event.actions.artifactDelta;
                            for (const [filename, version] of Object.entries(delta)) {
                                appendArtifactToList(filename);
                                
                                // If it is the Plotly json file, load and render it!
                                if (filename === "plotly_chart.json") {
                                    await fetchAndRenderChart(sessionId, filename);
                                }
                            }
                        }
                    } catch (e) {
                        console.error("Error parsing event line", e);
                        // Re-throw if it is an actual generation/agent error to display it to the user
                        if (e.message.includes("Agent execution error") || e.message.includes("Error:")) {
                            throw e;
                        }
                    }
                }
            }
        }
    } catch (err) {
        console.error(err);
        contentTextElement.innerHTML += `<p class="error-text" style="color: #f87171;"><i class="fa-solid fa-circle-exclamation"></i> Error: ${err.message}</p>`;
    } finally {
        statusDot.remove();
        isGenerating = false;
        toggleSendButton();
    }
}

// Create Code Block Accordion DOM element
function createCodeAccordion(card) {
    const accordion = document.createElement("details");
    accordion.className = "code-accordion";
    accordion.innerHTML = `
        <summary>View Executed Python Code</summary>
        <div class="code-block-wrapper">
            <div class="code-block-header">
                <span><i class="fa-brands fa-python"></i> python</span>
                <button class="copy-code-btn" onclick="navigator.clipboard.writeText(this.parentElement.nextElementSibling.textContent); alert('Code copied!')">
                    <i class="fa-regular fa-copy"></i> Copy
                </button>
            </div>
            <pre></pre>
        </div>
    `;
    card.querySelector(".message-content").appendChild(accordion);
    return accordion;
}

// Append User Message to Chat Log
function appendUserMessage(text, filename) {
    const history = document.getElementById("chat-history");
    const msg = document.createElement("div");
    msg.className = "message user-message";
    
    let fileBadge = "";
    if (filename) {
        fileBadge = `<div class="msg-file-badge" style="font-size: 0.8rem; background: rgba(255,255,255,0.06); padding: 0.25rem 0.5rem; border-radius: 4px; margin-top: 0.5rem; display: inline-flex; align-items: center; gap: 0.25rem; border: 1px solid var(--border-color);"><i class="fa-solid fa-file-csv" style="color: var(--accent-cyan);"></i> ${filename}</div>`;
    }

    msg.innerHTML = `
        <div class="message-avatar"><i class="fa-solid fa-user"></i></div>
        <div class="message-content">
            <p>${escapeHtml(text)}</p>
            ${fileBadge}
        </div>
    `;
    history.appendChild(msg);
    scrollToBottom();
}

// Append Assistant Placeholder Message
function appendAssistantMessagePlaceholder() {
    const history = document.getElementById("chat-history");
    const msg = document.createElement("div");
    msg.className = "message assistant-message";
    msg.innerHTML = `
        <div class="message-avatar"><i class="fa-solid fa-robot"></i></div>
        <div class="message-content">
            <div class="message-text-content"></div>
            <div class="typing-indicator">
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
            </div>
        </div>
    `;
    history.appendChild(msg);
    scrollToBottom();
    return msg;
}

// Markdown Formatter using Marked.js
function formatMarkdown(text) {
    if (window.marked && window.marked.parse) {
        // marked.parse handles standard markdown formatting
        return window.marked.parse(text);
    }
    
    // Fallback if marked library is not loaded
    let formatted = escapeHtml(text);
    
    // Replace bold formatting (**text**)
    formatted = formatted.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    
    // Replace inline code blocks (`code`)
    formatted = formatted.replace(/`(.*?)`/g, '<code style="background: rgba(255,255,255,0.06); font-family: monospace; padding: 0.1rem 0.3rem; border-radius: 3px;">$1</code>');
    
    // Simple line break replace
    formatted = formatted.replace(/\n/g, '<br>');
    
    return formatted;
}

// Escape HTML utility
function escapeHtml(text) {
    return text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// Scroll chat log to bottom
function scrollToBottom() {
    const history = document.getElementById("chat-history");
    history.scrollTop = history.scrollHeight;
}

// Log generated file in list
function appendArtifactToList(filename) {
    const logSection = document.getElementById("artifacts-log");
    const list = document.getElementById("artifacts-list");
    logSection.classList.remove("hidden");

    // Check if filename already in list
    const existing = Array.from(list.children).find(li => li.textContent === filename);
    if (!existing) {
        const li = document.createElement("li");
        li.innerHTML = `<i class="fa-regular fa-file"></i> ${filename}`;
        li.addEventListener("click", () => {
            if (filename === "plotly_chart.json") {
                fetchAndRenderChart(sessionId, filename);
            } else {
                // Let user open other artifacts
                window.open(`/api/artifacts/${sessionId}/${filename}`, "_blank");
            }
        });
        list.appendChild(li);
    }
}

// Fetch Plotly JSON and render it in dashboard
async function fetchAndRenderChart(sessId, filename) {
    try {
        const response = await fetch(`/api/artifacts/${sessId}/${filename}`);
        if (!response.ok) throw new Error("Failed to load chart artifact");

        const chartJson = await response.json();
        currentChartData = chartJson;

        document.getElementById("viz-empty-state").classList.add("hidden");
        const chartDiv = document.getElementById("plotly-chart");
        chartDiv.classList.remove("hidden");
        document.getElementById("export-chart-btn").classList.remove("hidden");

        // Format layout for dark theme
        const layout = chartJson.layout || {};
        layout.paper_bgcolor = "rgba(0,0,0,0)";
        layout.plot_bgcolor = "rgba(0,0,0,0)";
        layout.font = { color: "#f3f4f6", family: "'Plus Jakarta Sans', sans-serif" };
        
        // Clean up gridlines colors
        if (layout.xaxis) {
            layout.xaxis.gridcolor = "rgba(255,255,255,0.06)";
            layout.xaxis.linecolor = "rgba(255,255,255,0.1)";
        }
        if (layout.yaxis) {
            layout.yaxis.gridcolor = "rgba(255,255,255,0.06)";
            layout.yaxis.linecolor = "rgba(255,255,255,0.1)";
        }

        // Render Plotly chart
        Plotly.newPlot("plotly-chart", chartJson.data, layout, { responsive: true });
        
        // Resize layout slightly to fit panel
        Plotly.Plots.resize("plotly-chart");
    } catch (err) {
        console.error("Error rendering Plotly chart:", err);
    }
}
