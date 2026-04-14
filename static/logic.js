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
// 🧾 PREMIUM MARKDOWN RENDERER
// ============================
function formatMessage(text) {

    function escapeHtml(str) {
        return str
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
    }

    // ── Fenced code blocks ──────────────────────────────
    text = text.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
        const language = lang.trim() || "code";
        const escaped  = escapeHtml(code.trimEnd());
        return `<div class="code-block">
            <div class="code-header">
                <span class="lang-label">${language}</span>
                <button onclick="copyCode(this)">Copy</button>
            </div>
            <pre><code>${escaped}</code></pre>
        </div>`;
    });

    // ── Tables ───────────────────────────────────────────
    // Match a markdown table block
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

    // ── Headings ─────────────────────────────────────────
    text = text.replace(/^### (.+)$/gm, "<h3>$1</h3>");
    text = text.replace(/^## (.+)$/gm,  "<h2>$1</h2>");
    text = text.replace(/^# (.+)$/gm,   "<h1>$1</h1>");

    // ── Blockquote ───────────────────────────────────────
    text = text.replace(/^> (.+)$/gm, "<blockquote>$1</blockquote>");

    // ── Horizontal rule ──────────────────────────────────
    text = text.replace(/^---+$/gm, "<hr>");

    // ── Bold + Italic ────────────────────────────────────
    text = text.replace(/\*\*\*(.+?)\*\*\*/g, "<strong><em>$1</em></strong>");
    text = text.replace(/\*\*(.+?)\*\*/g,     "<strong>$1</strong>");
    text = text.replace(/\*(.+?)\*/g,         "<em>$1</em>");

    // ── Inline code ──────────────────────────────────────
    text = text.replace(/`([^`]+)`/g, '<span class="inline-code">$1</span>');

    // ── Ordered lists ────────────────────────────────────
    text = text.replace(/((?:^\d+\. .+\n?)+)/gm, (block) => {
        const items = block.trim().split("\n")
            .map(l => `<li>${l.replace(/^\d+\.\s/, "")}</li>`)
            .join("");
        return `<ol>${items}</ol>`;
    });

    // ── Unordered lists ──────────────────────────────────
    text = text.replace(/((?:^[-•*] .+\n?)+)/gm, (block) => {
        const items = block.trim().split("\n")
            .map(l => `<li>${l.replace(/^[-•*]\s/, "")}</li>`)
            .join("");
        return `<ul>${items}</ul>`;
    });

    // ── Paragraphs (wrap plain lines) ────────────────────
    const lines = text.split("\n");
    let result = "";
    let para   = "";

    for (const line of lines) {
        const trimmed = line.trim();
        const isBlock = /^<(h[123]|ul|ol|li|blockquote|hr|table|div|pre)/.test(trimmed);

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

    return result;
}

// ============================
// 📋 COPY CODE
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
        </div>
    `;
    return div;
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
        .from("chat_sessions")
        .update({ archived: true })
        .eq("user_id", currentUser.id);
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

    // ── Auto resize textarea ──────────────────────────────
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

        // User bubble
        const userMsg = document.createElement("div");
        userMsg.classList.add("message", "user");
        userMsg.innerText = message;
        chatbox.appendChild(userMsg);

        // Session creation on first message
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

        // Save user message
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

        // ── Thinking indicator ────────────────────────────
        const thinking = createThinkingIndicator();
        chatbox.appendChild(thinking);
        chatbox.scrollTop = chatbox.scrollHeight;

        // ============================
        // 🤖 STREAMING RESPONSE
        // ============================
        try {
            const res = await fetch(`/chat?prompt=${encodeURIComponent(message)}`);
            if (!res.ok) throw new Error("Server error " + res.status);

            thinking.remove();

            const botMsg = document.createElement("div");
            botMsg.classList.add("message", "bot");
            botMsg.classList.add("cursor-blink"); // blinking cursor while streaming
            chatbox.appendChild(botMsg);

            const reader  = res.body.getReader();
            const decoder = new TextDecoder();
            let buffer    = "";
            let fullReply = "";
            let firstToken = true;

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
                            break;
                        }
                        if (chunk.token) {
                            fullReply += chunk.token;
                            // Small delay feel on first token
                            if (firstToken) {
                                firstToken = false;
                                await new Promise(r => setTimeout(r, 30));
                            }
                            botMsg.innerHTML = formatMessage(fullReply);
                            chatbox.scrollTop = chatbox.scrollHeight;
                        }
                    } catch { continue; }
                }
            }

            // Remove cursor after done
            botMsg.classList.remove("cursor-blink");

            // Save to Supabase
            const { error: botError } = await supabaseClient.from("messages").insert([{
                role: "bot",
                content: fullReply,
                session_id: currentSessionId,
                user_id: currentUser.id
            }]);
            if (botError) console.error("❌ Bot message save failed:", botError.message);

        } catch (err) {
            thinking.remove();
            console.error("❌ AI fetch failed:", err);
            const errMsg = document.createElement("div");
            errMsg.classList.add("message", "bot");
            errMsg.innerHTML = `<p style="color:#e06c6c">⚠️ Failed to get a response. Please try again.</p>`;
            chatbox.appendChild(errMsg);
        }
    };

    // ── Enter to send ─────────────────────────────────────
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
            .from("messages")
            .select("*")
            .eq("session_id", sessionId)
            .eq("user_id", currentUser.id)
            .order("created_at", { ascending: true });

        if (error) { console.error("❌ Load session failed:", error.message); return; }

        data.forEach(msg => {
            const div = document.createElement("div");
            div.classList.add("message", msg.role);
            div.innerHTML = msg.role === "bot"
                ? formatMessage(msg.content)
                : msg.content;
            chatbox.appendChild(div);
        });

        chatbox.scrollTop = chatbox.scrollHeight;
        if (window.innerWidth <= 768) closeSidebar();
    }

    // ============================
    // 📜 SHOW HISTORY
    // ============================
    window.showHistory = async function () {
        const { data, error } = await supabaseClient
            .from("chat_sessions")
            .select("*")
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
