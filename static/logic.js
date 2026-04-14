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
    autoResize();
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
// Called ONCE after streaming is fully complete
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
        return `<ol style="list-style-type: decimal; counter-reset: none;">${items}</ol>`;
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
            if (para.trim()) {
                result += `<p>${para.trim()}</p>`;
                para = "";
            }
        } else if (isBlock) {
            if (para.trim()) {
                result += `<p>${para.trim()}</p>`;
                para = "";
            }
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
// 📋 COPY CODE (inside code block)
// ============================
function copyCode(btn) {
    const code = btn.closest(".code-block").querySelector("code").innerText;
    navigator.clipboard.writeText(code).then(() => {
        btn.textContent = "Copied!";
        btn.classList.add("copied");
        setTimeout(() => {
            btn.textContent = "Copy";
            btn.classList.remove("copied");
        }, 2000);
    });
}

// ============================
// 📋 COPY FULL BOT ANSWER
// ============================
function copyBotAnswer(btn) {
    // Walk up to the bot message wrapper and grab raw stored text
    const wrapper = btn.closest(".bot-msg-wrapper");
    const rawText = wrapper ? wrapper.dataset.raw : "";
    if (!rawText) return;

    navigator.clipboard.writeText(rawText).then(() => {
        btn.innerHTML = `
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="20 6 9 17 4 12"/>
            </svg>
            Copied!
        `;
        btn.classList.add("copied");
        setTimeout(() => {
            btn.innerHTML = `
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                </svg>
                Copy
            `;
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
        btn.innerHTML = `
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="20 6 9 17 4 12"/>
            </svg>
        `;
        btn.classList.add("copied");
        setTimeout(() => {
            btn.innerHTML = `
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                </svg>
            `;
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
        </div>
    `;
    return div;
}

// ============================
// 💬 LIGHT: SIMPLE DOTS INDICATOR
// ============================
function createLightIndicator() {
    const div = document.createElement("div");
    div.classList.add("message", "bot", "typing");
    div.innerHTML = `
        <div class="typing-dots">
            <span></span>
            <span></span>
            <span></span>
        </div>
    `;
    return div;
}

// ============================
// 📦 CREATE USER MESSAGE BUBBLE
// With hover-reveal copy button
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
    copyBtn.innerHTML = `
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
        </svg>
    `;
    copyBtn.onclick = () => copyUserMessage(copyBtn);

    wrapper.appendChild(copyBtn);
    wrapper.appendChild(bubble);
    return wrapper;
}

// ============================
// 📦 CREATE BOT MESSAGE WRAPPER
// With copy button shown below the answer
// ============================
function createBotWrapper() {
    const wrapper = document.createElement("div");
    wrapper.classList.add("bot-msg-wrapper");

    const botMsg = document.createElement("div");
    botMsg.classList.add("message", "bot");

    // Copy button row shown below the answer
    const actionsRow = document.createElement("div");
    actionsRow.classList.add("bot-actions");
    actionsRow.innerHTML = `
        <button class="bot-copy-btn" onclick="copyBotAnswer(this)" title="Copy answer">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
            </svg>
            Copy
        </button>
    `;

    wrapper.appendChild(botMsg);
    wrapper.appendChild(actionsRow);
    return { wrapper, botMsg };
}

// ============================
// ✍️ WORD-BY-WORD STREAM RENDERER
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

                if (chunk.error) {
                    botMsg.innerHTML = `<p style="color:#e06c6c">⚠️ ${chunk.error}</p>`;
                    return "";
                }

                if (chunk.token) {
                    fullReply += chunk.token;
                    liveSpan.textContent = fullReply;
                    chatbox.scrollTop = chatbox.scrollHeight;
                }
            } catch { continue; }
        }
    }

    // ✅ Streaming done — render markdown + store raw text for copy
    botMsg.innerHTML = formatMessage(fullReply);
    wrapper.dataset.raw = fullReply; // store raw for copy button

    return fullReply;
}

// ============================
// ⚙️ SETTINGS PANEL
// ============================
window.showSettings = function () {
    const menu     = document.querySelector(".sidebar-menu");
    const email    = currentUser?.email || "Not logged in";
    const fullName = document.getElementById("userFullname")?.textContent || "User";
    const initials = document.getElementById("userAvatar")?.textContent  || "?";

    menu.innerHTML = `
        <div class="history-header">
            <button class="back-btn" onclick="showMainMenu()">← Back</button>
            <span>Settings</span>
        </div>

        <div class="settings-section">
            <div class="settings-section-title">Profile</div>
            <div class="settings-profile-card">
                <div class="settings-avatar">${initials}</div>
                <div class="settings-profile-info">
                    <div class="settings-profile-name">${fullName}</div>
                    <div class="settings-profile-email">${email}</div>
                </div>
            </div>
        </div>

        <div class="settings-section">
            <div class="settings-section-title">Chat Settings</div>
            <div class="settings-item" onclick="archiveAllChats()">
                <span class="settings-item-icon">🗂️</span>
                <div class="settings-item-text">
                    <div class="settings-item-label">Archive all chats</div>
                    <div class="settings-item-sub">Hide chats from history</div>
                </div>
            </div>
            <div class="settings-item danger" onclick="clearAllChats()">
                <span class="settings-item-icon">🗑️</span>
                <div class="settings-item-text">
                    <div class="settings-item-label">Delete all chats</div>
                    <div class="settings-item-sub">Permanently remove all history</div>
                </div>
            </div>
        </div>

        <div class="settings-section">
            <div class="settings-section-title">Privacy</div>
            <div class="settings-item disabled">
                <span class="settings-item-icon">🔒</span>
                <div class="settings-item-text">
                    <div class="settings-item-label">Privacy controls</div>
                    <div class="settings-item-sub coming-soon">Coming soon</div>
                </div>
            </div>
        </div>

        <div class="settings-section">
            <div class="settings-section-title">Support</div>
            <div class="settings-item disabled">
                <span class="settings-item-icon">🐛</span>
                <div class="settings-item-text">
                    <div class="settings-item-label">Send bug report</div>
                    <div class="settings-item-sub coming-soon">Coming soon</div>
                </div>
            </div>
        </div>
    `;
};

// ============================
// 🗂️ ARCHIVE ALL CHATS
// ============================
window.archiveAllChats = async function () {
    if (!confirm("Archive all chats? They will be hidden from your history.")) return;
    const { error } = await supabaseClient
        .from("chat_sessions").update({ archived: true }).eq("user_id", currentUser.id);
    if (error) {
        alert("Archive needs an 'archived' boolean column in chat_sessions (default false).");
    } else {
        showToast("All chats archived.");
    }
};

// ============================
// 🗑️ DELETE ALL CHATS
// ============================
window.clearAllChats = async function () {
    if (!confirm("Delete ALL chats permanently? This cannot be undone.")) return;

    const { error: msgErr } = await supabaseClient
        .from("messages").delete().eq("user_id", currentUser.id);
    if (msgErr) { alert("Failed: " + msgErr.message); return; }

    const { error: sessErr } = await supabaseClient
        .from("chat_sessions").delete().eq("user_id", currentUser.id);
    if (sessErr) { alert("Failed: " + sessErr.message); return; }

    showToast("All chats deleted.");

    const chatbox   = document.getElementById("chatbox");
    const inputArea = document.getElementById("inputArea");
    const welcome   = document.getElementById("welcome");
    chatbox.innerHTML = "";
    currentSessionId = generateSessionId();
    firstMessage = true;
    if (welcome) welcome.style.display = "block";
    inputArea.classList.remove("bottom");
    inputArea.classList.add("center");
    showMainMenu();
};

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

        // ✅ User bubble with hover copy button
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

        // Smart indicator
        const heavy = isHeavyQuery(message);
        const thinking = heavy ? createThinkingIndicator() : createLightIndicator();
        chatbox.appendChild(thinking);
        chatbox.scrollTop = chatbox.scrollHeight;

        try {
            const res = await fetch(`/chat?prompt=${encodeURIComponent(message)}`);
            if (!res.ok) throw new Error("Server error " + res.status);

            thinking.remove();

            // ✅ Bot message with copy button below
            const { wrapper, botMsg } = createBotWrapper();
            chatbox.appendChild(wrapper);

            const reader  = res.body.getReader();
            const decoder = new TextDecoder();

            // ✅ Word-by-word stream, markdown + raw stored at end
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

        const { data, error } = await supabaseClient
            .from("messages").select("*")
            .eq("session_id", sessionId)
            .eq("user_id", currentUser.id)
            .order("created_at", { ascending: true });

        if (error) { console.error("❌ Load session failed:", error.message); return; }

        data.forEach(msg => {
            if (msg.role === "user") {
                const bubble = createUserBubble(msg.content);
                chatbox.appendChild(bubble);
            } else {
                const { wrapper, botMsg } = createBotWrapper();
                botMsg.innerHTML = formatMessage(msg.content);
                wrapper.dataset.raw = msg.content;
                chatbox.appendChild(wrapper);
            }
        });

        chatbox.scrollTop = chatbox.scrollHeight;
        if (window.innerWidth <= 768) closeSidebar();
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
            </div>
        `;

        if (data.length === 0) {
            menu.innerHTML += `<div class="no-history">No chats yet</div>`;
            return;
        }

        data.forEach(session => {
            const date = new Date(session.created_at).toLocaleDateString();
            const item = document.createElement("div");
            item.classList.add("sidebar-item", "history-item");
            item.innerHTML = `
                <div class="history-info">
                    <span class="history-title">${session.title || "Untitled"}</span>
                    <span class="history-date">${date}</span>
                </div>
            `;
            item.onclick = async () => {
                currentSessionId = session.session_id;
                firstMessage = false;
                await loadSession(session.session_id);
                showMainMenu();
            };
            menu.appendChild(item);
        });
    };

    // ============================
    // 🧭 MAIN MENU
    // ============================
    window.showMainMenu = function () {
        document.querySelector(".sidebar-menu").innerHTML = `
            <div class="sidebar-item" onclick="newChat()">
                <span class="sidebar-icon">✏️</span> New Chat
            </div>
            <div class="sidebar-item" onclick="showHistory()">
                <span class="sidebar-icon">🕘</span> Chat History
            </div>
            <div class="sidebar-item" onclick="showSettings()">
                <span class="sidebar-icon">⚙️</span> Settings
            </div>
        `;
    };

    // ============================
    // ➕ NEW CHAT
    // ============================
    window.newChat = function () {
        currentSessionId = generateSessionId();
        firstMessage = true;
        chatbox.innerHTML = "";
        if (welcome) welcome.style.display = "block";
        inputArea.classList.remove("bottom");
        inputArea.classList.add("center");
        if (window.innerWidth <= 768) closeSidebar();
    };

    // ============================
    // 🚪 LOGOUT
    // ============================
    window.logoutUser = async function () {
        await supabaseClient.auth.signOut();
        window.location.href = "/auth.html";
    };

});
