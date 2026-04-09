// ============================
// ✅ SUPABASE SETUP
// ============================
const supabaseUrl = "https://zhrjmnrfklzuxmfbdqhg.supabase.co";
const supabaseKey = "sb_publishable_aIbByN1rFc9V3AH41Kyz6A_e1XppA1Z";
const supabaseClient = window.supabase.createClient(supabaseUrl, supabaseKey);

// ============================
// ✅ SESSION MANAGEMENT
// Always create a new session on page load
// ============================
function generateSessionId() {
    return 'session_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
}

// Always fresh session on open
let currentSessionId = generateSessionId();
let chatTitle = "New Chat";
let firstMessage = true; // track if first message sent

document.addEventListener("DOMContentLoaded", function () {

    const chatbox   = document.getElementById("chatbox");
    const input     = document.getElementById("input");
    const inputArea = document.getElementById("inputArea");
    const welcome   = document.getElementById("welcome");

    // ============================
    // 🔥 AUTO RESIZE TEXTAREA
    // ============================
    function autoResize() {
        input.style.height = "auto";
        const computed = window.getComputedStyle(input);
        const lineHeight = parseFloat(computed.lineHeight);
        const padding = parseFloat(computed.paddingTop) + parseFloat(computed.paddingBottom);
        const singleLineHeight = lineHeight + padding;

        input.style.height = (input.scrollHeight > singleLineHeight + 2)
            ? input.scrollHeight + "px"
            : singleLineHeight + "px";
    }

    input.addEventListener("input", autoResize);

    // ============================
    // 🔥 SEND MESSAGE
    // ============================
    window.sendMessage = async function () {
        let message = input.value.trim();
        if (!message) return;

        // Hide welcome screen
        if (welcome) {
            welcome.style.opacity = "0";
            setTimeout(() => { welcome.style.display = "none"; }, 300);
        }

        inputArea.classList.remove("center");
        inputArea.classList.add("bottom");

        // Show user message
        const userMsg = document.createElement("div");
        userMsg.classList.add("message", "user");
        userMsg.innerText = message;
        chatbox.appendChild(userMsg);

        // ✅ On first message — create session in DB
        if (firstMessage) {
            firstMessage = false;
            chatTitle = message.length > 40
                ? message.substring(0, 40) + "..."
                : message;

            try {
                await supabaseClient.from("chat_sessions").insert([{
                    session_id: currentSessionId,
                    title: chatTitle
                }]);
            } catch (e) {
                console.warn("⚠️ Session create failed:", e);
            }
        }

        // ✅ Save user message with session_id
        try {
            const { error } = await supabaseClient
                .from("messages")
                .insert([{
                    role: "user",
                    content: message,
                    session_id: currentSessionId
                }]);
            if (error) console.warn("⚠️ Supabase user save failed:", error.message);
        } catch (e) {
            console.warn("⚠️ Supabase crashed on user save:", e);
        }

        input.value = "";
        input.style.height = "auto";
        chatbox.scrollTo({ top: chatbox.scrollHeight, behavior: "smooth" });

        // Show typing dots
        const typingMsg = document.createElement("div");
        typingMsg.classList.add("message", "bot", "typing");
        typingMsg.innerHTML = `
            <div class="dots">
                <span></span><span></span><span></span>
            </div>`;
        chatbox.appendChild(typingMsg);
        chatbox.scrollTo({ top: chatbox.scrollHeight, behavior: "smooth" });

        // ✅ Fetch AI reply
        try {
            let response = await fetch(`/chat?prompt=${encodeURIComponent(message)}`, {
                credentials: "same-origin"
            });

            if (!response.ok) throw new Error("Server returned " + response.status);

            let data = await response.json();
            typingMsg.remove();

            let reply = data.reply || data.error || "No response";

            const botMsg = document.createElement("div");
            botMsg.classList.add("message", "bot");
            chatbox.appendChild(botMsg);

            await typeMessage(botMsg, reply);
            botMsg.innerHTML = formatMessage(reply);

            chatbox.scrollTo({ top: chatbox.scrollHeight, behavior: "smooth" });

            // ✅ Save bot reply with session_id
            try {
                const { error } = await supabaseClient
                    .from("messages")
                    .insert([{
                        role: "bot",
                        content: reply,
                        session_id: currentSessionId
                    }]);
                if (error) console.warn("⚠️ Supabase bot save failed:", error.message);
            } catch (e) {
                console.warn("⚠️ Supabase crashed on bot save:", e);
            }

        } catch (err) {
            typingMsg.remove();
            console.error("❌ Fetch failed:", err);
            const errorMsg = document.createElement("div");
            errorMsg.classList.add("message", "bot");
            errorMsg.innerText = "❌ Server error. Check console.";
            chatbox.appendChild(errorMsg);
            chatbox.scrollTo({ top: chatbox.scrollHeight, behavior: "smooth" });
        }
    };

    // ============================
    // ✅ ENTER KEY
    // ============================
    input.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    // ============================
    // 🔥 LOAD A SPECIFIC SESSION
    // ============================
    async function loadSession(sessionId) {
        chatbox.innerHTML = "";

        try {
            const { data, error } = await supabaseClient
                .from("messages")
                .select("*")
                .eq("session_id", sessionId)
                .order("created_at", { ascending: true });

            if (error) { console.error("❌ Load session error:", error.message); return; }

            if (data.length > 0) {
                if (welcome) welcome.style.display = "none";
                inputArea.classList.remove("center");
                inputArea.classList.add("bottom");

                data.forEach(msg => {
                    const div = document.createElement("div");
                    div.classList.add("message", msg.role);
                    div.innerHTML = msg.role === "bot"
                        ? formatMessage(msg.content)
                        : msg.content;
                    chatbox.appendChild(div);
                });

                chatbox.scrollTop = chatbox.scrollHeight;
            }
        } catch (e) {
            console.error("❌ loadSession crashed:", e);
        }
    }

    // ============================
    // 🔥 SHOW CHAT HISTORY
    // ============================
    window.showHistory = async function () {
        try {
            const { data, error } = await supabaseClient
                .from("chat_sessions")
                .select("*")
                .order("created_at", { ascending: false });

            if (error) { console.error("❌ History error:", error.message); return; }

            // Build history panel inside sidebar-menu
            const menu = document.querySelector(".sidebar-menu");
            menu.innerHTML = `
                <div class="history-header">
                    <button class="back-btn" onclick="showMainMenu()">← Back</button>
                    <span>Chat History</span>
                </div>
            `;

            if (data.length === 0) {
                menu.innerHTML += `<div class="no-history">No past chats yet.</div>`;
                return;
            }

            data.forEach(session => {
                const item = document.createElement("div");
                item.classList.add("sidebar-item", "history-item");
                item.innerHTML = `
                    <span class="sidebar-icon">💬</span>
                    <div class="history-info">
                        <div class="history-title">${session.title || "Untitled"}</div>
                        <div class="history-date">${new Date(session.created_at).toLocaleDateString()}</div>
                    </div>
                `;
                item.onclick = async () => {
                    // Switch to this session
                    currentSessionId = session.session_id;
                    firstMessage = false;
                    await loadSession(session.session_id);
                    showMainMenu();
                    closeSidebar();
                };
                menu.appendChild(item);
            });

        } catch (e) {
            console.error("❌ showHistory crashed:", e);
        }
    };

    // ============================
    // 🔥 BACK TO MAIN MENU
    // ============================
    window.showMainMenu = function () {
        const menu = document.querySelector(".sidebar-menu");
        menu.innerHTML = `
            <div class="sidebar-item" onclick="newChat()">
                <span class="sidebar-icon">✏️</span>
                New Chat
            </div>
            <div class="sidebar-item" onclick="showHistory()">
                <span class="sidebar-icon">🕘</span>
                Chat History
            </div>
            <div class="sidebar-item" onclick="showSettings()">
                <span class="sidebar-icon">⚙️</span>
                Settings
            </div>
        `;
    };

    // ============================
    // 🔥 FORMAT MESSAGE
    // ============================
    function formatMessage(text) {
        return text
            .replace(/```(\w+)?\n([\s\S]*?)```/g, function (_, lang, code) {
                return `
                <div class="code-block">
                    <div class="code-header">
                        <span>${lang || "code"}</span>
                        <button onclick="copyCode(this)">Copy</button>
                    </div>
                    <pre><code>${escapeHtml(code)}</code></pre>
                </div>`;
            })
            .replace(/\n/g, "<br>");
    }

    function escapeHtml(text) {
        return text
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
    }

    function copyCode(btn) {
        const code = btn.parentElement.nextElementSibling.innerText;
        navigator.clipboard.writeText(code);
        btn.innerText = "Copied!";
        setTimeout(() => btn.innerText = "Copy", 1500);
    }
    window.copyCode = copyCode;

    // ============================
    // 🔥 SUGGESTION CARDS
    // ============================
    window.useSuggestion = function (el) {
        input.value = el.innerText.trim();
        sendMessage();
    };

    // ============================
    // 🔥 TYPING ANIMATION
    // ============================
    async function typeMessage(element, text) {
        let words = text.split(" ");
        element.innerHTML = "";
        for (let i = 0; i < words.length; i++) {
            element.innerHTML += words[i] + " ";
            await new Promise(r => setTimeout(r, Math.random() * 30 + 20));
        }
    }

    // ============================
    // 🔥 SIDEBAR FUNCTIONS
    // ============================
    let sidebarOpen = true;

    window.toggleSidebar = function () {
        const sidebar = document.getElementById("sidebar");
        const overlay = document.getElementById("sidebarOverlay");
        const isMobile = window.innerWidth <= 768;

        if (sidebarOpen) {
            sidebar.classList.remove("open");
            if (isMobile) overlay.classList.remove("show");
            sidebarOpen = false;
        } else {
            sidebar.classList.add("open");
            if (isMobile) overlay.classList.add("show");
            sidebarOpen = true;
        }
    };

    window.closeSidebar = function () {
        const isMobile = window.innerWidth <= 768;
        if (isMobile) {
            document.getElementById("sidebar").classList.remove("open");
            document.getElementById("sidebarOverlay").classList.remove("show");
            sidebarOpen = false;
        }
    };

    window.addEventListener("resize", function () {
        const sidebar = document.getElementById("sidebar");
        const overlay = document.getElementById("sidebarOverlay");
        if (window.innerWidth > 768) {
            sidebar.classList.add("open");
            overlay.classList.remove("show");
            sidebarOpen = true;
        } else {
            sidebar.classList.remove("open");
            overlay.classList.remove("show");
            sidebarOpen = false;
        }
    });

    window.newChat = function () {
        // Fresh session
        currentSessionId = generateSessionId();
        firstMessage = true;
        chatTitle = "New Chat";

        chatbox.innerHTML = "";

        if (welcome) {
            welcome.style.display = "block";
            welcome.style.opacity = "1";
        }

        inputArea.classList.remove("bottom");
        inputArea.classList.add("center");

        showMainMenu();
        closeSidebar();
    };

    window.showSettings = function () {
        alert("⚙️ Settings coming soon!");
        closeSidebar();
    };

    window.logoutUser = function () {
        alert("🚪 Logout coming soon!");
        closeSidebar();
    };

});
