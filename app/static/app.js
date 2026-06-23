// Global Application State
let sessionId = "";
let selectedFile = null;
let isGenerating = false;
let currentChartData = null;
let jwtToken = localStorage.getItem("token") || "";
let currentUserEmail = "";
let isLoginMode = true;
let isAdmin = false;

// Initialize
document.addEventListener("DOMContentLoaded", async () => {
    // Detect environment and set runtime selector
    const hostname = window.location.hostname;
    const runtimeModeSelect = document.getElementById("runtime-mode");
    if (runtimeModeSelect) {
        if (hostname !== "localhost" && hostname !== "127.0.0.1") {
            runtimeModeSelect.value = "deployed";
            runtimeModeSelect.disabled = true; // Lock to deployed in cloud deployment
        } else {
            runtimeModeSelect.value = "local";
        }
    }
    setupEventListeners();
    await checkAuth();
});

// Get Authorization headers
function getHeaders() {
    return {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + jwtToken
    };
}

// Check Authentication status
async function checkAuth() {
    if (!jwtToken) {
        showAuthModal();
        return;
    }

    try {
        const response = await fetch("/api/auth/me", {
            headers: getHeaders()
        });

        if (!response.ok) {
            throw new Error("Session expired");
        }

        const data = await response.json();
        currentUserEmail = data.email;
        isAdmin = data.is_admin || false;
        document.getElementById("user-email-display").textContent = currentUserEmail;
        
        // Show/hide admin panel button
        const adminBtn = document.getElementById("admin-panel-btn");
        if (adminBtn) {
            if (isAdmin) {
                adminBtn.classList.remove("hidden");
            } else {
                adminBtn.classList.add("hidden");
            }
        }

        // Hide modal
        document.getElementById("auth-modal").classList.add("hidden");
        
        // Load sessions
        await loadSessionsList();
    } catch (err) {
        console.error("Auth check failed:", err);
        logout();
    }
}

// Show Authentication Modal
function showAuthModal() {
    const modal = document.getElementById("auth-modal");
    modal.classList.remove("hidden");
    updateAuthModalUI();
}

// Update Auth Modal titles/buttons based on mode
function updateAuthModalUI() {
    const title = document.getElementById("auth-title");
    const subtitle = document.getElementById("auth-subtitle");
    const submitBtn = document.getElementById("auth-submit-btn");
    const togglePromptText = document.getElementById("auth-toggle-text");
    const toggleBtn = document.getElementById("auth-toggle-btn");
    const errorDiv = document.getElementById("auth-error");

    errorDiv.classList.add("hidden");

    if (isLoginMode) {
        title.textContent = "Welcome Back";
        subtitle.textContent = "Log in to your account to recover your workspace.";
        submitBtn.querySelector("span").textContent = "Login";
        togglePromptText.textContent = "Don't have an account?";
        toggleBtn.textContent = "Create account";
    } else {
        title.textContent = "Create Account";
        subtitle.textContent = "Sign up to start analyzing and storing your data.";
        submitBtn.querySelector("span").textContent = "Register";
        togglePromptText.textContent = "Already have an account?";
        toggleBtn.textContent = "Log in";
    }
}

// Log out user
function logout() {
    jwtToken = "";
    currentUserEmail = "";
    sessionId = "";
    selectedFile = null;
    isAdmin = false;
    localStorage.removeItem("token");

    // Hide admin UI
    const adminBtn = document.getElementById("admin-panel-btn");
    if (adminBtn) adminBtn.classList.add("hidden");
    const adminModal = document.getElementById("admin-modal");
    if (adminModal) adminModal.classList.add("hidden");

    // Clear file inputs UI
    document.getElementById("file-input").value = "";
    document.getElementById("file-status").classList.add("hidden");
    document.getElementById("dropzone").classList.remove("hidden");

    document.getElementById("sessions-list").innerHTML = "";
    document.getElementById("chat-history").innerHTML = "";
    document.getElementById("session-id-display").textContent = "None";
    document.getElementById("plotly-chart").innerHTML = "";
    document.getElementById("plotly-chart").classList.add("hidden");
    document.getElementById("viz-empty-state").classList.remove("hidden");
    document.getElementById("export-chart-btn").classList.add("hidden");
    document.getElementById("artifacts-log").classList.add("hidden");
    document.getElementById("artifacts-list").innerHTML = "";
    showAuthModal();
}

// Load and display session list
async function loadSessionsList(selectId = null) {
    try {
        const response = await fetch("/api/sessions", {
            headers: getHeaders()
        });

        if (!response.ok) throw new Error("Failed to load sessions");

        const data = await response.json();
        const listContainer = document.getElementById("sessions-list");
        listContainer.innerHTML = "";

        if (data.sessions.length === 0) {
            // Generate a fresh session if none exist
            generateNewSession();
            return;
        }

        data.sessions.forEach(sess => {
            const li = document.createElement("li");
            li.className = "session-item";
            if (sess.id === sessionId) {
                li.classList.add("active");
            }

            const formattedTime = new Date(sess.last_update_time * 1000).toLocaleString();
            
            li.innerHTML = `
                <div class="session-info">
                    <span class="session-title" title="${sess.id}">${sess.id}</span>
                    <span class="session-time">${formattedTime}</span>
                </div>
                <button class="session-delete-btn" title="Delete Session">
                    <i class="fa-solid fa-trash-can"></i>
                </button>
            `;

            // Select session on click
            li.addEventListener("click", () => {
                if (sess.id !== sessionId) {
                    selectSession(sess.id);
                }
            });

            // Delete session button listener
            const deleteBtn = li.querySelector(".session-delete-btn");
            deleteBtn.addEventListener("click", (e) => {
                e.stopPropagation();
                if (confirm(`Delete session ${sess.id}? This will permanently remove its conversation history and generated charts.`)) {
                    deleteSession(sess.id);
                }
            });

            listContainer.appendChild(li);
        });

        // Set active session if not selected yet
        if (!sessionId) {
            if (selectId) {
                selectSession(selectId);
            } else {
                selectSession(data.sessions[0].id);
            }
        }
    } catch (err) {
        console.error("Failed to load sessions list:", err);
    }
}

// Select a session and render its history
async function selectSession(id) {
    sessionId = id;
    document.getElementById("session-id-display").textContent = sessionId;

    // Close sidebar on mobile after selecting a session
    const sidebar = document.getElementById("sidebar");
    const sidebarOverlay = document.getElementById("sidebar-overlay");
    if (sidebar && sidebar.classList.contains("open")) {
        sidebar.classList.remove("open");
        if (sidebarOverlay) sidebarOverlay.classList.add("hidden");
    }

    // Highlight active session in list
    const items = document.querySelectorAll(".session-item");
    items.forEach(item => {
        const title = item.querySelector(".session-title").textContent;
        if (title === id) {
            item.classList.add("active");
        } else {
            item.classList.remove("active");
        }
    });

    // Clear main UI components
    document.getElementById("chat-history").innerHTML = "";
    document.getElementById("plotly-chart").innerHTML = "";
    document.getElementById("plotly-chart").classList.add("hidden");
    document.getElementById("viz-empty-state").classList.remove("hidden");
    document.getElementById("export-chart-btn").classList.add("hidden");
    document.getElementById("artifacts-log").classList.add("hidden");
    document.getElementById("artifacts-list").innerHTML = "";
    currentChartData = null;

    try {
        const response = await fetch(`/api/sessions/${id}`, {
            headers: getHeaders()
        });

        if (!response.ok) throw new Error("Failed to load session details");

        const data = await response.json();
        
        // Populate chat log from history turns
        if (data.history && data.history.length > 0) {
            data.history.forEach(turn => {
                if (turn.role === "user") {
                    appendUserMessage(turn.text, turn.file);
                } else if (turn.role === "assistant") {
                    const card = appendAssistantMessagePlaceholder();
                    const contentTextElement = card.querySelector(".message-text-content");
                    
                    // Render response text
                    if (turn.text) {
                        contentTextElement.innerHTML = formatMarkdown(turn.text);
                    }
                    
                    // Render code block accordion
                    if (turn.code) {
                        const accordion = createCodeAccordion(card);
                        accordion.querySelector("pre").textContent = turn.code;
                        accordion.classList.remove("hidden");
                        
                        if (turn.code_output) {
                            const outputDiv = document.createElement("div");
                            outputDiv.className = "code-output";
                            if (turn.code_outcome === "OUTCOME_FAILED") {
                                outputDiv.classList.add("error");
                            }
                            outputDiv.textContent = turn.code_output;
                            accordion.querySelector(".code-block-wrapper").appendChild(outputDiv);
                        }
                    }
                    
                    // Remove typing dot
                    card.querySelector(".typing-indicator").remove();
                    
                    // Append artifacts and render plot if generated
                    if (turn.artifacts && turn.artifacts.length > 0) {
                        turn.artifacts.forEach(filename => {
                            appendArtifactToList(filename);
                            if (filename === "plotly_chart.json") {
                                fetchAndRenderChart(id, filename);
                            }
                        });
                    }
                }
            });
        } else {
            // Default greeting for empty session
            document.getElementById("chat-history").innerHTML = `
                <div class="message system-message">
                    <div class="message-avatar"><i class="fa-solid fa-robot"></i></div>
                    <div class="message-content">
                        <p>Hello! I am your AI Data Analyst. Upload a CSV file and ask me to analyze it, compute statistics, or generate charts using natural language.</p>
                    </div>
                </div>
            `;
        }
    } catch (err) {
        console.error("Error recovering session:", err);
    }
}

// Delete a session
async function deleteSession(id) {
    try {
        const response = await fetch(`/api/sessions/${id}`, {
            method: "DELETE",
            headers: getHeaders()
        });

        if (!response.ok) throw new Error("Failed to delete session");

        if (sessionId === id) {
            sessionId = "";
        }
        await loadSessionsList();
    } catch (err) {
        console.error("Error deleting session:", err);
    }
}

// Generate New Session ID locally and save to Firestore
function generateNewSession() {
    sessionId = "session_" + Math.random().toString(36).substring(2, 15);
    document.getElementById("session-id-display").textContent = sessionId;
    
    // Clear visualizations/artifacts
    document.getElementById("plotly-chart").innerHTML = "";
    document.getElementById("plotly-chart").classList.add("hidden");
    document.getElementById("viz-empty-state").classList.remove("hidden");
    document.getElementById("export-chart-btn").classList.add("hidden");
    document.getElementById("artifacts-log").classList.add("hidden");
    document.getElementById("artifacts-list").innerHTML = "";
    currentChartData = null;

    // Reset Chat Log DOM
    const history = document.getElementById("chat-history");
    history.innerHTML = `
        <div class="message system-message">
            <div class="message-avatar"><i class="fa-solid fa-robot"></i></div>
            <div class="message-content">
                <p>Hello! I am your AI Data Analyst. Upload a CSV file and ask me to analyze it, compute statistics, or generate charts using natural language.</p>
            </div>
        </div>
    `;

    // Add session to sidebar
    const listContainer = document.getElementById("sessions-list");
    const li = document.createElement("li");
    li.className = "session-item active";
    
    const formattedTime = new Date().toLocaleString();
    li.innerHTML = `
        <div class="session-info">
            <span class="session-title" title="${sessionId}">${sessionId}</span>
            <span class="session-time">${formattedTime}</span>
        </div>
        <button class="session-delete-btn" title="Delete Session">
            <i class="fa-solid fa-trash-can"></i>
        </button>
    `;
    
    li.addEventListener("click", () => {
        selectSession(sessionId);
    });
    
    li.querySelector(".session-delete-btn").addEventListener("click", (e) => {
        e.stopPropagation();
        if (confirm(`Delete session ${sessionId}? This will permanently remove its conversation history and generated charts.`)) {
            deleteSession(sessionId);
        }
    });

    listContainer.insertBefore(li, listContainer.firstChild);

    // Reset files input state
    selectedFile = null;
    document.getElementById("file-input").value = "";
    document.getElementById("file-status").classList.add("hidden");
    document.getElementById("dropzone").classList.remove("hidden");
    document.getElementById("chat-input").value = "";
    document.getElementById("chat-input").style.height = "auto";
    toggleSendButton();
}

// Setup Event Listeners
function setupEventListeners() {
    const chatInput = document.getElementById("chat-input");
    const sendBtn = document.getElementById("send-btn");
    const fileInput = document.getElementById("file-input");
    const dropzone = document.getElementById("dropzone");
    const removeFileBtn = document.getElementById("remove-file-btn");
    const newSessionBtn = document.getElementById("new-session-sidebar-btn");
    const exportBtn = document.getElementById("export-chart-btn");
    const logoutBtn = document.getElementById("logout-btn");
    const authForm = document.getElementById("auth-form");
    const authToggleBtn = document.getElementById("auth-toggle-btn");

    // Auth toggle button (login vs register)
    authToggleBtn.addEventListener("click", () => {
        isLoginMode = !isLoginMode;
        updateAuthModalUI();
    });

    // Auth submit handler
    authForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const email = document.getElementById("auth-email").value.trim();
        const password = document.getElementById("auth-password").value;
        const errorDiv = document.getElementById("auth-error");
        const errorText = document.getElementById("auth-error-text");

        errorDiv.classList.add("hidden");

        const endpoint = isLoginMode ? "/api/auth/login" : "/api/auth/register";

        try {
            const response = await fetch(endpoint, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ email, password })
            });

            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.detail || "Authentication failed");
            }

            // Save credentials
            jwtToken = data.token;
            localStorage.setItem("token", jwtToken);
            
            // Clear fields
            document.getElementById("auth-email").value = "";
            document.getElementById("auth-password").value = "";

            await checkAuth();
        } catch (err) {
            console.error("Auth error:", err);
            errorText.textContent = err.message;
            errorDiv.classList.remove("hidden");
        }
    });

    // Logout
    logoutBtn.addEventListener("click", logout);

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

    // New Session Sidebar button
    newSessionBtn.addEventListener("click", () => {
        if (confirm("Start a new session?")) {
            generateNewSession();
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

    // Admin Modal Event Listeners
    const adminPanelBtn = document.getElementById("admin-panel-btn");
    const adminCloseBtn = document.getElementById("admin-close-btn");
    const adminModal = document.getElementById("admin-modal");
    const adminTabBtns = document.querySelectorAll(".admin-tab-btn");
    const cleanupBtn = document.getElementById("admin-trigger-cleanup-btn");

    if (adminPanelBtn) {
        adminPanelBtn.addEventListener("click", () => {
            adminModal.classList.remove("hidden");
            switchAdminTab("admin-tab-users");
        });
    }

    if (adminCloseBtn) {
        adminCloseBtn.addEventListener("click", () => {
            adminModal.classList.add("hidden");
        });
    }

    adminTabBtns.forEach(btn => {
        btn.addEventListener("click", () => {
            const tabId = btn.getAttribute("data-tab");
            switchAdminTab(tabId);
        });
    });

    if (cleanupBtn) {
        cleanupBtn.addEventListener("click", triggerManualCleanup);
    }

    // --- Mobile Responsive Events ---
    // 1. Sidebar Drawer Toggle
    const sidebarToggleBtn = document.getElementById("sidebar-toggle-btn");
    const sidebar = document.getElementById("sidebar");
    const sidebarOverlay = document.getElementById("sidebar-overlay");

    if (sidebarToggleBtn && sidebar && sidebarOverlay) {
        sidebarToggleBtn.addEventListener("click", () => {
            sidebar.classList.toggle("open");
            sidebarOverlay.classList.toggle("hidden");
        });

        sidebarOverlay.addEventListener("click", () => {
            sidebar.classList.remove("open");
            sidebarOverlay.classList.add("hidden");
        });
    }

    // 2. Mobile Workspace Tabs Switching (Chat vs Dashboard)
    const mobileWorkspaceTabs = document.querySelectorAll(".mobile-tab-btn");
    const workspace = document.querySelector(".app-workspace");
    const vizDot = document.getElementById("viz-dot");

    mobileWorkspaceTabs.forEach(btn => {
        btn.addEventListener("click", () => {
            const tab = btn.getAttribute("data-workspace-tab");

            // Toggle active status on tab buttons
            mobileWorkspaceTabs.forEach(b => b.classList.remove("active"));
            btn.classList.add("active");

            // Toggle workspace layout layout class
            if (tab === "viz") {
                workspace.classList.add("tab-viz-active");
                if (vizDot) vizDot.classList.add("hidden"); // Clear notification dot

                // Trigger Plotly chart resize to adapt to container layout width
                const chartDiv = document.getElementById("plotly-chart");
                if (chartDiv && !chartDiv.classList.contains("hidden")) {
                    Plotly.Plots.resize(chartDiv);
                }
            } else {
                workspace.classList.remove("tab-viz-active");
            }
        });
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

    // Append User Message to Chat Log
    appendUserMessage(messageText, selectedFile ? selectedFile.name : null);

    // Clear input box
    chatInput.value = "";
    chatInput.style.height = "auto";

    // Append Assistant message card for streaming
    const assistantCard = appendAssistantMessagePlaceholder();
    const contentTextElement = assistantCard.querySelector(".message-text-content");
    const statusDot = assistantCard.querySelector(".typing-indicator");

    try {
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
            headers: getHeaders(),
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
        
        // Refresh sidebar sessions to reflect last update time changes
        await loadSessionsList();
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
    const existing = Array.from(list.children).find(li => li.textContent.trim() === filename);
    if (!existing) {
        const li = document.createElement("li");
        li.innerHTML = `<i class="fa-regular fa-file"></i> ${filename}`;
        li.addEventListener("click", () => {
            if (filename === "plotly_chart.json") {
                fetchAndRenderChart(sessionId, filename);
            } else {
                // Open other artifacts with auth token appended as query or fetched
                const url = `/api/artifacts/${sessionId}/${filename}`;
                // Since window.open doesn't let us easily send Bearer header directly,
                // we can either download the artifact via fetch and create an object URL,
                // or redirect/open in new tab.
                // Fetching is much better as it keeps it secure and authenticates correctly!
                fetchArtifactAndDownload(url, filename);
            }
        });
        list.appendChild(li);
    }
}

// Securely fetch artifact and trigger browser download
async function fetchArtifactAndDownload(url, filename) {
    try {
        const response = await fetch(url, {
            headers: getHeaders()
        });
        if (!response.ok) throw new Error("Failed to download artifact");
        
        const blob = await response.blob();
        const objectUrl = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = objectUrl;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(objectUrl);
    } catch (err) {
        console.error("Artifact download failed:", err);
    }
}

// Fetch Plotly JSON and render it in dashboard
async function fetchAndRenderChart(sessId, filename) {
    try {
        const response = await fetch(`/api/artifacts/${sessId}/${filename}`, {
            headers: getHeaders()
        });
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

        // If on mobile and dashboard tab is not active, display notification dot
        const vizTabBtn = document.querySelector('[data-workspace-tab="viz"]');
        const workspace = document.querySelector(".app-workspace");
        if (vizTabBtn && workspace && !workspace.classList.contains("tab-viz-active")) {
            const vizDot = document.getElementById("viz-dot");
            if (vizDot) vizDot.classList.remove("hidden");
        }
    } catch (err) {
        console.error("Error rendering Plotly chart:", err);
    }
}

// Switch Admin Portal tabs and fetch corresponding data
function switchAdminTab(tabId) {
    const adminTabBtns = document.querySelectorAll(".admin-tab-btn");
    const adminTabContents = document.querySelectorAll(".admin-tab-content");

    adminTabBtns.forEach(btn => {
        if (btn.getAttribute("data-tab") === tabId) {
            btn.classList.add("active");
        } else {
            btn.classList.remove("active");
        }
    });

    adminTabContents.forEach(content => {
        if (content.id === tabId) {
            content.classList.add("active");
        } else {
            content.classList.remove("active");
        }
    });

    if (tabId === "admin-tab-users") {
        loadAdminUsers();
    } else if (tabId === "admin-tab-sessions") {
        loadAdminSessions();
    } else if (tabId === "admin-tab-system") {
        loadAdminStats();
    }
}

// Fetch and render registered users
async function loadAdminUsers() {
    const tbody = document.getElementById("admin-users-table-body");
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: var(--text-secondary); padding: 2rem;"><i class="fa-solid fa-circle-notch fa-spin"></i> Loading users...</td></tr>`;

    try {
        const response = await fetch("/api/admin/users", {
            headers: getHeaders()
        });
        if (!response.ok) throw new Error("Failed to fetch users");

        const data = await response.json();
        tbody.innerHTML = "";

        if (data.users.length === 0) {
            tbody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: var(--text-muted); padding: 2rem;">No users registered</td></tr>`;
            return;
        }

        data.users.forEach(user => {
            const tr = document.createElement("tr");

            // Format date if exists, or use N/A
            const regDate = user.created_at ? new Date(user.created_at).toLocaleString() : "N/A";

            // User email
            const emailTd = document.createElement("td");
            emailTd.textContent = user.email;
            tr.appendChild(emailTd);

            // Role badge
            const roleTd = document.createElement("td");
            roleTd.innerHTML = `
                <span class="role-badge ${user.is_admin ? 'admin' : 'user'}">
                    <i class="fa-solid ${user.is_admin ? 'fa-user-shield' : 'fa-user'}"></i>
                    ${user.is_admin ? 'Admin' : 'User'}
                </span>
            `;
            tr.appendChild(roleTd);

            // Created At
            const dateTd = document.createElement("td");
            dateTd.textContent = regDate;
            tr.appendChild(dateTd);

            // Actions
            const actionsTd = document.createElement("td");
            actionsTd.style.textAlign = "center";
            
            const toggleBtn = document.createElement("button");
            toggleBtn.className = "action-btn";
            toggleBtn.innerHTML = `<i class="fa-solid fa-user-gear"></i> Toggle Role`;
            
            // Safety constraints: cannot demote self or the primary admin
            if (user.email === currentUserEmail) {
                toggleBtn.disabled = true;
                toggleBtn.style.opacity = "0.5";
                toggleBtn.style.cursor = "not-allowed";
                toggleBtn.title = "You cannot demote yourself";
            } else if (user.is_primary_admin) {
                toggleBtn.disabled = true;
                toggleBtn.style.opacity = "0.5";
                toggleBtn.style.cursor = "not-allowed";
                toggleBtn.title = "Primary administrator cannot be demoted";
            } else {
                toggleBtn.addEventListener("click", () => toggleUserAdminRole(user.email));
            }
            
            actionsTd.appendChild(toggleBtn);
            tr.appendChild(actionsTd);

            tbody.appendChild(tr);
        });
    } catch (err) {
        console.error(err);
        tbody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: #f87171; padding: 2rem;"><i class="fa-solid fa-circle-exclamation"></i> Error loading users</td></tr>`;
    }
}

// Toggle user admin role
async function toggleUserAdminRole(email) {
    if (!confirm(`Toggle admin privileges for ${email}?`)) return;

    try {
        const response = await fetch(`/api/admin/users/${email}/toggle-admin`, {
            method: "POST",
            headers: getHeaders()
        });
        if (!response.ok) {
            const data = await response.json();
            throw new Error(data.detail || "Failed to toggle role");
        }
        await loadAdminUsers();
    } catch (err) {
        console.error("Error toggling admin role:", err);
        alert(err.message);
    }
}

// Fetch and render all sessions across all users
async function loadAdminSessions() {
    const tbody = document.getElementById("admin-sessions-table-body");
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: var(--text-secondary); padding: 2rem;"><i class="fa-solid fa-circle-notch fa-spin"></i> Loading sessions...</td></tr>`;

    try {
        const response = await fetch("/api/admin/sessions", {
            headers: getHeaders()
        });
        if (!response.ok) throw new Error("Failed to fetch sessions");

        const data = await response.json();
        tbody.innerHTML = "";

        if (data.sessions.length === 0) {
            tbody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: var(--text-muted); padding: 2rem;">No active sessions in the system</td></tr>`;
            return;
        }

        data.sessions.forEach(sess => {
            const tr = document.createElement("tr");

            // Session ID
            const idTd = document.createElement("td");
            idTd.textContent = sess.id;
            idTd.style.fontFamily = "var(--font-mono)";
            idTd.style.fontSize = "0.8rem";
            tr.appendChild(idTd);

            // User Email
            const userTd = document.createElement("td");
            userTd.textContent = sess.user_id;
            tr.appendChild(userTd);

            // Last Active
            const lastActive = new Date(sess.last_update_time * 1000).toLocaleString();
            const dateTd = document.createElement("td");
            dateTd.textContent = lastActive;
            tr.appendChild(dateTd);

            // Actions
            const actionsTd = document.createElement("td");
            actionsTd.style.textAlign = "center";
            
            const deleteBtn = document.createElement("button");
            deleteBtn.className = "action-btn danger-btn";
            deleteBtn.innerHTML = `<i class="fa-solid fa-trash-can"></i> Force End`;
            deleteBtn.addEventListener("click", () => deleteUserSessionAdmin(sess.user_id, sess.id));
            
            actionsTd.appendChild(deleteBtn);
            tr.appendChild(actionsTd);

            tr.style.opacity = (sess.id === sessionId) ? "0.9" : "1";
            tbody.appendChild(tr);
        });
    } catch (err) {
        console.error(err);
        tbody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: #f87171; padding: 2rem;"><i class="fa-solid fa-circle-exclamation"></i> Error loading sessions</td></tr>`;
    }
}

// Delete user session from Admin Panel
async function deleteUserSessionAdmin(userId, sessionToDeleteId) {
    const promptMessage = sessionToDeleteId === sessionId 
        ? `Force end your OWN active session ${sessionToDeleteId}? You will be disconnected from this session.`
        : `Force end and delete session ${sessionToDeleteId} for user ${userId}? This will remove all associated chat history and artifacts.`;

    if (!confirm(promptMessage)) return;

    try {
        const response = await fetch(`/api/admin/sessions/${userId}/${sessionToDeleteId}`, {
            method: "DELETE",
            headers: getHeaders()
        });
        if (!response.ok) throw new Error("Failed to delete session");

        // If the admin deleted their own current session, reset current session state
        if (sessionToDeleteId === sessionId) {
            sessionId = "";
            document.getElementById("admin-modal").classList.add("hidden");
            await loadSessionsList();
        } else {
            await loadAdminSessions();
        }
    } catch (err) {
        console.error("Error deleting session:", err);
        alert(err.message);
    }
}

// Fetch and render system stats
async function loadAdminStats() {
    try {
        const response = await fetch("/api/admin/stats", {
            headers: getHeaders()
        });
        if (!response.ok) throw new Error("Failed to fetch stats");

        const data = await response.json();
        
        document.getElementById("admin-stat-users").textContent = data.total_users;
        document.getElementById("admin-stat-sessions").textContent = data.total_sessions;
        document.getElementById("admin-stat-admins").textContent = data.admin_users;
        document.getElementById("admin-system-db-type").textContent = data.db_type;
        document.getElementById("admin-system-env").textContent = data.environment;
    } catch (err) {
        console.error("Error loading admin stats:", err);
    }
}

// Run manual GCS and Firestore cleanups
async function triggerManualCleanup() {
    const cleanupBtn = document.getElementById("admin-trigger-cleanup-btn");
    const feedback = document.getElementById("admin-cleanup-feedback");
    if (!cleanupBtn || !feedback) return;

    if (!confirm("Are you sure you want to trigger manual cleanup of data older than 7 days?")) return;

    cleanupBtn.disabled = true;
    cleanupBtn.style.opacity = "0.5";
    cleanupBtn.style.cursor = "not-allowed";

    try {
        const response = await fetch("/api/admin/cleanup", {
            method: "POST",
            headers: getHeaders()
        });
        if (!response.ok) throw new Error("Failed to run cleanup");

        feedback.classList.remove("hidden");
        
        // Reload stats in background
        setTimeout(async () => {
            await loadAdminStats();
        }, 1500);

        setTimeout(() => {
            feedback.classList.add("hidden");
            cleanupBtn.disabled = false;
            cleanupBtn.style.opacity = "1";
            cleanupBtn.style.cursor = "pointer";
        }, 4000);
    } catch (err) {
        console.error("Cleanup failed:", err);
        alert("Cleanup error: " + err.message);
        cleanupBtn.disabled = false;
        cleanupBtn.style.opacity = "1";
        cleanupBtn.style.cursor = "pointer";
    }
}

