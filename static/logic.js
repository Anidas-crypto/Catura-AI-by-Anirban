document.addEventListener("DOMContentLoaded", function () {

    const input = document.getElementById("input");
    const chatbox = document.getElementById("chatbox");
    const inputArea = document.getElementById("inputArea");

    window.sendMessage = async function () {
        let message = input.value.trim();

        if (!message) return;

        // ✅ Move input box to bottom
        inputArea.classList.remove("center");
        inputArea.classList.add("bottom");

        // ✅ Show user message
        const userMsg = document.createElement("div");
        userMsg.classList.add("message", "user");
        userMsg.innerText = message;
        chatbox.appendChild(userMsg);

        input.value = "";
        chatbox.scrollTop = chatbox.scrollHeight;

        try {
            // ✅ API call
            let response = await fetch(`/chat?prompt=${encodeURIComponent(message)}`);
            let data = await response.json();

            let reply = typeof data === "string"
                ? data
                : data.error || JSON.stringify(data);

            // ✅ Show bot reply
            const botMsg = document.createElement("div");
            botMsg.classList.add("message", "bot");
            botMsg.innerText = reply;
            chatbox.appendChild(botMsg);

        } catch (err) {
            const errorMsg = document.createElement("div");
            errorMsg.classList.add("message", "bot");
            errorMsg.innerText = "❌ Server error";
            chatbox.appendChild(errorMsg);
        }

        chatbox.scrollTop = chatbox.scrollHeight;
    };

    // ✅ Enter key support
    input.addEventListener("keypress", function (e) {
        if (e.key === "Enter") {
            sendMessage();
        }
    });

});