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

    // ✅ Populate user profile in sidebar
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
// 🔀 SIDEBAR FUNCTIONS
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
// 💡 SUGGESTION CARDS
// ============================
function useSuggestion(el) {
    const input = document.getElementById("input");
    input.value = el.innerText.trim();
    input.focus();
    autoResize();
}

// ============================
// 🧾 FORMAT MESSAGE
// ============================
function formatMessage(text) {
    function escapeHtml(str) {
        return str
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
    }

    text = text.replace(/```(\w*)\n?([\s\S]*?)```/g, function (_, lang, code) {
        const language = lang || "code";
        const escaped = escapeHtml(code.trimEnd());
        return `<div class="code-block">
            <div class="code-header">
                <span>${language}</span>
                <button onclick="copyCode(this)">Copy</button>
            </div>
            <pre><code>${escaped}</code></pre>
        </div>`;
    });

    text = text.replace(/`([^`]+)`/g, '<code style="background:#0d0d0d;padding:2px 6px;border-radius:4px;font-family:monospace;font-size:13px;">$1</code>');
    text = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    text = text.replace(/\*(.*?)\*/g, '<em>$1</em>');
    text = text.replace(/^\d+\.\s(.+)$/gm, '<li>$1</li>');
    text = text.replace(/(<li>.*<\/li>)/s, '<ol>$1</ol>');
    text = text.replace(/^[-•]\s(.+)$/gm, '<li>$1</li>');
    text = text.replace(/\n/g, "<br>");

    return text;
}

// ============================
// 📋 COPY CODE
// ============================
function copyCode(btn) {
    const code = btn.closest(".code-block").querySelector("code").innerText;
    navigator.clipboard.writeText(code).then(() => {
        btn.textContent = "Copied!";
        setTimeout(() => btn.textContent = "Copy", 2000);
    });
}

// ============================
// ⚙️ SETTINGS PANEL
// ============================
window.showSettings = function () {
    const menu = document.querySelector(".sidebar-menu");
    const email = currentUser?.email || "Not logged in";
    const fullName = document.getElementById("userFullname")?.textContent || "User";
    const initials = document.getElementById("userAvatar")?.textContent || "?";

    menu.innerHTML = `
        <div class="history-header">
            <button class="back-btn" onclick="showMainMenu()">← Back</button>
            <span>Settings</span>
        </div>

        <!-- 1. PROFILE -->
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

        <!-- 2. CHAT SETTINGS -->
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

        <!-- 3. PRIVACY -->
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

        <!-- 4. BUG REPORT -->
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
    const confirmed = confirm("Archive all chats? They will be hidden from your history.");
    if (!confirmed) return;

    const { error } = await supabaseClient
        .from("chat_sessions")
        .update({ archived: true })
        .eq("user_id", currentUser.id);

    if (error) {
        // If archive column doesn't exist yet, just show a soft message
        alert("Archive feature requires an 'archived' column in your chat_sessions table. Add it in Supabase: boolean, default false.");
    } else {
        showToast("All chats archived.");
    }
};

// ============================
// 🗑️ DELETE ALL CHATS
// ============================
window.clearAllChats = async function () {
    const confirmed = confirm("Delete ALL chats permanently? This cannot be undone.");
    if (!confirmed) return;

    // Delete all messages first (FK constraint)
    const { error: msgError } = await supabaseClient
        .from("messages")
        .delete()
        .eq("user_id", currentUser.id);

    if (msgError) {
        alert("Failed to delete messages: " + msgError.message);
        return;
    }

    // Then delete all sessions
    const { error: sessError } = await supabaseClient
        .from("chat_sessions")
        .delete()
        .eq("user_id", currentUser.id);

    if (sessError) {
        alert("Failed to delete sessions: " + sessError.message);
        return;
    }

    showToast("All chats deleted.");

    // Reset UI to welcome screen
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
// 🍞 TOAST NOTIFICATION
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

    // ============================
    // 🔥 AUTO RESIZE TEXTAREA
    // ============================
    window.autoResize = function () {
        input.style.height = "auto";
        input.style.height = input.scrollHeight + "px";
    };
    input.addEventListener("input", autoResize);

    // ============================
    // 🔥 SEND MESSAGE
    // ============================
    window.sendMessage = async function () {
        let message = input.value.trim();
        if (!message) return;

        if (welcome) welcome.style.display = "none";
        inputArea.classList.remove("center");
        inputArea.classList.add("bottom");

        const userMsg = document.createElement("div");
        userMsg.classList.add("message", "user");
        userMsg.innerText = message;
        chatbox.appendChild(userMsg);

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

        const typing = document.createElement("div");
        typing.classList.add("message", "bot", "typing");
        typing.innerHTML = `<div class="dots"><span></span><span></span><span></span></div>`;
        chatbox.appendChild(typing);
        chatbox.scrollTop = chatbox.scrollHeight;

        try {
            const res = await fetch(`/chat?prompt=${encodeURIComponent(message)}`);
            if (!res.ok) throw new Error("Server error " + res.status);

            typing.remove();

            const botMsg = document.createElement("div");
            botMsg.classList.add("message", "bot");
            chatbox.appendChild(botMsg);

            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let buffer = "";
            let fullReply = "";

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
                        if (chunk.error) { botMsg.innerText = "⚠️ Error: " + chunk.error; break; }
                        if (chunk.token) {
                            fullReply += chunk.token;
                            botMsg.innerHTML = formatMessage(fullReply);
                            chatbox.scrollTop = chatbox.scrollHeight;
                        }
                    } catch { continue; }
                }
            }

            const { error: botError } = await supabaseClient.from("messages").insert([{
                role: "bot",
                content: fullReply,
                session_id: currentSessionId,
                user_id: currentUser.id
            }]);
            if (botError) console.error("❌ Bot message save failed:", botError.message);

        } catch (err) {
            typing.remove();
            console.error("❌ AI fetch failed:", err);
            const errMsg = document.createElement("div");
            errMsg.classList.add("message", "bot");
            errMsg.innerText = "⚠️ Failed to get a response. Please try again.";
            chatbox.appendChild(errMsg);
        }
    };

    // ============================
    // ⌨️ ENTER KEY TO SEND
    // ============================
    input.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    // ============================
    // 📂 LOAD EXISTING SESSION
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
            div.innerHTML = msg.role === "bot" ? formatMessage(msg.content) : msg.content;
            chatbox.appendChild(div);
        });

        chatbox.scrollTop = chatbox.scrollHeight;
        if (window.innerWidth <= 768) closeSidebar();
    }

    // ============================
    // 📜 SHOW CHAT HISTORY
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
