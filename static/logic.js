document.addEventListener("DOMContentLoaded", function () {

    const input = document.getElementById("input");
    function autoResize() {
        input.style.height = "auto";
        const computed = window.getComputedStyle(input);
        const lineHeight = parseFloat(computed.lineHeight);
        const padding =
            parseFloat(computed.paddingTop) +
            parseFloat(computed.paddingBottom);
        const singleLineHeight = lineHeight + padding;

        if (input.scrollHeight > singleLineHeight + 2) {
            input.style.height = input.scrollHeight + "px";
        } else {
            input.style.height = singleLineHeight + "px";
        }
    }

    input.addEventListener("input", autoResize);

    const chatbox = document.getElementById("chatbox");
    const inputArea = document.getElementById("inputArea");
    const welcome = document.getElementById("welcome");

    window.sendMessage = async function () {
        let message = input.value.trim();

        if (!message) return;
        // ❌ Hide welcome screen after first message
        if (welcome) {
            welcome.style.opacity = "0";
            setTimeout(() => {
                welcome.style.display = "none";
            }, 300);
        }

        // ✅ Move input box to bottom
        inputArea.classList.remove("center");
        inputArea.classList.add("bottom");

        // ✅ Show user message
        const userMsg = document.createElement("div");
        userMsg.classList.add("message", "user");
        userMsg.innerText = message;
        chatbox.appendChild(userMsg);

        input.value = "";
        input.style.height = "auto";
        chatbox.scrollTo({
            top: chatbox.scrollHeight,
            behavior: "smooth"
        });


        // 🔥 Typing dots
        const typingMsg = document.createElement("div");
        typingMsg.classList.add("message", "bot", "typing");
        
        typingMsg.innerHTML = `
        <div class="dots">
            <span></span>
            <span></span>
            <span></span>
        </div>
        `;
        chatbox.appendChild(typingMsg);
        
        chatbox.scrollTo({
            top: chatbox.scrollHeight,
            behavior: "smooth"
        });


        try {
            // ✅ API call
            let response = await fetch(`/chat?prompt=${encodeURIComponent(message)}`, {
                credentials: "same-origin"
            });
            let data = await response.json();

            // ❌ remove typing dots
            typingMsg.remove();

            let reply = data.reply || data.error || "Error";
            
            // ✅ Show bot reply container
            const botMsg = document.createElement("div");
            botMsg.classList.add("message", "bot");
            chatbox.appendChild(botMsg);
            // 🔥 animate typing
            await typeMessage(botMsg, reply);
            // ✅ apply formatting AFTER typing
            botMsg.innerHTML = formatMessage(reply);
            
            // 🔥 scroll after reply
            chatbox.scrollTo({
                top: chatbox.scrollHeight,
                behavior: "smooth"
            });

        } catch (err) {
            typingMsg.remove();
            const errorMsg = document.createElement("div");
            errorMsg.classList.add("message", "bot");
            errorMsg.innerText = "❌ Server error";
            chatbox.appendChild(errorMsg);
            // 🔥 scroll after error too
            chatbox.scrollTo({
                top: chatbox.scrollHeight,
                behavior: "smooth"
            });
        }
    };
    // ✅ Enter key support
    input.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });





    function formatMessage(text) {
        return text.replace(/```(\w+)?\n([\s\S]*?)```/g, function (_, lang, code) {
            return `
            <div class="code-block">
                <div class="code-header">
                    <span>${lang || "code"}</span>
                    <button onclick="copyCode(this)">Copy</button>
                </div>
                <pre><code>${escapeHtml(code)}</code></pre>
            </div>
            `;
        }).replace(/\n/g, "<br>");
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
    
    
    window.useSuggestion = function (el) {
    const input = document.getElementById("input");

    input.value = el.innerText.trim();

    // send automatically
    sendMessage();
};
async function typeMessage(element, text) {
    let words = text.split(" ");
    element.innerHTML = "";

    for (let i = 0; i < words.length; i++) {
        element.innerHTML += words[i] + " ";
        await new Promise(resolve => setTimeout(resolve, Math.random() * 30 + 20)); // speed
    }
}

});