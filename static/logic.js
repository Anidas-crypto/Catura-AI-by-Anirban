/**
 * logic.js — Catura AI Frontend
 *
 * Architecture: IIFE-namespaced modules, single public API surface.
 * Modules: Config → Utils → State → Auth → Theme → Markdown → UI → Chat → Sidebar → Settings → App
 *
 * Rules enforced:
 *  - No duplicate clipboard / initials / "reset chat" logic
 *  - No global `document.addEventListener` accumulation in loops
 *  - No `window.*` pollution beyond the one `CaturaApp` public API
 *  - No inline onclick= in injected HTML (event delegation only)
 *  - `prompt()` / `confirm()` only inside dedicated modal helpers
 *  - All DOM queries cached at module-init time
 */

// ─────────────────────────────────────────────────────────────
// 1. CONFIG
// ─────────────────────────────────────────────────────────────
const Config = (() => {
    const SUPABASE_URL = "https://zhrjmnrfklzuxmfbdqhg.supabase.co";
    const SUPABASE_KEY = "sb_publishable_aIbByN1rFc9V3AH41Kyz6A_e1XppA1Z";
    const DB            = { SESSIONS: "chat_sessions", MESSAGES: "messages" };
    const BREAKPOINT    = 768;
    const STREAM_THROTTLE_MS = 80;
    const TOAST_DEFAULT_MS   = 3000;
    const AUTH_REDIRECT      = "/auth.html";

    return Object.freeze({ SUPABASE_URL, SUPABASE_KEY, DB, BREAKPOINT, STREAM_THROTTLE_MS, TOAST_DEFAULT_MS, AUTH_REDIRECT });
})();

// ─────────────────────────────────────────────────────────────
// 2. SUPABASE CLIENT (singleton)
// ─────────────────────────────────────────────────────────────
const DB = window.supabase.createClient(Config.SUPABASE_URL, Config.SUPABASE_KEY);

// ─────────────────────────────────────────────────────────────
// 3. UTILS — pure helpers, no DOM/state dependencies
// ─────────────────────────────────────────────────────────────
const Utils = (() => {

    function generateSessionId() {
        return 'session_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
    }

    /** Derive initials from a full name string. */
    function getInitials(fullName) {
        const parts = fullName.trim().split(/\s+/).filter(Boolean);
        return parts.length >= 2
            ? (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
            : parts[0].slice(0, 2).toUpperCase();
    }

    /** Derive display name from a Supabase user object. */
    function getDisplayName(user) {
        return (
            user?.user_metadata?.full_name ||
            user?.user_metadata?.name ||
            user?.email?.split('@')[0] ||
            'User'
        ).trim();
    }

    function getTimeOfDay() {
        const h = new Date().getHours();
        if (h >= 5 && h < 12)  return 'morning';
        if (h >= 12 && h < 17) return 'afternoon';
        if (h >= 17 && h < 21) return 'evening';
        return 'night';
    }

    function pickGreeting(userName) {
        const map = {
            morning:   [`Good morning, ${userName}`, `Rise and shine, ${userName}!`, `☀️ Good morning, ${userName}!`, `Good morning! Ready to code, ${userName}?`],
            afternoon: [`Good afternoon, ${userName}`, `Afternoon, ${userName}!`, `🌤️ Good afternoon, ${userName}!`, `Hope you're having a great afternoon, ${userName}!`],
            evening:   [`Good evening, ${userName}`, `Evening, ${userName}!`, `🌙 Good evening, ${userName}!`, `Good evening! Let's build something, ${userName}`],
            night:     [`Good night, ${userName}`, `🌌 Good night, ${userName}!`, `Night owl coding session, ${userName}?`, `Burning the midnight oil, ${userName}?`],
        };
        const list = map[getTimeOfDay()];
        return list[Math.floor(Math.random() * list.length)];
    }

    function isHeavyQuery(text) {
        const lower = text.toLowerCase().trim();
        if (lower.length > 80) return true;
        const kws = ["explain","write","create","build","code","script","program","function","debug","fix","error","bug","step by step","line by line","breakdown","compare","difference between"," vs ","how does","how do i","how to","generate","summarize","analyze","essay","give me","make a","implement","refactor","optimiz","algorithm","convert","translate"];
        return kws.some(k => lower.includes(k));
    }

    function detectClientIntent(message) {
        const lower = message.toLowerCase();
        if (/\btime\b|\bclock\b|\bwhat time\b|\bcurrent time\b|\btimezone\b|\btime zone\b|\bist\b|\butc\b|\bgmt\b/.test(lower)) return 'clock';
        if (/weather|temperature|humidity|forecast|sunny|cloudy|will it rain|feels like/.test(lower)) return 'weather';
        if (/share price|stock price|stock market|nse|bse|nifty|sensex|crypto|bitcoin|ethereum|exchange rate|rupee/.test(lower)) return 'finance';
        if (/(tata|reliance|infosys|wipro|hdfc|icici|bajaj|sbi).*(price|stock|share)|(price|stock|share).*(tata|reliance|infosys|wipro|hdfc|icici|bajaj|sbi)/.test(lower)) return 'finance';
        if (/cricket|ipl|test match|\bodi\b|\bt20\b|football|soccer|fifa|premier league|\bnba\b|\bnfl\b|tennis|live score|match today/.test(lower)) return 'sports';
        if (/\bnews\b|headlines|breaking|latest news|current events|what happened|recent news/.test(lower)) return 'news';
        if (/latest|currently?|right now|\btoday\b|recently?|who is|price of|how much|find (me|out)|search for|look up/.test(lower)) return 'web_search';
        return 'general';
    }

    function repairTruncated(text) {
        const fences = (text.match(/```/g) || []).length;
        return fences % 2 !== 0 ? text.trimEnd() + '\n```' : text;
    }

    return Object.freeze({ generateSessionId, getInitials, getDisplayName, getTimeOfDay, pickGreeting, isHeavyQuery, detectClientIntent, repairTruncated });
})();

// ─────────────────────────────────────────────────────────────
// 4. STATE — single source of truth for mutable app state
// ─────────────────────────────────────────────────────────────
const State = (() => {
    let _currentUser         = null;
    let _currentSessionId    = Utils.generateSessionId();
    let _chatTitle           = 'New Chat';
    let _firstMessage        = true;
    let _selectedModel       = 'dagr';
    let _webSearchEnabled    = true;
    let _isStreaming         = false;
    let _activeAbortCtrl     = null;

    // Getters
    const getUser           = () => _currentUser;
    const getSessionId      = () => _currentSessionId;
    const getChatTitle      = () => _chatTitle;
    const isFirstMessage    = () => _firstMessage;
    const getModel          = () => _selectedModel;
    const isWebSearchOn     = () => _webSearchEnabled;
    const isStreaming       = () => _isStreaming;
    const getAbortCtrl      = () => _activeAbortCtrl;

    // Setters
    const setUser           = u  => { _currentUser = u; };
    const setSessionId      = id => { _currentSessionId = id; };
    const setChatTitle      = t  => { _chatTitle = t; };
    const setFirstMessage   = v  => { _firstMessage = v; };
    const setModel          = m  => { _selectedModel = m; };
    const toggleWebSearch   = () => { _webSearchEnabled = !_webSearchEnabled; return _webSearchEnabled; };
    const setStreaming       = v  => { _isStreaming = v; };
    const setAbortCtrl      = c  => { _activeAbortCtrl = c; };

    /** Reset session + firstMessage for a new chat. Returns new sessionId. */
    const resetSession = () => {
        _currentSessionId = Utils.generateSessionId();
        _firstMessage     = true;
        _chatTitle        = 'New Chat';
        return _currentSessionId;
    };

    return Object.freeze({
        getUser, getSessionId, getChatTitle, isFirstMessage,
        getModel, isWebSearchOn, isStreaming, getAbortCtrl,
        setUser, setSessionId, setChatTitle, setFirstMessage,
        setModel, toggleWebSearch, setStreaming, setAbortCtrl, resetSession,
    });
})();

// ─────────────────────────────────────────────────────────────
// 5. DOM CACHE — single place to look up all elements
// ─────────────────────────────────────────────────────────────
const DOM = (() => {
    // Lazily resolved so this works before DOMContentLoaded for module-level code,
    // and is still available after. Each getter is memoised after first call.
    const cache = {};
    const q = (id) => { if (!cache[id]) cache[id] = document.getElementById(id); return cache[id]; };
    const qs = (sel) => document.querySelector(sel);

    return {
        chatbox:        () => q('chatbox'),
        input:          () => q('input'),
        inputArea:      () => q('inputArea'),
        app:            () => q('app'),
        sidebar:        () => q('sidebar'),
        iconRail:       () => q('iconRail'),
        sidebarOverlay: () => q('sidebarOverlay'),
        sidebarMenu:    () => qs('.sidebar-menu'),
        sendBtn:        () => q('sendBtn'),
        settingsOverlay:() => q('settingsOverlay'),
        settingsContent:() => q('settingsContent'),
        userAvatar:     () => q('userAvatar'),
        userFullname:   () => q('userFullname'),
        railAvatar:     () => q('railAvatar'),
        toastEl:        () => q('toastNotif'),
        plusDropdown:   () => q('plusDropdown'),
        plusMenuWrap:   () => q('plusMenuWrap'),
        modelDropdown:  () => q('modelDropdown'),
        modelSelectorWrap: () => q('modelSelectorWrap'),
        modelSelectorBtn:  () => q('modelSelectorBtn'),
        modelName:      () => q('modelName'),
        moreModelsPanel:() => q('moreModelsPanel'),
        moreModelsRow:  () => q('moreModelsRow'),
        fileInput:      () => q('fileInput'),
        hamburger:      () => qs('.mobile-hamburger'),
        // Clear memoisation when needed (e.g. after new chat clears chatbox)
        invalidate: (id) => { delete cache[id]; },
    };
})();

// ─────────────────────────────────────────────────────────────
// 6. TOAST
// ─────────────────────────────────────────────────────────────
const Toast = (() => {
    let _timer = null;

    function show(message, duration = Config.TOAST_DEFAULT_MS) {
        let el = DOM.toastEl();
        if (!el) {
            el = document.createElement('div');
            el.id = 'toastNotif';
            document.body.appendChild(el);
        }
        if (_timer) clearTimeout(_timer);
        el.textContent = message;
        el.classList.add('show');
        _timer = setTimeout(() => el.classList.remove('show'), duration);
    }

    return Object.freeze({ show });
})();

// ─────────────────────────────────────────────────────────────
// 7. THEME & FONT SIZE
// ─────────────────────────────────────────────────────────────
const ThemeManager = (() => {
    const LS_THEME = 'catura-theme';
    const LS_FONT  = 'catura-font';

    function applyTheme(theme) {
        const root = document.documentElement;
        root.removeAttribute('data-theme');
        if (theme === 'light') {
            root.setAttribute('data-theme', 'light');
        } else if (theme === 'auto') {
            if (!window.matchMedia('(prefers-color-scheme: dark)').matches)
                root.setAttribute('data-theme', 'light');
        }
    }

    function setTheme(theme) {
        localStorage.setItem(LS_THEME, theme);
        applyTheme(theme);
        document.querySelectorAll('.theme-option').forEach(b => {
            b.classList.toggle('active', b.dataset.theme === theme);
        });
        Toast.show(`Theme: ${theme[0].toUpperCase() + theme.slice(1)}`, 1500);
    }

    function initTheme() {
        const saved = localStorage.getItem(LS_THEME) || 'dark';
        applyTheme(saved);
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
            if ((localStorage.getItem(LS_THEME) || 'dark') === 'auto') applyTheme('auto');
        });
    }

    function applyFontSize(size) {
        const root = document.documentElement;
        root.removeAttribute('data-fontsize');
        if (size && size !== 'default') root.setAttribute('data-fontsize', size);
    }

    function setFontSize(size) {
        localStorage.setItem(LS_FONT, size);
        applyFontSize(size);
        document.querySelectorAll('.font-option').forEach(b => {
            b.classList.toggle('active', b.dataset.fontsize === size);
        });
        Toast.show(`Font size: ${size[0].toUpperCase() + size.slice(1)}`, 1500);
    }

    function initFontSize() {
        applyFontSize(localStorage.getItem(LS_FONT) || 'default');
    }

    function getTheme()    { return localStorage.getItem(LS_THEME) || 'dark'; }
    function getFontSize() { return localStorage.getItem(LS_FONT)  || 'default'; }

    return Object.freeze({ setTheme, initTheme, setFontSize, initFontSize, getTheme, getFontSize });
})();

// ─────────────────────────────────────────────────────────────
// 8. CLIPBOARD — single implementation, used by all copy actions
// ─────────────────────────────────────────────────────────────
const Clipboard = (() => {
    const COPY_SVG  = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`;
    const CHECK_SVG = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`;

    function copy(text, btn, showLabel = true, resetMs = 2000) {
        navigator.clipboard.writeText(text).then(() => {
            btn.innerHTML = showLabel ? `${CHECK_SVG} Copied!` : CHECK_SVG;
            btn.classList.add('copied');
            setTimeout(() => {
                btn.innerHTML = showLabel ? `${COPY_SVG} Copy` : COPY_SVG;
                btn.classList.remove('copied');
            }, resetMs);
        }).catch(() => Toast.show('Failed to copy'));
    }

    /** Copy a code block — triggered by the Copy button inside .code-block */
    function copyCode(btn) {
        const code = btn.closest('.code-block')?.querySelector('code')?.innerText || '';
        copy(code, btn, true);
    }

    /** Copy raw bot response text stored in wrapper.dataset.raw */
    function copyBotAnswer(btn) {
        const raw = btn.closest('.bot-msg-wrapper')?.dataset.raw || '';
        copy(raw, btn, true);
    }

    /** Copy user message text */
    function copyUserMessage(btn) {
        const text = btn.closest('.user-msg-wrapper')?.querySelector('.message.user')?.innerText || '';
        copy(text, btn, false);
    }

    return Object.freeze({ copyCode, copyBotAnswer, copyUserMessage });
})();

// ─────────────────────────────────────────────────────────────
// 9. MARKDOWN RENDERER
// ─────────────────────────────────────────────────────────────
const Markdown = (() => {

    function applyInline(text) {
        if (!text) return text;
        const inlineCode = [];
        text = text.replace(/`([^`]+)`/g, (_, c) => {
            inlineCode.push(`<span class="inline-code">${c.replace(/</g,'&lt;').replace(/>/g,'&gt;')}</span>`);
            return `\x01IC${inlineCode.length - 1}\x01`;
        });
        text = text.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
        text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        text = text.replace(/(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)/g, '<em>$1</em>');
        text = text.replace(/__(.+?)__/g, '<u>$1</u>');
        text = text.replace(/~~(.+?)~~/g, '<s>$1</s>');
        text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
        text = text.replace(/\x01IC(\d+)\x01/g, (_, i) => inlineCode[+i]);
        return text;
    }

    function render(rawText) {
        if (!rawText) return '';

        // Step 1: stash fenced code blocks
        const codeBlocks = [];
        let text = rawText.replace(/```([\w+\-#]*)\n?([\s\S]*?)```/g, (_match, lang, code) => {
            const language = lang.trim() || 'text';
            const escaped  = code.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
            codeBlocks.push(`<div class="code-block">
                <div class="code-header">
                    <span class="lang-label">${language}</span>
                    <button class="code-copy-btn">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg> Copy</button>
                </div>
                <pre><code>${escaped.trimEnd()}</code></pre>
            </div>`);
            return `\x00CODE${codeBlocks.length - 1}\x00`;
        });

        // Step 2: escape HTML in non-code segments
        text = text.split(/(\x00CODE\d+\x00)/).map((part, i) => {
            if (i % 2 === 1) return part;
            return part
                .replace(/&(?!(amp|lt|gt|quot|#\d+);)/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;');
        }).join('');

        // Step 3: tables
        text = text.replace(/((?:[ \t]*\|.+\|\s*\n?)+)/g, (block) => {
            const rows = block.trim().split('\n').map(r => r.trim()).filter(Boolean);
            if (rows.length < 2) return block;
            const isSep = r => /^\|[\s:|\-]+\|$/.test(r);
            if (!isSep(rows[1])) return block;
            const parseRow  = r => r.replace(/^\||\|$/g,'').split('|').map(c => c.trim());
            const aligns    = parseRow(rows[1]).map(c => /^:-+:$/.test(c) ? 'center' : /^-+:$/.test(c) ? 'right' : 'left');
            const headers   = parseRow(rows[0]);
            const thead     = `<thead><tr>${headers.map((h,i) => `<th style="text-align:${aligns[i]||'left'}">${applyInline(h)}</th>`).join('')}</tr></thead>`;
            const tbody     = `<tbody>${rows.slice(2).map(r => `<tr>${parseRow(r).map((c,i) => `<td style="text-align:${aligns[i]||'left'}">${applyInline(c)}</td>`).join('')}</tr>`).join('')}</tbody>`;
            return `<div class="table-wrap"><table>${thead}${tbody}</table></div>`;
        });

        // Step 4: block-level processing
        const outputLines = [];
        const rawLines    = text.split('\n');
        let i = 0;
        while (i < rawLines.length) {
            const line    = rawLines[i];
            const trimmed = line.trim();
            const h3 = trimmed.match(/^### (.+)$/); if (h3) { outputLines.push(`<h3>${applyInline(h3[1])}</h3>`); i++; continue; }
            const h2 = trimmed.match(/^## (.+)$/);  if (h2) { outputLines.push(`<h2>${applyInline(h2[1])}</h2>`); i++; continue; }
            const h1 = trimmed.match(/^# (.+)$/);   if (h1) { outputLines.push(`<h1>${applyInline(h1[1])}</h1>`); i++; continue; }
            if (/^---+$/.test(trimmed)) { outputLines.push('<hr>'); i++; continue; }
            if (/^&gt;\s?/.test(trimmed)) {
                const bq = [];
                while (i < rawLines.length && /^&gt;\s?/.test(rawLines[i].trim())) {
                    bq.push(applyInline(rawLines[i].trim().replace(/^&gt;\s?/, ''))); i++;
                }
                outputLines.push(`<blockquote>${bq.join('<br>')}</blockquote>`); continue;
            }
            if (/^[-*•]\s/.test(trimmed)) {
                const items = [];
                while (i < rawLines.length && /^[-*•]\s/.test(rawLines[i].trim())) {
                    items.push(`<li>${applyInline(rawLines[i].trim().replace(/^[-*•]\s/, ''))}</li>`); i++;
                }
                outputLines.push(`<ul>${items.join('')}</ul>`); continue;
            }
            if (/^\d+[\.)]\s/.test(trimmed)) {
                const items = [];
                while (i < rawLines.length && /^\d+[\.)]\s/.test(rawLines[i].trim())) {
                    items.push(`<li>${applyInline(rawLines[i].trim().replace(/^\d+[\.)]\s/, ''))}</li>`); i++;
                }
                outputLines.push(`<ol>${items.join('')}</ol>`); continue;
            }
            if (/^\x00CODE\d+\x00$/.test(trimmed)) { outputLines.push(trimmed); i++; continue; }
            if (!trimmed) { outputLines.push(''); i++; continue; }
            if (/^<(div class="table-wrap"|table|\/div|\/table|thead|tbody|tr|th|td)/.test(trimmed)) { outputLines.push(line); i++; continue; }
            outputLines.push(applyInline(trimmed)); i++;
        }

        // Step 5: group plain lines into <p>
        let html = '', paraBuf = [];
        const flushPara = () => { if (paraBuf.length) { html += `<p>${paraBuf.join(' ')}</p>`; paraBuf = []; } };
        const BLOCK_RE  = /^(<(h[123]|ul|ol|blockquote|hr|div|table|thead|tbody|tr|th|td)|<\/|<div|\x00CODE)/;
        for (const ln of outputLines) {
            if (!ln) { flushPara(); continue; }
            if (BLOCK_RE.test(ln)) { flushPara(); html += ln; continue; }
            paraBuf.push(ln);
        }
        flushPara();

        // Step 6: restore code blocks
        html = html.replace(/\x00CODE(\d+)\x00/g, (_, idx) => codeBlocks[+idx]);
        return html;
    }

    return Object.freeze({ render, applyInline });
})();

// ─────────────────────────────────────────────────────────────
// 10. AUTH
// ─────────────────────────────────────────────────────────────
const Auth = (() => {

    /** Populate all avatar/name DOM elements from a user object. */
    function _applyUserToDOM(user) {
        const name     = Utils.getDisplayName(user);
        const initials = Utils.getInitials(name);
        const avatarEl  = DOM.userAvatar();
        const nameEl    = DOM.userFullname();
        const railEl    = DOM.railAvatar();
        if (avatarEl)  avatarEl.textContent  = initials;
        if (nameEl)    nameEl.textContent     = name;
        if (railEl)    railEl.textContent     = initials;
        return { name, initials };
    }

    async function loadUser() {
        const { data, error } = await DB.auth.getUser();
        if (error) console.error('Auth error:', error.message);
        const user = data?.user || null;
        State.setUser(user);
        if (user) _applyUserToDOM(user);
        return user;
    }

    async function logout() {
        if (!confirm('Are you sure you want to logout?')) return;
        await DB.auth.signOut();
        window.location.href = Config.AUTH_REDIRECT;
    }

    async function editDisplayName() {
        const currentName = DOM.userFullname()?.textContent || 'User';
        const newName     = prompt('Enter your new display name:', currentName);
        if (!newName || newName.trim() === '' || newName.trim() === currentName) return;
        const trimmed = newName.trim();

        const { data, error } = await DB.auth.updateUser({ data: { full_name: trimmed } });
        if (error) { Toast.show('❌ Failed to update name. Please try again.'); return; }

        State.setUser(data.user);
        const initials = Utils.getInitials(trimmed);
        const avatarEl  = DOM.userAvatar();
        const nameEl    = DOM.userFullname();
        const railEl    = DOM.railAvatar();
        const profName  = document.querySelector('.sc-profile-name');
        if (avatarEl)  avatarEl.textContent  = initials;
        if (nameEl)    nameEl.textContent     = trimmed;
        if (railEl)    railEl.textContent     = initials;
        if (profName)  profName.textContent   = trimmed;
        Toast.show(`✓ Name updated to ${trimmed}`);
        setTimeout(() => Settings.showTab('profile', document.querySelector('.settings-nav-item.active')), 500);
    }

    async function deleteAccount() {
        const { data: sessionData } = await DB.auth.getSession();
        const token = sessionData?.session?.access_token;
        if (!token) { Toast.show('You are not logged in. Please refresh and try again.'); return false; }

        const res = await fetch('/delete_account', { method: 'POST', headers: { Authorization: `Bearer ${token}` } });
        if (res.ok) {
            await DB.auth.signOut();
            window.location.href = Config.AUTH_REDIRECT;
            return true;
        }
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error || 'Failed to delete account. Please contact support.');
    }

    return Object.freeze({ loadUser, logout, editDisplayName, deleteAccount });
})();

// ─────────────────────────────────────────────────────────────
// 11. UI PRIMITIVES — DOM element factories
// ─────────────────────────────────────────────────────────────
const UIFactory = (() => {

    const COPY_SVG_SM = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`;

    function buildFileAttachHTML(files) {
        return files.map(f => {
            if (f.type && f.type.startsWith('image/')) {
                return `<div class="attached-file img-attach"><img src="${f.url}" alt="${f.name}" class="attach-img"></div>`;
            }
            return `<div class="attached-file doc-attach"><span class="attach-icon">📄</span><span class="attach-name">${f.name}</span></div>`;
        }).join('');
    }

    function createThinkingIndicator() {
        const div = document.createElement('div');
        div.classList.add('message', 'bot', 'typing');
        div.innerHTML = `<div class="thinking-wrap">
            <div class="thinking-label"><span class="think-icon"></span>AI is thinking…</div>
            <div class="skeleton-lines">
                <div class="skeleton-line"></div>
                <div class="skeleton-line"></div>
                <div class="skeleton-line"></div>
            </div>
        </div>`;
        return div;
    }

    function createLightIndicator() {
        const div = document.createElement('div');
        div.classList.add('message', 'bot', 'typing');
        div.innerHTML = `<div class="typing-dots"><span></span><span></span><span></span></div>`;
        return div;
    }

    function createUserBubble(text, files) {
        const wrapper = document.createElement('div');
        wrapper.classList.add('user-msg-wrapper');

        if (files && files.length > 0) {
            const filesDiv = document.createElement('div');
            filesDiv.innerHTML = buildFileAttachHTML(files);
            wrapper.appendChild(filesDiv);
        }
        if (text) {
            const bubble = document.createElement('div');
            bubble.classList.add('message', 'user');
            bubble.innerText = text;
            wrapper.appendChild(bubble);
        }

        const copyBtn = document.createElement('button');
        copyBtn.classList.add('user-copy-btn');
        copyBtn.title     = 'Copy message';
        copyBtn.innerHTML = COPY_SVG_SM;
        // Event delegation: attach directly to the button (single element, not a loop)
        copyBtn.addEventListener('click', () => Clipboard.copyUserMessage(copyBtn));
        wrapper.appendChild(copyBtn);
        return wrapper;
    }

    function createBotWrapper() {
        const wrapper = document.createElement('div');
        wrapper.classList.add('bot-msg-wrapper');
        const botMsg = document.createElement('div');
        botMsg.classList.add('message', 'bot');

        const actionsRow  = document.createElement('div');
        actionsRow.classList.add('bot-actions');
        const copyBtn = document.createElement('button');
        copyBtn.classList.add('bot-copy-btn');
        copyBtn.title     = 'Copy answer';
        copyBtn.innerHTML = `${COPY_SVG_SM} Copy`;
        copyBtn.addEventListener('click', () => Clipboard.copyBotAnswer(copyBtn));
        actionsRow.appendChild(copyBtn);

        wrapper.appendChild(botMsg);
        wrapper.appendChild(actionsRow);
        return { wrapper, botMsg };
    }

    function createGreetingEl(userName) {
        const greeting = Utils.pickGreeting(userName);
        const el = document.createElement('div');
        el.className = 'greeting-card';
        el.innerHTML = `
            <div class="greeting-text">${greeting}</div>
            <div class="greeting-sub">How can I help you today?</div>`;
        return el;
    }

    return Object.freeze({ createThinkingIndicator, createLightIndicator, createUserBubble, createBotWrapper, createGreetingEl, buildFileAttachHTML });
})();

// ─────────────────────────────────────────────────────────────
// 12. CHAT STATE RESET — single implementation used everywhere
// ─────────────────────────────────────────────────────────────
const ChatReset = (() => {
    function resetToNewChat() {
        State.resetSession();
        const chatbox   = DOM.chatbox();
        const inputArea = DOM.inputArea();
        const app       = DOM.app();
        if (chatbox)   chatbox.innerHTML = '';
        if (inputArea) { inputArea.classList.remove('bottom'); inputArea.classList.add('center'); }
        if (app)       app.classList.add('greeting-mode');

        const userName = DOM.userFullname()?.textContent || 'User';
        const greeting = UIFactory.createGreetingEl(userName);
        if (chatbox) { chatbox.innerHTML = ''; chatbox.appendChild(greeting); }
    }

    return Object.freeze({ resetToNewChat });
})();

// ─────────────────────────────────────────────────────────────
// 13. SIDEBAR
// ─────────────────────────────────────────────────────────────
const Sidebar = (() => {
    const isMobile = () => window.innerWidth <= Config.BREAKPOINT;

    function open(section) {
        const sidebar  = DOM.sidebar();
        const iconRail = DOM.iconRail();
        const overlay  = DOM.sidebarOverlay();
        if (!sidebar) return;
        if (isMobile()) {
            sidebar.classList.add('open');
            overlay?.classList.add('show');
        } else {
            sidebar.classList.add('open');
            iconRail?.classList.remove('visible');
        }
        if (section === 'history') History.show();
    }

    function close() {
        const sidebar  = DOM.sidebar();
        const overlay  = DOM.sidebarOverlay();
        const hamburger = DOM.hamburger();
        sidebar?.classList.remove('open');
        overlay?.classList.remove('show');
        hamburger?.classList.remove('is-open');
    }

    function toggle() {
        const sidebar  = DOM.sidebar();
        const iconRail = DOM.iconRail();
        const overlay  = DOM.sidebarOverlay();
        const hamburger = DOM.hamburger();
        if (!sidebar) return;

        if (isMobile()) {
            sidebar.classList.toggle('open');
            overlay?.classList.toggle('show');
            hamburger?.classList.toggle('is-open', sidebar.classList.contains('open'));
        } else {
            if (sidebar.classList.contains('open')) {
                sidebar.classList.remove('open');
                iconRail?.classList.add('visible');
            } else {
                sidebar.classList.add('open');
                iconRail?.classList.remove('visible');
            }
        }
    }

    return Object.freeze({ open, close, toggle });
})();

// ─────────────────────────────────────────────────────────────
// 14. MODALS — Privacy & Delete Account
// ─────────────────────────────────────────────────────────────
const Modals = (() => {

    // ── Shared helpers ──────────────────────────────────────
    function _mount(modal) {
        document.body.appendChild(modal);
        modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
        requestAnimationFrame(() => modal.classList.add('priv-modal-open'));
    }

    // ── Privacy Policy ──────────────────────────────────────
    function showPrivacy() {
        document.getElementById('privacyModal')?.remove();
        const modal = document.createElement('div');
        modal.id        = 'privacyModal';
        modal.className = 'priv-modal-overlay';
        modal.innerHTML = `
            <div class="priv-modal-box" role="dialog" aria-modal="true" aria-label="Data &amp; Privacy">
                <div class="priv-modal-header">
                    <div class="priv-modal-title-wrap">
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#10a37f" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>
                        <h2 class="priv-modal-title">Data &amp; Privacy</h2>
                    </div>
                    <button class="priv-modal-close js-close-privacy" aria-label="Close">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                    </button>
                </div>
                <div class="priv-modal-body">
                    <p class="priv-intro">We respect your privacy and are committed to protecting your data.</p>
                    ${[
                        ['Data We Collect',        'We may collect basic information such as your email address, chat messages, and usage data to improve our AI services.'],
                        ['How We Use Your Data',   'Your data is used to provide responses, improve AI performance, and ensure security. We do not sell your personal data to third parties.'],
                        ['AI Conversations',       'Your conversations may be stored and analyzed to improve the quality of responses. Avoid sharing sensitive or personal information.'],
                        ['Email &amp; Authentication','We use your email for account creation, login verification (OTP), and password recovery. We do not send spam.'],
                        ['Data Security',          'We implement reasonable security measures to protect your data, but no system is completely secure.'],
                        ['Third-Party Services',   'We may use trusted third-party services (such as authentication and email providers) to operate our platform.'],
                        ['Your Control',           'You can request deletion of your data at any time by contacting support.'],
                        ['Changes to Policy',      'We may update this policy from time to time. Continued use of the service means you accept the changes.'],
                    ].map(([title, text], n) => `
                        <div class="priv-item">
                            <div class="priv-num">${n + 1}</div>
                            <div><p class="priv-item-title">${title}</p><p class="priv-item-text">${text}</p></div>
                        </div>`).join('')}
                    <div class="priv-contact">
                        <p class="priv-contact-title">Contact Us</p>
                        <div class="priv-contact-row">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#10a37f" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"></path><polyline points="22,6 12,13 2,6"></polyline></svg>
                            <a href="mailto:support@yourdomain.co" class="priv-link">support@yourdomain.co</a>
                        </div>
                        <div class="priv-contact-row">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#10a37f" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"></path></svg>
                            <a href="https://github.com/**************" target="_blank" rel="noopener" class="priv-link">github.com/**************</a>
                        </div>
                        <div class="priv-contact-row">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#10a37f" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 8a6 6 0 0 1 6 6v7h-4v-7a2 2 0 0 0-2-2 2 2 0 0 0-2 2v7h-4v-7a6 6 0 0 1 6-6z"></path><rect x="2" y="9" width="4" height="12"></rect><circle cx="4" cy="4" r="2"></circle></svg>
                            <a href="https://linkedin.com/in/************************" target="_blank" rel="noopener" class="priv-link">linkedin.com/in/************************</a>
                        </div>
                    </div>
                </div>
                <div class="priv-modal-footer">
                    <button class="priv-close-btn js-close-privacy">Close</button>
                </div>
            </div>`;
        _mount(modal);
        modal.querySelector('.js-close-privacy')?.addEventListener('click', () => modal.remove());
    }

    // ── Delete Account ───────────────────────────────────────
    function showDeleteAccount() {
        document.getElementById('deleteAccountModal')?.remove();
        const modal = document.createElement('div');
        modal.id        = 'deleteAccountModal';
        modal.className = 'priv-modal-overlay';
        modal.innerHTML = `
            <div class="priv-modal-box del-modal-box" role="dialog" aria-modal="true" aria-label="Delete Account">
                <div class="del-modal-icon-wrap">
                    <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#e06c6c" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path><path d="M10 11v6M14 11v6"></path><path d="M9 6V4h6v2"></path></svg>
                </div>
                <h2 class="del-modal-title">Delete Account</h2>
                <p class="del-modal-desc">This action is <strong>permanent and irreversible</strong>. All your conversations, settings, and account data will be deleted forever.</p>
                <p class="del-modal-confirm-label">Type <span class="del-confirm-word">DELETE</span> to confirm:</p>
                <input type="text" id="deleteConfirmInput" class="del-confirm-input" placeholder="Type DELETE here" autocomplete="off">
                <div class="del-modal-actions">
                    <button class="del-cancel-btn js-del-cancel">Cancel</button>
                    <button class="del-confirm-btn js-del-confirm" disabled>
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path></svg>
                        Delete my account
                    </button>
                </div>
            </div>`;
        _mount(modal);

        const confirmBtn = modal.querySelector('.js-del-confirm');
        const input      = modal.querySelector('#deleteConfirmInput');

        modal.querySelector('.js-del-cancel')?.addEventListener('click', () => modal.remove());
        input?.addEventListener('input', () => { confirmBtn.disabled = input.value !== 'DELETE'; });

        confirmBtn?.addEventListener('click', async () => {
            confirmBtn.disabled  = true;
            confirmBtn.innerHTML = '⏳ Deleting…';
            try {
                await Auth.deleteAccount();
                modal.remove();
            } catch (err) {
                Toast.show(err.message || '❌ Failed to delete account.');
                confirmBtn.disabled  = false;
                confirmBtn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path></svg> Delete my account`;
            }
        });

        setTimeout(() => input?.focus(), 100);
    }

    return Object.freeze({ showPrivacy, showDeleteAccount });
})();

// ─────────────────────────────────────────────────────────────
// 15. SETTINGS OVERLAY
// ─────────────────────────────────────────────────────────────
const Settings = (() => {

    // ── SVG ICONS (shared across settings tabs) ──────────────
    const ICONS = {
        gear:     `<circle cx="12" cy="12" r="3"></circle><path d="M12 1v6m0 6v6m10.39-9.39l-4.24 4.24m-8.3 0l-4.24-4.24m12.53 8.53l4.24 4.24m-8.3 0l4.24-4.24"></path>`,
        user:     `<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle>`,
        chat:     `<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>`,
        shield:   `<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>`,
        wave:     `<line x1="8" y1="6" x2="8" y2="18"></line><line x1="12" y1="3" x2="12" y2="21"></line><line x1="16" y1="7" x2="16" y2="17"></line><line x1="4" y1="9" x2="4" y2="15"></line><line x1="20" y1="9" x2="20" y2="15"></line>`,
        persona:  `<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle><path d="M16 3.13a4 4 0 0 1 0 7.75"></path>`,
        database: `<ellipse cx="12" cy="5" rx="9" ry="3"></ellipse><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"></path><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"></path>`,
        account:  `<circle cx="12" cy="7" r="4"></circle><path d="M5.5 21a9 9 0 0 1 13 0"></path>`,
    };

    function _icon(paths) {
        return `<svg class="sn-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`;
    }

    function _comingSoonSection(title, items) {
        const rows = items.map(({ icon, label, sub }) => `
            <div class="sc-row disabled">
                <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${icon}</svg>
                <div class="sc-row-body">
                    <p class="sc-row-label">${label}</p>
                    <p class="sc-row-sub soon">${sub || 'Coming soon'}</p>
                </div>
            </div>`).join('');
        return `<div class="sc-section"><div class="sc-section-title">${title}</div>${rows}</div>`;
    }

    // ── Tab content builders ────────────────────────────────
    function _tabGeneral() {
        const theme    = ThemeManager.getTheme();
        const fontSize = ThemeManager.getFontSize();
        return `
            <div class="sc-section">
                <div class="sc-section-title">Appearance</div>
                <div class="sc-row sc-row-block">
                    <div class="sc-row-top">
                        ${_icon(ICONS.gear)}
                        <p class="sc-row-label">Theme</p>
                    </div>
                    <div class="theme-picker">
                        ${['light','auto','dark'].map(t => `
                        <button class="theme-option ${theme === t ? 'active' : ''}" data-theme="${t}" data-action="set-theme">
                            <div class="theme-preview ${t}-preview">
                                ${t === 'auto' ? '<div class="tp-half-light"></div><div class="tp-half-dark"></div>' : ''}
                                <div class="tp-bar"></div><div class="tp-line"></div>
                                ${t !== 'auto' ? '<div class="tp-line short"></div>' : ''}
                                <div class="tp-bubble"></div>
                            </div>
                            <span>${t[0].toUpperCase() + t.slice(1)}</span>
                        </button>`).join('')}
                    </div>
                </div>
                <div class="sc-row sc-row-block" style="margin-top:8px;">
                    <div class="sc-row-top">
                        ${_icon(`<polyline points="4 7 4 4 20 4 20 7"></polyline><rect x="2" y="7" width="20" height="13" rx="2"></rect><path d="M9 17v-3m6 3v-3"></path>`)}
                        <p class="sc-row-label">Chat font size</p>
                    </div>
                    <div class="font-picker">
                        ${[['default','Aa','Default'],['small','Aa','Small'],['large','Aa','Large'],['xlarge','Aa','X-Large']].map(([s, sample, label]) => `
                        <button class="font-option ${fontSize === s ? 'active' : ''}" data-fontsize="${s}" data-action="set-font">
                            <span class="font-sample ${s !== 'default' ? s+'-sample' : ''}">${sample}</span>
                            <span>${label}</span>
                        </button>`).join('')}
                    </div>
                </div>
            </div>
            <div class="sc-section">
                <div class="sc-section-title">Support</div>
                <div class="sc-row" data-action="open-feedback-bug">
                    ${_icon(`<circle cx="12" cy="12" r="10"></circle><path d="M8 14s1.5 2 4 2 4-2 4-2"></path><line x1="9" y1="9" x2="9.01" y2="9"></line><line x1="15" y1="9" x2="15.01" y2="9"></line>`)}
                    <div class="sc-row-body"><p class="sc-row-label">Send bug report</p><p class="sc-row-sub">Help us fix issues</p></div>
                </div>
                <div class="sc-row" data-action="open-feedback-feature">
                    ${_icon(`<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon>`)}
                    <div class="sc-row-body"><p class="sc-row-label">Request a feature</p><p class="sc-row-sub">Suggest improvements</p></div>
                </div>
            </div>`;
    }

    function _tabProfile() {
        const email    = State.getUser()?.email || 'Not logged in';
        const fullName = DOM.userFullname()?.textContent || 'User';
        const initials = DOM.userAvatar()?.textContent  || '?';
        return `
            <div class="sc-section">
                <div class="sc-section-title">Account</div>
                <div class="sc-profile-card">
                    <div class="sc-avatar">${initials}</div>
                    <div><p class="sc-profile-name">${fullName}</p><p class="sc-profile-email">${email}</p></div>
                </div>
            </div>
            <div class="sc-section">
                <div class="sc-section-title">Actions</div>
                <div class="sc-row" data-action="edit-display-name">
                    ${_icon(`<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>`)}
                    <div class="sc-row-body"><p class="sc-row-label">Edit display name</p><p class="sc-row-sub">Change how your name appears</p></div>
                </div>
                <div class="sc-row danger" data-action="logout">
                    ${_icon(`<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path><polyline points="16 17 21 12 16 7"></polyline><line x1="21" y1="12" x2="9" y2="12"></line>`)}
                    <div class="sc-row-body"><p class="sc-row-label">Log out</p><p class="sc-row-sub">Sign out of your account</p></div>
                </div>
            </div>`;
    }

    function _tabChats() {
        return `
            <div class="sc-section">
                <div class="sc-section-title">Manage chats</div>
                <div class="sc-row" data-action="archive-all-chats">
                    ${_icon(`<polyline points="21 8 21 21 3 21 3 8"></polyline><rect x="1" y="3" width="22" height="5"></rect><line x1="10" y1="12" x2="14" y2="12"></line>`)}
                    <div class="sc-row-body"><p class="sc-row-label">Archive all chats</p><p class="sc-row-sub">Hide all chats from your history</p></div>
                </div>
                <div class="sc-row danger" data-action="delete-all-chats">
                    ${_icon(`<polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line>`)}
                    <div class="sc-row-body"><p class="sc-row-label">Delete all chats</p><p class="sc-row-sub">Permanently remove all history</p></div>
                </div>
            </div>
            ${_comingSoonSection('Preferences', [
                { icon: `<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"></path><polyline points="17 21 17 13 7 13 7 21"></polyline><polyline points="7 3 7 8 15 8"></polyline>`, label: 'Export chat history', sub: 'Coming soon' }
            ])}`;
    }

    function _tabPrivacy() {
        return `
            <div class="sc-section">
                <div class="sc-section-title">Privacy controls</div>
                <div class="sc-row" data-action="show-privacy-modal" style="cursor:pointer;">
                    ${_icon(ICONS.shield)}
                    <div class="sc-row-body"><p class="sc-row-label">Data &amp; privacy</p><p class="sc-row-sub">View how we collect and use your data</p></div>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#666" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;margin-left:8px;"><polyline points="9 18 15 12 9 6"></polyline></svg>
                </div>
                <div class="sc-row danger" data-action="show-delete-account-modal" style="cursor:pointer;">
                    ${_icon(`<polyline points="3 6 5 6 21 6"></polyline><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path><path d="M10 11v6M14 11v6"></path><path d="M9 6V4h6v2"></path>`)}
                    <div class="sc-row-body"><p class="sc-row-label" style="color:#e06c6c;">Delete my account</p><p class="sc-row-sub">Permanently delete your account and all data</p></div>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#e06c6c" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;margin-left:8px;"><polyline points="9 18 15 12 9 6"></polyline></svg>
                </div>
            </div>
            <div class="sc-section">
                <div class="sc-section-title">Data preferences</div>
                <div class="sc-row-block"><div class="sc-row-top">
                    <div class="sc-row-icon-wrap">${_icon(`<path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"></path><circle cx="12" cy="10" r="3"></circle>`)}</div>
                    <div class="sc-row-body"><p class="sc-row-label">Location metadata</p><p class="sc-row-sub">Allow approximate location to improve responses <span class="badge-soon">Coming soon</span></p></div>
                    <label class="toggle-switch disabled-toggle" title="Coming soon"><input type="checkbox" disabled><span class="toggle-slider"></span></label>
                </div></div>
                <div class="sc-row-block"><div class="sc-row-top">
                    <div class="sc-row-icon-wrap">${_icon(`<path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3H14z"></path><path d="M7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"></path>`)}</div>
                    <div class="sc-row-body"><p class="sc-row-label">Help to improve us</p><p class="sc-row-sub">Share anonymized usage data to help improve Catura AI <span class="badge-soon">Coming soon</span></p></div>
                    <label class="toggle-switch disabled-toggle" title="Coming soon"><input type="checkbox" disabled><span class="toggle-slider"></span></label>
                </div></div>
            </div>`;
    }

    function _tabSpeech() {
        return _comingSoonSection('Voice &amp; speech', [
            { icon: `<path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path><path d="M19 10v2a7 7 0 0 1-14 0v-2"></path><line x1="12" y1="19" x2="12" y2="23"></line><line x1="8" y1="23" x2="16" y2="23"></line>`, label: 'Voice input', sub: 'Coming soon' },
            { icon: ICONS.wave, label: 'Voice style', sub: 'Coming soon' },
        ]);
    }

    function _tabPersonalization() {
        return `
            ${_comingSoonSection('Custom instructions', [
                { icon: `<path d="M12 20h9"></path><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path>`, label: 'Custom instructions', sub: 'Tell Catura how to respond — coming soon' },
                { icon: `<circle cx="12" cy="12" r="10"></circle><path d="M8 14s1.5 2 4 2 4-2 4-2"></path><line x1="9" y1="9" x2="9.01" y2="9"></line><line x1="15" y1="9" x2="15.01" y2="9"></line>`, label: 'AI personality', sub: 'Coming soon' },
            ])}
            ${_comingSoonSection('Memory', [
                { icon: `<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path>`, label: 'Memory &amp; context', sub: 'Coming soon' },
            ])}`;
    }

    function _tabDataControls() {
        return _comingSoonSection('Your data', [
            { icon: `<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"></path><polyline points="17 21 17 13 7 13 7 21"></polyline><polyline points="7 3 7 8 15 8"></polyline>`, label: 'Export all data', sub: 'Download a copy of your data — coming soon' },
            { icon: ICONS.database, label: 'Manage stored data', sub: 'Coming soon' },
            { icon: ICONS.shield, label: 'Data sharing preferences', sub: 'Coming soon' },
        ]);
    }

    function _tabAccount() {
        const email    = State.getUser()?.email || 'Not logged in';
        const fullName = DOM.userFullname()?.textContent || 'User';
        const initials = DOM.userAvatar()?.textContent  || '?';
        return `
            <div class="sc-section">
                <div class="sc-section-title">Account details</div>
                <div class="sc-profile-card">
                    <div class="sc-avatar">${initials}</div>
                    <div><p class="sc-profile-name">${fullName}</p><p class="sc-profile-email">${email}</p></div>
                </div>
            </div>
            ${_comingSoonSection('Subscription', [
                { icon: `<rect x="1" y="4" width="22" height="16" rx="2" ry="2"></rect><line x1="1" y1="10" x2="23" y2="10"></line>`, label: 'Upgrade plan', sub: 'Pro features — coming soon' },
                { icon: `<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon>`, label: 'Current plan: Free', sub: 'Unlimited chats with Dagr &amp; Apep' },
            ])}
            ${_comingSoonSection('Security', [
                { icon: `<rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect><path d="M7 11V7a5 5 0 0 1 10 0v4"></path>`, label: 'Change password', sub: 'Coming soon' },
                { icon: `<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path><path d="M12 8v4M12 16h.01"></path>`, label: 'Two-factor authentication', sub: 'Coming soon' },
                { icon: `<polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>`, label: 'Delete account', sub: 'Permanently remove your account — coming soon' },
            ])}`;
    }

    const TAB_MAP = {
        general:         _tabGeneral,
        profile:         _tabProfile,
        chats:           _tabChats,
        privacy:         _tabPrivacy,
        speech:          _tabSpeech,
        personalization: _tabPersonalization,
        datacontrols:    _tabDataControls,
        account:         _tabAccount,
    };

    const NAV_ITEMS = [
        { id: 'general',         label: 'General',          icon: ICONS.gear },
        { id: 'profile',         label: 'Profile',          icon: ICONS.user },
        { id: 'chats',           label: 'Chats',            icon: ICONS.chat },
        { id: 'privacy',         label: 'Privacy',          icon: ICONS.shield },
        { id: 'speech',          label: 'Speech',           icon: ICONS.wave },
        { id: 'personalization', label: 'Personalization',  icon: ICONS.persona },
        { id: 'datacontrols',    label: 'Data controls',    icon: ICONS.database },
        { id: 'account',         label: 'Account',          icon: ICONS.account },
    ];

    // ── Event delegation handler for settings content ────────
    function _handleContentClick(e) {
        const row    = e.target.closest('[data-action]');
        if (!row) return;
        const action = row.dataset.action;
        switch (action) {
            case 'set-theme':             ThemeManager.setTheme(row.dataset.theme); break;
            case 'set-font':              ThemeManager.setFontSize(row.dataset.fontsize); break;
            case 'open-feedback-bug':     if (typeof openFeedbackModal === 'function') openFeedbackModal('bug'); break;
            case 'open-feedback-feature': if (typeof openFeedbackModal === 'function') openFeedbackModal('feature'); break;
            case 'edit-display-name':     Auth.editDisplayName(); break;
            case 'logout':                Auth.logout(); break;
            case 'archive-all-chats':     ChatActions.archiveAll(); break;
            case 'delete-all-chats':      ChatActions.deleteAll(); break;
            case 'show-privacy-modal':    Modals.showPrivacy(); break;
            case 'show-delete-account-modal': Modals.showDeleteAccount(); break;
        }
    }

    function showTab(tab, clickedEl) {
        document.querySelectorAll('.settings-nav-item').forEach(el => el.classList.remove('active'));
        if (clickedEl) clickedEl.classList.add('active');
        const content = DOM.settingsContent();
        if (!content) return;
        content.innerHTML = (TAB_MAP[tab] || TAB_MAP.general)();
        content.removeEventListener('click', _handleContentClick);
        content.addEventListener('click', _handleContentClick);
    }

    function show() {
        const overlay = DOM.settingsOverlay();
        if (!overlay) return;

        overlay.innerHTML = `
            <button class="settings-close-btn js-close-settings" title="Close">✕</button>
            <div class="settings-panel-wrap">
                <div class="settings-nav">
                    <h2 class="settings-nav-title">Settings</h2>
                    ${NAV_ITEMS.map((item, idx) => `
                    <div class="settings-nav-item ${idx === 0 ? 'active' : ''}" data-tab="${item.id}">
                        <svg class="sn-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${item.icon}</svg>
                        ${item.label}
                    </div>`).join('')}
                </div>
                <div class="settings-content" id="settingsContent"></div>
            </div>`;

        // DOM cache invalidation since settingsContent was just recreated
        DOM.invalidate('settingsContent');

        overlay.classList.add('active');
        showTab('general', overlay.querySelector('.settings-nav-item.active'));

        // Nav click delegation
        overlay.querySelector('.settings-nav').addEventListener('click', e => {
            const item = e.target.closest('.settings-nav-item[data-tab]');
            if (item) showTab(item.dataset.tab, item);
        });

        overlay.querySelector('.js-close-settings')?.addEventListener('click', close);

        if (window.innerWidth <= Config.BREAKPOINT) Sidebar.close();
    }

    function close() {
        DOM.settingsOverlay()?.classList.remove('active');
        Menu.showMain();
    }

    return Object.freeze({ show, close, showTab });
})();

// ─────────────────────────────────────────────────────────────
// 16. MENU (sidebar menu state machine)
// ─────────────────────────────────────────────────────────────
const Menu = (() => {

    const MAIN_HTML = `
        <div class="sidebar-item" data-menu-action="new-chat">
            <svg class="sidebar-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>
            New Chat
        </div>
        <div class="sidebar-item" data-menu-action="show-history">
            <svg class="sidebar-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
            Chat History
        </div>
        <div class="sidebar-item" data-menu-action="show-settings">
            <svg class="sidebar-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"></circle><path d="M12 1v6m0 6v6m10.39-9.39l-4.24 4.24m-8.3 0l-4.24-4.24m12.53 8.53l4.24 4.24m-8.3 0l4.24-4.24"></path></svg>
            Settings
        </div>`;

    function showMain() {
        const menu = DOM.sidebarMenu();
        if (!menu) return;
        menu.innerHTML = MAIN_HTML;
        menu.removeEventListener('click', _handleMenuClick);
        menu.addEventListener('click', _handleMenuClick);
    }

    function _handleMenuClick(e) {
        const item = e.target.closest('[data-menu-action]');
        if (!item) return;
        switch (item.dataset.menuAction) {
            case 'new-chat':      CaturaApp.newChat();      break;
            case 'show-history':  History.show();           break;
            case 'show-settings': Settings.show();          break;
        }
    }

    return Object.freeze({ showMain });
})();

// ─────────────────────────────────────────────────────────────
// 17. CHAT ACTIONS (archive / delete)
// ─────────────────────────────────────────────────────────────
const ChatActions = (() => {

    async function archiveAll() {
        if (!confirm('Archive all chats? They will be hidden from your history.')) return;
        const { error } = await DB.from(Config.DB.SESSIONS).update({ archived: true }).eq('user_id', State.getUser().id);
        if (error) { Toast.show('❌ Archive failed: ' + (error.message || 'Check console')); return; }
        Toast.show('✓ All chats archived successfully');
        History.show();
    }

    async function deleteAll() {
        if (!confirm('Delete ALL chats permanently? This cannot be undone.')) return;
        const uid = State.getUser().id;

        const { error: msgErr } = await DB.from(Config.DB.MESSAGES).delete().eq('user_id', uid);
        if (msgErr) { Toast.show('❌ Failed to delete messages'); return; }

        const { error: sessErr } = await DB.from(Config.DB.SESSIONS).delete().eq('user_id', uid);
        if (sessErr) { Toast.show('❌ Failed to delete sessions'); return; }

        Toast.show('✓ All chats deleted successfully');
        ChatReset.resetToNewChat();
        Settings.close();
    }

    async function deleteSingle(sessionId) {
        if (!confirm('Delete this chat? This cannot be undone.')) return;
        const uid = State.getUser().id;

        const { error: msgErr } = await DB.from(Config.DB.MESSAGES).delete().eq('session_id', sessionId).eq('user_id', uid);
        if (msgErr) { Toast.show('❌ Failed to delete messages'); return; }

        const { error: sessErr } = await DB.from(Config.DB.SESSIONS).delete().eq('session_id', sessionId).eq('user_id', uid);
        if (sessErr) { Toast.show('❌ Failed to delete session'); return; }

        if (State.getSessionId() === sessionId) ChatReset.resetToNewChat();
        Toast.show('✓ Chat deleted');
        History.show();
    }

    async function renameChat(sessionId, currentTitle, titleEl) {
        const newTitle = prompt('Rename chat:', currentTitle);
        if (!newTitle || newTitle.trim() === currentTitle) return;
        const { error } = await DB.from(Config.DB.SESSIONS)
            .update({ title: newTitle.trim() })
            .eq('session_id', sessionId)
            .eq('user_id', State.getUser().id);
        if (error) { Toast.show('❌ Failed to rename chat'); return; }
        titleEl.textContent = newTitle.trim();
        Toast.show('✓ Chat renamed');
    }

    return Object.freeze({ archiveAll, deleteAll, deleteSingle, renameChat });
})();

// ─────────────────────────────────────────────────────────────
// 18. HISTORY
// ─────────────────────────────────────────────────────────────
const History = (() => {

    // Single delegated click handler for ALL history dropdowns.
    // Attached once to the menu container, not per-item.
    let _dropdownDelegate = null;

    function _closeAllDropdowns() {
        document.querySelectorAll('.history-dropdown.open').forEach(d => d.classList.remove('open'));
    }

    function _buildItem(session, openFn) {
        const date = new Date(session.created_at).toLocaleDateString();
        const item = document.createElement('div');
        item.classList.add('sidebar-item', 'history-item');
        item.dataset.sessionId = session.session_id;
        item.dataset.title     = session.title || 'Untitled';
        item.innerHTML = `
            <div class="history-info" style="flex:1;min-width:0;cursor:pointer;" data-history-action="open">
                <span class="history-title">${session.title || 'Untitled'}</span>
                <span class="history-date">${date}</span>
            </div>
            <div class="history-menu-wrap">
                <button class="history-menu-btn" title="Options" data-history-action="toggle-menu">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="12" cy="19" r="1.5"/></svg>
                </button>
                <div class="history-dropdown">
                    <button class="history-dropdown-item" data-history-action="open">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg>
                        Open chat
                    </button>
                    <button class="history-dropdown-item" data-history-action="rename">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>
                        Rename
                    </button>
                    <button class="history-dropdown-item danger" data-history-action="delete">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>
                        Delete chat
                    </button>
                </div>
            </div>`;
        return item;
    }

    async function show() {
        DOM.settingsOverlay()?.classList.remove('active');
        const { data, error } = await DB.from(Config.DB.SESSIONS)
            .select('*')
            .eq('user_id', State.getUser().id)
            .order('created_at', { ascending: false });

        if (error) { console.error('❌ History failed:', error.message); return; }

        const menu = DOM.sidebarMenu();
        menu.innerHTML = `
            <div class="history-header">
                <button class="back-btn" data-menu-action="back">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"></line><polyline points="12 19 5 12 12 5"></polyline></svg>
                    Back
                </button>
                <span>Chat History</span>
            </div>`;

        if (!data.length) {
            menu.innerHTML += '<div class="no-history">No chats yet. Start a new chat to see history.</div>';
        } else {
            const frag = document.createDocumentFragment();
            data.forEach(session => frag.appendChild(_buildItem(session, _loadSession)));
            menu.appendChild(frag);
        }

        // Single delegated listener — remove old one first to avoid stacking
        if (_dropdownDelegate) menu.removeEventListener('click', _dropdownDelegate);
        _dropdownDelegate = (e) => {
            const target = e.target.closest('[data-history-action]');
            if (!target) { _closeAllDropdowns(); return; }
            e.stopPropagation();
            const action    = target.dataset.historyAction;
            const item      = target.closest('.history-item');
            const sessionId = item?.dataset.sessionId;
            const titleSpan = item?.querySelector('.history-title');
            const currentTitle = item?.dataset.title || 'Untitled';

            if (action === 'back')        { Menu.showMain(); return; }
            if (action === 'toggle-menu') {
                const dropdown = item.querySelector('.history-dropdown');
                const isOpen   = dropdown.classList.contains('open');
                _closeAllDropdowns();
                if (!isOpen) dropdown.classList.add('open');
                return;
            }
            _closeAllDropdowns();
            if (action === 'open')   _loadSession(sessionId);
            if (action === 'rename') ChatActions.renameChat(sessionId, currentTitle, titleSpan);
            if (action === 'delete') ChatActions.deleteSingle(sessionId);
        };
        menu.addEventListener('click', _dropdownDelegate);
    }

    async function _loadSession(sessionId) {
        const chatbox   = DOM.chatbox();
        const inputArea = DOM.inputArea();
        const app       = DOM.app();
        chatbox.innerHTML = '';
        inputArea.classList.remove('center');
        inputArea.classList.add('bottom');
        app.classList.remove('greeting-mode');

        State.setSessionId(sessionId);
        State.setFirstMessage(false);

        const { data, error } = await DB.from(Config.DB.MESSAGES)
            .select('*')
            .eq('session_id', sessionId)
            .eq('user_id', State.getUser().id)
            .order('created_at', { ascending: true });

        if (error) { console.error('❌ Load session failed:', error.message); return; }

        const frag = document.createDocumentFragment();
        data.forEach(msg => {
            if (msg.role === 'user') {
                const histFiles = (msg.file_urls?.length)
                    ? msg.file_urls.map(url => ({
                        url,
                        name: url.split('/').pop().replace(/^\d+_[a-z0-9]+_/, ''),
                        type: /\.(png|jpg|jpeg|gif|webp)$/i.test(url) ? 'image/jpeg' : 'application/octet-stream',
                        size: 0,
                    }))
                    : [];
                frag.appendChild(UIFactory.createUserBubble(msg.content, histFiles));
            } else {
                const { wrapper, botMsg } = UIFactory.createBotWrapper();
                botMsg.innerHTML    = Markdown.render(Utils.repairTruncated(msg.content));
                wrapper.dataset.raw = msg.content;
                frag.appendChild(wrapper);
            }
        });

        chatbox.appendChild(frag);
        chatbox.scrollTop = chatbox.scrollHeight;
        if (window.innerWidth <= Config.BREAKPOINT) Sidebar.close();
        Menu.showMain();
        DOM.settingsOverlay()?.classList.remove('active');
    }

    return Object.freeze({ show });
})();

// ─────────────────────────────────────────────────────────────
// 19. MODEL SELECTOR
// ─────────────────────────────────────────────────────────────
const ModelSelector = (() => {
    const isMobile = () => window.innerWidth <= Config.BREAKPOINT;
    const MORE_MODELS = ['apep', 'gemma', 'gemma4'];

    function closeAll() {
        DOM.modelDropdown()?.classList.remove('open');
        DOM.modelSelectorBtn()?.classList.remove('open');
        DOM.moreModelsPanel()?.classList.remove('open');
        DOM.moreModelsRow()?.classList.remove('open');
    }

    function toggle(e) {
        e.stopPropagation();
        const dropdown = DOM.modelDropdown();
        const btn      = DOM.modelSelectorBtn();
        if (!dropdown) return;

        const isOpen = dropdown.classList.contains('open');
        closeAll();
        if (isOpen) return;

        if (isMobile()) {
            dropdown.style.top = dropdown.style.left = dropdown.style.bottom = '';
            dropdown.classList.add('open');
            btn?.classList.add('open');
        } else {
            const rect  = btn.getBoundingClientRect();
            // Measure off-screen, then position
            dropdown.style.cssText = 'visibility:hidden;opacity:0;transform:none;display:block;top:-9999px;left:-9999px';
            const dropH = dropdown.scrollHeight || 180;
            const dropW = Math.max(dropdown.offsetWidth || 0, 220);
            dropdown.style.cssText = '';

            let top  = Math.max(8, Math.min(rect.top - dropH - 8, window.innerHeight - dropH - 8));
            if (top < 8) top = rect.bottom + 8;
            let left = Math.max(8, Math.min(rect.right - dropW, window.innerWidth - dropW - 8));

            dropdown.style.top    = top + 'px';
            dropdown.style.left   = left + 'px';
            dropdown.style.bottom = 'auto';
            dropdown.classList.add('open');
            btn?.classList.add('open');
        }

        if (MORE_MODELS.includes(State.getModel())) {
            requestAnimationFrame(() => requestAnimationFrame(() => toggleMoreModels(null)));
        }
    }

    function select(modelId, modelName, e) {
        if (e) e.stopPropagation();
        State.setModel(modelId.toLowerCase());
        const nameEl = DOM.modelName();
        if (nameEl) nameEl.textContent = modelName;
        document.querySelectorAll('.model-option').forEach(o => o.classList.remove('active'));
        document.querySelector(`[data-model="${modelId}"]`)?.classList.add('active');
        if (isMobile()) { setTimeout(() => { closeAll(); Toast.show(`✓ Switched to ${modelName}`, 1500); }, 180); }
        else            { closeAll(); Toast.show(`✓ Switched to ${modelName}`, 1500); }
    }

    function toggleMoreModels(e) {
        if (e) { e.stopPropagation(); e.preventDefault(); }
        const panel = DOM.moreModelsPanel();
        const row   = DOM.moreModelsRow();
        if (!panel || !row) return;

        if (panel.classList.contains('open')) { panel.classList.remove('open'); row.classList.remove('open'); return; }

        if (!isMobile()) {
            const dropRect = DOM.modelDropdown().getBoundingClientRect();
            const panelW   = 230, gap = 8;
            let left = dropRect.right + gap;
            if (left + panelW > window.innerWidth - 8) left = dropRect.left - panelW - gap;
            if (left < 8) left = 8;
            let top = Math.max(8, Math.min(dropRect.top, window.innerHeight - 210 - 8));
            panel.style.left = left + 'px';
            panel.style.top  = top + 'px';
        } else {
            panel.style.left = panel.style.top = '';
            const dd  = DOM.modelDropdown();
            const btn = DOM.modelSelectorBtn();
            dd?.classList.remove('open');
            btn?.classList.remove('open');
        }
        panel.classList.add('open');
        row.classList.add('open');
    }

    function goBackToMain(e) {
        if (e) { e.stopPropagation(); e.preventDefault(); }
        DOM.moreModelsPanel()?.classList.remove('open');
        DOM.moreModelsRow()?.classList.remove('open');
        const dd  = DOM.modelDropdown();
        const btn = DOM.modelSelectorBtn();
        if (dd)  { dd.style.top = dd.style.left = dd.style.bottom = ''; dd.classList.add('open'); }
        if (btn) btn.classList.add('open');
    }

    function init() {
        // Close on outside click — one permanent listener
        document.addEventListener('click', (e) => {
            const wrap  = DOM.modelSelectorWrap();
            const panel = DOM.moreModelsPanel();
            if (!wrap?.contains(e.target) && !DOM.modelDropdown()?.contains(e.target) && !panel?.contains(e.target)) {
                closeAll();
            }
        });
    }

    return Object.freeze({ toggle, select, toggleMoreModels, goBackToMain, closeAll, init });
})();

// ─────────────────────────────────────────────────────────────
// 20. PLUS MENU
// ─────────────────────────────────────────────────────────────
const PlusMenu = (() => {

    function toggle(e) {
        e.stopPropagation();
        DOM.plusDropdown()?.classList.toggle('open');
    }

    function handleAction(action) {
        DOM.plusDropdown()?.classList.remove('open');
        switch (action) {
            case 'file':     DOM.fileInput()?.click(); break;
            case 'connect':  Toast.show('Connect apps — coming soon!'); break;
            case 'think':    Toast.show('Think mode — coming soon!'); break;
            case 'research': Toast.show('Deep research — coming soon!'); break;
            case 'search':   _toggleWebSearch(); break;
        }
    }

    function _toggleWebSearch() {
        const enabled = State.toggleWebSearch();
        document.querySelector('.plus-dropdown-item[data-action="search"]')?.classList.toggle('search-active', enabled);
        Toast.show(enabled ? '🌐 Web search enabled' : 'Web search disabled', 1500);
    }

    function init() {
        // Init active state
        const searchItem = document.querySelector('.plus-dropdown-item[data-action="search"]');
        searchItem?.classList.toggle('search-active', State.isWebSearchOn());

        // Close on outside click
        document.addEventListener('click', (e) => {
            if (!DOM.plusMenuWrap()?.contains(e.target)) DOM.plusDropdown()?.classList.remove('open');
        });
    }

    return Object.freeze({ toggle, handleAction, init });
})();

// ─────────────────────────────────────────────────────────────
// 21. STREAMING
// ─────────────────────────────────────────────────────────────
const Stream = (() => {

    function setStreamingUI(streaming) {
        State.setStreaming(streaming);
        const btn = DOM.sendBtn();
        if (!btn) return;
        btn.classList.toggle('is-stopping', streaming);
        btn.title = streaming ? 'Stop generating' : 'Send message';
    }

    async function read(botMsg, wrapper, reader, decoder, chatbox, onToolUsed) {
        let buffer = '', fullReply = '', renderTimer = null;

        const scheduleRender = () => {
            if (renderTimer) return;
            renderTimer = setTimeout(() => {
                renderTimer = null;
                if (fullReply) {
                    botMsg.innerHTML = Markdown.render(Utils.repairTruncated(fullReply)) + '<span class="stream-cursor"></span>';
                    chatbox.scrollTop = chatbox.scrollHeight;
                }
            }, Config.STREAM_THROTTLE_MS);
        };

        try {
            outer: while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop();
                for (const line of lines) {
                    if (!line.startsWith('data: ')) continue;
                    const payload = line.slice(6).trim();
                    if (payload === '[DONE]') break outer;
                    try {
                        const data = JSON.parse(payload);
                        if (data.tool_used !== undefined) { onToolUsed?.(data.tool_used); continue; }
                        if (data.error) {
                            if (!fullReply.trim()) botMsg.innerHTML = `<p style="color:#e06c6c">⚠️ ${data.error}</p>`;
                            if (renderTimer) { clearTimeout(renderTimer); renderTimer = null; }
                            return fullReply || '';
                        }
                        if (data.token) { fullReply += data.token; scheduleRender(); }
                    } catch { continue; }
                }
            }
        } catch (e) { console.warn('Stream read error:', e); }

        if (renderTimer) { clearTimeout(renderTimer); renderTimer = null; }

        if (fullReply.trim()) {
            botMsg.innerHTML    = Markdown.render(Utils.repairTruncated(fullReply));
            wrapper.dataset.raw = fullReply;
            chatbox.scrollTop   = chatbox.scrollHeight;
            return fullReply;
        }
        botMsg.innerHTML = `<p style="color:#e06c6c">⚠️ No response received. Please try again.</p>`;
        return '';
    }

    return Object.freeze({ setStreamingUI, read });
})();

// ─────────────────────────────────────────────────────────────
// 22. SEND MESSAGE
// ─────────────────────────────────────────────────────────────
const SendMessage = (() => {

    async function _performWebSearch(query) {
        try {
            const res  = await fetch(`/search?q=${encodeURIComponent(query)}&max_results=5`);
            const data = await res.json();
            return data.results || [];
        } catch (e) { console.error('❌ Web search failed:', e); return []; }
    }

    async function send() {
        const inputEl  = DOM.input();
        const chatbox  = DOM.chatbox();
        const inputArea = DOM.inputArea();
        const app       = DOM.app();

        const message  = inputEl.value.trim();
        const hasFiles = typeof attachedFiles !== 'undefined' && attachedFiles.length > 0;
        if (!message && !hasFiles) return;

        // ── Layout: transition on first message ──────────────
        if (State.isFirstMessage()) {
            chatbox.innerHTML = '';
            inputArea.classList.remove('center');
            inputArea.classList.add('bottom');
            app.classList.remove('greeting-mode');
        }

        // ── Snapshot files, clear preview immediately ────────
        const filesToSend = typeof attachedFiles !== 'undefined' ? attachedFiles.slice() : [];
        if (typeof attachedFiles !== 'undefined') attachedFiles = [];
        if (typeof renderAttachedPreview === 'function') renderAttachedPreview();

        // ── User bubble ──────────────────────────────────────
        chatbox.appendChild(UIFactory.createUserBubble(message, filesToSend));

        // ── Create session on first message ──────────────────
        if (State.isFirstMessage()) {
            State.setFirstMessage(false);
            const title = (message || (filesToSend[0]?.name || 'File Chat')).substring(0, 40);
            State.setChatTitle(title);
            const { error } = await DB.from(Config.DB.SESSIONS).insert([{
                session_id: State.getSessionId(),
                title,
                user_id: State.getUser().id,
            }]);
            if (error) console.error('❌ Session insert failed:', error.message);
        }

        // ── Save user message ────────────────────────────────
        const fileUrls = filesToSend.map(f => f.url);
        const { error: userError } = await DB.from(Config.DB.MESSAGES).insert([{
            role:       'user',
            content:    message,
            session_id: State.getSessionId(),
            user_id:    State.getUser().id,
            file_urls:  fileUrls.length ? fileUrls : null,
        }]);
        if (userError) console.error('❌ User message save failed:', userError.message);

        // ── Clear input ──────────────────────────────────────
        inputEl.value          = '';
        inputEl.style.height   = 'auto';
        chatbox.scrollTop      = chatbox.scrollHeight;

        // ── Thinking indicator ───────────────────────────────
        const query   = message || (filesToSend[0]?.name || '');
        const heavy   = Utils.isHeavyQuery(query);
        const thinking = heavy ? UIFactory.createThinkingIndicator() : UIFactory.createLightIndicator();
        chatbox.appendChild(thinking);
        chatbox.scrollTop = chatbox.scrollHeight;

        // ── Intent label in thinking indicator ───────────────
        const intent = message ? Utils.detectClientIntent(message) : 'general';
        const intentLabels = { clock: '🕐 Checking live time…', weather: '🌤️ Checking live weather…', finance: '💹 Fetching market data…', sports: '🏏 Fetching live scores…', news: '📰 Getting latest news…', web_search: '🔍 Searching the web…' };
        const thinkLabel = thinking.querySelector('.thinking-label');
        if (thinkLabel && intentLabels[intent]) thinkLabel.innerHTML = `<span class="think-icon"></span>${intentLabels[intent]}`;

        // ── Streaming setup ──────────────────────────────────
        const abortCtrl = new AbortController();
        State.setAbortCtrl(abortCtrl);
        Stream.setStreamingUI(true);

        // ── Optional legacy web search ────────────────────────
        let webResults = [];
        if (message && State.isWebSearchOn() && intent === 'general') {
            webResults = await _performWebSearch(message);
        }

        let promptText = message || 'Please analyse the attached file(s) and describe what you see in detail.';

        try {
            const res = await fetch('/chat', {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                signal:  abortCtrl.signal,
                body:    JSON.stringify({ prompt: promptText, model: State.getModel(), file_urls: fileUrls, web_results: webResults }),
            });
            if (!res.ok) throw new Error('Server error ' + res.status);

            thinking.remove();
            const { wrapper, botMsg } = UIFactory.createBotWrapper();
            chatbox.appendChild(wrapper);

            let toolUsed  = null;
            const fullReply = await Stream.read(botMsg, wrapper, res.body.getReader(), new TextDecoder(), chatbox, tu => { toolUsed = tu; });

            State.setAbortCtrl(null);
            Stream.setStreamingUI(false);

            // ── Tool badge ────────────────────────────────────
            if (toolUsed) {
                const BADGES = { clock: { icon: '🕐', label: 'Live Clock' }, weather: { icon: '🌤️', label: 'Live Weather' }, finance: { icon: '💹', label: 'Market Data' }, sports: { icon: '🏏', label: 'Live Scores' }, news: { icon: '📰', label: 'Latest News' }, web_search: { icon: '🔍', label: 'Web Search' }, wikipedia: { icon: '📖', label: 'Wikipedia' } };
                const badge = BADGES[toolUsed] || { icon: '🔧', label: toolUsed };
                const badgeDiv = document.createElement('div');
                badgeDiv.className = 'tool-badge';
                badgeDiv.setAttribute('data-tool', toolUsed);
                badgeDiv.innerHTML = `<span class="tool-badge-icon">${badge.icon}</span><span class="tool-badge-label">${badge.label} used</span>`;
                wrapper.insertBefore(badgeDiv, botMsg);
            }

            // ── Source chips ──────────────────────────────────
            if (fullReply && webResults.length) {
                const src = document.createElement('div');
                src.className = 'search-sources';
                src.innerHTML = `<span class="sources-label">🌐 Sources:</span>` + webResults.slice(0, 4).map(r => `<a href="${r.href}" target="_blank" rel="noopener" class="source-chip">${r.title}</a>`).join('');
                wrapper.appendChild(src);
            }

            // ── Persist bot message ───────────────────────────
            if (fullReply) {
                const { error: botError } = await DB.from(Config.DB.MESSAGES).insert([{
                    role:       'bot',
                    content:    fullReply,
                    session_id: State.getSessionId(),
                    user_id:    State.getUser().id,
                }]);
                if (botError) console.error('❌ Bot message save failed:', botError.message);
            }

        } catch (err) {
            State.setAbortCtrl(null);
            Stream.setStreamingUI(false);
            try { thinking.remove(); } catch (_) {}
            if (err?.name === 'AbortError') return;
            console.error('❌ AI fetch failed:', err);
            const last = chatbox.querySelector('.bot-msg-wrapper:last-child');
            if (!last || last.querySelector('.message.bot')?.innerHTML?.trim() === '') {
                const errMsg = document.createElement('div');
                errMsg.classList.add('message', 'bot');
                errMsg.innerHTML = `<p style="color:#e06c6c">⚠️ Failed to get a response. Please try again.</p>`;
                chatbox.appendChild(errMsg);
            }
        }
    }

    function handleSendOrStop() {
        if (State.isStreaming()) {
            State.getAbortCtrl()?.abort();
            State.setAbortCtrl(null);
            Stream.setStreamingUI(false);
            Toast.show('Response stopped', 1500);
        } else {
            send();
        }
    }

    return Object.freeze({ send, handleSendOrStop });
})();

// ─────────────────────────────────────────────────────────────
// 23. PUBLIC API — CaturaApp (replaces all window.* pollution)
//     Only this object is exposed on window.
// ─────────────────────────────────────────────────────────────
const CaturaApp = (() => {

    async function newChat() {
        DOM.settingsOverlay()?.classList.remove('active');
        ChatReset.resetToNewChat();
        if (window.innerWidth <= Config.BREAKPOINT) Sidebar.close();
        Menu.showMain();
        Toast.show('New chat started', 2000);
    }

    function init() {
        // ── Input auto-resize ────────────────────────────────
        const inputEl = DOM.input();
        if (inputEl) {
            const autoResize = () => { inputEl.style.height = 'auto'; inputEl.style.height = inputEl.scrollHeight + 'px'; };
            inputEl.addEventListener('input', autoResize);
            inputEl.addEventListener('keydown', e => {
                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); SendMessage.send(); }
            });
            window.autoResize = autoResize; // kept for useSuggestion compatibility
        }
    }

    return Object.freeze({ newChat, init });
})();

// ─────────────────────────────────────────────────────────────
// 24. GLOBAL BRIDGE
//     All onclick= attributes in the existing HTML reference
//     these names. We bind them once here cleanly.
// ─────────────────────────────────────────────────────────────
window.CaturaApp            = CaturaApp;

// Sidebar
window.toggleSidebar        = ()             => Sidebar.toggle();
window.closeSidebar         = ()             => Sidebar.close();
window.openSidebarTo        = (section)      => Sidebar.open(section);

// Chat
window.newChat              = ()             => CaturaApp.newChat();
window.sendMessage          = ()             => SendMessage.send();
window.handleSendOrStop     = ()             => SendMessage.handleSendOrStop();

// History / menu
window.showMainMenu         = ()             => Menu.showMain();
window.showHistory          = ()             => History.show();

// Settings
window.showSettings         = ()             => Settings.show();
window.closeSettings        = ()             => Settings.close();
window.showSettingsTab      = (tab, el)      => Settings.showTab(tab, el);

// Theme / font
window.setTheme             = (t)            => ThemeManager.setTheme(t);
window.setFontSize          = (s)            => ThemeManager.setFontSize(s);

// Auth
window.logoutUser           = ()             => Auth.logout();
window.editDisplayName      = ()             => Auth.editDisplayName();

// Modals
window.showPrivacyModal     = ()             => Modals.showPrivacy();
window.showDeleteAccountModal = ()           => Modals.showDeleteAccount();

// Chat management
window.archiveAllChats      = ()             => ChatActions.archiveAll();
window.clearAllChats        = ()             => ChatActions.deleteAll();

// Clipboard
window.copyCode             = (btn)          => Clipboard.copyCode(btn);
window.copyBotAnswer        = (btn)          => Clipboard.copyBotAnswer(btn);
window.copyUserMessage      = (btn)          => Clipboard.copyUserMessage(btn);

// Model selector
window.toggleModelSelector  = (e)            => ModelSelector.toggle(e);
window.selectModel          = (id, name, e)  => ModelSelector.select(id, name, e);
window.toggleMoreModels     = (e)            => ModelSelector.toggleMoreModels(e);
window.goBackToMainModels   = (e)            => ModelSelector.goBackToMain(e);

// Plus menu
window.togglePlusMenu       = (e)            => PlusMenu.toggle(e);
window.handlePlusAction     = (action)       => PlusMenu.handleAction(action);
window.toggleWebSearch      = ()             => PlusMenu.handleAction('search');

// Suggestions
window.useSuggestion        = (el)           => {
    const input = DOM.input();
    if (input) { input.value = el.innerText.trim(); input.focus(); window.autoResize?.(); }
};

// ─────────────────────────────────────────────────────────────
// 25. APP BOOT
// ─────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
    ThemeManager.initTheme();
    ThemeManager.initFontSize();
    PlusMenu.init();
    ModelSelector.init();
    Menu.showMain();

    const user = await Auth.loadUser();
    if (!user) { window.location.href = Config.AUTH_REDIRECT; return; }

    ChatReset.resetToNewChat(); // Sets greeting for fresh load
    CaturaApp.init();           // Binds input events
});
