// ============================
// ✅ SUPABASE SETUP
// ============================
const supabaseUrl = "https://zhrjmnrfklzuxmfbdqhg.supabase.co";
const supabaseKey = "YOUR_PUBLIC_ANON_KEY"; // keep yours here
const supabaseClient = window.supabase.createClient(supabaseUrl, supabaseKey);

// ============================
// ✅ USER AUTH
// ============================
let currentUser = null;

async function getUser() {
    const { data, error } = await supabaseClient.auth.getUser();
    if (error) console.error("Auth error:", error.message);
    currentUser = data.user;
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
// 🚀 APP START
// ============================
document.addEventListener("DOMContentLoaded", async function () {

    await getUser();

    // 🔐 Redirect if not logged in
    if (!currentUser) {
        window.location.href = "/auth.html";
        return;
    }

    const chatbox   = document.getElementById("chatbox");
    const input     = document.getElementById("input");
    const inputArea = document.getElementById("inputArea");
    const welcome   = document.getElementById("welcome");

    // ============================
    // 🔥 AUTO RESIZE
    // ============================
    function autoResize() {
        input.style.height = "auto";
        input.style.height = input.scrollHeight + "px";
    }
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

        // USER MESSAGE UI
        const userMsg = document.createElement("div");
        userMsg.classList.add("message", "user");
        userMsg.innerText = message;
        chatbox.appendChild(userMsg);

        // ============================
        // ✅ CREATE SESSION (FIRST MESSAGE)
        // ============================
        if (firstMessage) {
            firstMessage = false;
            chatTitle = message.substring(0, 40);

            const { error } = await supabaseClient.from("chat_sessions").insert([{
                session_id: currentSessionId,
                title: chatTitle,
                user_id: currentUser.id
            }]);

            if (error) {
                console.error("❌ Session insert failed:", error.message);
                return;
            }
        }

        // ============================
        // ✅ SAVE USER MESSAGE
        // ============================
        const { error: userError } = await supabaseClient.from("messages").insert([{
            role: "user",
            content: message,
            session_id: currentSessionId,
            user_id: currentUser.id
        }]);

        if (userError) {
            console.error("❌ User message save failed:", userError.message);
            return;
        }

        input.value = "";
        chatbox.scrollTop = chatbox.scrollHeight;

        // ============================
        // 🤖 TYPING UI
        // ============================
        const typing = document.createElement("div");
        typing.classList.add("message", "bot");
        typing.innerText = "Typing...";
        chatbox.appendChild(typing);

        // ============================
        // 🤖 FETCH AI RESPONSE
        // ============================
        try {
            let res = await fetch(`/chat?prompt=${encodeURIComponent(message)}`);
            let data = await res.json();
            typing.remove();

            let reply = data.reply || "No response";

            const botMsg = document.createElement("div");
            botMsg.classList.add("message", "bot");
            botMsg.innerHTML = formatMessage(reply);
            chatbox.appendChild(botMsg);

            chatbox.scrollTop = chatbox.scrollHeight;

            // ============================
            // ✅ SAVE BOT MESSAGE
            // ============================
            const { error: botError } = await supabaseClient.from("messages").insert([{
                role: "bot",
                content: reply,
                session_id: currentSessionId,
                user_id: currentUser.id
            }]);

            if (botError) {
                console.error("❌ Bot message save failed:", botError.message);
            }

        } catch (err) {
            typing.remove();
            console.error("❌ AI fetch failed:", err);
        }
    };

    // ============================
    // ⌨️ ENTER KEY
    // ============================
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

        const { data, error } = await supabaseClient
            .from("messages")
            .select("*")
            .eq("session_id", sessionId)
            .eq("user_id", currentUser.id)
            .order("created_at", { ascending: true });

        if (error) {
            console.error("❌ Load session failed:", error.message);
            return;
        }

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

    // ============================
    // 📜 SHOW HISTORY
    // ============================
    window.showHistory = async function () {

        const { data, error } = await supabaseClient
            .from("chat_sessions")
            .select("*")
            .eq("user_id", currentUser.id)
            .order("created_at", { ascending: false });

        if (error) {
            console.error("❌ History failed:", error.message);
            return;
        }

        const menu = document.querySelector(".sidebar-menu");
        menu.innerHTML = `<button onclick="showMainMenu()">← Back</button>`;

        data.forEach(session => {
            const item = document.createElement("div");
            item.classList.add("sidebar-item");
            item.innerText = session.title;

            item.onclick = async () => {
                currentSessionId = session.session_id;
                firstMessage = false;
                await loadSession(session.session_id);
            };

            menu.appendChild(item);
        });
    };

    // ============================
    // 🧭 MENU
    // ============================
    window.showMainMenu = function () {
        document.querySelector(".sidebar-menu").innerHTML = `
            <div class="sidebar-item" onclick="newChat()">New Chat</div>
            <div class="sidebar-item" onclick="showHistory()">Chat History</div>
            <div class="sidebar-item" onclick="logoutUser()">Logout</div>
        `;
    };

    // ============================
    // ➕ NEW CHAT
    // ============================
    window.newChat = function () {
        currentSessionId = generateSessionId();
        firstMessage = true;
        chatbox.innerHTML = "";
    };

    // ============================
    // 🚪 LOGOUT
    // ============================
    window.logoutUser = async function () {
        await supabaseClient.auth.signOut();
        window.location.href = "/auth.html";
    };

    // ============================
    // 🧾 FORMAT MESSAGE
    // ============================
    function formatMessage(text) {
        return text.replace(/\n/g, "<br>");
    }

});
