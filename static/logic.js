// ============================
// ✅ SUPABASE SETUP
// ============================
const supabaseUrl = "https://zhrjmnrfklzuxmfbdqhg.supabase.co";
const supabaseKey = "sb_publishable_aIbByN1rFc9V3AH41Kyz6A_e1XppA1Z";
const supabaseClient = window.supabase.createClient(supabaseUrl, supabaseKey);

// ============================
// ✅ USER AUTH
// ============================
let currentUser = null;

async function getUser() {
    const { data, error } = await supabaseClient.auth.getUser();
    if (error) console.error("Auth error:", error.message);
    currentUser = data?.user || null;

    if (currentUser) {
        const fullName = (
            currentUser.user_metadata?.full_name ||
            currentUser.user_metadata?.name ||
            currentUser.email?.split("@")[0] ||
            "User"
        ).trim();

        const parts = fullName.split(/\s+/).filter(Boolean);
        const initials = parts.length >= 2
            ? (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
            : parts[0].slice(0, 2).toUpperCase();

        const avatarEl  = document.getElementById("userAvatar");
        const nameEl    = document.getElementById("userFullname");
        const railAvatar = document.getElementById("railAvatar");

        if (avatarEl)   avatarEl.textContent  = initials;
        if (nameEl)     nameEl.textContent     = fullName;
        if (railAvatar) railAvatar.textContent = initials;
    }
}

// ============================
// ✅ SESSION MANAGEMENT
// ============================
function generateSessionId() {
    return 'session_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
}

let currentSessionId = generateSessionId();
let chatTitle = "New Chat";
let firstMessage = true;

// ============================
// ⏰ TIME-BASED GREETING SYSTEM
// ============================
function getTimeOfDay() {
    const hour = new Date().getHours();
    
    if (hour >= 5 && hour < 12) return "morning";
    if (hour >= 12 && hour < 17) return "afternoon";
    if (hour >= 17 && hour < 21) return "evening";
    return "night";
}

function getGreetingMessage(userName) {
    const timeOfDay = getTimeOfDay();
    const greetings = {
        morning: [
            `Good morning, ${userName}`,
            `Rise and shine, ${userName}!`,
            `Good morning! Ready to code, ${userName}?`,
            `☀️ Good morning, ${userName}!`,
        ],
        afternoon: [
            `Good afternoon, ${userName}`,
            `Hope you're having a great afternoon, ${userName}!`,
            `Afternoon, ${userName}!`,
            `🌤️ Good afternoon, ${userName}!`,
        ],
        evening: [
            `Good evening, ${userName}`,
            `Evening, ${userName}!`,
            `Good evening! Let's build something, ${userName}`,
            `🌙 Good evening, ${userName}!`,
        ],
        night: [
            `Good night, ${userName}`,
            `Night owl coding session, ${userName}?`,
            `Burning the midnight oil, ${userName}?`,
            `🌌 Good night, ${userName}!`,
        ]
    };
    
    const greetingList = greetings[timeOfDay];
    return greetingList[Math.floor(Math.random() * greetingList.length)];
}

function displayGreeting() {
    const userNameEl = document.getElementById("userFullname");
    const userName = userNameEl?.textContent || "User";
    
    const greeting = getGreetingMessage(userName);
    
    // Create greeting element
    const greetingDiv = document.createElement("div");
    greetingDiv.style.cssText = `
        text-align: center;
        margin-top: 20px;
        margin-bottom: 30px;
        padding: 20px;
        background: linear-gradient(135deg, #10a37f11 0%, #0d8c6d11 100%);
        border: 1px solid #10a37f22;
        border-radius: 12px;
        animation: fadeIn 0.6s ease-in-out;
    `;
    greetingDiv.innerHTML = `
        <div style="font-size: 24px; font-weight: 600; color: #10a37f; letter-spacing: -0.02em;">
            ${greeting}
        </div>
        <div style="font-size: 14px; color: #888; margin-top: 8px;">
            How can I help you today?
        </div>
    `;
    
    const chatbox = document.getElementById("chatbox");
    const app = document.getElementById("app");
    if (chatbox) {
        chatbox.innerHTML = "";
        chatbox.appendChild(greetingDiv);
    }
    if (app) app.classList.add("greeting-mode");
}

// ============================
// 🔀 SIDEBAR TOGGLE
// ============================
function toggleSidebar() {
    const sidebar = document.getElementById("sidebar");
    const iconRail = document.getElementById("iconRail");
    const overlay  = document.getElementById("sidebarOverlay");

    const isMobile = window.innerWidth <= 768;

    if (isMobile) {
        sidebar.classList.toggle("open");
        overlay.classList.toggle("show");
    } else {
        const isOpen = sidebar.classList.contains("open");
        if (isOpen) {
            sidebar.classList.remove("open");
            iconRail.classList.add("visible");
        } else {
            sidebar.classList.add("open");
            iconRail.classList.remove("visible");
        }
    }
}

function closeSidebar() {
    const sidebar  = document.getElementById("sidebar");
    const overlay  = document.getElementById("sidebarOverlay");
    sidebar.classList.remove("open");
    overlay.classList.remove("show");
}

window.openSidebarTo = function (section) {
    const sidebar  = document.getElementById("sidebar");
    const iconRail = document.getElementById("iconRail");

    if (window.innerWidth <= 768) {
        sidebar.classList.add("open");
        document.getElementById("sidebarOverlay").classList.add("show");
    } else {
        sidebar.classList.add("open");
        iconRail.classList.remove("visible");
    }

    if (section === 'history') showHistory();
};

// ============================
// 💡 SUGGESTIONS
// ============================
function useSuggestion(el) {
    const input = document.getElementById("input");
    input.value = el.innerText.trim();
    input.focus();
    if (typeof autoResize === "function") autoResize();
}

// ============================
// 🧠 QUERY COMPLEXITY DETECTOR
// ============================
function isHeavyQuery(text) {
    const lower = text.toLowerCase().trim();
    if (lower.length > 80) return true;
    const heavyKeywords = [
        "explain", "write", "create", "build",
        "code", "script", "program", "function",
        "debug", "fix", "error", "bug",
        "step by step", "line by line", "breakdown",
        "compare", "difference between", " vs ",
        "how does", "how do i", "how to",
        "generate", "summarize", "analyze",
        "essay", "give me", "make a",
        "implement", "refactor", "optimiz",
        "algorithm", "convert", "translate"
    ];
    return heavyKeywords.some(kw => lower.includes(kw));
}

// ============================
// 🧾 MARKDOWN RENDERER
// ============================
function formatMessage(rawText) {
    const codeBlocks = [];
    let text = rawText.replace(/```([\w]*)\n?([\s\S]*?)```/g, (match, lang, code) => {
        const language = lang.trim() || "code";
        const escapedCode = code
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
        const html = `<div class="code-block">
            <div class="code-header">
                <span class="lang-label">${language}</span>
                <button onclick="copyCode(this)">Copy</button>
            </div>
            <pre><code>${escapedCode}</code></pre>
        </div>`;
        codeBlocks.push(html);
        return `%%CODEBLOCK_${codeBlocks.length - 1}%%`;
    });

    const parts = text.split(/(%%CODEBLOCK_\d+%%)/);
    text = parts.map((part) => {
        if (part.startsWith("%%CODEBLOCK_")) return part;
        return part
            .replace(/&(?!amp;|lt;|gt;)/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
    }).join("");

    // Tables
    text = text.replace(/((?:\|.+\|\n?)+)/g, (block) => {
        const rows = block.trim().split("\n").filter(r => r.trim());
        if (rows.length < 2) return block;
        const isSep = r => /^\|[\s\-\|:]+\|$/.test(r.trim());
        if (!isSep(rows[1])) return block;
        const parseRow = (row) =>
            row.trim().replace(/^\||\|$/g, "").split("|").map(c => c.trim());
        const headers = parseRow(rows[0]);
        const body    = rows.slice(2);
        const thead = `<thead><tr>${headers.map(h => `<th>${h}</th>`).join("")}</tr></thead>`;
        const tbody = `<tbody>${body.map(r => `<tr>${parseRow(r).map(c => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody>`;
        return `<table>${thead}${tbody}</table>`;
    });

    text = text.replace(/^### (.+)$/gm, "<h3>$1</h3>");
    text = text.replace(/^## (.+)$/gm,  "<h2>$1</h2>");
    text = text.replace(/^# (.+)$/gm,   "<h1>$1</h1>");
    text = text.replace(/^&gt; (.+)$/gm, "<blockquote>$1</blockquote>");
    text = text.replace(/^---+$/gm, "<hr>");
    text = text.replace(/\*\*\*(.+?)\*\*\*/g, "<strong><em>$1</em></strong>");
    text = text.replace(/\*\*(.+?)\*\*/g,     "<strong>$1</strong>");
    text = text.replace(/\*(.+?)\*/g,         "<em>$1</em>");
    text = text.replace(/`([^`]+)`/g, '<span class="inline-code">$1</span>');

    text = text.replace(/((?:^\d+\. .+\n?)+)/gm, (block) => {
        const items = block.trim().split("\n")
            .map(l => `<li>${l.replace(/^\d+\.\s/, "")}</li>`)
            .join("");
        return `<ol style="list-style-type: decimal;">${items}</ol>`;
    });

    text = text.replace(/((?:^[-•*] .+\n?)+)/gm, (block) => {
        const items = block.trim().split("\n")
            .map(l => `<li>${l.replace(/^[-•*]\s/, "")}</li>`)
            .join("");
        return `<ul>${items}</ul>`;
    });

    const lines = text.split("\n");
    let result = "";
    let para   = "";
    for (const line of lines) {
        const trimmed = line.trim();
        const isBlock = /^(<(h[123]|ul|ol|li|blockquote|hr|table|div|pre)|%%CODEBLOCK)/.test(trimmed);
        if (!trimmed) {
            if (para.trim()) { result += `<p>${para.trim()}</p>`; para = ""; }
        } else if (isBlock) {
            if (para.trim()) { result += `<p>${para.trim()}</p>`; para = ""; }
            result += line + "\n";
        } else {
            para += (para ? " " : "") + trimmed;
        }
    }
    if (para.trim()) result += `<p>${para.trim()}</p>`;
    result = result.replace(/%%CODEBLOCK_(\d+)%%/g, (_, i) => codeBlocks[parseInt(i)]);
    return result;
}

// ============================
// 📋 COPY CODE
// ============================
function copyCode(btn) {
    const code = btn.closest(".code-block").querySelector("code").innerText;
    navigator.clipboard.writeText(code).then(() => {
        btn.textContent = "✓ Copied!";
        btn.classList.add("copied");
        setTimeout(() => { btn.textContent = "Copy"; btn.classList.remove("copied"); }, 2000);
    }).catch(() => showToast("Failed to copy code"));
}

// ============================
// 📋 COPY BOT ANSWER
// ============================
function copyBotAnswer(btn) {
    const wrapper = btn.closest(".bot-msg-wrapper");
    const rawText = wrapper ? wrapper.dataset.raw : "";
    if (!rawText) return;
    navigator.clipboard.writeText(rawText).then(() => {
        btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg> Copied!`;
        btn.classList.add("copied");
        setTimeout(() => {
            btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg> Copy`;
            btn.classList.remove("copied");
        }, 2000);
    }).catch(() => showToast("Failed to copy message"));
}

// ============================
// 📋 COPY USER MESSAGE
// ============================
function copyUserMessage(btn) {
    const wrapper = btn.closest(".user-msg-wrapper");
    const text = wrapper ? wrapper.querySelector(".message.user").innerText : "";
    if (!text) return;
    navigator.clipboard.writeText(text).then(() => {
        btn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`;
        btn.classList.add("copied");
        setTimeout(() => {
            btn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`;
            btn.classList.remove("copied");
        }, 2000);
    }).catch(() => showToast("Failed to copy message"));
}

// ============================
// 🍞 TOAST
// ============================
function showToast(message, duration = 3000) {
    let toast = document.getElementById("toastNotif");
    if (!toast) {
        toast = document.createElement("div");
        toast.id = "toastNotif";
        document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.classList.add("show");
    setTimeout(() => toast.classList.remove("show"), duration);
}

// ============================
// 🤔 THINKING INDICATOR
// ============================
function createThinkingIndicator() {
    const div = document.createElement("div");
    div.classList.add("message", "bot", "typing");
    div.innerHTML = `
        <div class="thinking-wrap">
            <div class="thinking-label">
                <span class="think-icon"></span>
                AI is thinking…
            </div>
            <div class="skeleton-lines">
                <div class="skeleton-line"></div>
                <div class="skeleton-line"></div>
                <div class="skeleton-line"></div>
            </div>
        </div>`;
    return div;
}

// ============================
// 💬 DOTS INDICATOR
// ============================
function createLightIndicator() {
    const div = document.createElement("div");
    div.classList.add("message", "bot", "typing");
    div.innerHTML = `<div class="typing-dots"><span></span><span></span><span></span></div>`;
    return div;
}

// ============================
// 📦 USER BUBBLE
// ============================
function createUserBubble(text) {
    const wrapper = document.createElement("div");
    wrapper.classList.add("user-msg-wrapper");

    const bubble = document.createElement("div");
    bubble.classList.add("message", "user");
    bubble.innerText = text;

    const copyBtn = document.createElement("button");
    copyBtn.classList.add("user-copy-btn");
    copyBtn.title = "Copy message";
    copyBtn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`;
    copyBtn.onclick = () => copyUserMessage(copyBtn);

    wrapper.appendChild(copyBtn);
    wrapper.appendChild(bubble);
    return wrapper;
}

// ============================
// 📦 BOT WRAPPER
// ============================
function createBotWrapper() {
    const wrapper = document.createElement("div");
    wrapper.classList.add("bot-msg-wrapper");

    const botMsg = document.createElement("div");
    botMsg.classList.add("message", "bot");

    const actionsRow = document.createElement("div");
    actionsRow.classList.add("bot-actions");
    actionsRow.innerHTML = `
        <button class="bot-copy-btn" onclick="copyBotAnswer(this)" title="Copy answer">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
            Copy
        </button>`;

    wrapper.appendChild(botMsg);
    wrapper.appendChild(actionsRow);
    return { wrapper, botMsg };
}

// ============================
// ✍️ STREAMER
// ============================
async function streamWords(botMsg, wrapper, reader, decoder, chatbox) {
    let buffer    = "";
    let fullReply = "";

    const liveSpan = document.createElement("span");
    liveSpan.style.cssText = "white-space: pre-wrap; word-break: break-word; font-size: 15px; line-height: 1.8; color: #d4d4d4;";
    botMsg.appendChild(liveSpan);

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop();

        for (const line of lines) {
            if (!line.startsWith("data: ")) continue;
            const payload = line.slice(6).trim();
            if (payload === "[DONE]") break;
            try {
                const chunk = JSON.parse(payload);
                if (chunk.error) { botMsg.innerHTML = `<p style="color:#e06c6c">⚠️ ${chunk.error}</p>`; return ""; }
                if (chunk.token) {
                    fullReply += chunk.token;
                    liveSpan.textContent = fullReply;
                    chatbox.scrollTop = chatbox.scrollHeight;
                }
            } catch { continue; }
        }
    }

    botMsg.innerHTML = formatMessage(fullReply);
    wrapper.dataset.raw = fullReply;
    return fullReply;
}

// ============================
// ➕ NEW CHAT
// ============================
window.newChat = function () {
    const overlay = document.getElementById("settingsOverlay");
    if (overlay) overlay.classList.remove("active");

    currentSessionId = generateSessionId();
    firstMessage = true;

    const chatbox   = document.getElementById("chatbox");
    const inputArea = document.getElementById("inputArea");
    const app       = document.getElementById("app");

    if (chatbox)   chatbox.innerHTML = "";
    if (inputArea) {
        inputArea.classList.remove("bottom");
        inputArea.classList.add("center");
    }
    if (app) app.classList.add("greeting-mode");
    
    displayGreeting();
    
    if (window.innerWidth <= 768) closeSidebar();
    showMainMenu();
    showToast("New chat started", 2000);
};

// ============================
// 🚪 LOGOUT
// ============================
window.logoutUser = async function () {
    if (confirm("Are you sure you want to logout?")) {
        await supabaseClient.auth.signOut();
        window.location.href = "/auth.html";
    }
};

// ============================
// 🧭 MAIN MENU
// ============================
window.showMainMenu = function () {
    const menu = document.querySelector(".sidebar-menu");
    if (!menu) return;
    menu.innerHTML = `
        <div class="sidebar-item" onclick="newChat()">
            <svg class="sidebar-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path>
                <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>
            </svg>
            New Chat
        </div>
        <div class="sidebar-item" onclick="showHistory()">
            <svg class="sidebar-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="12" cy="12" r="10"></circle>
                <polyline points="12 6 12 12 16 14"></polyline>
            </svg>
            Chat History
        </div>
        <div class="sidebar-item" onclick="showSettings()">
            <svg class="sidebar-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="12" cy="12" r="3"></circle>
                <path d="M12 1v6m0 6v6m10.39-9.39l-4.24 4.24m-8.3 0l-4.24-4.24m12.53 8.53l4.24 4.24m-8.3 0l4.24-4.24"></path>
            </svg>
            Settings
        </div>`;
};

// ============================
// ⚙️ SETTINGS OVERLAY
// ============================
window.showSettings = function () {
    const overlay = document.getElementById("settingsOverlay");
    const email    = currentUser?.email || "Not logged in";
    const fullName = document.getElementById("userFullname")?.textContent || "User";
    const initials = document.getElementById("userAvatar")?.textContent  || "?";

    overlay.innerHTML = `
        <button class="settings-close-btn" onclick="closeSettings()" title="Close">✕</button>
        <div class="settings-panel-wrap">
            <div class="settings-nav">
                <h2 class="settings-nav-title">Settings</h2>
                <div class="settings-nav-item active" onclick="showSettingsTab('general', this)">
                    <svg class="sn-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <circle cx="12" cy="12" r="3"></circle>
                        <path d="M12 1v6m0 6v6m10.39-9.39l-4.24 4.24m-8.3 0l-4.24-4.24m12.53 8.53l4.24 4.24m-8.3 0l4.24-4.24"></path>
                    </svg> General
                </div>
                <div class="settings-nav-item" onclick="showSettingsTab('profile', this)">
                    <svg class="sn-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
                        <circle cx="12" cy="7" r="4"></circle>
                    </svg> Profile
                </div>
                <div class="settings-nav-item" onclick="showSettingsTab('chats', this)">
                    <svg class="sn-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
                    </svg> Chats
                </div>
                <div class="settings-nav-item" onclick="showSettingsTab('privacy', this)">
                    <svg class="sn-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>
                    </svg> Privacy
                </div>
            </div>
            <div class="settings-content" id="settingsContent"></div>
        </div>`;

    overlay.classList.add("active");
    showSettingsTab('general', overlay.querySelector('.settings-nav-item.active'));

    if (window.innerWidth <= 768) closeSidebar();
};

window.closeSettings = function () {
    const overlay = document.getElementById("settingsOverlay");
    if (overlay) overlay.classList.remove("active");
    showMainMenu();
};

// ============================
// ✏️ EDIT DISPLAY NAME
// ============================
window.editDisplayName = async function () {
    const currentName = document.getElementById("userFullname")?.textContent || "User";
    const newName = prompt("Enter your new display name:", currentName);
    
    if (!newName || newName.trim() === "" || newName.trim() === currentName) {
        return;
    }

    const trimmedName = newName.trim();

    try {
        // Update Supabase Auth metadata
        const { data, error } = await supabaseClient.auth.updateUser({
            data: { full_name: trimmedName }
        });

        if (error) {
            console.error("❌ Update failed:", error.message);
            showToast("❌ Failed to update name. Please try again.");
            return;
        }

        // Update current user object
        currentUser = data.user;

        // Generate new initials
        const parts = trimmedName.split(/\s+/).filter(Boolean);
        const initials = parts.length >= 2
            ? (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
            : parts[0].slice(0, 2).toUpperCase();

        // Update all name displays in the UI
        const avatarEl    = document.getElementById("userAvatar");
        const nameEl      = document.getElementById("userFullname");
        const railAvatar  = document.getElementById("railAvatar");
        const scProfileName = document.querySelector(".sc-profile-name");

        if (avatarEl)   avatarEl.textContent  = initials;
        if (nameEl)     nameEl.textContent    = trimmedName;
        if (railAvatar) railAvatar.textContent = initials;
        if (scProfileName) scProfileName.textContent = trimmedName;

        showToast(`✓ Name updated to ${trimmedName}`);
        
        // Refresh settings panel to show updated name
        setTimeout(() => {
            showSettingsTab('profile', document.querySelector('.settings-nav-item.active'));
        }, 500);

    } catch (err) {
        console.error("❌ Error:", err);
        showToast("❌ Failed to update name. Please try again.");
    }
};

window.showSettingsTab = function (tab, clickedEl) {
    document.querySelectorAll(".settings-nav-item").forEach(el => el.classList.remove("active"));
    if (clickedEl) clickedEl.classList.add("active");

    const content  = document.getElementById("settingsContent");
    if (!content) return;

    const email    = currentUser?.email || "Not logged in";
    const fullName = document.getElementById("userFullname")?.textContent || "User";
    const initials = document.getElementById("userAvatar")?.textContent  || "?";

    const tabs = {

        general: `
            <div class="sc-section">
                <div class="sc-section-title">Appearance</div>

                <!-- THEME PICKER -->
                <div class="sc-row sc-row-block">
                    <div class="sc-row-top">
                        <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <circle cx="12" cy="12" r="5"></circle>
                            <path d="M12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4"></path>
                        </svg>
                        <p class="sc-row-label">Theme</p>
                    </div>
                    <div class="theme-picker">
                        <button class="theme-option ${(localStorage.getItem('catura-theme') || 'dark') === 'light' ? 'active' : ''}" onclick="setTheme('light')" title="Light">
                            <div class="theme-preview light-preview">
                                <div class="tp-bar"></div>
                                <div class="tp-line"></div>
                                <div class="tp-line short"></div>
                                <div class="tp-bubble"></div>
                            </div>
                            <span>Light</span>
                        </button>
                        <button class="theme-option ${(localStorage.getItem('catura-theme') || 'dark') === 'auto' ? 'active' : ''}" onclick="setTheme('auto')" title="Auto">
                            <div class="theme-preview auto-preview">
                                <div class="tp-half-light"></div>
                                <div class="tp-half-dark"></div>
                                <div class="tp-bar"></div>
                                <div class="tp-line"></div>
                                <div class="tp-bubble"></div>
                            </div>
                            <span>Auto</span>
                        </button>
                        <button class="theme-option ${(localStorage.getItem('catura-theme') || 'dark') === 'dark' ? 'active' : ''}" onclick="setTheme('dark')" title="Dark">
                            <div class="theme-preview dark-preview">
                                <div class="tp-bar"></div>
                                <div class="tp-line"></div>
                                <div class="tp-line short"></div>
                                <div class="tp-bubble"></div>
                            </div>
                            <span>Dark</span>
                        </button>
                    </div>
                </div>

                <!-- FONT SIZE PICKER -->
                <div class="sc-row sc-row-block" style="margin-top:8px;">
                    <div class="sc-row-top">
                        <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <polyline points="4 7 4 4 20 4 20 7"></polyline>
                            <rect x="2" y="7" width="20" height="13" rx="2"></rect>
                            <path d="M9 17v-3m6 3v-3"></path>
                        </svg>
                        <p class="sc-row-label">Chat font size</p>
                    </div>
                    <div class="font-picker">
                        <button class="font-option ${(localStorage.getItem('catura-font') || 'default') === 'default' ? 'active' : ''}" onclick="setFontSize('default')">
                            <span class="font-sample">Aa</span>
                            <span>Default</span>
                        </button>
                        <button class="font-option ${(localStorage.getItem('catura-font') || 'default') === 'small' ? 'active' : ''}" onclick="setFontSize('small')">
                            <span class="font-sample small-sample">Aa</span>
                            <span>Small</span>
                        </button>
                        <button class="font-option ${(localStorage.getItem('catura-font') || 'default') === 'large' ? 'active' : ''}" onclick="setFontSize('large')">
                            <span class="font-sample large-sample">Aa</span>
                            <span>Large</span>
                        </button>
                        <button class="font-option ${(localStorage.getItem('catura-font') || 'default') === 'xlarge' ? 'active' : ''}" onclick="setFontSize('xlarge')">
                            <span class="font-sample xlarge-sample">Aa</span>
                            <span>X-Large</span>
                        </button>
                    </div>
                </div>
            </div>
            <div class="sc-section">
                <div class="sc-section-title">Support</div>
                <div class="sc-row disabled">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <circle cx="12" cy="12" r="10"></circle>
                        <path d="M8 14s1.5 2 4 2 4-2 4-2"></path>
                        <line x1="9" y1="9" x2="9.01" y2="9"></line>
                        <line x1="15" y1="9" x2="15.01" y2="9"></line>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Send bug report</p>
                        <p class="sc-row-sub soon">Coming soon</p>
                    </div>
                </div>
                <div class="sc-row disabled">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Request a feature</p>
                        <p class="sc-row-sub soon">Coming soon</p>
                    </div>
                </div>
            </div>`,

        profile: `
            <div class="sc-section">
                <div class="sc-section-title">Account</div>
                <div class="sc-profile-card">
                    <div class="sc-avatar">${initials}</div>
                    <div>
                        <p class="sc-profile-name">${fullName}</p>
                        <p class="sc-profile-email">${email}</p>
                    </div>
                </div>
            </div>
            <div class="sc-section">
                <div class="sc-section-title">Actions</div>
                <div class="sc-row" onclick="editDisplayName()">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path>
                        <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Edit display name</p>
                        <p class="sc-row-sub">Change how your name appears</p>
                    </div>
                </div>
                <div class="sc-row danger" onclick="logoutUser()">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path>
                        <polyline points="16 17 21 12 16 7"></polyline>
                        <line x1="21" y1="12" x2="9" y2="12"></line>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Log out</p>
                        <p class="sc-row-sub">Sign out of your account</p>
                    </div>
                </div>
            </div>`,

        chats: `
            <div class="sc-section">
                <div class="sc-section-title">Manage chats</div>
                <div class="sc-row" onclick="archiveAllChats()">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="21 8 21 21 3 21 3 8"></polyline>
                        <rect x="1" y="3" width="22" height="5"></rect>
                        <line x1="10" y1="12" x2="14" y2="12"></line>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Archive all chats</p>
                        <p class="sc-row-sub">Hide all chats from your history</p>
                    </div>
                </div>
                <div class="sc-row danger" onclick="clearAllChats()">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="3 6 5 6 21 6"></polyline>
                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                        <line x1="10" y1="11" x2="10" y2="17"></line>
                        <line x1="14" y1="11" x2="14" y2="17"></line>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Delete all chats</p>
                        <p class="sc-row-sub">Permanently remove all history</p>
                    </div>
                </div>
            </div>
            <div class="sc-section">
                <div class="sc-section-title">Preferences</div>
                <div class="sc-row disabled">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"></path>
                        <polyline points="17 21 17 13 7 13 7 21"></polyline>
                        <polyline points="7 3 7 8 15 8"></polyline>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Export chat history</p>
                        <p class="sc-row-sub soon">Coming soon</p>
                    </div>
                </div>
            </div>`,

        privacy: `
            <div class="sc-section">
                <div class="sc-section-title">Privacy controls</div>
                <div class="sc-row disabled">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Data & privacy</p>
                        <p class="sc-row-sub soon">Coming soon</p>
                    </div>
                </div>
                <div class="sc-row disabled">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>
                        <path d="M12 8v4M12 16h.01"></path>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Delete my account</p>
                        <p class="sc-row-sub soon">Coming soon</p>
                    </div>
                </div>
            </div>`
    };

    content.innerHTML = tabs[tab] || tabs.general;
};

// ============================
// 🗂️ ARCHIVE ALL CHATS
// ============================
window.archiveAllChats = async function () {
    if (!confirm("Archive all chats? They will be hidden from your history.")) return;
    const { error } = await supabaseClient
        .from("chat_sessions").update({ archived: true }).eq("user_id", currentUser.id);
    if (error) showToast("❌ Archive failed. Please try again.");
    else showToast("✓ All chats archived successfully");
};

// ============================
// 🗑️ DELETE ALL CHATS
// ============================
window.clearAllChats = async function () {
    if (!confirm("Delete ALL chats permanently? This cannot be undone.")) return;

    const { error: msgErr } = await supabaseClient.from("messages").delete().eq("user_id", currentUser.id);
    if (msgErr) { showToast("❌ Failed to delete messages"); return; }

    const { error: sessErr } = await supabaseClient.from("chat_sessions").delete().eq("user_id", currentUser.id);
    if (sessErr) { showToast("❌ Failed to delete sessions"); return; }

    showToast("✓ All chats deleted successfully");

    const chatbox   = document.getElementById("chatbox");
    const inputArea = document.getElementById("inputArea");
    const app       = document.getElementById("app");

    if (chatbox) chatbox.innerHTML = "";
    currentSessionId = generateSessionId();
    firstMessage = true;
    if (inputArea) {
        inputArea.classList.remove("bottom");
        inputArea.classList.add("center");
    }
    if (app) app.classList.add("greeting-mode");
    displayGreeting();
    closeSettings();
};

// ============================
// 🗑️ DELETE SINGLE CHAT
// ============================
async function deleteSingleChat(sessionId) {
    if (!confirm("Delete this chat? This cannot be undone.")) return;

    const { error: msgErr } = await supabaseClient.from("messages").delete()
        .eq("session_id", sessionId).eq("user_id", currentUser.id);
    if (msgErr) { showToast("�� Failed to delete messages"); return; }

    const { error: sessErr } = await supabaseClient.from("chat_sessions").delete()
        .eq("session_id", sessionId).eq("user_id", currentUser.id);
    if (sessErr) { showToast("❌ Failed to delete session"); return; }

    if (currentSessionId === sessionId) {
        currentSessionId = generateSessionId();
        firstMessage = true;
        const chatbox   = document.getElementById("chatbox");
        const inputArea = document.getElementById("inputArea");
        const app       = document.getElementById("app");
        if (chatbox) chatbox.innerHTML = "";
        if (inputArea) {
            inputArea.classList.remove("bottom");
            inputArea.classList.add("center");
        }
        if (app) app.classList.add("greeting-mode");
        displayGreeting();
    }

    showToast("✓ Chat deleted");
    showHistory();
}

// ============================
// ✏️ RENAME CHAT
// ============================
async function renameChat(sessionId, currentTitle, titleEl) {
    const newTitle = prompt("Rename chat:", currentTitle);
    if (!newTitle || newTitle.trim() === currentTitle) return;

    const { error } = await supabaseClient.from("chat_sessions")
        .update({ title: newTitle.trim() })
        .eq("session_id", sessionId)
        .eq("user_id", currentUser.id);

    if (error) { showToast("❌ Failed to rename chat"); return; }
    titleEl.textContent = newTitle.trim();
    showToast("✓ Chat renamed");
}

// ============================
// ⋯ HISTORY 3-DOT MENU
// ============================
function closeAllMenus() {
    document.querySelectorAll(".history-dropdown.open").forEach(d => d.classList.remove("open"));
}

function buildHistoryItem(session, openSessionFn) {
    const date = new Date(session.created_at).toLocaleDateString();

    const item = document.createElement("div");
    item.classList.add("sidebar-item", "history-item");

    const info = document.createElement("div");
    info.classList.add("history-info");
    info.style.flex = "1";
    info.style.minWidth = "0";
    info.style.cursor = "pointer";

    const titleEl = document.createElement("span");
    titleEl.classList.add("history-title");
    titleEl.textContent = session.title || "Untitled";

    const dateEl = document.createElement("span");
    dateEl.classList.add("history-date");
    dateEl.textContent = date;

    info.appendChild(titleEl);
    info.appendChild(dateEl);
    info.onclick = () => {
        const overlay = document.getElementById("settingsOverlay");
        if (overlay) overlay.classList.remove("active");
        openSessionFn(session.session_id);
    };

    const menuBtn = document.createElement("button");
    menuBtn.classList.add("history-menu-btn");
    menuBtn.title = "Options";
    menuBtn.innerHTML = `
        <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor">
            <circle cx="12" cy="5"  r="1.5"/>
            <circle cx="12" cy="12" r="1.5"/>
            <circle cx="12" cy="19" r="1.5"/>
        </svg>`;

    const dropdown = document.createElement("div");
    dropdown.classList.add("history-dropdown");
    dropdown.innerHTML = `
        <button class="history-dropdown-item" data-action="open">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg>
            Open chat
        </button>
        <button class="history-dropdown-item" data-action="rename">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>
            Rename
        </button>
        <button class="history-dropdown-item danger" data-action="delete">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>
            Delete chat
        </button>`;

    menuBtn.onclick = (e) => {
        e.stopPropagation();
        const isOpen = dropdown.classList.contains("open");
        closeAllMenus();
        if (!isOpen) dropdown.classList.add("open");
    };

    dropdown.querySelectorAll(".history-dropdown-item").forEach(btn => {
        btn.onclick = (e) => {
            e.stopPropagation();
            closeAllMenus();
            const action = btn.dataset.action;
            if (action === "open")   openSessionFn(session.session_id);
            if (action === "rename") renameChat(session.session_id, session.title || "Untitled", titleEl);
            if (action === "delete") deleteSingleChat(session.session_id);
        };
    });

    document.addEventListener("click", closeAllMenus, { once: false });

    const menuWrap = document.createElement("div");
    menuWrap.classList.add("history-menu-wrap");
    menuWrap.appendChild(menuBtn);
    menuWrap.appendChild(dropdown);

    item.appendChild(info);
    item.appendChild(menuWrap);
    return item;
}

// ============================
// 🚀 APP START
// ============================
document.addEventListener("DOMContentLoaded", async function () {

    initTheme();
    initFontSize();

    await getUser();

    if (!currentUser) {
        window.location.href = "/auth.html";
        return;
    }

    displayGreeting();

    const chatbox   = document.getElementById("chatbox");
    const input     = document.getElementById("input");
    const inputArea = document.getElementById("inputArea");
    const app       = document.getElementById("app");

    window.autoResize = function () {
        input.style.height = "auto";
        input.style.height = input.scrollHeight + "px";
    };
    input.addEventListener("input", autoResize);

    // ============================
    // 🔥 SEND MESSAGE - FIXED
    // ============================
    window.sendMessage = async function () {
        const message = input.value.trim();
        if (!message) return;

        // Clear greeting and adjust layout on first message
        if (firstMessage) {
            chatbox.innerHTML = "";
            inputArea.classList.remove("center");
            inputArea.classList.add("bottom");
            app.classList.remove("greeting-mode");
        }

        const userBubble = createUserBubble(message);
        chatbox.appendChild(userBubble);

        if (firstMessage) {
            firstMessage = false;
            chatTitle = message.substring(0, 40);
            const { error } = await supabaseClient.from("chat_sessions").insert([{
                session_id: currentSessionId,
                title: chatTitle,
                user_id: currentUser.id
            }]);
            if (error) console.error("❌ Session insert failed:", error.message);
        }

        const { error: userError } = await supabaseClient.from("messages").insert([{
            role: "user",
            content: message,
            session_id: currentSessionId,
            user_id: currentUser.id
        }]);
        if (userError) console.error("❌ User message save failed:", userError.message);

        input.value = "";
        input.style.height = "auto";
        chatbox.scrollTop = chatbox.scrollHeight;

        const heavy = isHeavyQuery(message);
        const thinking = heavy ? createThinkingIndicator() : createLightIndicator();
        chatbox.appendChild(thinking);
        chatbox.scrollTop = chatbox.scrollHeight;

        try {
            const model = getSelectedModel(); // Get selected model
            const res = await fetch(`/chat?prompt=${encodeURIComponent(message)}&model=${model}`);
            if (!res.ok) throw new Error("Server error " + res.status);

            thinking.remove();

            const { wrapper, botMsg } = createBotWrapper();
            chatbox.appendChild(wrapper);

            const reader  = res.body.getReader();
            const decoder = new TextDecoder();
            const fullReply = await streamWords(botMsg, wrapper, reader, decoder, chatbox);

            if (fullReply) {
                const { error: botError } = await supabaseClient.from("messages").insert([{
                    role: "bot",
                    content: fullReply,
                    session_id: currentSessionId,
                    user_id: currentUser.id
                }]);
                if (botError) console.error("❌ Bot message save failed:", botError.message);
            }

        } catch (err) {
            thinking.remove();
            console.error("❌ AI fetch failed:", err);
            const errMsg = document.createElement("div");
            errMsg.classList.add("message", "bot");
            errMsg.innerHTML = `<p style="color:#e06c6c">⚠️ Failed to get a response. Please try again.</p>`;
            chatbox.appendChild(errMsg);
        }
    };

    input.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    // ============================
    // 📂 LOAD SESSION
    // ============================
    async function loadSession(sessionId) {
        chatbox.innerHTML = "";
        inputArea.classList.remove("center");
        inputArea.classList.add("bottom");
        app.classList.remove("greeting-mode");

        currentSessionId = sessionId;
        firstMessage = false;

        const { data, error } = await supabaseClient
            .from("messages").select("*")
            .eq("session_id", sessionId)
            .eq("user_id", currentUser.id)
            .order("created_at", { ascending: true });

        if (error) { console.error("❌ Load session failed:", error.message); return; }

        data.forEach(msg => {
            if (msg.role === "user") {
                chatbox.appendChild(createUserBubble(msg.content));
            } else {
                const { wrapper, botMsg } = createBotWrapper();
                botMsg.innerHTML = formatMessage(msg.content);
                wrapper.dataset.raw = msg.content;
                chatbox.appendChild(wrapper);
            }
        });

        chatbox.scrollTop = chatbox.scrollHeight;
        if (window.innerWidth <= 768) closeSidebar();
        showMainMenu();
    }

    // ============================
    // 📜 SHOW HISTORY
    // ============================
    window.showHistory = async function () {
        const overlay = document.getElementById("settingsOverlay");
        if (overlay) overlay.classList.remove("active");

        const { data, error } = await supabaseClient
            .from("chat_sessions").select("*")
            .eq("user_id", currentUser.id)
            .order("created_at", { ascending: false });

        if (error) { console.error("❌ History failed:", error.message); return; }

        const menu = document.querySelector(".sidebar-menu");
        menu.innerHTML = `
            <div class="history-header">
                <button class="back-btn" onclick="showMainMenu()">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"></line><polyline points="12 19 5 12 12 5"></polyline></svg>
                    Back
                </button>
                <span>Chat History</span>
            </div>`;

        if (data.length === 0) {
            menu.innerHTML += `<div class="no-history">No chats yet. Start a new chat to see history.</div>`;
            return;
        }

        data.forEach(session => {
            const item = buildHistoryItem(session, loadSession);
            menu.appendChild(item);
        });
    };

});

// ============================
// 🎨 THEME SYSTEM
// ============================
window.setTheme = function(theme) {
    localStorage.setItem('catura-theme', theme);
    applyTheme(theme);
    // Update active button
    document.querySelectorAll('.theme-option').forEach(btn => btn.classList.remove('active'));
    const activeBtn = document.querySelector(`.theme-option[onclick="setTheme('${theme}')"]`);
    if (activeBtn) activeBtn.classList.add('active');
    showToast(`Theme: ${theme.charAt(0).toUpperCase() + theme.slice(1)}`, 1500);
};

function applyTheme(theme) {
    const root = document.documentElement;
    root.removeAttribute('data-theme');
    if (theme === 'light') {
        root.setAttribute('data-theme', 'light');
    } else if (theme === 'auto') {
        const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
        if (!prefersDark) root.setAttribute('data-theme', 'light');
    }
    // dark = default, no attribute needed
}

function initTheme() {
    const saved = localStorage.getItem('catura-theme') || 'dark';
    applyTheme(saved);
    // Listen for system preference changes (for auto mode)
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
        if ((localStorage.getItem('catura-theme') || 'dark') === 'auto') applyTheme('auto');
    });
}

// ============================
// 🔠 FONT SIZE SYSTEM
// ============================
window.setFontSize = function(size) {
    localStorage.setItem('catura-font', size);
    applyFontSize(size);
    document.querySelectorAll('.font-option').forEach(btn => btn.classList.remove('active'));
    const activeBtn = document.querySelector(`.font-option[onclick="setFontSize('${size}')"]`);
    if (activeBtn) activeBtn.classList.add('active');
    showToast(`Font size: ${size.charAt(0).toUpperCase() + size.slice(1)}`, 1500);
};

function applyFontSize(size) {
    const root = document.documentElement;
    root.removeAttribute('data-fontsize');
    if (size && size !== 'default') root.setAttribute('data-fontsize', size);
}

function initFontSize() {
    const saved = localStorage.getItem('catura-font') || 'default';
    applyFontSize(saved);
}

// ============================
// ✅ PLUS MENU (+ Button dropdown)
// ============================
function togglePlusMenu(e) {
    e.stopPropagation();
    const dropdown = document.getElementById('plusDropdown');
    dropdown.classList.toggle('open');
}

// Close dropdown when clicking outside
document.addEventListener('click', function(e) {
    const wrap = document.getElementById('plusMenuWrap');
    if (wrap && !wrap.contains(e.target)) {
        const dropdown = document.getElementById('plusDropdown');
        if (dropdown) dropdown.classList.remove('open');
    }
});

function handlePlusAction(action) {
    const dropdown = document.getElementById('plusDropdown');
    if (dropdown) dropdown.classList.remove('open');

    if (action === 'file') {
        const fi = document.getElementById('fileInput');
        if (fi) fi.click();
    } else if (action === 'connect') {
        showToast('Connect apps — coming soon!');
    } else if (action === 'think') {
        showToast('Think mode — coming soon!');
    } else if (action === 'research') {
        showToast('Deep research — coming soon!');
    } else if (action === 'search') {
        showToast('Web search — coming soon!');
    }
}

function handleFileSelect(event) {
    const files = event.target.files;
    if (!files || files.length === 0) return;
    const names = Array.from(files).map(f => f.name).join(', ');
    showToast(`Selected: ${names}`);
    event.target.value = '';
}


// ============================
// 🤖 MODEL SELECTOR
// ============================
let selectedModel = 'dagr'; // Default model

window.toggleModelSelector = function (e) {
    e.stopPropagation();
    const dropdown = document.getElementById('modelDropdown');
    const btn = document.getElementById('modelSelectorBtn');
    
    const isOpen = dropdown.classList.contains('open');
    closeAllModelMenus();
    
    if (!isOpen) {
        dropdown.classList.add('open');
        btn.classList.add('open');
    }
};

window.selectModel = function (modelId, modelName) {
    selectedModel = modelId.toLowerCase();
    
    // Update button text
    const modelNameEl = document.getElementById('modelName');
    if (modelNameEl) {
        modelNameEl.textContent = modelName;
    }
    
    // Update active state for all model options
    document.querySelectorAll('.model-option').forEach(opt => {
        opt.classList.remove('active');
    });
    
    // Find and activate the clicked option
    const activeOption = document.querySelector(`[data-model="${modelId}"]`);
    if (activeOption) {
        activeOption.classList.add('active');
    }
    
    // Close dropdown
    closeAllModelMenus();
    
    showToast(`✓ Switched to ${modelName}`, 1500);
};

function closeAllModelMenus() {
    const dropdown = document.getElementById('modelDropdown');
    const btn = document.getElementById('modelSelectorBtn');
    if (dropdown) dropdown.classList.remove('open');
    if (btn) btn.classList.remove('open');
}

// Close dropdown when clicking outside
document.addEventListener('click', function (e) {
    const wrap = document.getElementById('modelSelectorWrap');
    if (wrap && !wrap.contains(e.target)) {
        closeAllModelMenus();
    }
});

// Get currently selected model
function getSelectedModel() {
    return selectedModel;
}