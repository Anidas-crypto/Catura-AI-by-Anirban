const supabaseUrl = "https://zhrjmnrfklzuxmfbdqhg.supabase.co";
const supabaseKey = "sb_publishable_aIbByN1rFc9V3AH41Kyz6A_e1XppA1Z";

const supabaseClient = window.supabase.createClient(supabaseUrl, supabaseKey, {
    auth: {
        persistSession: true,
        autoRefreshToken: true
    }
});

let currentUser = null;

// ✅ FIX: get session properly
async function getSessionUser() {
    const { data } = await supabaseClient.auth.getSession();
    currentUser = data.session?.user || null;
}

// ✅ FIX: handle OAuth return
supabaseClient.auth.onAuthStateChange((event, session) => {
    if (session?.user) {
        currentUser = session.user;
    }
});

document.addEventListener("DOMContentLoaded", async () => {

    await getSessionUser();

    // ✅ FIX: redirect if not logged in
    if (!currentUser) {
        window.location.href = "/auth.html";
        return;
    }

    const chatbox = document.getElementById("chatbox");
    const input = document.getElementById("input");
    const inputArea = document.getElementById("inputArea");
    const welcome = document.getElementById("welcome");

    input.addEventListener("input", () => {
        input.style.height = "auto";
        input.style.height = input.scrollHeight + "px";
    });

    window.sendMessage = async () => {

        let message = input.value.trim();
        if (!message) return;

        welcome.style.display = "none";

        inputArea.classList.remove("center");
        inputArea.classList.add("bottom");

        const userMsg = document.createElement("div");
        userMsg.className = "message user";
        userMsg.innerText = message;
        chatbox.appendChild(userMsg);

        input.value = "";

        const typing = document.createElement("div");
        typing.className = "message bot";
        typing.innerText = "Typing...";
        chatbox.appendChild(typing);

        try {
            const res = await fetch(`/chat?prompt=${encodeURIComponent(message)}`);
            const data = await res.json();

            typing.remove();

            const botMsg = document.createElement("div");
            botMsg.className = "message bot";
            botMsg.innerHTML = data.reply.replace(/\n/g, "<br>");

            chatbox.appendChild(botMsg);
            chatbox.scrollTop = chatbox.scrollHeight;

        } catch (err) {
            typing.remove();
            console.error(err);
        }
    };

    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    window.logoutUser = async () => {
        await supabaseClient.auth.signOut();
        window.location.href = "/auth.html";
    };
});