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

        const avatarEl = document.getElementById("userAvatar");
        const nameEl   = document.getElementById("userFullname");
        if (avatarEl) avatarEl.textContent = initials;
        if (nameEl)   nameEl.textContent   = fullName;
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
// 🔀 SIDEBAR
// ============================
function toggleSidebar() {
    const sidebar = document.getElementById("sidebar");
    const overlay = document.getElementById("sidebarOverlay");
    sidebar.classList.toggle("open");
    overlay.classList.toggle("show");
}

function closeSidebar() {
    const sidebar = document.getElementById("sidebar");
    const overlay = document.getElementById("sidebarOverlay");
    sidebar.classList.remove("open");
    overlay.classList.remove("show");
}

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
// 📋 COPY CODE (code block)
// ============================
function copyCode(btn) {
    const code = btn.closest(".code-block").querySelector("code").innerText;
    navigator.clipboard.writeText(code).then(() => {
        btn.textContent = "Copied!";
        btn.classList.add("copied");
        setTimeout(() => { btn.textContent = "Copy"; btn.classList.remove("copied"); }, 2000);
    });
}

// ============================
// 📋 COPY FULL BOT ANSWER
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
    });
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
    });
}

// ============================
// 🍞 TOAST
// ============================
function showToast(message) {
    let toast = document.getElementById("toastNotif");
    if (!toast) {
        toast = document.createElement("div");
        toast.id = "toastNotif";
        document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.classList.add("show");
    setTimeout(() => toast.classList.remove("show"), 3000);
}

// ============================
// 🤔 HEAVY: THINKING INDICATOR
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
// 💬 LIGHT: DOTS INDICATOR
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
// 📦 BOT MESSAGE WRAPPER
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
// ✍️ WORD-BY-WORD STREAMER
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
// ➕ NEW CHAT  ← FIXED: outside DOMContentLoaded
// ============================
window.newChat = function () {
    currentSessionId = generateSessionId();
    firstMessage = true;

    const chatbox   = document.getElementById("chatbox");
    const inputArea = document.getElementById("inputArea");
    const welcome   = document.getElementById("welcome");

    if (chatbox)   chatbox.innerHTML = "";
    if (welcome)   welcome.style.display = "block";
    if (inputArea) {
        inputArea.classList.remove("bottom");
        inputArea.classList.add("center");
    }
    if (window.innerWidth <= 768) closeSidebar();
};

// ============================
// 🚪 LOGOUT  ← FIXED: outside DOMContentLoaded
// ============================
window.logoutUser = async function () {
    await supabaseClient.auth.signOut();
    window.location.href = "/auth.html";
};

// ============================
// 🧭 MAIN MENU  ← FIXED: outside DOMContentLoaded
// ============================
window.showMainMenu = function () {
    const menu = document.querySelector(".sidebar-menu");
    if (!menu) return;
    menu.innerHTML = `
        <div class="sidebar-item" onclick="newChat()">
            <span class="sidebar-icon">✏️</span> New Chat
        </div>
        <div class="sidebar-item" onclick="showHistory()">
            <span class="sidebar-icon">🕘</span> Chat History
        </div>
        <div class="sidebar-item" onclick="showSettings()">
            <span class="sidebar-icon">⚙️</span> Settings
        </div>`;
};

// ============================
// ⚙️ SETTINGS OVERLAY  ← FIXED: outside DOMContentLoaded
// ============================
window.showSettings = function () {
    const overlay = document.getElementById("settingsOverlay");
    overlay.style.display = "block";

    const email    = currentUser?.email || "Not logged in";
    const fullName = document.getElementById("userFullname")?.textContent || "User";
    const initials = document.getElementById("userAvatar")?.textContent  || "?";

    overlay.innerHTML = `
        <button class="settings-close-btn" onclick="closeSettings()" title="Close">✕</button>
        <div class="settings-panel-wrap">
            <div class="settings-nav">
                <h2 class="settings-nav-title">Settings</h2>
                <div class="settings-nav-item active" onclick="showSettingsTab('general', this)">
                    <span class="sn-icon">⚙️</span> General
                </div>
                <div class="settings-nav-item" onclick="showSettingsTab('profile', this)">
                    <span class="sn-icon">👤</span> Profile
                </div>
                <div class="settings-nav-item" onclick="showSettingsTab('chats', this)">
                    <span class="sn-icon">💬</span> Chats
                </div>
                <div class="settings-nav-item" onclick="showSettingsTab('privacy', this)">
                    <span class="sn-icon">🔒</span> Privacy
                </div>
            </div>
            <div class="settings-content" id="settingsContent"></div>
        </div>`;

    showSettingsTab('general', overlay.querySelector('.settings-nav-item.active'));

    // ✅ FIX: Close settings when clicking outside the panel
    overlay.addEventListener('click', function(e) {
        if (e.target === overlay) {
            closeSettings();
        }
    }, { once: false });

    if (window.innerWidth <= 768) closeSidebar();
};

// ✅ FIX: REBUILD SIDEBAR MENU AFTER CLOSING SETTINGS
window.closeSettings = function () {
    const overlay = document.getElementById("settingsOverlay");
    if (overlay) {
        overlay.style.display = "none";
        // ✅ CRITICAL FIX: Rebuild the sidebar menu after closing settings
        showMainMenu();
    }
};

window.showSettingsTab = function (tab, clickedEl) {
    document.querySelectorAll(".settings-nav-item").forEach(el => el.classList.remove("active"));
    if (clickedEl) clickedEl.classList.add("active");

    const content = document.getElementById("settingsContent");
    if (!content) return;

    const email    = currentUser?.email || "Not logged in";
    const fullName = document.getElementById("userFullname")?.textContent || "User";
    const initials = document.getElementById("userAvatar")?.textContent  || "?";

    const tabs = {

        general: `
            <div class="sc-section">
                <div class="sc-section-title">Appearance</div>
                <div class="sc-row disabled">
                    <span class="sc-row-icon">🌙</span>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Theme</p>
                        <p class="sc-row-sub soon">Dark mode — more options coming soon</p>
                    </div>
                </div>
                <div class="sc-row disabled">
                    <span class="sc-row-icon">🔤</span>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Font size</p>
                        <p class="sc-row-sub soon">Coming soon</p>
                    </div>
                </div>
            </div>
            <div class="sc-section">
                <div class="sc-section-title">Support</div>
                <div class="sc-row disabled">
                    <span class="sc-row-icon">🐛</span>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Send bug report</p>
                        <p class="sc-row-sub soon">Coming soon</p>
                    </div>
                </div>
                <div class="sc-row disabled">
                    <span class="sc-row-icon">💡</span>
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
                <div class="sc-row disabled">
                    <span class="sc-row-icon">✏️</span>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Edit display name</p>
                        <p class="sc-row-sub soon">Coming soon</p>
                    </div>
                </div>
                <div class="sc-row danger" onclick="logoutUser()">
                    <span class="sc-row-icon">🚪</span>
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
                    <span class="sc-row-icon">🗂️</span>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Archive all chats</p>
                        <p class="sc-row-sub">Hide all chats from your history</p>
                    </div>
                </div>
                <div class="sc-row danger" onclick="clearAllChats()">
                    <span class="sc-row-icon">🗑️</span>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Delete all chats</p>
                        <p class="sc-row-sub">Permanently remove all history</p>
                    </div>
                </div>
            </div>
            <div class="sc-section">
                <div class="sc-section-title">Preferences</div>
                <div class="sc-row disabled">
                    <span class="sc-row-icon">💾</span>
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
                    <span class="sc-row-icon">🔒</span>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Data & privacy</p>
                        <p class="sc-row-sub soon">Coming soon</p>
                    </div>
                </div>
                <div class="sc-row disabled">
                    <span class="sc-row-icon">🛡️</span>
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
    if (error) alert("Archive needs an 'archived' boolean column in chat_sessions.");
    else showToast("All chats archived.");
};

// ============================
// 🗑️ DELETE ALL CHATS
// ============================
window.clearAllChats = async function () {
    if (!confirm("Delete ALL chats permanently? This cannot be undone.")) return;

    const { error: msgErr } = await supabaseClient.from("messages").delete().eq("user_id", currentUser.id);
    if (msgErr) { alert("Failed: " + msgErr.message); return; }

    const { error: sessErr } = await supabaseClient.from("chat_sessions").delete().eq("user_id", currentUser.id);
    if (sessErr) { alert("Failed: " + sessErr.message); return; }

    showToast("All chats deleted.");

    const chatbox   = document.getElementById("chatbox");
    const inputArea = document.getElementById("inputArea");
    const welcome   = document.getElementById("welcome");

    if (chatbox) chatbox.innerHTML = "";
    currentSessionId = generateSessionId();
    firstMessage = true;
    if (welcome)   welcome.style.display = "block";
    if (inputArea) {
        inputArea.classList.remove("bottom");
        inputArea.classList.add("center");
    }
    closeSettings();
};

// ============================
// 🗑️ DELETE SINGLE CHAT
// ============================
async function deleteSingleChat(sessionId) {
    if (!confirm("Delete this chat? This cannot be undone.")) return;

    const { error: msgErr } = await supabaseClient.from("messages").delete()
        .eq("session_id", sessionId).eq("user_id", currentUser.id);
    if (msgErr) { showToast("Failed to delete messages."); return; }

    const { error: sessErr } = await supabaseClient.from("chat_sessions").delete()
        .eq("session_id", sessionId).eq("user_id", currentUser.id);
    if (sessErr) { showToast("Failed to delete session."); return; }

    if (currentSessionId === sessionId) {
        currentSessionId = generateSessionId();
        firstMessage = true;
        const chatbox   = document.getElementById("chatbox");
        const inputArea = document.getElementById("inputArea");
        const welcome   = document.getElementById("welcome");
        if (chatbox) chatbox.innerHTML = "";
        if (welcome) welcome.style.display = "block";
        if (inputArea) {
            inputArea.classList.remove("bottom");
            inputArea.classList.add("center");
        }
    }

    showToast("Chat deleted.");
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

    if (error) { showToast("Failed to rename."); return; }

    titleEl.textContent = newTitle.trim();
    showToast("Chat renamed.");
}

// ============================
// ⋯ HISTORY ITEM 3-DOT MENU
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
    info.onclick = () => openSessionFn(session.session_id);

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
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
            Open chat
        </button>
        <button class="history-dropdown-item" data-action="rename">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
            Rename
        </button>
        <button class="history-dropdown-item danger" data-action="delete">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>
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

    await getUser();

    if (!currentUser) {
        window.location.href = "/auth.html";
        return;
    }

    const chatbox   = document.getElementById("chatbox");
    const input     = document.getElementById("input");
    const inputArea = document.getElementById("inputArea");
    const welcome   = document.getElementById("welcome");

    window.autoResize = function () {
        input.style.height = "auto";
        input.style.height = input.scrollHeight + "px";
    };
    input.addEventListener("input", autoResize);

    // ============================
    // 🔥 SEND MESSAGE
    // ============================
    window.sendMessage = async function () {
        const message = input.value.trim();
        if (!message) return;

        if (welcome) welcome.style.display = "none";
        inputArea.classList.remove("center");
        inputArea.classList.add("bottom");

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
            const res = await fetch(`/chat?prompt=${encodeURIComponent(message)}`);
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
        if (welcome) welcome.style.display = "none";
        inputArea.classList.remove("center");
        inputArea.classList.add("bottom");

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
        const { data, error } = await supabaseClient
            .from("chat_sessions").select("*")
            .eq("user_id", currentUser.id)
            .order("created_at", { ascending: false });

        if (error) { console.error("❌ History failed:", error.message); return; }

        const menu = document.querySelector(".sidebar-menu");
        menu.innerHTML = `
            <div class="history-header">
                <button class="back-btn" onclick="showMainMenu()">← Back</button>
                <span>Chat History</span>
            </div>`;

        if (data.length === 0) {
            menu.innerHTML += `<div class="no-history">No chats yet</div>`;
            return;
        }

        data.forEach(session => {
            const item = buildHistoryItem(session, loadSession);
            menu.appendChild(item);
        });
    };

});