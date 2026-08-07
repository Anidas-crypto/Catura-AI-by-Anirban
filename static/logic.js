// ============================
// 🐞 DEBUG LOGGING
// ============================
// Set to false (default) for production. Toggle at runtime via:
// localStorage.setItem('DEBUG','true')  then reload the page.
const DEBUG = (typeof localStorage !== 'undefined' && localStorage.getItem('DEBUG') === 'true');
const debugLog = DEBUG ? console.log.bind(console) : function () {};

// ============================
// 🎨 UNIVERSAL MODAL SYSTEM
// ============================
/**
 * showModal(options) — replaces all browser confirm/prompt/alert calls
 *
 * options {
 *   type: 'confirm' | 'prompt' | 'alert' | 'danger'
 *   icon: SVG string (optional)
 *   iconColor: hex (default #10a37f)
 *   title: string
 *   subtitle: string (optional, shown under title)
 *   message: string (optional, body text)
 *   inputValue: string (default value for prompt)
 *   inputPlaceholder: string
 *   inputLabel: string (label above input)
 *   inputNote: string (small text below input)
 *   inputType: 'text'|'email'|'password' (default 'text')
 *   confirmLabel: string (default 'Confirm')
 *   cancelLabel: string  (default 'Cancel')
 *   dangerous: bool — red confirm button
 *   onConfirm: function(value?) — called with input value or true
 *   onCancel: function() (optional)
 * }
 */
window.showModal = function(options) {
    const existing = document.getElementById('catura-modal-overlay');
    if (existing) existing.remove();

    const {
        type = 'confirm',
        icon = null,
        iconColor = '#10a37f',
        title = '',
        subtitle = '',
        message = '',
        inputValue = '',
        inputPlaceholder = '',
        inputLabel = '',
        inputNote = '',
        inputType = 'text',
        confirmLabel = type === 'alert' ? 'OK' : 'Confirm',
        cancelLabel = 'Cancel',
        dangerous = false,
        onConfirm = () => {},
        onCancel = () => {}
    } = options;

    const hasInput  = type === 'prompt';
    const hasCancel = type !== 'alert';

    const defaultIcon = hasInput
        ? `<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>`
        : dangerous
            ? `<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>`
            : `<circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>`;

    const iconSvg = icon || defaultIcon;
    const iconBg = dangerous ? 'rgba(224,108,108,0.12)' : `${iconColor}1a`;
    const iconStroke = dangerous ? '#e06c6c' : iconColor;

    const overlay = document.createElement('div');
    overlay.id = 'catura-modal-overlay';
    overlay.innerHTML = `
        <div class="cm-backdrop" id="cmBackdrop"></div>
        <div class="cm-dialog" role="dialog" aria-modal="true">
            <div class="cm-icon-wrap" style="background:${iconBg}">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="${iconStroke}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${iconSvg}</svg>
            </div>
            <div class="cm-body">
                <h3 class="cm-title">${title}</h3>
                ${subtitle ? `<p class="cm-subtitle">${subtitle}</p>` : ''}
                ${message ? `<p class="cm-message">${message}</p>` : ''}
                ${hasInput ? `
                    ${inputLabel ? `<label class="cm-input-label">${inputLabel}</label>` : ''}
                    <input id="cmInput" type="${inputType}" value="${inputValue.replace(/"/g,'&quot;')}"
                        placeholder="${inputPlaceholder}"
                        class="cm-input"
                        autocomplete="off"
                        spellcheck="false"
                    >
                    ${inputNote ? `<p class="cm-input-note">${inputNote}</p>` : ''}
                ` : ''}
            </div>
            <div class="cm-actions">
                ${hasCancel ? `<button class="cm-btn cm-btn-cancel" id="cmCancelBtn">${cancelLabel}</button>` : ''}
                <button class="cm-btn ${dangerous ? 'cm-btn-danger' : 'cm-btn-confirm'}" id="cmConfirmBtn">${confirmLabel}</button>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);
    requestAnimationFrame(() => overlay.classList.add('cm-visible'));

    const input = document.getElementById('cmInput');
    if (input) {
        input.focus();
        input.select();
        input.addEventListener('keydown', e => {
            if (e.key === 'Enter') doConfirm();
            if (e.key === 'Escape') doCancel();
        });
    }

    function doConfirm() {
        const val = input ? input.value.trim() : true;
        close();
        onConfirm(val);
    }
    function doCancel() {
        close();
        onCancel();
    }
    function close() {
        overlay.classList.remove('cm-visible');
        setTimeout(() => overlay.remove(), 300);
    }

    document.getElementById('cmConfirmBtn').onclick = doConfirm;
    if (hasCancel) document.getElementById('cmCancelBtn').onclick = doCancel;
    document.getElementById('cmBackdrop').onclick = doCancel;
    document.addEventListener('keydown', function escHandler(e) {
        if (e.key === 'Escape') { doCancel(); document.removeEventListener('keydown', escHandler); }
    });
};

// ============================
// ✅ SUPABASE SETUP
// ============================
const supabaseUrl = "https://zhrjmnrfklzuxmfbdqhg.supabase.co";
const supabaseKey = "sb_publishable_aIbByN1rFc9V3AH41Kyz6A_e1XppA1Z";
const supabaseClient = window.supabase.createClient(supabaseUrl, supabaseKey);

// ============================
// 🔑 USER-SCOPED LOCALSTORAGE
// ============================
/**
 * All per-user keys are namespaced by userId so that switching
 * accounts on the same browser never leaks data between users.
 *
 * Usage:
 *   userKey('catura_call_name')   → 'catura_call_name_abc123'
 *   getUserItem('catura_call_name')
 *   setUserItem('catura_call_name', value)
 *   removeUserItem('catura_call_name')
 */
function userKey(key) {
    const uid = currentUser?.id || '__anon__';
    return `${key}_${uid}`;
}
function getUserItem(key) {
    return localStorage.getItem(userKey(key));
}
function setUserItem(key, value) {
    localStorage.setItem(userKey(key), value);
}
function removeUserItem(key) {
    localStorage.removeItem(userKey(key));
}

/**
 * Remove the old non-namespaced keys so previous-user data
 * never bleeds into a freshly-logged-in session.
 */
function clearStaleGlobalKeys() {
    // Remove old non-namespaced keys so a previous user's data never bleeds in.
    // Also remove the old base64 profile_pic key (replaced by profile_pic_url).
    localStorage.removeItem('catura_call_name');
    localStorage.removeItem('catura_profile_pic');
    localStorage.removeItem('catura_profile_pic_url'); // old non-namespaced version
}

// ============================
// ☁️ SETTINGS SYNC (cross-device)
// ============================

/**
 * Save a single setting to Supabase user_settings table.
 * Uses upsert so it works whether the row exists or not.
 */
/**
 * Save ALL current settings to Supabase in a single upsert.
 * Call this after the user first logs in on a brand-new device
 * if no cloud row was found — so the cloud row gets created
 * from whatever the user configured locally.
 * Also used after bulk changes (e.g. import settings).
 */
async function saveAllSettingsToCloud() {
    if (!currentUser) return;
    try {
        const payload = {
            user_id:          currentUser.id,
            theme:            localStorage.getItem('catura-theme') || 'dark',
            font_size:        localStorage.getItem('catura-font') || 'default',
            user_msg_color:   localStorage.getItem('catura-usercolor') || 'green',
            call_name:        getUserItem('catura_call_name') || null,
            profile_pic_url:  getUserItem('catura_profile_pic_url') || null,
            profile_pic:      null, // legacy base64 cleared
            shortcuts:        JSON.parse(localStorage.getItem('catura-shortcuts') || 'null'),
            privacy_prefs:    JSON.parse(localStorage.getItem('catura-privacy-prefs') || 'null'),
            location_enabled: localStorage.getItem('catura_location_enabled') === '1',
            updated_at:       new Date().toISOString()
        };
        const { error } = await supabaseClient
            .from('user_settings')
            .upsert(payload, { onConflict: 'user_id' });
        if (error) console.warn('[Sync] saveAllSettingsToCloud error:', error.message);
        else debugLog('[Sync] All settings pushed to cloud ✓');
    } catch (err) {
        console.warn('[Sync] saveAllSettingsToCloud exception:', err);
    }
}

async function saveSettingToCloud(key, value) {
    if (!currentUser) return;
    try {
        const payload = {
            user_id: currentUser.id,
            [key]: value,
            updated_at: new Date().toISOString()
        };
        const { error } = await supabaseClient
            .from('user_settings')
            .upsert(payload, { onConflict: 'user_id' });
        if (error) console.warn('[Sync] saveSettingToCloud error:', error.message);
    } catch (err) {
        console.warn('[Sync] saveSettingToCloud exception:', err);
    }
}

/**
 * Load all settings from Supabase and apply them locally.
 * Called once after login. Local localStorage is updated so
 * all existing code reading localStorage still works.
 *
 * Cross-device guarantee:
 *   - theme, font_size, shortcuts, privacy_prefs, location_enabled
 *     are written to plain localStorage so they survive the next page
 *     load on this device without another cloud round-trip.
 *   - call_name and profile_pic_url are user-namespaced in localStorage
 *     (so multiple accounts on the same browser stay isolated).
 *   - profile_pic is stored as a public Supabase Storage URL so it works
 *     on every device without localStorage size limits.
 */
async function loadSettingsFromCloud() {
    if (!currentUser) return;
    try {
        const { data, error } = await supabaseClient
            .from('user_settings')
            .select('*')
            .eq('user_id', currentUser.id)
            .single();

        // PGRST116 = "no rows returned" — normal for a brand-new user or first device.
        // Anything else is a real error worth logging.
        if (error) {
            if (error.code !== 'PGRST116') {
                console.warn('[Sync] loadSettingsFromCloud error:', error.message);
            } else {
                debugLog('[Sync] No cloud settings yet — creating initial row from local state.');
                // Push whatever the user has configured locally so it syncs to other devices.
                // This handles: user configures settings on Device A before any row exists,
                // then logs in on Device B — Device A's settings will now sync.
                clearStaleGlobalKeys();
                // Kick off a background push (don't await — don't block the UI)
                saveAllSettingsToCloud().catch(e => console.warn('[Sync] Initial push failed:', e));
            }
            window._profilePicDataUrl = null;
            if (typeof _applyProfilePicToAllAvatars === 'function') _applyProfilePicToAllAvatars(null);
            return;
        }

        debugLog('[Sync] Settings loaded from cloud:', data);

        // Wipe leftover keys from any previous user on this browser
        clearStaleGlobalKeys();

        // call_name (greeting nickname) — user-namespaced
        if (data.call_name !== null && data.call_name !== undefined) {
            setUserItem('catura_call_name', data.call_name);
        } else {
            removeUserItem('catura_call_name');
        }

        // theme
        if (data.theme) {
            localStorage.setItem('catura-theme', data.theme);
            if (typeof applyTheme === 'function') applyTheme(data.theme);
        }

        // font_size
        if (data.font_size) {
            localStorage.setItem('catura-font', data.font_size);
            if (typeof applyFontSize === 'function') applyFontSize(data.font_size);
        }

        // user message color
        if (data.user_msg_color) {
            localStorage.setItem('catura-usercolor', data.user_msg_color);
            if (typeof applyUserMsgColor === 'function') applyUserMsgColor(data.user_msg_color);
        }

        // profile_pic — stored as Supabase Storage public URL (not base64)
        // Falls back to legacy base64 blob in data.profile_pic for old rows.
        const picUrl = data.profile_pic_url || data.profile_pic || null;
        if (picUrl) {
            setUserItem('catura_profile_pic_url', picUrl);
            window._profilePicDataUrl = picUrl;
            if (typeof _applyProfilePicToAllAvatars === 'function') {
                _applyProfilePicToAllAvatars(picUrl);
            }
        } else {
            removeUserItem('catura_profile_pic_url');
            window._profilePicDataUrl = null;
            if (typeof _applyProfilePicToAllAvatars === 'function') {
                _applyProfilePicToAllAvatars(null);
            }
        }

        // shortcuts
        if (data.shortcuts) {
            localStorage.setItem('catura-shortcuts', JSON.stringify(data.shortcuts));
        }

        // privacy_prefs
        if (data.privacy_prefs) {
            localStorage.setItem('catura-privacy-prefs', JSON.stringify(data.privacy_prefs));
        }

        // location toggle
        if (data.location_enabled !== null && data.location_enabled !== undefined) {
            localStorage.setItem('catura_location_enabled', data.location_enabled ? '1' : '0');
        }

    } catch (err) {
        console.warn('[Sync] loadSettingsFromCloud exception:', err);
    }
}

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

        const avatarEl  = document.getElementById("userAvatar");
        const nameEl    = document.getElementById("userFullname");
        const railAvatar = document.getElementById("railAvatar");

        if (avatarEl)   avatarEl.textContent  = initials;
        if (nameEl)     nameEl.textContent     = fullName;
        if (railAvatar) railAvatar.textContent = initials;

        // ☁️ Load synced settings from Supabase after login.
        // This applies theme, font, profile pic, call_name from the cloud.
        // It also calls displayGreeting() after applying call_name so the
        // greeting always shows the correct nickname from the cloud.
        await loadSettingsFromCloud();

        // Re-render greeting with the freshly-resolved user data.
        // (Safe to call even if loadSettingsFromCloud already called it.)
        if (typeof displayGreeting === 'function') displayGreeting();
        // 🧠 Load memory toggle state + saved memories from Supabase
        await loadMemoryState();

        // 📜 Chat History accordion is open by default on load.
        // After this, it is fully manual — only toggleHistoryAccordion() (user click)
        // opens/closes it. Nothing else in the app should auto-close or auto-open it again.
        if (typeof window.showHistory === 'function') window.showHistory();
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
// 👻 GHOST CHAT STATE
// ============================
let ghostChatEnabled = false;
let ghostMemory = [];          // sliding window — max 12 exchanges (24 messages)
const GHOST_WINDOW = 24;       // 12 user + 12 bot = 24 messages kept

// ============================
// 🧠 MEMORY STATE
// ============================
let memoryEnabled = false;
let userMemories = [];  // array of memory strings loaded from Supabase

// ============================
// ✏️ EDIT-MESSAGE STATE
// ============================
// Tracks an in-progress edit: which turn-group id was edited
let pendingEditGroupId = null;

async function loadMemoryState() {
    if (!currentUser) return;
    try {
        const { data, error } = await supabaseClient
            .from('user_settings')
            .select('memory_enabled')
            .eq('user_id', currentUser.id)
            .single();
        if (!error && data) {
            memoryEnabled = data.memory_enabled || false;
        }
        // Sync toggle UI if settings panel is open
        const toggle = document.getElementById('memoryToggle');
        if (toggle) toggle.checked = memoryEnabled;
        if (memoryEnabled) await loadUserMemories();
    } catch (e) { console.warn('[Memory] loadMemoryState error:', e); }
}

async function loadUserMemories() {
    if (!currentUser) return;
    try {
        const headers = await _authHeaders();
        if (!headers) return;
        const resp = await fetch(`/api/memory/load`, { headers });
        const data = await resp.json();
        if (data.ok) userMemories = (data.memories || []).map(m => m.memory_text);
    } catch (e) { console.warn('[Memory] loadUserMemories error:', e); }
}

// ── Shared helper: builds headers with the current Supabase bearer token ────
// Memory endpoints require auth; the server derives user_id from this token
// rather than trusting anything the client sends.
async function _authHeaders(extra = {}) {
    try {
        const { data: sessionData } = await supabaseClient.auth.getSession();
        const accessToken = sessionData?.session?.access_token;
        if (!accessToken) return null;
        return { ...extra, "Authorization": `Bearer ${accessToken}` };
    } catch (e) {
        console.warn('[Memory] could not get auth session:', e);
        return null;
    }
}

async function saveMemoryEnabled(enabled) {
    if (!currentUser) return;
    try {
        await supabaseClient.from('user_settings').upsert(
            { user_id: currentUser.id, memory_enabled: enabled, updated_at: new Date().toISOString() },
            { onConflict: 'user_id' }
        );
    } catch (e) { console.warn('[Memory] saveMemoryEnabled error:', e); }
}

window.toggleMemoryEnabled = async function(checked) {
    memoryEnabled = checked;
    await saveMemoryEnabled(checked);
    if (checked) {
        await loadUserMemories();
        showToast('✓ Memory enabled — Catura will remember things you share');
    } else {
        userMemories = [];
        showToast('Memory disabled — info is only used in the current chat');
    }
};

async function maybeExtractAndSaveMemory(userMessage) {
    if (!memoryEnabled || !currentUser || ghostChatEnabled) return;
    if (!userMessage || userMessage.trim().length < 3) return;

    // ── Wider pre-filter: catch natural intro-style messages too ────────────
    const personalSignals = [
        /\bmy\b/i, /\bi'?m\b/i, /\bi am\b/i, /\bmyself\b/i,
        /\bcall me\b/i, /\bname'?s?\b/i, /\bi work\b/i, /\bi live\b/i,
        /\bi speak\b/i, /\bi like\b/i, /\bi love\b/i, /\bi hate\b/i,
        /\bi enjoy\b/i, /\bi prefer\b/i, /\bi study\b/i, /\bi learn\b/i,
        /\bi'?m from\b/i, /\bborn in\b/i, /\bage\b/i, /\byears old\b/i,
        /\bmy goal\b/i, /\bmy dream\b/i, /\bmy job\b/i, /\bmy hobby\b/i,
        /\bmy fav(ou?rite)?\b/i, /\bmy project\b/i, /\bmy name\b/i,
        /\bim\b/i,          // catches "im anirban" (no apostrophe)
        /\bi do\b/i,        // "i do machine learning"
        /\bi use\b/i,       // "i use python"
        /\bi build\b/i,     // "i build apps"
        /\bi know\b/i,      // "i know javascript"
        /\bi can\b/i,       // "i can code"
        /\bi have\b/i,      // "i have 3 years of experience"
        /\bwe are\b/i,      // "we are from kolkata"
        /^[a-z]+\s+[a-z]/i  // bare intro like "anirban here" (short opener)
    ];
    const mightBePersonal = personalSignals.some(p => p.test(userMessage));
    if (!mightBePersonal) return;

    // ── AI-powered extraction via /api/memory/extract ─────────────────────────
    try {
        const headers = await _authHeaders({ 'Content-Type': 'application/json' });
        if (!headers) { await _memoryDirectFallback(userMessage); return; }
        const resp = await fetch('/api/memory/extract', {
            method: 'POST',
            headers,
            body: JSON.stringify({
                message: userMessage.trim().slice(0, 800),
                existing_memories: userMemories.slice(0, 30)
            })
        });
        const data = await resp.json();
        if (data.ok && data.facts && data.facts.length > 0) {
            for (const fact of data.facts) {
                if (!fact) continue;
                const factLower = fact.toLowerCase().trim();
                const alreadyKnown = userMemories.some(m =>
                    m.toLowerCase().trim() === factLower ||
                    m.toLowerCase().includes(factLower.slice(0, 30))
                );
                if (!alreadyKnown) {
                    userMemories.push(fact);
                    debugLog('[Memory] ✅ AI extracted & saved:', fact);
                }
            }
        } else {
            // AI extraction failed or returned nothing — use direct regex fallback
            console.warn('[Memory] AI extraction returned ok:false or empty, using direct fallback');
            await _memoryDirectFallback(userMessage);
        }
    } catch (e) {
        console.warn('[Memory] extract network error:', e);
        await _memoryDirectFallback(userMessage);
    }
}

// ── Direct memory fallback: regex extracts name/location, saves via backend ──
// Called when AI extraction fails. Guaranteed path: no model, no rate limits.
async function _memoryDirectFallback(userMessage) {
    if (!currentUser || !memoryEnabled || ghostChatEnabled) return;
    const factsToSave = [];

    // Name extraction
    const nameRx = [
        /(?:my name is|i am|i'?m|call me|name'?s?)\s+([A-Z][a-z]{1,20}(?:\s+[A-Z][a-z]{1,20})?)/i,
        /(?:^|\s)([A-Z][a-z]{2,15}\s+[A-Z][a-z]{2,15})(?:\s+here|\s*$)/,
    ];
    for (const rx of nameRx) {
        const m = userMessage.match(rx);
        if (m && m[1] && m[1].trim().length > 2) {
            const name = m[1].trim();
            if (!userMemories.some(mem => mem.toLowerCase().includes(name.toLowerCase()))) {
                factsToSave.push(`The user's name is ${name}`);
            }
            break;
        }
    }

    // Location extraction
    const locRx = /(?:i'?m from|i live in|i am from|from)\s+([A-Z][a-z]+(?:[\s,]+[A-Z][a-z]+)*)/i;
    const locM = userMessage.match(locRx);
    if (locM && locM[1]) {
        const loc = locM[1].trim();
        if (!userMemories.some(mem => mem.toLowerCase().includes(loc.toLowerCase()))) {
            factsToSave.push(`The user is from ${loc}`);
        }
    }

    // Education extraction
    const eduRx = /(?:i study|i'?m studying|i do|i am in|enrolled in)\s+([A-Za-z0-9\s]{3,50})/i;
    const eduM = userMessage.match(eduRx);
    if (eduM && eduM[1]) {
        const edu = eduM[1].trim();
        if (!userMemories.some(mem => mem.toLowerCase().includes(edu.toLowerCase()))) {
            factsToSave.push(`The user studies ${edu}`);
        }
    }

    // Save each fact via backend save-direct endpoint
    for (const fact of factsToSave) {
        try {
            const headers = await _authHeaders({ 'Content-Type': 'application/json' });
            if (!headers) continue;
            const r = await fetch('/api/memory/save-direct', {
                method: 'POST',
                headers,
                body: JSON.stringify({ memory_text: fact })
            });
            const d = await r.json();
            if (d.ok) {
                userMemories.push(fact);
                debugLog('[Memory] ✅ fallback direct-saved:', fact);
            }
        } catch (saveErr) {
            // Last resort: write directly via authenticated Supabase client
            try {
                const { error } = await supabaseClient.from('user_memories').insert({
                    user_id: currentUser.id,
                    memory_text: fact,
                    created_at: new Date().toISOString()
                });
                if (!error) {
                    userMemories.push(fact);
                    debugLog('[Memory] ✅ fallback supabase-direct saved:', fact);
                }
            } catch (_) {}
        }
    }
}

function escapeHtml(str) {
    return String(str ?? '').replace(/[&<>"']/g, (c) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
    }[c]));
}

window.clearAllMemories = async function() {
    if (!currentUser) { showToast('Please log in first'); return; }
    showModal({
        type: 'confirm', dangerous: true,
        icon: `<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4h6v2"/>`,
        title: 'Clear all memories',
        subtitle: 'This cannot be undone',
        message: 'Catura will forget everything it has learned about you.',
        confirmLabel: 'Clear all',
        onConfirm: async () => {
            try {
                const headers = await _authHeaders({ 'Content-Type': 'application/json' });
                if (!headers) { showToast('❌ Please log in again'); return; }
                const resp = await fetch('/api/memory/clear', {
                    method: 'DELETE',
                    headers,
                    body: JSON.stringify({})
                });
                if ((await resp.json()).ok) {
                    userMemories = [];
                    showToast('✓ All memories cleared');
                    // Refresh the panel if it's open
                    const activeItem = document.querySelector('.settings-nav-item.active');
                    if (activeItem?.getAttribute('onclick')?.includes('personalization')) activeItem.click();
                } else { showToast('❌ Failed to clear memories'); }
            } catch (e) { showToast('❌ Error clearing memories'); }
        }
    });
};

window.viewSavedMemories = async function() {
    if (!currentUser) { showToast('Please log in first'); return; }
    try {
        const headers = await _authHeaders();
        if (!headers) { showToast('❌ Please log in again'); return; }
        const resp = await fetch(`/api/memory/load`, { headers });
        const data = await resp.json();
        const memories = data.memories || [];
        if (memories.length === 0) {
            showModal({ type: 'alert', title: 'No memories saved', message: 'Catura has not learned anything about you yet. Enable memory and share things about yourself — it will start remembering.', confirmLabel: 'OK' });
            return;
        }
        const existing = document.getElementById('memoryViewerModal');
        if (existing) existing.remove();
        const modal = document.createElement('div');
        modal.id = 'memoryViewerModal';
        modal.style.cssText = 'position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;';
        modal.innerHTML = `
            <div style="position:absolute;inset:0;background:rgba(0,0,0,0.65);" onclick="document.getElementById('memoryViewerModal').remove()"></div>
            <div style="position:relative;background:var(--bg-modal,#1a1a1a);border:1px solid var(--border,#2a2a2a);border-radius:14px;padding:24px;width:min(480px,90vw);max-height:70vh;display:flex;flex-direction:column;gap:16px;z-index:1;">
                <div style="display:flex;align-items:center;justify-content:space-between;">
                    <h3 style="margin:0;font-size:16px;font-weight:600;color:var(--text-primary,#e5e5e5);">🧠 Saved Memories (${memories.length})</h3>
                    <button onclick="document.getElementById('memoryViewerModal').remove()" style="background:none;border:none;cursor:pointer;color:var(--text-muted,#888);font-size:18px;">✕</button>
                </div>
                <p style="margin:0;font-size:12px;color:var(--text-muted,#888);">Facts Catura has remembered about you. Click ✕ to delete individual ones.</p>
                <div style="overflow-y:auto;display:flex;flex-direction:column;gap:8px;max-height:50vh;">
                    ${memories.map(m => `
                        <div style="display:flex;align-items:flex-start;gap:10px;padding:10px 12px;background:var(--bg-surface2,#161616);border-radius:8px;border:1px solid var(--border-subtle,#1e1e1e);">
                            <span style="font-size:13px;color:var(--text-secondary,#ccc);flex:1;line-height:1.5;">${escapeHtml(m.memory_text)}</span>
                            <button onclick="deleteOneMemory('${m.id}',this)" title="Delete" style="background:none;border:none;cursor:pointer;color:#e06c6c;font-size:16px;padding:0;flex-shrink:0;line-height:1;">✕</button>
                        </div>
                    `).join('')}
                </div>
                <div style="display:flex;justify-content:flex-end;gap:8px;">
                    <button onclick="clearAllMemories()" style="padding:8px 14px;background:rgba(224,108,108,0.12);border:1px solid rgba(224,108,108,0.3);border-radius:8px;color:#e06c6c;font-size:13px;cursor:pointer;">Clear All</button>
                    <button onclick="document.getElementById('memoryViewerModal').remove()" style="padding:8px 14px;background:var(--accent,#10a37f);border:none;border-radius:8px;color:#fff;font-size:13px;cursor:pointer;">Done</button>
                </div>
            </div>`;
        document.body.appendChild(modal);
    } catch (e) { showToast('❌ Failed to load memories'); }
};

window.deleteOneMemory = async function(id, btn) {
    if (!currentUser) return;
    try {
        const deletedText = btn.closest('div[style]')?.querySelector('span')?.textContent?.trim();
        const headers = await _authHeaders();
        if (!headers) { showToast('❌ Please log in again'); return; }
        const resp = await fetch(`/api/memory/delete-one?id=${id}`, { method: 'DELETE', headers });
        if ((await resp.json()).ok) {
            btn.closest('div[style]').remove();
            // Properly remove from local array so memory context stays in sync
            if (deletedText) userMemories = userMemories.filter(m => m.trim() !== deletedText);
            showToast('✓ Memory deleted');
        }
    } catch (e) { showToast('❌ Failed to delete memory'); }
};

// ============================================================
// 🛠️ SKILLS FEATURE — frontend (Stage 2 + chat-triggered install)
// ============================================================

// Keep a lightweight in-memory cache so the settings row subtitle
// and the panel don't both have to hit the network every time.
let _skillsCache = null;

async function _fetchSkills(forceRefresh = false) {
    if (!currentUser) return [];
    if (_skillsCache && !forceRefresh) return _skillsCache;
    try {
        const headers = await _authHeaders();
        if (!headers) return [];
        const resp = await fetch('/api/skills/list', { headers });
        const data = await resp.json();
        _skillsCache = data.skills || [];
        return _skillsCache;
    } catch (e) {
        console.warn('[Skills] list fetch error:', e);
        return [];
    }
}

window.refreshSkillsCountSub = async function () {
    const sub = document.getElementById('skillsCountSub');
    if (!sub) return;
    const skills = await _fetchSkills();
    const enabledCount = skills.filter(s => s.enabled).length;
    sub.textContent = skills.length
        ? `${enabledCount} of ${skills.length} skill${skills.length === 1 ? '' : 's'} enabled`
        : 'Install and manage skills Catura can use';
};

// ── Terminal-style install + manage panel ──────────────────────────────────
window.openSkillsPanel = async function () {
    if (!currentUser) { showToast('Please log in first'); return; }
    const existing = document.getElementById('skillsPanelModal');
    if (existing) existing.remove();

    const modal = document.createElement('div');
    modal.id = 'skillsPanelModal';
    modal.style.cssText = 'position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;';
    modal.innerHTML = `
        <div style="position:absolute;inset:0;background:rgba(0,0,0,0.65);" onclick="document.getElementById('skillsPanelModal').remove()"></div>
        <div style="position:relative;background:var(--bg-modal,#1a1a1a);border:1px solid var(--border,#2a2a2a);border-radius:14px;padding:24px;width:min(560px,92vw);max-height:80vh;display:flex;flex-direction:column;gap:16px;z-index:1;">
            <div style="display:flex;align-items:center;justify-content:space-between;">
                <h3 style="margin:0;font-size:16px;font-weight:600;color:var(--text-primary,#e5e5e5);">🛠️ Skills</h3>
                <button onclick="document.getElementById('skillsPanelModal').remove()" style="background:none;border:none;cursor:pointer;color:var(--text-muted,#888);font-size:18px;">✕</button>
            </div>
            <p style="margin:0;font-size:12px;color:var(--text-muted,#888);">Paste a skill link below to install it. Catura will use it automatically when relevant.</p>

            <!-- Terminal-style install box -->
            <div style="background:#0d0d0d;border:1px solid #2a2a2a;border-radius:10px;padding:12px 14px;font-family:'SF Mono',Consolas,monospace;">
                <div style="display:flex;align-items:center;gap:8px;color:#6ee7a7;font-size:12px;margin-bottom:8px;">
                    <span>$</span><span>skills install</span>
                </div>
                <input id="skillInstallInput" type="text" placeholder="https://raw.githubusercontent.com/.../SKILL.md"
                    style="width:100%;background:#161616;border:1px solid #2a2a2a;border-radius:6px;padding:9px 10px;color:#e5e5e5;font-size:13px;font-family:inherit;outline:none;box-sizing:border-box;"
                    onkeydown="if(event.key==='Enter') installSkillFromPanel()">
                <button onclick="installSkillFromPanel()" id="skillInstallBtn"
                    style="margin-top:10px;width:100%;padding:9px;background:#10a37f;border:none;border-radius:6px;color:#fff;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;">
                    Install
                </button>
                <p id="skillInstallStatus" style="margin:8px 0 0;font-size:12px;color:#888;min-height:14px;"></p>
            </div>

            <div style="display:flex;align-items:center;justify-content:space-between;">
                <span style="font-size:12px;font-weight:600;color:var(--text-muted,#888);text-transform:uppercase;letter-spacing:0.04em;">Installed skills</span>
            </div>
            <div id="skillsListContainer" style="overflow-y:auto;display:flex;flex-direction:column;gap:8px;max-height:40vh;">
                <p style="font-size:13px;color:var(--text-muted,#888);">Loading…</p>
            </div>
        </div>`;
    document.body.appendChild(modal);
    await _renderSkillsList();
    document.getElementById('skillInstallInput')?.focus();
};

async function _renderSkillsList() {
    const container = document.getElementById('skillsListContainer');
    if (!container) return;
    const skills = await _fetchSkills(true);
    if (skills.length === 0) {
        container.innerHTML = `<p style="font-size:13px;color:var(--text-muted,#888);">No skills installed yet.</p>`;
        return;
    }
    container.innerHTML = skills.map(s => `
        <div style="display:flex;align-items:flex-start;gap:10px;padding:10px 12px;background:var(--bg-surface2,#161616);border-radius:8px;border:1px solid var(--border-subtle,#1e1e1e);">
            <div style="flex:1;min-width:0;">
                <div style="display:flex;align-items:center;gap:6px;">
                    <span style="font-size:13px;font-weight:600;color:var(--text-primary,#e5e5e5);">${escapeHtml(s.name)}</span>
                    ${s.is_preinstalled ? '<span style="font-size:10px;padding:1px 6px;border-radius:4px;background:rgba(16,163,127,0.15);color:#10a37f;">preinstalled</span>' : ''}
                </div>
                <p style="margin:2px 0 0;font-size:12px;color:var(--text-muted,#888);line-height:1.4;">${escapeHtml(s.description || '')}</p>
            </div>
            <label class="toggle-switch" title="Enable/disable" style="flex-shrink:0;">
                <input type="checkbox" ${s.enabled ? 'checked' : ''} onchange="toggleSkillEnabled('${s.id}', this.checked)">
                <span class="toggle-slider"></span>
            </label>
            ${!s.is_preinstalled ? `<button onclick="deleteSkillFromPanel('${s.id}')" title="Remove" style="background:none;border:none;cursor:pointer;color:#e06c6c;font-size:15px;padding:0;flex-shrink:0;line-height:1.4;">✕</button>` : ''}
        </div>
    `).join('');
}

window.installSkillFromPanel = async function () {
    const input = document.getElementById('skillInstallInput');
    const status = document.getElementById('skillInstallStatus');
    const btn = document.getElementById('skillInstallBtn');
    const url = input?.value.trim();
    if (!url) { if (status) status.textContent = 'Paste a skill link first.'; return; }

    if (btn) { btn.disabled = true; btn.textContent = 'Installing…'; }
    if (status) { status.style.color = '#888'; status.textContent = 'Fetching and parsing skill…'; }

    const result = await _installSkill(url);

    if (btn) { btn.disabled = false; btn.textContent = 'Install'; }
    if (status) {
        status.style.color = result.ok ? '#6ee7a7' : '#e06c6c';
        status.textContent = result.ok ? `✓ Installed: ${result.skill?.name || 'skill'}` : `✗ ${result.error || 'Install failed'}`;
    }
    if (result.ok) {
        if (input) input.value = '';
        await _renderSkillsList();
        await refreshSkillsCountSub();
    }
};

// Shared install call used by BOTH the panel and the chat-triggered flow
async function _installSkill(sourceUrl) {
    if (!currentUser) return { ok: false, error: 'Please log in first' };
    try {
        const headers = await _authHeaders({ 'Content-Type': 'application/json' });
        if (!headers) return { ok: false, error: 'Please log in again' };
        const resp = await fetch('/api/skills/install', {
            method: 'POST',
            headers,
            body: JSON.stringify({ source_url: sourceUrl })
        });
        const data = await resp.json();
        return data;
    } catch (e) {
        console.warn('[Skills] install error:', e);
        return { ok: false, error: 'Network error while installing skill' };
    }
}

window.toggleSkillEnabled = async function (id, enabled) {
    try {
        const headers = await _authHeaders({ 'Content-Type': 'application/json' });
        if (!headers) { showToast('❌ Please log in again'); return; }
        const resp = await fetch('/api/skills/toggle', {
            method: 'POST',
            headers,
            body: JSON.stringify({ skill_id: id, enabled })
        });
        const data = await resp.json();
        if (data.ok) {
            if (_skillsCache) {
                const s = _skillsCache.find(x => x.id === id);
                if (s) s.enabled = enabled;
            }
            await refreshSkillsCountSub();
            showToast(enabled ? '✓ Skill enabled' : 'Skill disabled');
        } else {
            showToast('❌ Failed to update skill');
        }
    } catch (e) { showToast('❌ Failed to update skill'); }
};

window.deleteSkillFromPanel = async function (id) {
    showModal({
        type: 'confirm', dangerous: true,
        title: 'Remove skill',
        subtitle: 'This cannot be undone',
        message: 'Catura will no longer use this skill.',
        confirmLabel: 'Remove',
        onConfirm: async () => {
            try {
                const headers = await _authHeaders();
                if (!headers) { showToast('❌ Please log in again'); return; }
                const resp = await fetch(`/api/skills/delete?id=${id}`, { method: 'DELETE', headers });
                const data = await resp.json();
                if (data.ok) {
                    showToast('✓ Skill removed');
                    await _renderSkillsList();
                    await refreshSkillsCountSub();
                } else {
                    showToast('❌ Failed to remove skill');
                }
            } catch (e) { showToast('❌ Failed to remove skill'); }
        }
    });
};

// ── Chat-triggered install: "install <link>" typed directly in the message box ──
// Matches a URL together with an install-intent word/phrase, anywhere in the message.
// Returns true if it handled (and short-circuited) the send — caller should stop.
const _SKILL_URL_RX = /(https?:\/\/[^\s]+)/i;
const _SKILL_INSTALL_INTENT_RX = /\b(install|add)\b.{0,20}\b(skill|this)\b|\binstall\b.{0,60}(https?:\/\/)/i;

async function _maybeHandleChatSkillInstall(message) {
    if (!message) return false;
    const urlMatch = message.match(_SKILL_URL_RX);
    if (!urlMatch) return false;
    if (!_SKILL_INSTALL_INTENT_RX.test(message)) return false;

    const url = urlMatch[1].replace(/[),.]+$/, ''); // strip trailing punctuation
    showToast('🛠️ Installing skill…');
    const result = await _installSkill(url);
    _skillsCache = null; // force refresh next time panel/subtitle is read
    if (result.ok) {
        showToast(`✓ Installed skill: ${result.skill?.name || 'skill'} — Catura will use it when relevant`);
    } else {
        showToast(`❌ Couldn't install skill: ${result.error || 'unknown error'}`);
    }
    return true;
}

window.toggleGhostChat = function () {
    ghostChatEnabled = !ghostChatEnabled;
    const btn        = document.getElementById('ghostChatBtn');
    const mobileBtn  = document.getElementById('mobileGhostBtn');
    const banner     = document.getElementById('ghostBanner');

    if (ghostChatEnabled) {
        ghostMemory = [];
        btn?.classList.add('ghost-active');
        mobileBtn?.classList.add('ghost-active');
        if (banner) banner.style.display = 'flex';
        document.getElementById('app')?.classList.add('ghost-mode');
        newChat();
        showToast('Ghost Chat ON — nothing will be saved');
    } else {
        ghostMemory = [];
        btn?.classList.remove('ghost-active');
        mobileBtn?.classList.remove('ghost-active');
        if (banner) banner.style.display = 'none';
        document.getElementById('app')?.classList.remove('ghost-mode');
        newChat();
        showToast('Ghost Chat OFF — back to normal');
    }
};


function getTimeOfDay() {
    const hour = new Date().getHours();
    
    if (hour >= 5 && hour < 12) return "morning";
    if (hour >= 12 && hour < 17) return "afternoon";
    if (hour >= 17 && hour < 21) return "evening";
    return "night";
}

function getGreetingMessage(userName) {
    const timeOfDay = getTimeOfDay();
    const greetings = {
        morning: [
            `Good morning, ${userName}! ☀️`,
            `Rise and shine, ${userName}!`,
            `Morning, ${userName}! ☕`,
            `Hey ${userName}, good morning!`,
            `${userName}, the morning is full of possibilities.`,
            `Morning! Let's make it a great one, ${userName}!`,
            `Good morning! What's on your mind today?`,
            `Top of the morning, ${userName}!`,
            `A fresh start — good morning, ${userName}! 🌅`,
        ],
        afternoon: [
            `Good afternoon, ${userName}! 🌤️`,
            `Afternoon, ${userName}! Let's keep the momentum going.`,
            `Hey ${userName}, hope the day's treating you well!`,
            `${userName}! Afternoon mode: ON. ✅`,
            `Halfway through — good afternoon, ${userName}!`,
            `Afternoon, ${userName}! Ready when you are.`,
            `Good afternoon! What's on your mind, ${userName}?`,
            `${userName}, the afternoon is yours. 🌤️`,
            `Productivity hour, ${userName}! 🚀`,
        ],
        evening: [
            `Good evening, ${userName}! 🌙`,
            `Evening, ${userName}! Ready to wind down or dive in?`,
            `Hey ${userName}, good evening! ✨`,
            `${userName}, the evening is yours. 🌇`,
            `Good evening! What's on your mind tonight?`,
            `Evening, ${userName}! Let's get started.`,
            `Hey ${userName}! Good evening. 🌃`,
            `Evenings are for deep work, ${userName}.`,
            `Evening check-in, ${userName}! 🌆`,
        ],
        night: [
            `Still up, ${userName}? 🌌`,
            `Burning the midnight oil, ${userName}?`,
            `Late night session, ${userName}! 🦉`,
            `Night mode: ON, ${userName}. 🔦`,
            `Can't sleep, ${userName}? Let's make it productive. 😄`,
            `${userName}, the night is young. ✨`,
            `Late night, big ideas, ${userName}! 🌠`,
            `Hey ${userName}! Moon's out. 🌕`,
            `${userName}, midnight hustle! 💻`,
        ]
    };
    
    const greetingList = greetings[timeOfDay];
    return greetingList[Math.floor(Math.random() * greetingList.length)];
}

function displayGreeting() {
    const userNameEl = document.getElementById("userFullname");
    // 1. Use the per-user nickname if set
    const nickname = getUserItem("catura_call_name");
    // 2. Otherwise fall back to the display name ("Edit display name" value)
    const userName = (nickname && nickname.trim())
        ? nickname.trim()
        : (userNameEl?.textContent?.trim() || "User");

    const isGhost = typeof ghostChatEnabled !== 'undefined' && ghostChatEnabled;

    const greetingText = isGhost
        ? " You saw nothing. Ghost Mode enabled."
        : getGreetingMessage(userName);

    const subText = isGhost
        ? "Nothing is saved. Nothing is remembered."
        : "How can I help you today?";

    // Colors: ghost = red palette, normal = green palette
    const accentColor  = isGhost ? "#e05555"   : "#10a37f";
    const accentColor2 = isGhost ? "#c03030"   : "#0d8c6d";
    const subColor     = isGhost ? "#a06060"   : "#888";
    const bgGradient   = isGhost
        ? "linear-gradient(135deg, #e0555511 0%, #c0303011 100%)"
        : "linear-gradient(135deg, #10a37f11 0%, #0d8c6d11 100%)";
    const borderColor  = isGhost ? "#e0555522" : "#10a37f22";

    // Create greeting element
    const greetingDiv = document.createElement("div");
    greetingDiv.style.cssText = `
        text-align: center;
        margin-top: 20px;
        margin-bottom: 30px;
        padding: 20px;
        background: ${bgGradient};
        border: 1px solid ${borderColor};
        border-radius: 12px;
        animation: fadeIn 0.6s ease-in-out;
    `;
    greetingDiv.innerHTML = `
        <div style="font-size: 24px; font-weight: 600; color: ${accentColor}; letter-spacing: -0.02em;">
            ${greetingText}
        </div>
        <div style="font-size: 14px; color: ${subColor}; margin-top: 8px;">
            ${subText}
        </div>
    `;

    const chatbox = document.getElementById("chatbox");
    const app = document.getElementById("app");
    if (chatbox) {
        chatbox.innerHTML = "";
        chatbox.appendChild(greetingDiv);
    }
    if (app) app.classList.add("greeting-mode");
}

// ============================
// 🔀 SIDEBAR TOGGLE
// ============================
function toggleSidebar() {
    const sidebar = document.getElementById("sidebar");
    const iconRail = document.getElementById("iconRail");
    const overlay  = document.getElementById("sidebarOverlay");
    const hamburger = document.querySelector(".mobile-hamburger");

    const isMobile = window.innerWidth <= 768;

    if (isMobile) {
        sidebar.classList.toggle("open");
        overlay.classList.toggle("show");
        if (hamburger) hamburger.classList.toggle("is-open", sidebar.classList.contains("open"));
    } else {
        const isOpen = sidebar.classList.contains("open");
        if (isOpen) {
            sidebar.classList.remove("open");
            iconRail.classList.add("visible");
        } else {
            sidebar.classList.add("open");
            iconRail.classList.remove("visible");
        }
    }
}

function closeSidebar() {
    const sidebar  = document.getElementById("sidebar");
    const overlay  = document.getElementById("sidebarOverlay");
    const hamburger = document.querySelector(".mobile-hamburger");
    sidebar.classList.remove("open");
    overlay.classList.remove("show");
    if (hamburger) hamburger.classList.remove("is-open");
}

window.openSidebarTo = function (section) {
    const sidebar  = document.getElementById("sidebar");
    const iconRail = document.getElementById("iconRail");

    if (window.innerWidth <= 768) {
        sidebar.classList.add("open");
        document.getElementById("sidebarOverlay").classList.add("show");
    } else {
        sidebar.classList.add("open");
        iconRail.classList.remove("visible");
    }

    if (section === 'history') showHistory();
};

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
// �� QUERY COMPLEXITY DETECTOR
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
// 🩹 REPAIR TRUNCATED RESPONSES
// ============================
// If the AI was cut off mid-output (token limit or relay handoff),
// the response may end with an unclosed code fence (```).
// This closes any dangling fences so the markdown renderer doesn't break.
function repairTruncated(text) {
    const fenceMatches = (text.match(/```/g) || []).length;
    // Odd number of ``` means one is unclosed
    if (fenceMatches % 2 !== 0) {
        // Close it — append newline + closing fence
        text = text.trimEnd() + "\n```";
    }
    return text;
}

// ============================
// 🧾 MARKDOWN RENDERER — v3
// Full ChatGPT/Claude-quality rendering:
//   h1–h6, ul, ol, roman-numeral lists, alpha lists,
//   nested lists, blockquotes, tables, code, inline formatting.
// ============================
// ============================================================
// 🧠 THINKING DROPDOWN — Gemma reasoning/answer split + collapsible UI
// ============================================================
// Canonical persisted format: raw text may contain a single
// <think>...</think> block (reasoning) followed by the final answer.
// This is model-agnostic by design — any model's stored output can
// carry the tag and will render with the same collapsible UI.
function splitThinking(rawText) {
    if (!rawText) return { thinking: null, answer: rawText || "" };
    const m = rawText.match(/<think>([\s\S]*?)<\/think>([\s\S]*)/);
    if (m && m[1].trim()) {
        return { thinking: m[1].trim(), answer: m[2].trim() };
    }
    // Unclosed tag (e.g. stream cut off mid-thought) — treat all of it as thinking
    const openOnly = rawText.match(/<think>([\s\S]*)$/);
    if (openOnly && openOnly[1].trim()) {
        return { thinking: openOnly[1].trim(), answer: "" };
    }
    return { thinking: null, answer: rawText };
}

let _thinkingIdSeq = 0;
function buildThinkingHTML(thinkingText, expanded, active) {
    const id = `think-block-${Date.now()}-${_thinkingIdSeq++}`;
    const isOpen = !!expanded;
    const isActive = !!active;
    return `<div class="thinking-block">
        <button type="button" class="thinking-toggle${isActive ? ' active' : ''}" aria-expanded="${isOpen}" aria-controls="${id}" onclick="toggleThinking(this)">
            <svg class="thinking-brain-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><path d="M9.5 2a3.5 3.5 0 0 0-3.5 3.5v.126A3.001 3.001 0 0 0 4 8.5v1.09a3.001 3.001 0 0 0-1 5.582V16.5A3.5 3.5 0 0 0 6.5 20a2.5 2.5 0 0 0 2.5-2.5V4.5A2.5 2.5 0 0 0 9.5 2Zm5 0A2.5 2.5 0 0 0 12 4.5v13a2.5 2.5 0 0 0 2.5 2.5A3.5 3.5 0 0 0 18 16.5v-1.328a3.001 3.001 0 0 0-1-5.582V8.5a3.001 3.001 0 0 0-2-2.874V5.5A3.5 3.5 0 0 0 14.5 2Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>
            <span class="thinking-label">Thinking</span>
            <svg class="thinking-arrow" width="10" height="10" viewBox="0 0 10 10" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><path d="M2 1l5 4-5 4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>
        <div class="thinking-content${isOpen ? ' open' : ''}" id="${id}" role="region" aria-label="Model reasoning">
            <div class="thinking-inner">${formatMessage(thinkingText)}</div>
        </div>
    </div>`;
}

window.toggleThinking = function (btn) {
    const expanded = btn.getAttribute('aria-expanded') === 'true';
    btn.setAttribute('aria-expanded', String(!expanded));
    const content = document.getElementById(btn.getAttribute('aria-controls'));
    if (content) content.classList.toggle('open', !expanded);
};

// Renders a finalized bot message (thinking dropdown, if present, + answer body)
// into botMsg, and stores the raw (tag-inclusive) text on wrapper.dataset.raw.
function renderBotContent(wrapper, botMsg, rawText) {
    const { thinking, answer } = splitThinking(rawText);
    let html = "";
    if (thinking) {
        html += buildThinkingHTML(thinking, false);
    }
    html += formatMessage(repairTruncated(answer));
    botMsg.innerHTML = html;
    if (wrapper) wrapper.dataset.raw = rawText;
}

function formatMessage(rawText) {
    if (!rawText) return "";

    // ── STEP 1: Stash fenced code blocks ──────────────────────────────────
    const codeBlocks = [];
    let text = rawText.replace(/```([\w+\-#. ]*)\n?([\s\S]*?)```/g, (_match, lang, code) => {
        const language = lang.trim() || "text";
        const escapedCode = code
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
        codeBlocks.push(`<div class="code-block">
            <div class="code-header">
                <div class="mac-dots" aria-hidden="true">
                    <span class="mac-dot mac-dot-red"></span>
                    <span class="mac-dot mac-dot-yellow"></span>
                    <span class="mac-dot mac-dot-green"></span>
                </div>
                <span class="lang-label">${language}</span>
                <button class="copy-btn" onclick="copyCode(this)">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
                        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                    </svg><span class="copy-btn-label">Copy</span></button>
            </div>
            <pre><code>${escapedCode.trimEnd()}</code></pre>
        </div>`);
        return `\x00CODE${codeBlocks.length - 1}\x00`;
    });

    // ── STEP 2: Escape HTML in non-code sections only ─────────────────────
    text = text.split(/(\x00CODE\d+\x00)/).map((part, i) => {
        if (i % 2 === 1) return part;
        return part
            .replace(/&(?!(amp|lt|gt|quot|#\d+);)/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
    }).join("");

    // ── STEP 3: Tables ────────────────────────────────────────────────────
    text = text.replace(/((?:[ \t]*\|.+\|\s*\n?)+)/g, (block) => {
        const rawRows = block.trim().split("\n").map(r => r.trim()).filter(Boolean);
        if (rawRows.length < 2) return block;
        const isSep = r => /^\|[\s:|\-]+\|$/.test(r);
        if (!isSep(rawRows[1])) return block;

        const parseRow = r =>
            r.replace(/^\||\|$/g, "").split("|").map(c => c.trim());

        const sepCells = parseRow(rawRows[1]);
        const aligns   = sepCells.map(c => {
            if (/^:-+:$/.test(c)) return 'center';
            if (/^-+:$/.test(c))  return 'right';
            return 'left';
        });

        const headers = parseRow(rawRows[0]);
        const bodyRows = rawRows.slice(2);

        const thead = `<thead><tr>${
            headers.map((h, i) =>
                `<th style="text-align:${aligns[i] || 'left'}">${applyInline(h)}</th>`
            ).join("")
        }</tr></thead>`;

        const tbody = `<tbody>${
            bodyRows.map(r =>
                `<tr>${parseRow(r).map((c, i) =>
                    `<td style="text-align:${aligns[i] || 'left'}">${applyInline(c)}</td>`
                ).join("")}</tr>`
            ).join("")
        }</tbody>`;

        return `<div class="table-wrap"><table>${thead}${tbody}</table></div>`;
    });

    // ── STEP 4: Block-level elements ──────────────────────────────────────
    const outputLines = [];
    const rawLines    = text.split("\n");
    let i = 0;

    // Helper: detect list-item type
    const isUL      = s => /^[-*•+]\s/.test(s);
    const isOL      = s => /^\d+[.)]\s/.test(s);
    const isRoman   = s => /^[ivxlcdmIVXLCDM]+[.)]\s/i.test(s) && s.length < 20;
    const isAlpha   = s => /^[a-z][.)]\s/.test(s) || /^[A-Z][.)]\s/.test(s);

    while (i < rawLines.length) {
        const line    = rawLines[i];
        const trimmed = line.trim();

        // Headings h1–h6
        const h6 = trimmed.match(/^#{6}\s+(.+)$/); if (h6) { outputLines.push(`<h6>${applyInline(h6[1])}</h6>`); i++; continue; }
        const h5 = trimmed.match(/^#{5}\s+(.+)$/);  if (h5) { outputLines.push(`<h5>${applyInline(h5[1])}</h5>`); i++; continue; }
        const h4 = trimmed.match(/^#{4}\s+(.+)$/);  if (h4) { outputLines.push(`<h4>${applyInline(h4[1])}</h4>`); i++; continue; }
        const h3 = trimmed.match(/^###\s+(.+)$/);   if (h3) { outputLines.push(`<h3>${applyInline(h3[1])}</h3>`); i++; continue; }
        const h2 = trimmed.match(/^##\s+(.+)$/);    if (h2) { outputLines.push(`<h2>${applyInline(h2[1])}</h2>`); i++; continue; }
        const h1 = trimmed.match(/^#\s+(.+)$/);     if (h1) { outputLines.push(`<h1>${applyInline(h1[1])}</h1>`); i++; continue; }

        // Horizontal rule
        if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) { outputLines.push("<hr>"); i++; continue; }

        // Blockquote — collect consecutive > lines
        if (/^&gt;\s?/.test(trimmed)) {
            const bqLines = [];
            while (i < rawLines.length && /^&gt;\s?/.test(rawLines[i].trim())) {
                bqLines.push(applyInline(rawLines[i].trim().replace(/^&gt;\s?/, "")));
                i++;
            }
            outputLines.push(`<blockquote>${bqLines.join("<br>")}</blockquote>`);
            continue;
        }

        // Unordered list (-, *, •, +) — supports indented sub-lists
        if (isUL(trimmed)) {
            outputLines.push(buildList(rawLines, i, 'ul'));
            // advance i past all consumed lines
            let consumed = countListLines(rawLines, i, isUL);
            i += consumed;
            continue;
        }

        // Roman numeral list (i. ii. iii. / I. II. III.)
        if (isRoman(trimmed) && !isOL(trimmed)) {
            const items = [];
            const listClass = /^[ivxlcdm]+[.)]/i.test(trimmed) ? 'list-roman' : 'list-roman-upper';
            while (i < rawLines.length && isRoman(rawLines[i].trim()) && !isOL(rawLines[i].trim())) {
                const t = rawLines[i].trim();
                items.push(`<li>${applyInline(t.replace(/^[ivxlcdmIVXLCDM]+[.)]\s/, ""))}</li>`);
                i++;
            }
            outputLines.push(`<ol class="${listClass}">${items.join("")}</ol>`);
            continue;
        }

        // Alpha list (a. b. c. / A. B. C.)
        if (isAlpha(trimmed)) {
            const items = [];
            const upper = /^[A-Z][.)]/.test(trimmed);
            while (i < rawLines.length && isAlpha(rawLines[i].trim())) {
                const t = rawLines[i].trim();
                items.push(`<li>${applyInline(t.replace(/^[a-zA-Z][.)]\s/, ""))}</li>`);
                i++;
            }
            outputLines.push(`<ol class="${upper ? 'list-alpha-upper' : 'list-alpha'}">${items.join("")}</ol>`);
            continue;
        }

        // Ordered list (1. 2. 3.) — preserve original numbers
        if (isOL(trimmed)) {
            const items = [];
            while (i < rawLines.length && isOL(rawLines[i].trim())) {
                const t = rawLines[i].trim();
                const numMatch = t.match(/^(\d+)[.)]\s/);
                const num = numMatch ? parseInt(numMatch[1], 10) : null;
                const content = applyInline(t.replace(/^\d+[.)]\s/, ""));
                items.push(num !== null ? `<li value="${num}">${content}</li>` : `<li>${content}</li>`);
                i++;
            }
            outputLines.push(`<ol>${items.join("")}</ol>`);
            continue;
        }

        // Code placeholder — pass through
        if (/^\x00CODE\d+\x00$/.test(trimmed)) {
            outputLines.push(trimmed);
            i++;
            continue;
        }

        // Blank line — paragraph break
        if (!trimmed) {
            outputLines.push("");
            i++;
            continue;
        }

        // Table pass-through
        if (/^<(div class="table-wrap"|table|\/div|\/table|thead|tbody|tr|th|td)/.test(trimmed)) {
            outputLines.push(line);
            i++;
            continue;
        }

        // Plain text — apply inline formatting
        outputLines.push(applyInline(trimmed));
        i++;
    }

    // ── STEP 5: Group plain text lines into <p> blocks ────────────────────
    // Key fix: each non-blank, non-block line becomes its own <p> rather than
    // being merged with its neighbour — prevents run-on walls of text.
    let html    = "";
    let paraBuf = [];

    const flushPara = () => {
        if (paraBuf.length) {
            // Join lines that are truly continuation (no blank separator) into one <p>,
            // but treat each blank-separated group as its own paragraph.
            html += `<p>${paraBuf.join(" ")}</p>`;
            paraBuf = [];
        }
    };

    const BLOCK_RE = /^(<(h[1-6]|ul|ol|blockquote|hr|div|table|thead|tbody|tr|th|td)|<\/|<div|\x00CODE)/;

    for (const ln of outputLines) {
        if (!ln) { flushPara(); continue; }
        if (BLOCK_RE.test(ln)) { flushPara(); html += ln; continue; }
        paraBuf.push(ln);
    }
    flushPara();

    // ── STEP 6: Restore code blocks ───────────────────────────────────────
    html = html.replace(/\x00CODE(\d+)\x00/g, (_, idx) => codeBlocks[+idx]);

    // ── STEP 7: Merge adjacent same-type list blocks ──────────────────────
    html = html.replace(/<\/ol>\s*<ol>/g, "");
    html = html.replace(/<\/ul>\s*<ul>/g, "");

    return html;
}

// ── Nested list builder ───────────────────────────────────────────────────────
// Handles indented sub-lists by tracking leading whitespace depth.
function buildList(lines, start, type) {
    const items = [];
    let i = start;
    const isItem = type === 'ul'
        ? s => /^[-*•+]\s/.test(s)
        : s => /^\d+[.)]\s/.test(s);

    while (i < lines.length && isItem(lines[i].trim())) {
        const content = applyInline(lines[i].trim().replace(/^[-*•+]\s|^\d+[.)]\s/, ""));
        items.push(`<li>${content}</li>`);
        i++;
    }
    return `<${type}>${items.join("")}</${type}>`;
}

function countListLines(lines, start, isItem) {
    let count = 0;
    let i = start;
    while (i < lines.length && isItem(lines[i].trim())) { count++; i++; }
    return count || 1;
}

// ── Inline formatting helper (bold, italic, inline-code, links) ──────────────
function applyInline(text) {
    if (!text) return text;
    // Inline code — protect first so bold/italic don't match inside it
    const inlineCode = [];
    text = text.replace(/`([^`]+)`/g, (_, c) => {
        inlineCode.push(`<span class="inline-code">${c.replace(/</g,"&lt;").replace(/>/g,"&gt;")}</span>`);
        return `\x01IC${inlineCode.length - 1}\x01`;
    });

    // Bold+italic
    text = text.replace(/\*\*\*(.+?)\*\*\*/g, "<strong><em>$1</em></strong>");
    // Bold
    text = text.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    // Italic (only single asterisk surrounded by non-space)
    text = text.replace(/(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)/g, "<em>$1</em>");
    // Underline via __text__
    text = text.replace(/__(.+?)__/g, "<u>$1</u>");
    // Strikethrough
    text = text.replace(/~~(.+?)~~/g, "<s>$1</s>");
    // Links  [label](url)
    text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^\)]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');

    // Restore inline code
    text = text.replace(/\x01IC(\d+)\x01/g, (_, i) => inlineCode[+i]);
    return text;
}

// ============================
// 📋 COPY CODE
// ============================
function copyCode(btn) {
    const code = btn.closest(".code-block").querySelector("code").innerText;
    navigator.clipboard.writeText(code).then(() => {
        btn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg><span class="copy-btn-label">Copied!</span>`;
        btn.classList.add("copied");
        setTimeout(() => {
            btn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg><span class="copy-btn-label">Copy</span>`;
            btn.classList.remove("copied");
        }, 2000);
    }).catch(() => showToast("Failed to copy code"));
}

// ============================
// 📋 COPY BOT ANSWER
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
    }).catch(() => showToast("Failed to copy message"));
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
    }).catch(() => showToast("Failed to copy message"));
}

// ============================
// ✏️ EDIT USER MESSAGE
// ============================
function editUserMessage(btn) {
    const userWrapper = btn.closest(".user-msg-wrapper");
    if (!userWrapper) return;

    // Only allow one edit per turn (disable button so it can't be clicked again)
    btn.disabled = true;
    btn.style.opacity = "0.3";
    btn.style.cursor = "default";
    btn.title = "Already edited";

    const bubble = userWrapper.querySelector(".message.user");
    const text   = bubble ? bubble.innerText.trim() : "";
    if (!text) return;

    // ── Assign a stable group-id to this turn if not already set ────────────
    const groupId = userWrapper.dataset.turnGroup || ("tg_" + Date.now());
    userWrapper.dataset.turnGroup = groupId;

    // ── Find the bot wrapper that immediately follows this user wrapper ─────
    let botWrapper = userWrapper.nextElementSibling;
    // Skip any tool-badge divs — bot-msg-wrapper comes after
    while (botWrapper && !botWrapper.classList.contains("bot-msg-wrapper")) {
        botWrapper = botWrapper.nextElementSibling;
    }
    if (botWrapper) botWrapper.dataset.turnGroup = groupId;

    // ── Snapshot ghost memory for v1 so we can restore it when navigating ──
    const v1GhostSnapshot = typeof ghostMemory !== "undefined" ? ghostMemory.slice() : [];

    // ── Hide the original turn (v1) — keep in DOM for switching ────────────
    userWrapper.dataset.version = "1";
    userWrapper.style.display   = "none";
    if (botWrapper) {
        botWrapper.dataset.version = "1";
        botWrapper.style.display   = "none";
    }

    // ── Trim ghostMemory so new send starts clean from before this turn ────
    if (typeof ghostMemory !== "undefined" && ghostMemory.length > 0) {
        let lastUserIdx = -1;
        for (let i = ghostMemory.length - 1; i >= 0; i--) {
            if (ghostMemory[i].role === "user") { lastUserIdx = i; break; }
        }
        if (lastUserIdx !== -1) ghostMemory = ghostMemory.slice(0, lastUserIdx);
    }

    // ── Store the pending edit context so sendMessage can wire up v2 ───────
    pendingEditGroupId = groupId;

    // ── Populate textarea ────────────────────────────────────────────────────
    const inputEl = document.getElementById("input");
    if (inputEl) {
        inputEl.value = text;
        inputEl.style.height = "auto";
        inputEl.style.height = inputEl.scrollHeight + "px";
        inputEl.focus();
        inputEl.selectionStart = inputEl.selectionEnd = inputEl.value.length;
        if (typeof updateSendBtn === "function") updateSendBtn();
    }
}

// ============================
// 🔀 VERSION NAVIGATOR (‹ 1/2 ›)
// ============================
function createVersionNavigator(groupId, v1UserWrapper, v1BotWrapper) {
    const nav = document.createElement("div");
    nav.classList.add("msg-version-nav");
    nav.dataset.navGroup = groupId;
    // currentVersion: 1 = old, 2 = new (we start on 2 after editing)
    nav.dataset.current = "2";
    nav.dataset.total   = "2";

    function render(current) {
        nav.innerHTML = `
            <button class="version-nav-btn prev-version" title="View previous reply"
                    ${current <= 1 ? "disabled" : ""}>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                     stroke="currentColor" stroke-width="2.5"
                     stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="15 18 9 12 15 6"/>
                </svg>
            </button>
            <span class="version-nav-label">${current} / 2</span>
            <button class="version-nav-btn next-version" title="View latest reply"
                    ${current >= 2 ? "disabled" : ""}>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                     stroke="currentColor" stroke-width="2.5"
                     stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="9 18 15 12 9 6"/>
                </svg>
            </button>`;

        nav.querySelector(".prev-version").onclick = () => switchVersion(1);
        nav.querySelector(".next-version").onclick = () => switchVersion(2);
    }

    function switchVersion(target) {
        nav.dataset.current = String(target);
        render(target);

        const chatbox = document.getElementById("chatbox");
        if (!chatbox) return;

        // All turn-group elements for this group
        const all = chatbox.querySelectorAll(`[data-turn-group="${groupId}"]`);
        all.forEach(el => {
            // v1 elements have data-version="1", v2 have data-version="2"
            if (el.dataset.version === String(target)) {
                el.style.display = "";
            } else {
                el.style.display = "none";
            }
        });
    }

    render(2); // start on version 2 (the new edit)
    return nav;
}

// ============================
// 🍞 TOAST
// ============================
function showToast(message, duration = 3000) {
    let toast = document.getElementById("toastNotif");
    if (!toast) {
        toast = document.createElement("div");
        toast.id = "toastNotif";
        document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.classList.add("show");
    setTimeout(() => toast.classList.remove("show"), duration);
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
        </div>`;
    return div;
}

// ============================
// 💬 DOTS INDICATOR
// ============================
function createLightIndicator() {
    const div = document.createElement("div");
    div.classList.add("message", "bot", "typing");
    div.innerHTML = `<div class="typing-dots"><span></span><span></span><span></span></div>`;
    return div;
}

// ============================
// 📦 USER BUBBLE (supports file attachments)
// ============================
function createUserBubble(text, files, turnGroupId) {
    const wrapper = document.createElement("div");
    wrapper.classList.add("user-msg-wrapper");
    if (turnGroupId) wrapper.dataset.turnGroup = turnGroupId;

    // File attachments (images + docs) shown above text
    if (files && files.length > 0) {
        const filesDiv = document.createElement("div");
        filesDiv.innerHTML = buildFileAttachHTML(files);
        wrapper.appendChild(filesDiv);
    }

    // Text bubble (only if there is text)
    if (text) {
        const bubble = document.createElement("div");
        bubble.classList.add("message", "user");
        bubble.innerText = text;

        // Show more / Show less for long user messages (display-only, like Claude)
        const isLong = text.length > 400 || (text.match(/\n/g) || []).length > 6;
        if (isLong) {
            bubble.classList.add("user-collapsed");

            // Column holder so the toggle sits *under* the bubble, not beside it
            // (the outer wrapper is a row-flex used for the bubble + hover action buttons)
            const textCol = document.createElement("div");
            textCol.classList.add("user-text-col");
            textCol.appendChild(bubble);

            const toggleBtn = document.createElement("button");
            toggleBtn.type = "button";
            toggleBtn.classList.add("user-toggle-btn");
            toggleBtn.textContent = "Show more";
            toggleBtn.onclick = () => {
                const collapsed = bubble.classList.toggle("user-collapsed");
                toggleBtn.textContent = collapsed ? "Show more" : "Show less";
            };
            textCol.appendChild(toggleBtn);

            wrapper.appendChild(textCol);
        } else {
            wrapper.appendChild(bubble);
        }
    }

    // Action buttons container (edit + copy, shown on hover)
    const btnGroup = document.createElement("div");
    btnGroup.classList.add("user-btn-group");

    const editBtn = document.createElement("button");
    editBtn.classList.add("user-edit-btn");
    editBtn.title = "Edit message";
    editBtn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>`;
    editBtn.onclick = () => editUserMessage(editBtn);

    const copyBtn = document.createElement("button");
    copyBtn.classList.add("user-copy-btn");
    copyBtn.title = "Copy message";
    copyBtn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`;
    copyBtn.onclick = () => copyUserMessage(copyBtn);

    btnGroup.appendChild(editBtn);
    btnGroup.appendChild(copyBtn);
    wrapper.appendChild(btnGroup);
    return wrapper;
}

// ============================
// 📦 BOT WRAPPER
// ============================
function createBotWrapper() {
    const wrapper = document.createElement("div");
    wrapper.classList.add("bot-msg-wrapper");

    const botMsg = document.createElement("div");
    botMsg.classList.add("message", "bot");

    const actionsRow = document.createElement("div");
    actionsRow.classList.add("bot-actions");
    actionsRow.innerHTML = `
        <button class="bot-copy-btn" onclick="copyBotAnswer(this)" title="Copy">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
        </button>`;

    // Per-response disclaimer — mobile only, always moves to latest response
    const isMobileDisclaimer = window.innerWidth <= 768;
    if (isMobileDisclaimer) {
        // Remove disclaimer from all previous bot wrappers so it only shows on latest
        document.querySelectorAll(".bot-response-disclaimer").forEach(el => el.remove());

        const disclaimer = document.createElement("div");
        disclaimer.classList.add("bot-response-disclaimer");
        disclaimer.innerHTML = `
            <img src="static/logo.png" alt="" style="width:14px;height:14px;border-radius:3px;opacity:0.45;flex-shrink:0;">
            <span>Catura can make mistakes. Double-check responses.</span>`;
        wrapper.appendChild(botMsg);
        wrapper.appendChild(actionsRow);
        wrapper.appendChild(disclaimer);
    } else {
        wrapper.appendChild(botMsg);
        wrapper.appendChild(actionsRow);
    }
    return { wrapper, botMsg };
}


// ============================
// ➕ NEW CHAT
// ============================
window.newChat = function () {
    const overlay = document.getElementById("settingsOverlay");
    if (overlay) overlay.classList.remove("active");

    currentSessionId = generateSessionId();
    firstMessage = true;

    const chatbox   = document.getElementById("chatbox");
    const inputArea = document.getElementById("inputArea");
    const app       = document.getElementById("app");

    if (chatbox)   chatbox.innerHTML = "";
    if (inputArea) {
        inputArea.classList.remove("bottom");
        inputArea.classList.add("center");
    }
    if (app) app.classList.add("greeting-mode");
    
    displayGreeting();
    if (typeof window.refreshMsgNavTracker === 'function') window.refreshMsgNavTracker();

    if (window.innerWidth <= 768) closeSidebar();
    showMainMenu();
    if (typeof window.highlightActiveHistoryItem === 'function') window.highlightActiveHistoryItem(null);
    showToast("New chat started", 2000);

    // Auto-focus the input so the user can start typing immediately
    setTimeout(() => document.getElementById('input')?.focus(), 50);
};

// ============================
// 🚪 LOGOUT
// ============================
window.logoutUser = async function () {
    showModal({
        type: 'confirm',
        dangerous: true,
        icon: `<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>`,
        title: 'Log out',
        subtitle: 'Are you sure you want to sign out?',
        confirmLabel: 'Log out',
        cancelLabel: 'Stay',
        onConfirm: async () => {
            await supabaseClient.auth.signOut();
            window.location.href = "/auth";
        }
    });
};

// ============================
// 👤 USER PROFILE DROPDOWN
// ============================
window.toggleUserDropdown = function (e) {
    e.stopPropagation();
    const dropdown = document.getElementById("userDropdown");
    const btn      = document.getElementById("userProfileBtn");
    const isOpen   = dropdown.classList.contains("open");
    if (isOpen) {
        dropdown.classList.remove("open");
        btn.classList.remove("active");
    } else {
        dropdown.classList.add("open");
        btn.classList.add("active");
    }
};

window.closeUserDropdown = function () {
    document.getElementById("userDropdown")?.classList.remove("open");
    document.getElementById("userProfileBtn")?.classList.remove("active");
};

// Close dropdown when clicking anywhere outside
document.addEventListener("click", function (e) {
    const wrap = document.getElementById("userProfileWrap");
    if (wrap && !wrap.contains(e.target)) {
        closeUserDropdown();
    }
});

window.goToProfile = function () {
    closeUserDropdown();
    showSettings();
    // After settings opens, switch to profile tab
    setTimeout(() => {
        const profileTab = document.querySelector('.settings-nav-item[onclick*="profile"]');
        if (profileTab) profileTab.click();
    }, 50);
};

// ============================
// 🧭 MAIN MENU
// ============================
window.showMainMenu = function () {
    // NOTE: History accordion open/close state is now fully manual —
    // controlled only by the user via toggleHistoryAccordion().
    // It must NOT be auto-closed here (e.g. on new chat, settings close, or session load).
};

// ============================
// ⚙️ SETTINGS OVERLAY
// ============================
// ============================
// ⚙️ SETTINGS OVERLAY
// ============================
window.showSettings = function () {
    const overlay = document.getElementById("settingsOverlay");
    const email    = currentUser?.email || "Not logged in";
    const fullName = document.getElementById("userFullname")?.textContent || "User";
    const initials = document.getElementById("userAvatar")?.textContent  || "?";

    overlay.innerHTML = `
        <button class="settings-close-btn" onclick="closeSettings()" title="Close">✕</button>
        <div class="settings-panel-wrap">
            <div class="settings-nav">
                <h2 class="settings-nav-title">Settings</h2>
                <div class="settings-nav-item active" onclick="showSettingsTab('general', this)">
                    <svg class="sn-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <circle cx="12" cy="12" r="3"></circle>
                        <path d="M12 1v6m0 6v6m10.39-9.39l-4.24 4.24m-8.3 0l-4.24-4.24m12.53 8.53l4.24 4.24m-8.3 0l4.24-4.24"></path>
                    </svg> General
                </div>
                <div class="settings-nav-item" onclick="showSettingsTab('profile', this)">
                    <svg class="sn-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
                        <circle cx="12" cy="7" r="4"></circle>
                    </svg> Profile
                </div>
                <div class="settings-nav-item" onclick="showSettingsTab('chats', this)">
                    <svg class="sn-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
                    </svg> Chats
                </div>
                <div class="settings-nav-item" onclick="showSettingsTab('privacy', this)">
                    <svg class="sn-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>
                    </svg> Privacy
                </div>
                <div class="settings-nav-item" onclick="showSettingsTab('speech', this)">
                    <svg class="sn-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <line x1="8" y1="6" x2="8" y2="18"></line>
                        <line x1="12" y1="3" x2="12" y2="21"></line>
                        <line x1="16" y1="7" x2="16" y2="17"></line>
                        <line x1="4" y1="9" x2="4" y2="15"></line>
                        <line x1="20" y1="9" x2="20" y2="15"></line>
                    </svg> Speech
                </div>
                <div class="settings-nav-item" onclick="showSettingsTab('personalization', this)">
                    <svg class="sn-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
                        <circle cx="12" cy="7" r="4"></circle>
                        <path d="M16 3.13a4 4 0 0 1 0 7.75"></path>
                    </svg> Personalization
                </div>
                <div class="settings-nav-item" onclick="showSettingsTab('datacontrols', this)">
                    <svg class="sn-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <ellipse cx="12" cy="5" rx="9" ry="3"></ellipse>
                        <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"></path>
                        <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"></path>
                    </svg> Data controls
                </div>
                <div class="settings-nav-item" onclick="showSettingsTab('account', this)">
                    <svg class="sn-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <circle cx="12" cy="7" r="4"></circle>
                        <path d="M5.5 21a9 9 0 0 1 13 0"></path>
                    </svg> Account
                </div>
                <div class="settings-nav-item" onclick="showSettingsTab('shortcuts', this)">
                    <svg class="sn-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <rect x="2" y="6" width="20" height="12" rx="2"></rect>
                        <path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M8 14h8"></path>
                    </svg> Shortcuts
                </div>
            </div>
            <div class="settings-content" id="settingsContent"></div>
        </div>`;

    overlay.classList.add("active");
    showSettingsTab('general', overlay.querySelector('.settings-nav-item.active'));

    if (window.innerWidth <= 768) closeSidebar();
};

window.closeSettings = function () {
    const overlay = document.getElementById("settingsOverlay");
    if (overlay) overlay.classList.remove("active");
    showMainMenu();
};

// ============================
// ✏️ EDIT DISPLAY NAME
// ============================
window.editDisplayName = async function () {
    const currentName = document.getElementById("userFullname")?.textContent || "User";
    showModal({
        type: 'prompt',
        icon: `<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>`,
        title: 'Edit display name',
        subtitle: 'Change how your name appears',
        inputValue: currentName,
        inputPlaceholder: 'Your name',
        confirmLabel: 'Save',
        onConfirm: async (newName) => {
            if (!newName || newName === currentName) return;
            const trimmedName = newName.trim();
            try {
                const { data, error } = await supabaseClient.auth.updateUser({
                    data: { full_name: trimmedName }
                });
                if (error) { showToast("❌ Failed to update name. Please try again."); return; }
                currentUser = data.user;
                const parts = trimmedName.split(/\s+/).filter(Boolean);
                const initials = parts.length >= 2
                    ? (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
                    : parts[0].slice(0, 2).toUpperCase();
                const avatarEl    = document.getElementById("userAvatar");
                const nameEl      = document.getElementById("userFullname");
                const railAvatar  = document.getElementById("railAvatar");
                const scProfileName = document.querySelector(".sc-profile-name");
                if (avatarEl)   avatarEl.textContent  = initials;
                if (nameEl)     nameEl.textContent    = trimmedName;
                if (railAvatar) railAvatar.textContent = initials;
                if (scProfileName) scProfileName.textContent = trimmedName;
                showToast(`✓ Name updated to ${trimmedName}`);
                setTimeout(() => {
                    showSettingsTab('profile', document.querySelector('.settings-nav-item.active'));
                }, 500);
            } catch (err) {
                showToast("❌ Failed to update name. Please try again.");
            }
        }
    });
};

window.editCaturaCallName = function () {
    const existing = document.getElementById('caturaCallNameModal');
    if (existing) existing.remove();

    const current = getUserItem('catura_call_name') || '';

    const isLight = document.documentElement.getAttribute('data-theme') === 'light';
    const t = isLight ? {
        overlay: 'rgba(0,0,0,0.35)',
        box: '#ffffff',
        boxBorder: '#e4e7ef',
        boxShadow: '0 24px 64px rgba(0,0,0,0.14)',
        title: '#0f1117',
        sub: '#7a8399',
        divider: '#eaecf0',
        inputBg: '#f9fafb',
        inputBorder: '#dde1eb',
        inputBorderFocus: '#10a37f',
        inputColor: '#0f1117',
        note: '#9aa0b5',
        cancelBorder: '#dde1eb',
        cancelBg: '#f4f6fb',
        cancelBgHover: '#e8eaf0',
        cancelColor: '#3d4455',
    } : {
        overlay: 'rgba(0,0,0,0.6)',
        box: '#1a1a1a',
        boxBorder: '#2a2a2a',
        boxShadow: '0 24px 64px rgba(0,0,0,0.7)',
        title: '#eeeeee',
        sub: '#666666',
        divider: '#222222',
        inputBg: '#111111',
        inputBorder: '#333333',
        inputBorderFocus: '#10a37f',
        inputColor: '#eeeeee',
        note: '#555555',
        cancelBorder: '#333333',
        cancelBg: 'transparent',
        cancelBgHover: '#222222',
        cancelColor: '#999999',
    };

    const modal = document.createElement('div');
    modal.id = 'caturaCallNameModal';
    modal.style.cssText = `position:fixed;inset:0;z-index:10000;display:flex;align-items:center;justify-content:center;background:${t.overlay};backdrop-filter:blur(6px);animation:caturaModalFadeIn 0.2s ease;`;

    modal.innerHTML = `
    <div style="background:${t.box};border:1px solid ${t.boxBorder};border-radius:16px;width:min(400px,92vw);padding:28px 24px 20px;box-shadow:${t.boxShadow};position:relative;animation:caturaModalSlideUp 0.25s cubic-bezier(0.34,1.2,0.64,1);">
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:6px;">
            <div style="width:36px;height:36px;border-radius:10px;background:rgba(16,163,127,0.12);display:flex;align-items:center;justify-content:center;flex-shrink:0;">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#10a37f" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>
                </svg>
            </div>
            <div>
                <p style="margin:0;font-size:15px;font-weight:600;color:${t.title};">What should Catura call you?</p>
                <p style="margin:2px 0 0;font-size:12px;color:${t.sub};">Only shown in your home screen greeting</p>
            </div>
        </div>
        <div style="height:1px;background:${t.divider};margin:16px 0;"></div>
        <input id="caturaCallNameInput" type="text" placeholder="Enter a name or nickname…" maxlength="40"
            value="${current.replace(/"/g,'&quot;')}"
            style="width:100%;box-sizing:border-box;background:${t.inputBg};border:1.5px solid ${t.inputBorder};border-radius:10px;padding:11px 14px;font-size:14px;color:${t.inputColor};outline:none;transition:border-color 0.2s;font-family:inherit;"
            onfocus="this.style.borderColor='#10a37f'" onblur="this.style.borderColor='${t.inputBorder}'"
            onkeydown="if(event.key==='Enter')document.getElementById('caturaCallNameSave').click();if(event.key==='Escape')document.getElementById('caturaCallNameModal').remove();"
        >
        <p style="margin:8px 0 16px;font-size:11px;color:${t.note};">This name only appears in the "Good morning, …" greeting. Your display name stays unchanged.</p>
        <div style="display:flex;gap:10px;justify-content:flex-end;">
            <button onclick="document.getElementById('caturaCallNameModal').remove()"
                style="padding:9px 18px;border-radius:8px;border:1px solid ${t.cancelBorder};background:${t.cancelBg};color:${t.cancelColor};font-size:13px;font-weight:500;cursor:pointer;font-family:inherit;transition:background 0.15s;"
                onmouseover="this.style.background='${t.cancelBgHover}'" onmouseout="this.style.background='${t.cancelBg}'">Cancel</button>
            <button id="caturaCallNameSave"
                style="padding:9px 20px;border-radius:8px;border:none;background:#10a37f;color:#fff;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;transition:opacity 0.15s;"
                onmouseover="this.style.opacity='0.85'" onmouseout="this.style.opacity='1'"
                onclick="
                    const val = document.getElementById('caturaCallNameInput').value.trim();
                    if (val) {
                        setUserItem('catura_call_name', val);
                    } else {
                        removeUserItem('catura_call_name');
                    }
                    saveSettingToCloud('call_name', val || null);
                    document.getElementById('caturaCallNameModal').remove();
                    if (typeof showToast === 'function') showToast(val ? '✓ Greeting name updated' : '✓ Greeting name cleared');
                    if (typeof displayGreeting === 'function') displayGreeting();
                ">Save</button>
        </div>
    </div>
    <style>
        @keyframes caturaModalFadeIn { from { opacity:0; } to { opacity:1; } }
        @keyframes caturaModalSlideUp { from { opacity:0; transform:translateY(16px) scale(0.97); } to { opacity:1; transform:translateY(0) scale(1); } }
    </style>`;

    modal.addEventListener('click', (e) => { if (e.target === modal) modal.remove(); });
    document.body.appendChild(modal);
    requestAnimationFrame(() => document.getElementById('caturaCallNameInput')?.focus());
};

// ============================
// 🔲 COMING SOON HELPER
// ============================
function comingSoonSection(title, items) {
    const rows = items.map(({ icon, label, sub }) => `
        <div class="sc-row disabled">
            <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                ${icon}
            </svg>
            <div class="sc-row-body">
                <p class="sc-row-label">${label}</p>
                <p class="sc-row-sub soon">${sub || 'Coming soon'}</p>
            </div>
        </div>`).join('');
    return `<div class="sc-section"><div class="sc-section-title">${title}</div>${rows}</div>`;
}

window.showSettingsTab = function (tab, clickedEl) {
    document.querySelectorAll(".settings-nav-item").forEach(el => el.classList.remove("active"));
    if (clickedEl) clickedEl.classList.add("active");

    const content  = document.getElementById("settingsContent");
    if (!content) return;

    const email    = currentUser?.email || "Not logged in";
    const fullName = document.getElementById("userFullname")?.textContent || "User";
    const initials = document.getElementById("userAvatar")?.textContent  || "?";

    const tabs = {

        general: `
            <div class="sc-section">
                <div class="sc-section-title">Appearance</div>

                <div class="sc-row sc-row-block">
                    <div class="sc-row-top">
                        <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <circle cx="12" cy="12" r="5"></circle>
                            <path d="M12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4"></path>
                        </svg>
                        <p class="sc-row-label">Theme</p>
                    </div>
                    <div class="theme-picker">
                        <button class="theme-option ${(localStorage.getItem('catura-theme') || 'dark') === 'light' ? 'active' : ''}" onclick="setTheme('light')" title="Light">
                            <div class="theme-preview light-preview">
                                <div class="tp-bar"></div>
                                <div class="tp-line"></div>
                                <div class="tp-line short"></div>
                                <div class="tp-bubble"></div>
                            </div>
                            <span>Light</span>
                        </button>
                        <button class="theme-option ${(localStorage.getItem('catura-theme') || 'dark') === 'auto' ? 'active' : ''}" onclick="setTheme('auto')" title="Auto">
                            <div class="theme-preview auto-preview">
                                <div class="tp-half-light"></div>
                                <div class="tp-half-dark"></div>
                                <div class="tp-bar"></div>
                                <div class="tp-line"></div>
                                <div class="tp-bubble"></div>
                            </div>
                            <span>Auto</span>
                        </button>
                        <button class="theme-option ${(localStorage.getItem('catura-theme') || 'dark') === 'dark' ? 'active' : ''}" onclick="setTheme('dark')" title="Dark">
                            <div class="theme-preview dark-preview">
                                <div class="tp-bar"></div>
                                <div class="tp-line"></div>
                                <div class="tp-line short"></div>
                                <div class="tp-bubble"></div>
                            </div>
                            <span>Dark</span>
                        </button>
                    </div>
                </div>

                <div class="sc-row sc-row-block" style="margin-top:8px;">
                    <div class="sc-row-top">
                        <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <polyline points="4 7 4 4 20 4 20 7"></polyline>
                            <rect x="2" y="7" width="20" height="13" rx="2"></rect>
                            <path d="M9 17v-3m6 3v-3"></path>
                        </svg>
                        <p class="sc-row-label">Chat font size</p>
                    </div>
                    <div class="font-picker">
                        <button class="font-option ${(localStorage.getItem('catura-font') || 'default') === 'default' ? 'active' : ''}" onclick="setFontSize('default')">
                            <span class="font-sample">Aa</span>
                            <span>Default</span>
                        </button>
                        <button class="font-option ${(localStorage.getItem('catura-font') || 'default') === 'small' ? 'active' : ''}" onclick="setFontSize('small')">
                            <span class="font-sample small-sample">Aa</span>
                            <span>Small</span>
                        </button>
                        <button class="font-option ${(localStorage.getItem('catura-font') || 'default') === 'large' ? 'active' : ''}" onclick="setFontSize('large')">
                            <span class="font-sample large-sample">Aa</span>
                            <span>Large</span>
                        </button>
                        <button class="font-option ${(localStorage.getItem('catura-font') || 'default') === 'xlarge' ? 'active' : ''}" onclick="setFontSize('xlarge')">
                            <span class="font-sample xlarge-sample">Aa</span>
                            <span>X-Large</span>
                        </button>
                    </div>
                </div>
            </div>
            <div class="sc-section">
                <div class="sc-section-title">Support</div>
                <div class="sc-row" onclick="openFeedbackModal('bug')">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <circle cx="12" cy="12" r="10"></circle>
                        <path d="M8 14s1.5 2 4 2 4-2 4-2"></path>
                        <line x1="9" y1="9" x2="9.01" y2="9"></line>
                        <line x1="15" y1="9" x2="15.01" y2="9"></line>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Send bug report</p>
                        <p class="sc-row-sub">Help us fix issues</p>
                    </div>
                </div>
                <div class="sc-row" onclick="openFeedbackModal('feature')">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Request a feature</p>
                        <p class="sc-row-sub">Suggest improvements</p>
                    </div>
                </div>
            </div>`,

        profile: `
            <div class="sc-section">
                <div class="sc-section-title">Account</div>
                <div class="sc-profile-card">
                    <div class="sc-avatar-wrap" onclick="triggerProfilePicUpload()" title="Change profile picture">
                        <div class="sc-avatar" id="scAvatarDisplay">${window._profilePicDataUrl ? '' : initials}</div>
                        ${window._profilePicDataUrl ? `<img class="sc-avatar-img" src="${window._profilePicDataUrl}" alt="Profile">` : ''}
                        <div class="sc-avatar-overlay">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"></path>
                                <circle cx="12" cy="13" r="4"></circle>
                            </svg>
                        </div>
                    </div>
                    <div>
                        <p class="sc-profile-name">${fullName}</p>
                        <p class="sc-profile-email">${email}</p>
                    </div>
                </div>
            </div>
            <div class="sc-section">
                <div class="sc-section-title">Actions</div>
                <div class="sc-row" onclick="triggerProfilePicUpload()">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"></path>
                        <circle cx="12" cy="13" r="4"></circle>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Change profile picture</p>
                        <p class="sc-row-sub">Upload a custom avatar</p>
                    </div>
                </div>
                <div class="sc-row" onclick="editDisplayName()">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path>
                        <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Edit display name</p>
                        <p class="sc-row-sub">Change how your name appears</p>
                    </div>
                </div>
                <div class="sc-row sc-color-row">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <circle cx="13.5" cy="6.5" r=".5"></circle>
                        <circle cx="17.5" cy="10.5" r=".5"></circle>
                        <circle cx="8.5" cy="7.5" r=".5"></circle>
                        <circle cx="6.5" cy="12.5" r=".5"></circle>
                        <path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.926 0 1.648-.746 1.648-1.688 0-.437-.18-.835-.437-1.125-.29-.289-.438-.652-.438-1.125a1.64 1.64 0 0 1 1.668-1.668h1.996c3.051 0 5.555-2.503 5.555-5.554C21.965 6.012 17.461 2 12 2z"></path>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">User message color</p>
                        <p class="sc-row-sub">Choose your message bubble color</p>
                    </div>
                    <div class="sc-color-dropdown-wrap" id="scColorDropdownWrap">
                        <div class="sc-color-trigger" id="scColorTrigger">
                            <span class="sc-color-dot" id="scColorDot"></span>
                            <span class="sc-color-label" id="scColorLabel">Green</span>
                            <svg class="sc-color-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                <polyline points="6 9 12 15 18 9"></polyline>
                            </svg>
                        </div>
                        <div class="sc-color-dropdown" id="scColorDropdown">
                            ${[
                                ['red', '#e5484d', 'Red'],
                                ['yellow', '#e0b400', 'Yellow'],
                                ['green', '#10a37f', 'Green'],
                                ['brown', '#8a5a3b', 'Brown'],
                                ['blue', '#3b82f6', 'Blue']
                            ].map(([key, hex, label]) => `
                            <div class="sc-color-option" data-usercolor-option="${key}" onclick="setUserMsgColor('${key}')">
                                <span class="sc-color-dot" style="background:${hex};"></span>
                                <span>${label}</span>
                                <svg class="sc-color-option-check" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
                                    <polyline points="20 6 9 17 4 12"></polyline>
                                </svg>
                            </div>`).join('')}
                        </div>
                    </div>
                </div>
                <div class="sc-row" onclick="editCaturaCallName()">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
                        <circle cx="12" cy="7" r="4"></circle>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">What should Catura call you?</p>
                        <p class="sc-row-sub">Set a nickname for your greeting</p>
                    </div>
                </div>
                <div class="sc-row danger" onclick="logoutUser()">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path>
                        <polyline points="16 17 21 12 16 7"></polyline>
                        <line x1="21" y1="12" x2="9" y2="12"></line>
                    </svg>
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
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="21 8 21 21 3 21 3 8"></polyline>
                        <rect x="1" y="3" width="22" height="5"></rect>
                        <line x1="10" y1="12" x2="14" y2="12"></line>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Archive all chats</p>
                        <p class="sc-row-sub">Hide all chats from your history</p>
                    </div>
                </div>
                <div class="sc-row" onclick="openArchivedChatsModal()">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <rect x="3" y="4" width="18" height="5" rx="1.5"></rect>
                        <path d="M5 9v9a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9"></path>
                        <line x1="10" y1="13" x2="14" y2="13"></line>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Archive chats</p>
                        <p class="sc-row-sub">View, restore, or delete archived chats</p>
                    </div>
                </div>
                <div class="sc-row danger" onclick="clearAllChats()">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="3 6 5 6 21 6"></polyline>
                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                        <line x1="10" y1="11" x2="10" y2="17"></line>
                        <line x1="14" y1="11" x2="14" y2="17"></line>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Delete all chats</p>
                        <p class="sc-row-sub">Permanently remove all history</p>
                    </div>
                </div>
            </div>
            <div class="sc-section">
                <div class="sc-section-title">Preferences</div>
                <div class="sc-row" onclick="exportChatHistory()" style="cursor:pointer;">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"></path>
                        <polyline points="17 21 17 13 7 13 7 21"></polyline>
                        <polyline points="7 3 7 8 15 8"></polyline>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Export chat history</p>
                        <p class="sc-row-sub">Download all your chats as a JSON file</p>
                    </div>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#666" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;margin-left:8px;">
                        <polyline points="8 17 12 21 16 17"></polyline>
                        <line x1="12" y1="12" x2="12" y2="21"></line>
                        <path d="M20.88 18.09A5 5 0 0 0 18 9h-1.26A8 8 0 1 0 3 16.29"></path>
                    </svg>
                </div>
            </div>`,

        privacy: (() => {
            const prefs = JSON.parse(localStorage.getItem('catura-privacy-prefs') || '{"analytics":true,"training":true}');
            return `
            <!-- Privacy overview row -->
            <div class="sc-section">
                <div class="sc-section-title">Privacy controls</div>
                <div class="sc-row" onclick="showPrivacyModal()" style="cursor:pointer;">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Data &amp; privacy policy</p>
                        <p class="sc-row-sub">View how we collect, use, and protect your data</p>
                    </div>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#666" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;margin-left:8px;">
                        <polyline points="9 18 15 12 9 6"></polyline>
                    </svg>
                </div>
            </div>

            <!-- Data preferences toggles -->
            <div class="sc-section">
                <div class="sc-section-title">Data preferences</div>

                <!-- Toggle: Location metadata -->
                <div class="sc-row-block">
                    <div class="sc-row-top">
                        <div class="sc-row-icon-wrap">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"></path>
                                <circle cx="12" cy="10" r="3"></circle>
                            </svg>
                        </div>
                        <div class="sc-row-body">
                            <p class="sc-row-label">Location metadata</p>
                            <p class="sc-row-sub">Allow approximate location to improve nearby, regional, and timezone-aware responses. City and region only — never precise GPS.</p>
                        </div>
                        <label class="toggle-switch" title="Enable location metadata">
                            <input type="checkbox" id="locationMetadataToggle" onchange="handleLocationToggle(this.checked)">
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                </div>

                <!-- Toggle: Help improve Catura AI (analytics) -->
                <div class="sc-row-block">
                    <div class="sc-row-top">
                        <div class="sc-row-icon-wrap">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3H14z"></path>
                                <path d="M7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"></path>
                            </svg>
                        </div>
                        <div class="sc-row-body">
                            <p class="sc-row-label">Help improve Catura AI</p>
                            <p class="sc-row-sub">Share anonymous usage statistics, crash reports, feature usage, and quality signals to help improve performance, reliability, and response quality. No conversations, passwords, or personal data are ever collected.</p>
                        </div>
                        <label class="toggle-switch" title="Help improve Catura AI">
                            <input type="checkbox" id="analyticsToggle" ${prefs.analytics ? 'checked' : ''} onchange="savePrivacyPref('analytics', this.checked)">
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                    <div class="priv-toggle-detail">
                        <div class="priv-detail-group">
                            <span class="priv-detail-badge priv-badge-collect">Collects</span>
                            <span class="priv-detail-text">Response speed · Failed requests · Feature usage · Thumbs up/down · Browser type · Country/region</span>
                        </div>
                        <div class="priv-detail-group">
                            <span class="priv-detail-badge priv-badge-never">Never</span>
                            <span class="priv-detail-text">Conversations · Passwords · API keys · Personal identifiers</span>
                        </div>
                    </div>
                </div>

                <!-- Toggle: AI model training -->
                <div class="sc-row-block">
                    <div class="sc-row-top">
                        <div class="sc-row-icon-wrap">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <ellipse cx="12" cy="5" rx="9" ry="3"></ellipse>
                                <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"></path>
                                <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"></path>
                            </svg>
                        </div>
                        <div class="sc-row-body">
                            <p class="sc-row-label">AI model training</p>
                            <p class="sc-row-sub">Allow conversations and feedback to be used to improve future AI models and responses. Sensitive information is automatically filtered before storage.</p>
                        </div>
                        <label class="toggle-switch" title="Allow AI model training">
                            <input type="checkbox" id="trainingToggle" ${prefs.training ? 'checked' : ''} onchange="savePrivacyPref('training', this.checked)">
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                    <div class="priv-toggle-detail">
                        <div class="priv-detail-group">
                            <span class="priv-detail-badge priv-badge-collect">Stores</span>
                            <span class="priv-detail-text">Sanitized prompts · Sanitized responses · Feedback ratings · Regenerate events · Language</span>
                        </div>
                        <div class="priv-detail-group">
                            <span class="priv-detail-badge priv-badge-filtered">Auto-filtered</span>
                            <span class="priv-detail-text">Emails · Phone numbers · Passwords · API keys · Addresses · Banking info · Personal IDs</span>
                        </div>
                        <div class="priv-detail-group">
                            <span class="priv-detail-badge priv-badge-never">Never</span>
                            <span class="priv-detail-text">Real-time live training · Raw sensitive data · GPS coordinates</span>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Privacy assurance strip -->
            <div class="priv-assurance-strip">
                <div class="priv-assurance-item">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#10a37f" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
                    <span>PII auto-filtered</span>
                </div>
                <div class="priv-assurance-item">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#10a37f" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
                    <span>Opt-out anytime</span>
                </div>
                <div class="priv-assurance-item">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#10a37f" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
                    <span>No data sold</span>
                </div>
                <div class="priv-assurance-item">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#10a37f" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
                    <span>Transparent collection</span>
                </div>
            </div>`;
        })(),

        // ============================
        // 🔊 SPEECH TAB
        // ============================
        speech: `
            <div class="sc-section">
                <div class="sc-section-title">Voice & speech</div>
                <div class="sc-row disabled">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path>
                        <path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>
                        <line x1="12" y1="19" x2="12" y2="23"></line>
                        <line x1="8" y1="23" x2="16" y2="23"></line>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Voice input</p>
                        <p class="sc-row-sub soon">Coming soon</p>
                    </div>
                </div>
                <div class="sc-row disabled">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon>
                        <path d="M19.07 4.93a10 10 0 0 1 0 14.14"></path>
                        <path d="M15.54 8.46a5 5 0 0 1 0 7.07"></path>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Text-to-speech</p>
                        <p class="sc-row-sub soon">Coming soon</p>
                    </div>
                </div>
                <div class="sc-row disabled">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <line x1="8" y1="6" x2="8" y2="18"></line>
                        <line x1="12" y1="3" x2="12" y2="21"></line>
                        <line x1="16" y1="7" x2="16" y2="17"></line>
                        <line x1="4" y1="9" x2="4" y2="15"></line>
                        <line x1="20" y1="9" x2="20" y2="15"></line>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Voice style</p>
                        <p class="sc-row-sub soon">Coming soon</p>
                    </div>
                </div>
            </div>`,

        // ============================
        // 🎨 PERSONALIZATION TAB
        // ============================
        personalization: `
            <div class="sc-section">
                <div class="sc-section-title">Custom instructions</div>
                <div class="sc-row disabled">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M12 20h9"></path>
                        <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Custom instructions</p>
                        <p class="sc-row-sub soon">Tell Catura how to respond — coming soon</p>
                    </div>
                </div>
                <div class="sc-row disabled">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <circle cx="12" cy="12" r="10"></circle>
                        <path d="M8 14s1.5 2 4 2 4-2 4-2"></path>
                        <line x1="9" y1="9" x2="9.01" y2="9"></line>
                        <line x1="15" y1="9" x2="15.01" y2="9"></line>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">AI personality</p>
                        <p class="sc-row-sub soon">Coming soon</p>
                    </div>
                </div>
            </div>
            <div class="sc-section">
                <div class="sc-section-title">Memory &amp; context</div>

                <!-- Enable Memory toggle -->
                <div class="sc-row-block">
                    <div class="sc-row-top">
                        <div class="sc-row-icon-wrap">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path>
                            </svg>
                        </div>
                        <div class="sc-row-body">
                            <p class="sc-row-label">Enable Memory</p>
                            <p class="sc-row-sub">When on, Catura saves what you share and uses it across all chats. When off, info is only used in the current session and forgotten when the chat ends.</p>
                        </div>
                        <label class="toggle-switch" title="Enable Memory">
                            <input type="checkbox" id="memoryToggle" ${memoryEnabled ? 'checked' : ''} onchange="toggleMemoryEnabled(this.checked)">
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                </div>

                <!-- View Saved Memories -->
                <div class="sc-row" onclick="viewSavedMemories()" style="cursor:pointer;">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
                        <circle cx="12" cy="12" r="3"></circle>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">View Saved Memories</p>
                        <p class="sc-row-sub">See and manage what Catura has stored about you</p>
                    </div>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;margin-left:8px;opacity:0.4;"><polyline points="9 18 15 12 9 6"/></svg>
                </div>

                <!-- Clear All Memories -->
                <div class="sc-row sc-memory-danger" onclick="clearAllMemories()" style="cursor:pointer;">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="3 6 5 6 21 6"></polyline>
                        <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path>
                        <path d="M10 11v6"></path><path d="M14 11v6"></path>
                        <path d="M9 6V4h6v2"></path>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Clear All Memories</p>
                        <p class="sc-row-sub">Delete everything Catura has saved — cannot be undone</p>
                    </div>
                </div>
            </div>

            <!-- ============================ -->
            <!-- 🛠️ SKILLS SECTION -->
            <!-- ============================ -->
            <div class="sc-section">
                <div class="sc-section-title">Skills</div>

                <!-- Manage Skills (opens terminal-style install panel) -->
                <div class="sc-row" onclick="openSkillsPanel()" style="cursor:pointer;">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="16 18 22 12 16 6"></polyline>
                        <polyline points="8 6 2 12 8 18"></polyline>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Manage Skills</p>
                        <p class="sc-row-sub" id="skillsCountSub">Install and manage skills Catura can use</p>
                    </div>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;margin-left:8px;opacity:0.4;"><polyline points="9 18 15 12 9 6"/></svg>
                </div>
            </div>`,

        // ============================
        // 🗄️ DATA CONTROLS TAB
        // ============================
        datacontrols: `
            <div class="sc-section">
                <div class="sc-section-title">Your data</div>
                <div class="sc-row disabled">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"></path>
                        <polyline points="17 21 17 13 7 13 7 21"></polyline>
                        <polyline points="7 3 7 8 15 8"></polyline>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Export all data</p>
                        <p class="sc-row-sub soon">Download a copy of your data — coming soon</p>
                    </div>
                </div>
                <div class="sc-row disabled">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <ellipse cx="12" cy="5" rx="9" ry="3"></ellipse>
                        <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"></path>
                        <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"></path>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Manage stored data</p>
                        <p class="sc-row-sub soon">Coming soon</p>
                    </div>
                </div>
                <div class="sc-row disabled">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Data sharing preferences</p>
                        <p class="sc-row-sub soon">Coming soon</p>
                    </div>
                </div>
            </div>`,

        // ============================
        // 👤 ACCOUNT TAB
        // ============================
        account: `
            <div class="sc-section">
                <div class="sc-section-title">Account details</div>
                <div class="sc-profile-card">
                    <div class="sc-avatar">${initials}</div>
                    <div>
                        <p class="sc-profile-name">${fullName}</p>
                        <p class="sc-profile-email">${email}</p>
                    </div>
                </div>
            </div>
            <div class="sc-section">
                <div class="sc-section-title">Subscription</div>
                <div class="sc-row" onclick="openPlansModal()" style="cursor:pointer;">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <rect x="1" y="4" width="22" height="16" rx="2" ry="2"></rect>
                        <line x1="1" y1="10" x2="23" y2="10"></line>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Upgrade plan</p>
                        <p class="sc-row-sub soon">View all plans</p>
                    </div>
                </div>
                <div class="sc-row">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Current plan: Free</p>
                        <p class="sc-row-sub">Access to core models and features</p>
                    </div>
                </div>
            </div>
            <div class="sc-section">
                <div class="sc-section-title">Security</div>
                <div class="sc-row" onclick="openChangePasswordModal()" style="cursor:pointer;">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
                        <path d="M7 11V7a5 5 0 0 1 10 0v4"></path>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Change password</p>
                        <p class="sc-row-sub">Update your account password</p>
                    </div>
                </div>
                <div class="sc-row" id="mfaSettingsRow" onclick="openMFAModal()" style="cursor:pointer;">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>
                        <line x1="9" y1="12" x2="11" y2="14"></line>
                        <line x1="11" y1="14" x2="15" y2="10"></line>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label">Multi-factor authentication</p>
                        <p class="sc-row-sub" id="mfaStatusSub">Loading…</p>
                    </div>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;margin-left:8px;opacity:0.4;">
                        <polyline points="9 18 15 12 9 6"></polyline>
                    </svg>
                </div>
                <div class="sc-row danger" onclick="showDeleteAccountModal()" style="cursor:pointer;">
                    <svg class="sc-row-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="3 6 5 6 21 6"></polyline>
                        <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path>
                        <path d="M10 11v6M14 11v6"></path>
                        <path d="M9 6V4h6v2"></path>
                    </svg>
                    <div class="sc-row-body">
                        <p class="sc-row-label" style="color:#e06c6c;">Delete my account</p>
                        <p class="sc-row-sub">Permanently delete your account and all data</p>
                    </div>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#e06c6c" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;margin-left:8px;">
                        <polyline points="9 18 15 12 9 6"></polyline>
                    </svg>
                </div>
            </div>`,

        // ============================
        // ⌨️ SHORTCUTS TAB
        // ============================
        shortcuts: (() => {
            const sc = JSON.parse(localStorage.getItem('catura-shortcuts') || '{"darkMode":true,"newChat":true,"voice":false,"addFiles":true,"openSettings":true,"ghostChat":true}');
            return `
            <div class="sc-section">
                <div class="sc-section-title">Keyboard Shortcuts</div>
                <p class="sc-section-desc">Enable or disable keyboard shortcuts for quick actions.</p>

                <!-- Dark Mode -->
                <div class="sc-row-block">
                    <div class="sc-row-top">
                        <div class="sc-row-icon-wrap">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>
                            </svg>
                        </div>
                        <div class="sc-row-body">
                            <p class="sc-row-label">Dark mode</p>
                            <p class="sc-row-sub">Toggle dark/light theme <kbd class="shortcut-key">Ctrl</kbd>+<kbd class="shortcut-key">D</kbd></p>
                        </div>
                        <label class="toggle-switch" title="Toggle Dark Mode shortcut">
                            <input type="checkbox" id="sc-toggle-darkMode" ${sc.darkMode ? 'checked' : ''} onchange="saveShortcutToggle('darkMode', this.checked)">
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                </div>

                <!-- New Chat -->
                <div class="sc-row-block">
                    <div class="sc-row-top">
                        <div class="sc-row-icon-wrap">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path>
                                <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>
                            </svg>
                        </div>
                        <div class="sc-row-body">
                            <p class="sc-row-label">New chat</p>
                            <p class="sc-row-sub">Start a new conversation <kbd class="shortcut-key">Ctrl</kbd>+<kbd class="shortcut-key">Shift</kbd>+<kbd class="shortcut-key">N</kbd></p>
                        </div>
                        <label class="toggle-switch" title="Toggle New Chat shortcut">
                            <input type="checkbox" id="sc-toggle-newChat" ${sc.newChat ? 'checked' : ''} onchange="saveShortcutToggle('newChat', this.checked)">
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                </div>

                <!-- Voice -->
                <div class="sc-row-block">
                    <div class="sc-row-top">
                        <div class="sc-row-icon-wrap">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path>
                                <path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>
                                <line x1="12" y1="19" x2="12" y2="23"></line>
                                <line x1="8" y1="23" x2="16" y2="23"></line>
                            </svg>
                        </div>
                        <div class="sc-row-body">
                            <p class="sc-row-label">Voice input</p>
                            <p class="sc-row-sub">Activate voice input <kbd class="shortcut-key">Ctrl</kbd>+<kbd class="shortcut-key">Shift</kbd>+<kbd class="shortcut-key">V</kbd> <span class="badge-soon">Coming soon</span></p>
                        </div>
                        <label class="toggle-switch disabled-toggle" title="Coming soon">
                            <input type="checkbox" disabled>
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                </div>

                <!-- Add Files -->
                <div class="sc-row-block">
                    <div class="sc-row-top">
                        <div class="sc-row-icon-wrap">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"></path>
                            </svg>
                        </div>
                        <div class="sc-row-body">
                            <p class="sc-row-label">Add files</p>
                            <p class="sc-row-sub">Open file picker <kbd class="shortcut-key">Ctrl</kbd>+<kbd class="shortcut-key">U</kbd></p>
                        </div>
                        <label class="toggle-switch" title="Toggle Add Files shortcut">
                            <input type="checkbox" id="sc-toggle-addFiles" ${sc.addFiles ? 'checked' : ''} onchange="saveShortcutToggle('addFiles', this.checked)">
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                </div>

                <!-- Open Settings -->
                <div class="sc-row-block">
                    <div class="sc-row-top">
                        <div class="sc-row-icon-wrap">
                            <svg width="18" height="18" viewBox="0 0 100 100" fill="currentColor" xmlns="http://www.w3.org/2000/svg">
                                <path d="M50 61a11 11 0 1 1 0-22 11 11 0 0 1 0 22zm38-7-7.7-1.4a31.7 31.7 0 0 0-2.4-5.7l4.4-6.5a3 3 0 0 0-.4-3.8l-8.5-8.5a3 3 0 0 0-3.8-.4l-6.5 4.4a31.7 31.7 0 0 0-5.7-2.4L56 22a3 3 0 0 0-3-2.5h-6A3 3 0 0 0 44 22l-1.4 7.7a31.7 31.7 0 0 0-5.7 2.4l-6.5-4.4a3 3 0 0 0-3.8.4l-8.5 8.5a3 3 0 0 0-.4 3.8l4.4 6.5a31.7 31.7 0 0 0-2.4 5.7L12 54a3 3 0 0 0-2.5 3v6A3 3 0 0 0 12 66l7.7 1.4a31.7 31.7 0 0 0 2.4 5.7l-4.4 6.5a3 3 0 0 0 .4 3.8l8.5 8.5a3 3 0 0 0 3.8.4l6.5-4.4a31.7 31.7 0 0 0 5.7 2.4L44 78a3 3 0 0 0 3 2.5h6A3 3 0 0 0 56 78l1.4-7.7a31.7 31.7 0 0 0 5.7-2.4l6.5 4.4a3 3 0 0 0 3.8-.4l8.5-8.5a3 3 0 0 0 .4-3.8l-4.4-6.5a31.7 31.7 0 0 0 2.4-5.7L88 46a3 3 0 0 0 2.5-3v-6A3 3 0 0 0 88 34z"/>
                            </svg>
                        </div>
                        <div class="sc-row-body">
                            <p class="sc-row-label">Open settings</p>
                            <p class="sc-row-sub">Open the settings panel <kbd class="shortcut-key">Ctrl</kbd>+<kbd class="shortcut-key">A</kbd></p>
                        </div>
                        <label class="toggle-switch" title="Toggle Open Settings shortcut">
                            <input type="checkbox" id="sc-toggle-openSettings" ${sc.openSettings ? 'checked' : ''} onchange="saveShortcutToggle('openSettings', this.checked)">
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                </div>

                <!-- Ghost Chat -->
                <div class="sc-row-block">
                    <div class="sc-row-top">
                        <div class="sc-row-icon-wrap">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M9 10h.01"></path><path d="M15 10h.01"></path>
                                <path d="M12 2a8 8 0 0 1 8 8v10l-3-3-3 3-3-3-3 3-3-3V10a8 8 0 0 1 8-8z"></path>
                            </svg>
                        </div>
                        <div class="sc-row-body">
                            <p class="sc-row-label">Ghost chat</p>
                            <p class="sc-row-sub">Toggle ghost chat mode <kbd class="shortcut-key">Ctrl</kbd>+<kbd class="shortcut-key">G</kbd></p>
                        </div>
                        <label class="toggle-switch" title="Toggle Ghost Chat shortcut">
                            <input type="checkbox" id="sc-toggle-ghostChat" ${sc.ghostChat ? 'checked' : ''} onchange="saveShortcutToggle('ghostChat', this.checked)">
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                </div>

            </div>`;
        })()
    };

    content.innerHTML = tabs[tab] || tabs.general;

    // Sync the user message color picker UI to the saved color
    if (tab === 'profile' && typeof syncUserMsgColorUI === 'function') syncUserMsgColorUI();

    // Wire up Memory & context expand/collapse
    if (tab === 'personalization') {
        if (typeof refreshSkillsCountSub === 'function') refreshSkillsCountSub();
        const allRows = content.querySelectorAll('.sc-section .sc-row');
        allRows.forEach(row => {
            const chevron = row.querySelector('.sc-row-chevron');
            if (!chevron) return;
            const submenu = row.parentElement.querySelector('.sc-memory-submenu');
            if (!submenu) return;
            row.removeAttribute('onclick');
            row.style.cursor = 'pointer';
            row.style.opacity = '1';
            row.classList.remove('disabled');
            let memOpen = false;
            row.addEventListener('click', () => {
                memOpen = !memOpen;
                submenu.classList.toggle('open', memOpen);
                chevron.style.transform = memOpen ? 'rotate(90deg)' : '';
            });
        });
    }
};

// ============================
// ⌨️ KEYBOARD SHORTCUTS
// ============================
window.saveShortcutToggle = function(key, value) {
    const sc = JSON.parse(localStorage.getItem('catura-shortcuts') || '{"darkMode":true,"newChat":true,"voice":false,"addFiles":true,"openSettings":true,"ghostChat":true}');
    sc[key] = value;
    localStorage.setItem('catura-shortcuts', JSON.stringify(sc));
    saveSettingToCloud('shortcuts', sc);
    showToast(value ? `✓ Shortcut enabled` : `Shortcut disabled`);
};

function getShortcuts() {
    return JSON.parse(localStorage.getItem('catura-shortcuts') || '{"darkMode":true,"newChat":true,"voice":false,"addFiles":true,"openSettings":true,"ghostChat":true}');
}

document.addEventListener('keydown', function(e) {
    const sc = getShortcuts();
    const tag = document.activeElement?.tagName;
    const isInput = tag === 'INPUT' || tag === 'TEXTAREA' || document.activeElement?.isContentEditable;

    // Ctrl+D — Dark mode toggle
    if (sc.darkMode && e.ctrlKey && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'd') {
        if (isInput) return;
        e.preventDefault();
        const current = localStorage.getItem('catura-theme') || 'dark';
        const next = current === 'dark' ? 'light' : 'dark';
        if (typeof setTheme === 'function') setTheme(next);
        else document.documentElement.setAttribute('data-theme', next);
        showToast(`Theme: ${next.charAt(0).toUpperCase() + next.slice(1)}`);
        return;
    }

    // Ctrl+Shift+N — New chat
    if (sc.newChat && e.ctrlKey && e.shiftKey && !e.altKey && e.key.toLowerCase() === 'n') {
        e.preventDefault();
        if (typeof newChat === 'function') newChat();
        return;
    }

    // Ctrl+U — Add files
    if (sc.addFiles && e.ctrlKey && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'u') {
        e.preventDefault();
        const fi = document.getElementById('fileInput');
        if (fi) fi.click();
        return;
    }

    // Ctrl+A — Open settings
    if (sc.openSettings && e.ctrlKey && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'a') {
        if (isInput) return;
        e.preventDefault();
        if (typeof showSettings === 'function') showSettings();
        return;
    }

    // Ctrl+G — Toggle ghost chat
    if (sc.ghostChat && e.ctrlKey && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'g') {
        if (isInput) return;
        e.preventDefault();
        if (typeof toggleGhostChat === 'function') toggleGhostChat();
        return;
    }
});

// ============================
// 📥 EXPORT CHAT HISTORY
// ============================
window.exportChatHistory = async function () {
    if (!currentUser) { showToast('❌ Not logged in'); return; }

    showToast('⏳ Preparing export…');

    try {
        // 1. Fetch all sessions
        const { data: sessions, error: sessErr } = await supabaseClient
            .from('chat_sessions')
            .select('*')
            .eq('user_id', currentUser.id)
            .order('created_at', { ascending: true });

        if (sessErr) throw sessErr;

        // 2. Fetch all messages
        const { data: messages, error: msgErr } = await supabaseClient
            .from('messages')
            .select('*')
            .eq('user_id', currentUser.id)
            .order('created_at', { ascending: true });

        if (msgErr) throw msgErr;

        // 3. Group messages under their sessions
        const sessionMap = {};
        (sessions || []).forEach(s => {
            sessionMap[s.session_id || s.id] = {
                title: s.title || 'Untitled chat',
                created_at: s.created_at,
                messages: []
            };
        });

        (messages || []).forEach(m => {
            const key = m.session_id;
            if (sessionMap[key]) {
                sessionMap[key].messages.push({
                    role: m.role,
                    content: m.content,
                    created_at: m.created_at
                });
            }
        });

        // 4. Build export object
        const exportData = {
            exported_at: new Date().toISOString(),
            user_email: currentUser.email,
            total_chats: Object.keys(sessionMap).length,
            total_messages: (messages || []).length,
            chats: Object.values(sessionMap)
        };

        // 5. Download as JSON file
        const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        const dateStr = new Date().toISOString().slice(0, 10);
        a.download = `catura-chats-${dateStr}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);

        showToast(`✓ Exported ${exportData.total_chats} chats successfully`);

    } catch (err) {
        console.error('Export error:', err);
        showToast('❌ Export failed. Please try again.');
    }
};

// ============================
// 🗂️ ARCHIVE ALL CHATS
// ============================
window.archiveAllChats = async function () {
    showModal({
        type: 'confirm',
        icon: `<polyline points="21 8 21 21 3 21 3 8"/><rect x="1" y="3" width="22" height="5"/><line x1="10" y1="12" x2="14" y2="12"/>`,
        title: 'Archive all chats',
        subtitle: 'They will be hidden from your history',
        confirmLabel: 'Archive',
        onConfirm: async () => {
            const { error } = await supabaseClient
                .from("chat_sessions").update({ archived: true }).eq("user_id", currentUser.id);
            if (error) {
                showToast("❌ Archive failed: " + (error.message || "Check console for details"));
            } else {
                showToast("✓ All chats archived successfully");
                if (typeof window.refreshHistoryList === "function") window.refreshHistoryList(true);
                else if (typeof loadChatHistory === "function") loadChatHistory();
                else if (typeof showHistory === "function") showHistory();
                if (typeof window._lsync_broadcast === "function") window._lsync_broadcast("history_changed");
            }
        }
    });
};

// ============================
// 🗑️ DELETE ALL CHATS
// ============================
window.clearAllChats = async function () {
    showModal({
        type: 'confirm',
        dangerous: true,
        icon: `<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>`,
        title: 'Delete all chats',
        subtitle: 'This action cannot be undone',
        message: 'All your chat history and messages will be permanently removed.',
        confirmLabel: 'Delete all',
        onConfirm: async () => {
            const { error: msgErr } = await supabaseClient.from("messages").delete().eq("user_id", currentUser.id);
            if (msgErr) { showToast("❌ Failed to delete messages"); return; }
            const { error: sessErr } = await supabaseClient.from("chat_sessions").delete().eq("user_id", currentUser.id);
            if (sessErr) { showToast("❌ Failed to delete sessions"); return; }
            showToast("✓ All chats deleted successfully");
            const chatbox   = document.getElementById("chatbox");
            const inputArea = document.getElementById("inputArea");
            const app       = document.getElementById("app");
            if (chatbox) chatbox.innerHTML = "";
            currentSessionId = generateSessionId();
            firstMessage = true;
            if (inputArea) { inputArea.classList.remove("bottom"); inputArea.classList.add("center"); }
            if (app) app.classList.add("greeting-mode");
            displayGreeting();
            closeSettings();
            if (typeof window.refreshHistoryList === "function") window.refreshHistoryList();
            if (typeof window._lsync_broadcast === "function") window._lsync_broadcast("history_changed");
        }
    });
};

// ============================
// 🗄️ ARCHIVED CHATS MODAL
// ============================
window.openArchivedChatsModal = async function () {
    if (!currentUser) {
        showToast("Please log in to view archived chats");
        return;
    }

    const existing = document.getElementById('archive-modal-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'archive-modal-overlay';
    overlay.innerHTML = `
        <div class="cm-backdrop" id="archiveModalBackdrop"></div>
        <div class="archive-dialog" role="dialog" aria-modal="true">
            <div class="archive-modal-header">
                <div class="archive-modal-title-wrap">
                    <h3 class="cm-title">Archive chats</h3>
                    <p class="cm-subtitle">Restore or permanently delete archived chats</p>
                </div>
                <button class="archive-modal-close" id="archiveModalCloseBtn" title="Close" aria-label="Close">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <line x1="18" y1="6" x2="6" y2="18"></line>
                        <line x1="6" y1="6" x2="18" y2="18"></line>
                    </svg>
                </button>
            </div>
            <div class="archive-modal-toolbar">
                <button class="archive-tool-btn restore" id="archiveRestoreBtn" disabled>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M3 12a9 9 0 1 0 3-6.7"></path>
                        <polyline points="3 4 3 9 8 9"></polyline>
                    </svg>
                    <span>Restore selected</span>
                </button>
                <button class="archive-tool-btn delete" id="archiveDeleteBtn" disabled>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="3 6 5 6 21 6"></polyline>
                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                        <line x1="10" y1="11" x2="10" y2="17"></line>
                        <line x1="14" y1="11" x2="14" y2="17"></line>
                    </svg>
                    <span>Delete selected</span>
                </button>
            </div>
            <div class="archive-list" id="archiveList">
                <div style="padding:24px 14px;text-align:center;font-size:12px;color:#666;">Loading…</div>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);
    requestAnimationFrame(() => overlay.classList.add('cm-visible'));

    function closeArchiveModal() {
        overlay.classList.remove('cm-visible');
        setTimeout(() => overlay.remove(), 300);
        document.removeEventListener('keydown', escHandler);
    }
    function escHandler(e) {
        if (e.key === 'Escape') closeArchiveModal();
    }
    document.getElementById('archiveModalCloseBtn').onclick = closeArchiveModal;
    document.getElementById('archiveModalBackdrop').onclick = closeArchiveModal;
    document.addEventListener('keydown', escHandler);

    const selected = new Set();
    const restoreBtn = document.getElementById('archiveRestoreBtn');
    const deleteBtn = document.getElementById('archiveDeleteBtn');
    const list = document.getElementById('archiveList');

    function updateToolbar() {
        const has = selected.size > 0;
        restoreBtn.disabled = !has;
        deleteBtn.disabled = !has;
    }

    function removeRows(ids) {
        ids.forEach(id => {
            selected.delete(id);
            const el = list.querySelector(`.archive-item[data-session-id="${CSS.escape(id)}"]`);
            if (el) el.remove();
        });
        if (!list.querySelector('.archive-item')) {
            list.innerHTML = `<div class="no-history">No archived chats.</div>`;
        }
        updateToolbar();
    }

    const { data, error } = await supabaseClient
        .from("chat_sessions").select("*")
        .eq("user_id", currentUser.id)
        .eq("archived", true)
        .order("created_at", { ascending: false });

    // Modal may have been closed while the request was in flight
    if (!document.getElementById('archive-modal-overlay')) return;

    if (error) {
        list.innerHTML = `<div style="padding:24px 14px;text-align:center;font-size:12px;color:#e06c6c;">Failed to load archived chats.</div>`;
        console.error("❌ Archived chats failed:", error.message);
        return;
    }

    if (!data || data.length === 0) {
        list.innerHTML = `<div class="no-history">No archived chats.</div>`;
        return;
    }

    list.innerHTML = "";
    data.forEach(session => {
        const date = new Date(session.created_at).toLocaleDateString();
        const row = document.createElement('div');
        row.classList.add('archive-item');
        row.dataset.sessionId = session.session_id;
        row.innerHTML = `
            <label class="archive-checkbox-wrap">
                <input type="checkbox" class="archive-checkbox">
            </label>
            <div class="archive-item-info">
                <span class="archive-item-title">${(session.title || "Untitled").replace(/</g, '&lt;')}</span>
                <span class="archive-item-date">${date}</span>
            </div>
        `;
        const checkbox = row.querySelector('.archive-checkbox');
        checkbox.onchange = () => {
            if (checkbox.checked) selected.add(session.session_id);
            else selected.delete(session.session_id);
            row.classList.toggle('checked', checkbox.checked);
            updateToolbar();
        };
        list.appendChild(row);
    });

    restoreBtn.onclick = async () => {
        if (selected.size === 0) return;
        const ids = Array.from(selected);
        restoreBtn.disabled = true; deleteBtn.disabled = true;
        const { error } = await supabaseClient
            .from("chat_sessions")
            .update({ archived: false })
            .in("session_id", ids)
            .eq("user_id", currentUser.id);
        if (error) {
            showToast("❌ Restore failed");
            updateToolbar();
            return;
        }
        showToast(`✓ Restored ${ids.length} chat${ids.length > 1 ? 's' : ''}`);
        removeRows(ids);
        if (typeof window.refreshHistoryList === "function") window.refreshHistoryList(true);
        else if (typeof showHistory === "function") showHistory();
        if (typeof window._lsync_broadcast === "function") window._lsync_broadcast("history_changed");
    };

    deleteBtn.onclick = () => {
        if (selected.size === 0) return;
        const ids = Array.from(selected);
        showModal({
            type: 'confirm',
            dangerous: true,
            icon: `<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>`,
            title: `Delete ${ids.length} chat${ids.length > 1 ? 's' : ''}`,
            subtitle: 'This action cannot be undone',
            message: 'These chats and all their messages will be permanently removed.',
            confirmLabel: 'Delete',
            onConfirm: async () => {
                const { error: msgErr } = await supabaseClient.from("messages").delete()
                    .in("session_id", ids).eq("user_id", currentUser.id);
                if (msgErr) { showToast("❌ Failed to delete messages"); return; }
                const { error: sessErr } = await supabaseClient.from("chat_sessions").delete()
                    .in("session_id", ids).eq("user_id", currentUser.id);
                if (sessErr) { showToast("❌ Failed to delete chats"); return; }
                showToast(`✓ Deleted ${ids.length} chat${ids.length > 1 ? 's' : ''}`);
                removeRows(ids);
                if (typeof window.refreshHistoryList === "function") window.refreshHistoryList();
                if (typeof window._lsync_broadcast === "function") window._lsync_broadcast("history_changed");
                if (ids.includes(currentSessionId)) {
                    currentSessionId = generateSessionId();
                    firstMessage = true;
                    const chatbox = document.getElementById("chatbox");
                    const inputArea = document.getElementById("inputArea");
                    const app = document.getElementById("app");
                    if (chatbox) chatbox.innerHTML = "";
                    if (inputArea) { inputArea.classList.remove("bottom"); inputArea.classList.add("center"); }
                    if (app) app.classList.add("greeting-mode");
                    displayGreeting();
                }
            }
        });
    };

    updateToolbar();
};

// ============================
// 🗑️ DELETE SINGLE CHAT
// ============================
async function deleteSingleChat(sessionId) {
    showModal({
        type: 'confirm',
        dangerous: true,
        icon: `<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>`,
        title: 'Delete chat',
        subtitle: 'This cannot be undone',
        confirmLabel: 'Delete',
        onConfirm: async () => {
            const { error: msgErr } = await supabaseClient.from("messages").delete()
                .eq("session_id", sessionId).eq("user_id", currentUser.id);
            if (msgErr) { showToast("❌ Failed to delete messages"); return; }
            const { error: sessErr } = await supabaseClient.from("chat_sessions").delete()
                .eq("session_id", sessionId).eq("user_id", currentUser.id);
            if (sessErr) { showToast("❌ Failed to delete session"); return; }
            if (currentSessionId === sessionId) {
                currentSessionId = generateSessionId();
                firstMessage = true;
                const chatbox   = document.getElementById("chatbox");
                const inputArea = document.getElementById("inputArea");
                const app       = document.getElementById("app");
                if (chatbox) chatbox.innerHTML = "";
                if (inputArea) { inputArea.classList.remove("bottom"); inputArea.classList.add("center"); }
                if (app) app.classList.add("greeting-mode");
                displayGreeting();
            }
            showToast("✓ Chat deleted");
            if (typeof window.refreshHistoryList === "function") window.refreshHistoryList(true);
            else if (typeof showHistory === "function") showHistory();
            if (typeof window._lsync_broadcast === "function") window._lsync_broadcast("history_changed");
        }
    });
}

// ============================
// 🗄️ ARCHIVE SINGLE CHAT
// ============================
async function archiveSingleChat(sessionId) {
    const { error } = await supabaseClient
        .from("chat_sessions")
        .update({ archived: true })
        .eq("session_id", sessionId)
        .eq("user_id", currentUser.id);
    if (error) {
        showToast("❌ Failed to archive chat");
        return;
    }
    showToast("✓ Chat archived");
    // Archived chats are hidden from the main history list, refresh it
    if (typeof window.refreshHistoryList === "function") window.refreshHistoryList(true);
    else if (typeof showHistory === "function") showHistory();
    if (typeof window._lsync_broadcast === "function") window._lsync_broadcast("history_changed");
}
window.archiveSingleChat = archiveSingleChat;

// ============================
// ✏️ RENAME CHAT
// ============================
async function renameChat(sessionId, currentTitle, titleEl) {
    showModal({
        type: 'prompt',
        icon: `<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>`,
        title: 'Rename chat',
        subtitle: 'Give this conversation a new title',
        inputValue: currentTitle,
        inputPlaceholder: 'Chat title…',
        confirmLabel: 'Rename',
        onConfirm: async (newTitle) => {
            if (!newTitle || newTitle === currentTitle) return;
            const { error } = await supabaseClient.from("chat_sessions")
                .update({ title: newTitle.trim() })
                .eq("session_id", sessionId)
                .eq("user_id", currentUser.id);
            if (error) { showToast("❌ Failed to rename chat"); return; }
            // Update immediately in place (fast path)...
            if (titleEl && titleEl.isConnected) titleEl.textContent = newTitle.trim();
            // ...and also do a full refresh so the cached session list, any
            // duplicate render, or a stale titleEl reference can't leave the
            // sidebar showing the old title.
            if (typeof window.refreshHistoryList === "function") window.refreshHistoryList(true);
            showToast("✓ Chat renamed");
            if (typeof window._lsync_broadcast === "function") window._lsync_broadcast("history_changed");
        }
    });
}

// ============================
// ⋯ HISTORY 3-DOT MENU
// ============================
function closeAllMenus() {
    document.querySelectorAll(".history-dropdown.open").forEach(d => d.classList.remove("open"));
}

// Highlight whichever history item matches the currently open session,
// and clear the highlight from every other one. Safe to call any time
// (e.g. list not built yet, or session not in the currently loaded list).
function highlightActiveHistoryItem(sessionId) {
    const list = document.getElementById("historyAccordionList");
    if (!list) return;
    list.querySelectorAll(".history-item").forEach(el => {
        el.classList.toggle("active", el.dataset.sessionId === sessionId);
    });
}
window.highlightActiveHistoryItem = highlightActiveHistoryItem;

function buildHistoryItem(session, openSessionFn) {
    const date = new Date(session.created_at).toLocaleDateString();

    const item = document.createElement("div");
    item.classList.add("sidebar-item", "history-item");
    item.dataset.sessionId = session.session_id;
    if (session.session_id === currentSessionId) item.classList.add("active");

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
    info.onclick = () => {
        const overlay = document.getElementById("settingsOverlay");
        if (overlay) overlay.classList.remove("active");
        highlightActiveHistoryItem(session.session_id);
        openSessionFn(session.session_id);
    };

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
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg>
            Open chat
        </button>
        <button class="history-dropdown-item" data-action="rename">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>
            Rename
        </button>
        <button class="history-dropdown-item" data-action="archive">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="5" rx="1.5"></rect><path d="M5 9v9a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9"></path><line x1="10" y1="13" x2="14" y2="13"></line></svg>
            Archive
        </button>
        <button class="history-dropdown-item danger" data-action="delete">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>
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
            if (action === "open") { highlightActiveHistoryItem(session.session_id); openSessionFn(session.session_id); }
            if (action === "rename") renameChat(session.session_id, session.title || "Untitled", titleEl);
            if (action === "archive") archiveSingleChat(session.session_id);
            if (action === "delete") deleteSingleChat(session.session_id);
        };
    });

    // NOTE: closeAllMenus on document click is registered once at module level (below buildHistoryItem).

    const menuWrap = document.createElement("div");
    menuWrap.classList.add("history-menu-wrap");
    menuWrap.appendChild(menuBtn);
    menuWrap.appendChild(dropdown);

    item.appendChild(info);
    item.appendChild(menuWrap);
    return item;
}

// ── Single global handler to close all history dropdowns on outside click ──
document.addEventListener("click", closeAllMenus);

// ============================
// 🔭 MUTATION OBSERVER REGISTRY
// ============================
// Every MutationObserver in this file is registered here instead of being
// left as a bare `new MutationObserver(...).observe(...)`. Today that's not
// fixing a live leak — #app, #chatbox, and document.body all live for the
// whole page lifetime in this single-page app, so nothing is ever piling up.
// This exists for when that stops being true: the moment any client-side
// routing/navigation replaces #chatbox or #app with a fresh node (instead of
// just mutating their contents), call window.disconnectAllObservers() before
// the swap and window.reattachObserver(meta, newNode) after it, and none of
// the 4 observers below will end up orphaned on a detached element.
const __observerRegistry = [];

function registerObserver(meta, observer, target, options) {
    observer.observe(target, options);
    __observerRegistry.push({ meta, observer, target, options });
    return observer;
}

// Disconnects every tracked observer. Call this before tearing down or
// replacing a watched root element (#app, #chatbox, document.body).
window.disconnectAllObservers = function () {
    __observerRegistry.forEach(({ observer }) => observer.disconnect());
};

// Re-points a specific observer (by its registration name) at a new node —
// use after swapping in a replacement #chatbox/#app so the same observer
// keeps working instead of silently watching a detached element.
window.reattachObserver = function (meta, newTarget) {
    const entry = __observerRegistry.find(e => e.meta === meta);
    if (!entry || !newTarget) return false;
    entry.observer.disconnect();
    entry.observer.observe(newTarget, entry.options);
    entry.target = newTarget;
    return true;
};

// ============================
// 🚀 APP START
// ============================
document.addEventListener("DOMContentLoaded", async function () {

    // ============================
    // 👻🔗 SYNC "greeting-mode" TO <body> + SHARE CHAT
    // ============================
    // The mobile header (ghost + share buttons) lives OUTSIDE #app in the
    // DOM, so CSS rules scoped to ".app:not(.greeting-mode) ..." never
    // matched it. Mirroring the class onto <body> lets both the desktop
    // ghost/share buttons (inside #app) and the mobile header buttons
    // (outside #app) react identically, no matter where they sit in markup.
    (function syncGreetingModeToBody() {
        const appEl = document.getElementById('app');
        if (!appEl) return;
        const sync = () => {
            document.body.classList.toggle('greeting-mode', appEl.classList.contains('greeting-mode'));
        };
        sync();
        registerObserver('greetingModeSync', new MutationObserver(sync), appEl, { attributes: true, attributeFilter: ['class'] });
    })();

    // ============================
    // 🔗 SHARE CHAT
    // ============================
    // Shown only once a message has been sent inside a chat (exactly when
    // the Ghost button fades out) — never on a fresh "New Chat" screen.
    // Generates a short, URL-safe random id, e.g. "aB3xQ9zK1m"
    function generateShareSlug(len) {
        len = len || 24;
        const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789';
        const bytes = new Uint8Array(len);
        crypto.getRandomValues(bytes);
        let out = '';
        for (let i = 0; i < len; i++) out += chars[bytes[i] % chars.length];
        return out;
    }

    window.shareChat = async function () {
        try {
            if (!currentUser || !currentSessionId) {
                if (typeof showToast === 'function') showToast('Nothing to share yet');
                return;
            }

            if (typeof showToast === 'function') showToast('Creating share link…');

            // 1. Pull the current conversation straight from the DB so the
            //    snapshot exactly matches what's saved (not just DOM state).
            const { data: msgs, error: msgErr } = await supabaseClient
                .from('messages')
                .select('role, content, file_urls, created_at')
                .eq('session_id', currentSessionId)
                .eq('user_id', currentUser.id)
                .order('created_at', { ascending: true });

            if (msgErr || !msgs || msgs.length === 0) {
                console.warn('Share: no messages to snapshot', msgErr);
                if (typeof showToast === 'function') showToast('Nothing to share yet');
                return;
            }

            // 2. Look up the session title (falls back to a generic label)
            let title = 'Catura AI Chat';
            try {
                const { data: sess } = await supabaseClient
                    .from('chat_sessions')
                    .select('title')
                    .eq('session_id', currentSessionId)
                    .eq('user_id', currentUser.id)
                    .single();
                if (sess && sess.title) title = sess.title;
            } catch (_) { /* non-fatal */ }

            // 3. Generate a unique slug, retrying on the rare collision
            let slug = generateShareSlug();
            for (let i = 0; i < 3; i++) {
                const { data: existing } = await supabaseClient
                    .from('shared_chats')
                    .select('id')
                    .eq('share_slug', slug)
                    .maybeSingle();
                if (!existing) break;
                slug = generateShareSlug();
            }

            // 4. Insert the snapshot — this is what makes the link work
            //    even if the original chat is later edited or deleted.
            const { error: insertErr } = await supabaseClient
                .from('shared_chats')
                .insert([{
                    share_slug: slug,
                    owner_id: currentUser.id,
                    session_id: currentSessionId,
                    title: title,
                    messages: msgs,
                    model_used: (typeof currentModel !== 'undefined' ? currentModel : null)
                }]);

            if (insertErr) {
                console.error('Share insert failed:', insertErr.message);
                if (typeof showToast === 'function') showToast('Share failed: ' + insertErr.message);
                return;
            }

            // 5. Build the real public link — NOT window.location.href
            const shareUrl = `${window.location.origin}/share/${slug}`;

            if (navigator.share) {
                await navigator.share({ title: title, url: shareUrl });
                return;
            }
            if (navigator.clipboard && navigator.clipboard.writeText) {
                await navigator.clipboard.writeText(shareUrl);
                if (typeof showToast === 'function') showToast('Chat link copied to clipboard');
                return;
            }
            if (typeof showToast === 'function') showToast('Unable to share on this device');
        } catch (err) {
            if (err && err.name === 'AbortError') return;
            console.warn('Share failed:', err);
            if (typeof showToast === 'function') showToast('Share failed');
        }
    };

    // ============================
    // ↓ SCROLL TO BOTTOM BUTTON
    // ============================
    (function initScrollToBottom() {
        const chatbox = document.getElementById('chatbox');
        const btn     = document.getElementById('scrollToBottomBtn');
        if (!chatbox || !btn) return;

        const THRESHOLD = 200;

        function updateBtn() {
            // Only show if content actually overflows the viewport
            const overflows = chatbox.scrollHeight > chatbox.clientHeight + 10;
            const distFromBottom = chatbox.scrollHeight - chatbox.scrollTop - chatbox.clientHeight;
            if (overflows && distFromBottom > THRESHOLD) {
                btn.classList.add('visible');
            } else {
                btn.classList.remove('visible');
            }
        }

        // Update on scroll
        chatbox.addEventListener('scroll', updateBtn, { passive: true });

        // Update whenever chatbox content changes (new chat, messages added/removed)
        registerObserver('scrollToBottom', new MutationObserver(updateBtn), chatbox, { childList: true, subtree: true });

        // Also update on window resize
        window.addEventListener('resize', updateBtn, { passive: true });

        // Initial check
        updateBtn();

        btn.addEventListener('click', function () {
            chatbox.scrollTo({ top: chatbox.scrollHeight, behavior: 'smooth' });
        });
    })();

    // ============================
    // 🧭 MESSAGE NAV TRACKER — ChatGPT-style right-edge minimap
    // ============================
    (function initMsgNavTracker() {
        const chatbox = document.getElementById('chatbox');
        const tracker = document.getElementById('msgNavTracker');
        const app     = document.getElementById('app');
        if (!chatbox || !tracker) return;

        const MIN_USER_MESSAGES = 5; // only appears once user has sent MORE than 5 messages
        let bodyModalOpen = false;   // true whenever a full-screen modal/overlay is present
        let rafPending = false;

        function isElementVisible(el) {
            if (!el) return false;
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity) === 0) return false;
            // feedbackModal and similar toggle visibility via an "active"/"open" class rather
            // than removal from the DOM — rely on actual rendered size as the real signal.
            const rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
        }

        function isBodyModalOpen() {
            // Any known overlay/modal pattern currently mounted directly on <body>,
            // plus the settings overlay when explicitly active.
            const settingsOverlay = document.getElementById('settingsOverlay');
            if (settingsOverlay && settingsOverlay.classList.contains('active') && isElementVisible(settingsOverlay)) return true;
            const selectors = [
                '[id$="Modal"]', '[class$="-modal-overlay"]', '[class*="modal-overlay"]'
            ];
            for (const sel of selectors) {
                const nodes = document.body.querySelectorAll(':scope > ' + sel);
                for (const node of nodes) {
                    if (isElementVisible(node)) return true;
                }
            }
            return false;
        }

        function getUserBars() {
            return Array.from(chatbox.querySelectorAll('.user-msg-wrapper'));
        }

        function snippetFor(wrapperEl) {
            const bubble = wrapperEl.querySelector('.message.user');
            let text = bubble ? bubble.innerText.trim() : '';
            if (!text) text = '📎 Attachment';
            return text.length > 60 ? text.slice(0, 60) + '…' : text;
        }

        function shouldShow() {
            if (bodyModalOpen) return false;
            if (app && app.classList.contains('greeting-mode')) return false;
            const overflows = chatbox.scrollHeight > chatbox.clientHeight + 40;
            if (!overflows) return false;
            return getUserBars().length > MIN_USER_MESSAGES;
        }

        // Build the two persistent pieces once; render() only updates their contents.
        const barsCol = document.createElement('div');
        barsCol.className = 'msg-nav-bars';
        const listPanel = document.createElement('div');
        listPanel.className = 'msg-nav-list';
        tracker.appendChild(barsCol);
        tracker.appendChild(listPanel);

        // Show the list ONLY while the pointer is over the bars or the list itself —
        // never from hovering the empty space around/between them.
        let hideTimer = null;
        function showList() {
            clearTimeout(hideTimer);
            listPanel.classList.add('show');
        }
        function scheduleHideList() {
            clearTimeout(hideTimer);
            hideTimer = setTimeout(() => listPanel.classList.remove('show'), 120);
        }
        barsCol.addEventListener('mouseenter', showList);
        barsCol.addEventListener('mouseleave', scheduleHideList);
        listPanel.addEventListener('mouseenter', showList);
        listPanel.addEventListener('mouseleave', scheduleHideList);
        // Keyboard users: focusing a bar or list item also reveals the list.
        barsCol.addEventListener('focusin', showList);
        listPanel.addEventListener('focusin', showList);
        barsCol.addEventListener('focusout', scheduleHideList);
        listPanel.addEventListener('focusout', scheduleHideList);

        function render() {
            const bars = getUserBars();
            barsCol.innerHTML = '';
            listPanel.innerHTML = '';

            if (!shouldShow()) {
                tracker.classList.remove('visible');
                listPanel.classList.remove('show');
                return;
            }

            function jumpTo(wrapperEl) {
                wrapperEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
                wrapperEl.classList.add('msg-nav-flash');
                setTimeout(() => wrapperEl.classList.remove('msg-nav-flash'), 900);
            }

            bars.forEach((wrapperEl, idx) => {
                // Thin bar (always visible)
                const bar = document.createElement('div');
                bar.className = 'msg-nav-bar';
                bar.dataset.index = String(idx);
                barsCol.appendChild(bar);

                // Matching row in the hover list panel
                const item = document.createElement('div');
                item.className = 'msg-nav-item';
                item.dataset.index = String(idx);
                item.setAttribute('role', 'button');
                item.setAttribute('tabindex', '0');
                item.textContent = snippetFor(wrapperEl);

                function jump() { jumpTo(wrapperEl); }
                item.addEventListener('click', jump);
                item.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); jump(); }
                });
                bar.addEventListener('click', jump);

                listPanel.appendChild(item);
            });

            tracker.classList.add('visible');
            updateActiveBar();
        }

        function updateActiveBar() {
            const bars = getUserBars();
            const barEls = barsCol.querySelectorAll('.msg-nav-bar');
            const itemEls = listPanel.querySelectorAll('.msg-nav-item');
            if (!bars.length || !barEls.length) return;

            const boxTop = chatbox.getBoundingClientRect().top;
            let closestIdx = 0;
            let closestDist = Infinity;
            bars.forEach((wrapperEl, idx) => {
                const dist = Math.abs(wrapperEl.getBoundingClientRect().top - boxTop);
                if (dist < closestDist) { closestDist = dist; closestIdx = idx; }
            });
            barEls.forEach((el, idx) => el.classList.toggle('active', idx === closestIdx));
            itemEls.forEach((el, idx) => el.classList.toggle('active', idx === closestIdx));

            // Keep the active row in view within the (possibly scrollable) hover list
            const activeItem = listPanel.querySelector('.msg-nav-item.active');
            if (activeItem) activeItem.scrollIntoView({ block: 'nearest' });
        }

        function scheduleRender() {
            if (rafPending) return;
            rafPending = true;
            requestAnimationFrame(() => { rafPending = false; render(); });
        }

        // Rebuild whenever chat content changes (new/loaded/streamed messages)
        registerObserver('msgNavChat', new MutationObserver(scheduleRender), chatbox, { childList: true, subtree: false });

        // Track modal/overlay open state on <body> so the tracker hides itself
        // whenever settings, or any other full-screen section, is opened.
        const bodyObserver = new MutationObserver(() => {
            const wasOpen = bodyModalOpen;
            bodyModalOpen = isBodyModalOpen();
            if (wasOpen !== bodyModalOpen) scheduleRender();
        });
        registerObserver('msgNavBody', bodyObserver, document.body, { childList: true, subtree: false, attributes: true, attributeFilter: ['class'] });

        // Reposition the active-bar highlight while the user scrolls the conversation
        chatbox.addEventListener('scroll', () => {
            if (tracker.classList.contains('visible')) updateActiveBar();
        }, { passive: true });

        window.addEventListener('resize', scheduleRender, { passive: true });

        // Expose a manual hook other flows (sendMessage, newChat, loadSession) can call
        window.refreshMsgNavTracker = scheduleRender;

        // Initial check
        scheduleRender();
    })();

    // Apply locally-cached theme/font immediately so the page doesn't flash defaults.
    // getUser() → loadSettingsFromCloud() will override these with the cloud values
    // if they differ (e.g. user changed settings on another device).
    initTheme();
    initFontSize();
    initUserMsgColor();
    initWebSearchUI();

    // ── Sidebar: open on desktop, closed on mobile ────────────────────────
    if (window.innerWidth > 768) {
        document.getElementById('sidebar')?.classList.add('open');
    }

    // getUser() fetches the Supabase session, populates currentUser, then calls
    // loadSettingsFromCloud() which overwrites theme/font/profile-pic/call-name
    // with the authoritative cloud values and calls displayGreeting() internally.
    await getUser();

    if (!currentUser) {
        window.location.href = "/auth";
        return;
    }

    // displayGreeting() was already called inside getUser() → loadSettingsFromCloud().
    // Call it once more here as a safety net in case the cloud load was a no-op
    // (new user with no saved settings) so the greeting still shows.
    displayGreeting();
    setTimeout(() => document.getElementById('input')?.focus(), 100);

    const chatbox   = document.getElementById("chatbox");
    const input     = document.getElementById("input");
    const inputArea = document.getElementById("inputArea");
    const app       = document.getElementById("app");

    window.autoResize = function () {
        input.style.height = "auto";
        input.style.height = input.scrollHeight + "px";
    };
    input.addEventListener("input", autoResize);

    // ============================
    // ✉️ SEND BUTTON — disabled state
    // ============================
    window.updateSendBtn = function () {
        const btn = document.getElementById('sendBtn');
        if (!btn) return;
        // Never disable while streaming (it's a stop button then)
        if (isStreaming) { btn.disabled = false; return; }
        const hasText  = input.value.trim().length > 0;
        const hasFiles = (typeof attachedFiles !== 'undefined') && attachedFiles.length > 0;
        btn.disabled = !(hasText || hasFiles);
    };
    // Run once on init so button starts disabled
    updateSendBtn();
    // Re-evaluate on every keystroke
    input.addEventListener("input", updateSendBtn);

    // ============================
    // 🔥 SEND MESSAGE — with file upload support
    // ============================
    window.sendMessage = async function () {
        const message  = input.value.trim();
        const hasFiles = (typeof attachedFiles !== 'undefined') && attachedFiles.length > 0;
        if (!message && !hasFiles) return;

        // ── 🛠️ Chat-triggered skill install: "install <link>" ──────────────
        // Short-circuits the normal send flow entirely — this never reaches the model.
        if (typeof _maybeHandleChatSkillInstall === 'function') {
            const handled = await _maybeHandleChatSkillInstall(message);
            if (handled) {
                input.value = '';
                if (typeof autoResizeInput === 'function') autoResizeInput();
                return;
            }
        }

        // ── Layout transition on first message ──────────────────────────────
        if (firstMessage) {
            chatbox.innerHTML = "";
            inputArea.classList.remove("center");
            inputArea.classList.add("bottom");
            app.classList.remove("greeting-mode");
        }

        // ── Snapshot + immediately clear attached files from preview ─────────
        const filesToSend = (typeof attachedFiles !== 'undefined') ? attachedFiles.slice() : [];
        if (typeof attachedFiles !== 'undefined') attachedFiles = [];
        if (typeof renderAttachedPreview === 'function') renderAttachedPreview();

        // ── Build user bubble (text + file previews) ─────────────────────────
        // If this send is the result of an edit, carry the group id forward
        const _editGroupId = pendingEditGroupId || null;
        pendingEditGroupId = null; // consume immediately

        const userBubble = createUserBubble(message, filesToSend, _editGroupId || undefined);
        if (_editGroupId) {
            userBubble.dataset.turnGroup = _editGroupId;
            userBubble.dataset.version   = "2";
        }
        chatbox.appendChild(userBubble);

        // ── Session: create on very first message ────────────────────────────
        if (firstMessage) {
            firstMessage = false;

            if (!ghostChatEnabled) {
            // Immediately insert with a placeholder title, then update with AI-generated one
            chatTitle = (message || (filesToSend.length ? filesToSend[0].name : 'File Chat')).substring(0, 40);
            const { error } = await supabaseClient.from("chat_sessions").insert([{
                session_id: currentSessionId,
                title     : chatTitle,
                user_id   : currentUser.id
            }]);
            if (error) {
                console.error("❌ Session insert failed:", error.message);
            } else {
                // 🔄 Instantly reflect the brand-new chat in the history list
                // (only rebuilds the DOM if the accordion is already open —
                // otherwise it'll simply be fresh next time it's opened).
                if (typeof refreshHistoryList === "function") refreshHistoryList();
                // Let other open tabs know a new session exists.
                if (typeof window._lsync_broadcast === "function") {
                    window._lsync_broadcast("history_changed");
                }
            }

            // 🏷️ Generate a smart AI title in the background
            const msgForTitle = message || (filesToSend.length ? filesToSend[0].name : '');
            if (msgForTitle) {
                fetch("/generate-title", {
                    method : "POST",
                    headers: { "Content-Type": "application/json" },
                    body   : JSON.stringify({ message: msgForTitle })
                })
                .then(r => r.json())
                .then(data => {
                    const aiTitle = data.title?.trim();
                    if (aiTitle && aiTitle.length > 1) {
                        chatTitle = aiTitle;
                        // Update in Supabase
                        supabaseClient.from("chat_sessions")
                            .update({ title: aiTitle })
                            .eq("session_id", currentSessionId)
                            .then(({ error: updErr }) => {
                                if (updErr) console.error("❌ Title update failed:", updErr.message);
                            });
                        // Update in history accordion if it's open
                        const histList = document.getElementById("historyAccordionList");
                        if (histList) {
                            const titleEls = histList.querySelectorAll(".history-title");
                            titleEls.forEach(el => {
                                // Find the item whose session matches (it'll be the first/top one)
                                const item = el.closest("[data-session-id]");
                                if (item && item.dataset.sessionId === currentSessionId) {
                                    el.textContent = aiTitle;
                                }
                            });
                        }
                        if (typeof window._lsync_broadcast === "function") {
                            window._lsync_broadcast("history_changed");
                        }
                    }
                })
                .catch(err => console.warn("Title gen failed:", err));
            }
            } // end !ghostChatEnabled
        }

        // ── File URLs (needed for DB save + fetch payload) ───────────────────
        const fileUrls = filesToSend.map(function(f) { return f.url; });

        // ── Save user message to DB ──────────────────────────────────────────
        if (!ghostChatEnabled) {
        const { error: userError } = await supabaseClient.from("messages").insert([{
            role      : "user",
            content   : message,
            session_id: currentSessionId,
            user_id   : currentUser.id,
            file_urls : fileUrls.length > 0 ? fileUrls : null
        }]);
        if (userError) console.error("❌ User message save failed:", userError.message);
        }

        // ── Clear input ──────────────────────────────────────────────────────
        input.value = "";
        input.style.height = "auto";
        if (typeof updateSendBtn === 'function') updateSendBtn();
        chatbox.scrollTop = chatbox.scrollHeight;

        // ── Thinking indicator ───────────────────────────────────────────────
        const heavy   = isHeavyQuery(message || (filesToSend.length ? filesToSend[0].name : ''));
        const thinking = heavy ? createThinkingIndicator() : createLightIndicator();
        chatbox.appendChild(thinking);
        chatbox.scrollTop = chatbox.scrollHeight;

        // ── Set streaming state + create abort controller ────────────────────
        activeAbortController = new AbortController();
        setStreamingState(true);

        // ── Build prompt text ────────────────────────────────────────────────
        let promptText = message;
        if (filesToSend.length > 0 && !message) {
            promptText = "Please analyse the attached file(s) and describe what you see in detail.";
        }

        // ── TOOL ROUTER: detect intent, update thinking label ──────────────
        // Backend handles ALL tool execution (search, weather, finance, etc.)
        // Frontend only detects intent to show the right "thinking" label.
        // We NEVER run frontend search — it causes spurious Sources chips on greetings.
        const webResults = [];
        const detectedIntent = message ? detectClientIntent(message) : "general";

        try {
            const model = getSelectedModel();

            // ── 🟣 PUTER-ROUTED MODELS (Claude, GPT-5.4 Mini, ...) — frontend-only, skips /chat entirely ──
            if (isPuterRoutedModel(model)) {
                const fullReply = await sendViaPuterModel(model, promptText, thinking, chatbox, filesToSend, detectedIntent, message);

                activeAbortController = null;
                setStreamingState(false);

                if (fullReply) {
                    if (ghostChatEnabled) {
                        ghostMemory.push({ role: "assistant", content: fullReply });
                        if (ghostMemory.length > GHOST_WINDOW) {
                            ghostMemory = ghostMemory.slice(ghostMemory.length - GHOST_WINDOW);
                        }
                    } else {
                        const { error: botError } = await supabaseClient.from("messages").insert([{
                            role      : "bot",
                            content   : fullReply,
                            session_id: currentSessionId,
                            user_id   : currentUser.id
                        }]);
                        if (botError) console.error("❌ Bot message save failed:", botError.message);
                    }
                }

                if (memoryEnabled && !ghostChatEnabled && currentUser && message) {
                    maybeExtractAndSaveMemory(message);
                }

                if (_editGroupId) {
                    const claudeWrapper = chatbox.querySelector(".bot-msg-wrapper:last-child");
                    if (claudeWrapper) {
                        claudeWrapper.dataset.turnGroup = _editGroupId;
                        claudeWrapper.dataset.version   = "2";
                        const v1User = chatbox.querySelector(`.user-msg-wrapper[data-turn-group="${_editGroupId}"][data-version="1"]`);
                        const v1Bot  = chatbox.querySelector(`.bot-msg-wrapper[data-turn-group="${_editGroupId}"][data-version="1"]`);
                        const nav = createVersionNavigator(_editGroupId, v1User, v1Bot);
                        chatbox.insertBefore(nav, userBubble);
                    }
                }

                return;
            }

            // ── Build ghost history payload (sliding window) ─────────────────
            if (ghostChatEnabled) {
                ghostMemory.push({ role: "user", content: promptText });
                if (ghostMemory.length > GHOST_WINDOW) {
                    ghostMemory = ghostMemory.slice(ghostMemory.length - GHOST_WINDOW);
                }
            }

            // ── Attach Supabase auth token so backend can verify the caller ──
            const { data: sessionData } = await supabaseClient.auth.getSession();
            const accessToken = sessionData?.session?.access_token;
            if (!accessToken) {
                throw new Error("Not authenticated — please sign in again.");
            }

            const res = await fetch("/chat", {
                method : "POST",
                headers: {
                    "Content-Type": "application/json",
                    "Authorization": `Bearer ${accessToken}`
                },
                signal : activeAbortController ? activeAbortController.signal : undefined,
                body   : JSON.stringify({
                    prompt    : promptText,
                    model     : model,
                    file_urls : ghostChatEnabled ? [] : fileUrls,
                    web_results: webResults,
                    web_search_enabled: ghostChatEnabled ? false : webSearchEnabled,
                    ghost_mode : ghostChatEnabled,
                    ghost_history: ghostChatEnabled ? ghostMemory.slice(0, -1) : [],
                    user_memories: (memoryEnabled && !ghostChatEnabled) ? userMemories : []
                })
            });
            if (!res.ok) throw new Error("Server error " + res.status);

            const reader    = res.body.getReader();
            const decoder   = new TextDecoder();

            // ── Stream with tool-badge support ──────────────────────────────
            // thinking indicator stays visible until first real token arrives
            let toolUsed = null;
            let wrapper = null;
            let botMsg = null;
            let pendingSources = null;

            const fullReply = await streamWordsWithTools(
                null, null, reader, decoder, chatbox,
                (tu) => { toolUsed = tu; },
                // onFirstToken: create bot wrapper lazily on first real content
                () => {
                    if (!wrapper) {
                        thinking.remove();
                        const created = createBotWrapper();
                        wrapper = created.wrapper;
                        botMsg  = created.botMsg;
                        chatbox.appendChild(wrapper);
                    }
                    return { wrapper, botMsg };
                },
                // onToolRunning: update thinking label
                (intentLabel) => {
                    const thinkLabelEl = thinking.querySelector(".thinking-label");
                    if (thinkLabelEl) {
                        thinkLabelEl.innerHTML = `<span class="think-icon"></span>${intentLabel}`;
                    }
                },
                // onSources: store sources to render after answer
                (sources) => { pendingSources = sources; }
            );

            // ── Reset streaming state ────────────────────────────────────────
            activeAbortController = null;
            setStreamingState(false);

            renderToolBadgeAndSources(wrapper, botMsg, toolUsed, pendingSources);

            if (fullReply) {
                if (ghostChatEnabled) {
                    // Append bot reply to ghost memory (sliding window)
                    ghostMemory.push({ role: "assistant", content: fullReply });
                    if (ghostMemory.length > GHOST_WINDOW) {
                        ghostMemory = ghostMemory.slice(ghostMemory.length - GHOST_WINDOW);
                    }
                } else {
                const { error: botError } = await supabaseClient.from("messages").insert([{
                    role      : "bot",
                    content   : fullReply,
                    session_id: currentSessionId,
                    user_id   : currentUser.id
                }]);
                if (botError) console.error("❌ Bot message save failed:", botError.message);
                }
            }

            // 🧠 Memory extraction — save user info if memory is enabled
            if (memoryEnabled && !ghostChatEnabled && currentUser && message) {
                maybeExtractAndSaveMemory(message);
            }

            // ── If this was an edited turn, tag v2 bot wrapper + inject navigator ──
            if (_editGroupId && wrapper) {
                wrapper.dataset.turnGroup = _editGroupId;
                wrapper.dataset.version   = "2";

                // Find the original v1 user + bot wrappers in the DOM
                const v1User = chatbox.querySelector(
                    `.user-msg-wrapper[data-turn-group="${_editGroupId}"][data-version="1"]`
                );
                const v1Bot  = chatbox.querySelector(
                    `.bot-msg-wrapper[data-turn-group="${_editGroupId}"][data-version="1"]`
                );

                // Insert the ‹ 1/2 › navigator between v1 (hidden) and v2 (shown)
                const nav = createVersionNavigator(_editGroupId, v1User, v1Bot);
                chatbox.insertBefore(nav, userBubble);
            }

        } catch (err) {
            activeAbortController = null;
            setStreamingState(false);
            try { thinking.remove(); } catch (_) {}
            // Don't show error if user intentionally stopped the response
            if (err && err.name === 'AbortError') return;
            console.error("❌ AI fetch failed:", err);
            const existingWrapper = chatbox.querySelector(".bot-msg-wrapper:last-child");
            if (!existingWrapper || existingWrapper.querySelector(".message.bot")?.innerHTML?.trim() === "") {
                const errMsg = document.createElement("div");
                errMsg.classList.add("message", "bot");
                errMsg.innerHTML = `<p style="color:#e06c6c">⚠️ Failed to get a response. Please try again.</p>`;
                chatbox.appendChild(errMsg);
            }
        }    };

    input.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey) {
            const isMobileInput = window.innerWidth <= 768;
            if (isMobileInput) {
                // On mobile, Enter always inserts a new line.
                // Sending only happens via tapping the send button.
                return;
            }
            e.preventDefault();
            const btn = document.getElementById('sendBtn');
            if (btn && btn.disabled && !isStreaming) return;  // empty input
            sendMessage();
        }
    });

    // ============================
    // 📂 LOAD SESSION
    // ============================
    async function loadSession(sessionId) {
        chatbox.innerHTML = "";
        inputArea.classList.remove("center");
        inputArea.classList.add("bottom");
        app.classList.remove("greeting-mode");

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
                // Reconstruct file objects from stored URLs for history display
                const historyFiles = (msg.file_urls && msg.file_urls.length)
                    ? msg.file_urls.map(function(url) {
                        return { url: url, name: url.split('/').pop().replace(/^\d+_[a-z0-9]+_/, ''), type: /\.(png|jpg|jpeg|gif|webp)$/i.test(url) ? 'image/jpeg' : 'application/octet-stream', size: 0 };
                      })
                    : [];
                chatbox.appendChild(createUserBubble(msg.content, historyFiles));
            } else {
                const { wrapper, botMsg } = createBotWrapper();
                renderBotContent(wrapper, botMsg, msg.content);
                chatbox.appendChild(wrapper);
            }
        });

        chatbox.scrollTop = chatbox.scrollHeight;
        if (window.innerWidth <= 768) closeSidebar();
        showMainMenu();
        highlightActiveHistoryItem(sessionId);
        if (typeof window.refreshMsgNavTracker === 'function') window.refreshMsgNavTracker();
    }

    // ============================
    // 📜 SHOW HISTORY
    // ============================
    window.showHistory = async function () {
        // Compatibility: called from icon rail / openSidebarTo — just open accordion
        await openHistoryAccordion();
    };

    window.toggleHistoryAccordion = async function () {
        const trigger   = document.getElementById("historyAccordionTrigger");
        const list      = document.getElementById("historyAccordionList");
        const searchWrap = document.getElementById("historySearchWrap");
        if (!trigger || !list) return;

        const isOpen = trigger.classList.contains("open");

        if (isOpen) {
            // Close accordion
            trigger.classList.remove("open");
            list.classList.remove("open");
            list.innerHTML = "";
            if (searchWrap) {
                searchWrap.classList.remove("open");
                // Reset search
                const inp = document.getElementById("historySearchInput");
                const clr = document.getElementById("historySearchClear");
                if (inp) inp.value = "";
                if (clr) clr.classList.remove("visible");
            }
        } else {
            // Open accordion — load chats
            await openHistoryAccordion();
        }
    };

    // Cache of loaded sessions for client-side search
    let _cachedSessions = [];

    // Bumped on every fetch — lets an in-flight fetch detect it's been
    // superseded by a newer one and bail out instead of clobbering fresher data.
    let _historyFetchToken = 0;

    async function openHistoryAccordion() {
        const trigger    = document.getElementById("historyAccordionTrigger");
        const list       = document.getElementById("historyAccordionList");
        const searchWrap = document.getElementById("historySearchWrap");
        if (!trigger || !list) return;

        // Mark as open
        trigger.classList.add("open");
        list.classList.add("open");
        if (searchWrap) searchWrap.classList.add("open");
        list.innerHTML = `<div style="padding:8px 14px;font-size:12px;color:#555;">Loading…</div>`;

        if (!currentUser) {
            list.innerHTML = `<div style="padding:8px 14px;font-size:12px;color:#666;">Please log in to see history.</div>`;
            return;
        }

        const myToken = ++_historyFetchToken;

        const { data, error } = await supabaseClient
            .from("chat_sessions").select("*")
            .eq("user_id", currentUser.id)
            .or("archived.is.null,archived.eq.false")
            .order("created_at", { ascending: false });

        // A newer fetch started while this one was in flight — drop this result.
        if (myToken !== _historyFetchToken) return;

        // Accordion may have been closed while the request was in flight.
        if (!document.getElementById("historyAccordionList")) return;

        if (error) {
            list.innerHTML = `<div style="padding:8px 14px;font-size:12px;color:#e06c6c;">Failed to load history.</div>`;
            console.error("❌ History failed:", error.message);
            return;
        }

        _cachedSessions = data || [];
        list.innerHTML = "";

        if (_cachedSessions.length === 0) {
            list.innerHTML = `<div class="no-history">No chats yet. Start a new chat to see history.</div>`;
            return;
        }

        _cachedSessions.forEach(session => {
            const item = buildHistoryItem(session, loadSession);
            list.appendChild(item);
        });
    }

    // ============================
    // 🔄 UNIFIED HISTORY REFRESH
    // ============================
    // Call this after ANY mutation (new chat, delete, rename, archive,
    // restore) so the sidebar list updates immediately without requiring
    // the user to manually close/reopen the accordion.
    //
    //   refreshHistoryList()      → refresh only if the accordion is
    //                                 currently open (no-op otherwise,
    //                                 since it re-fetches fresh on open anyway)
    //   refreshHistoryList(true)  → force it open AND refresh (used when the
    //                                 action was triggered from inside the
    //                                 accordion itself, so it's already open)
    async function refreshHistoryList(forceOpen) {
        const trigger = document.getElementById("historyAccordionTrigger");
        const list    = document.getElementById("historyAccordionList");
        if (!trigger || !list) return;

        const isOpen = trigger.classList.contains("open");
        if (!isOpen && !forceOpen) return;

        await openHistoryAccordion();

        // Re-apply any active search filter so the refreshed list still
        // respects what the user was searching for.
        const searchInput = document.getElementById("historySearchInput");
        if (searchInput && searchInput.value.trim() && typeof window.filterHistoryList === "function") {
            window.filterHistoryList(searchInput.value);
        }
    }
    window.refreshHistoryList = refreshHistoryList;

    // ── Chat search ──────────────────────────────────────────────────────────
    window.filterHistoryList = function (query) {
        const list = document.getElementById("historyAccordionList");
        const clr  = document.getElementById("historySearchClear");
        if (!list) return;

        // Show/hide clear button
        if (clr) clr.classList.toggle("visible", query.trim().length > 0);

        // Remove any previous no-results message
        const prev = list.querySelector(".history-no-results");
        if (prev) prev.remove();

        const q = query.trim().toLowerCase();
        const items = list.querySelectorAll(".history-item");
        let visibleCount = 0;

        items.forEach(item => {
            const sid = item.dataset.sessionId;
            const session = _cachedSessions.find(s => s.session_id === sid);
            if (!session) { item.style.display = ""; return; }

            // Search title + any snippet stored on the session object
            const haystack = [
                session.title || "",
                session.snippet || session.preview || session.last_message || ""
            ].join(" ").toLowerCase();

            const matches = !q || haystack.includes(q);
            item.style.display = matches ? "" : "none";
            if (matches) visibleCount++;
        });

        if (q && visibleCount === 0) {
            const msg = document.createElement("div");
            msg.className = "history-no-results";
            msg.textContent = `No chats match "${query}"`;
            list.appendChild(msg);
        }
    };

    window.clearHistorySearch = function () {
        const inp = document.getElementById("historySearchInput");
        if (inp) { inp.value = ""; inp.focus(); }
        window.filterHistoryList("");
    };

});

// ============================
// 🎨 THEME SYSTEM
// ============================
window.setTheme = function(theme) {
    localStorage.setItem('catura-theme', theme);
    applyTheme(theme);
    saveSettingToCloud('theme', theme);
    // Update active button
    document.querySelectorAll('.theme-option').forEach(btn => btn.classList.remove('active'));
    const activeBtn = document.querySelector(`.theme-option[onclick="setTheme('${theme}')"]`);
    if (activeBtn) activeBtn.classList.add('active');
    showToast(`Theme: ${theme.charAt(0).toUpperCase() + theme.slice(1)}`, 1500);
};

function applyTheme(theme) {
    const root = document.documentElement;
    root.removeAttribute('data-theme');
    if (theme === 'light') {
        root.setAttribute('data-theme', 'light');
    } else if (theme === 'auto') {
        const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
        if (!prefersDark) root.setAttribute('data-theme', 'light');
    }
    // dark = default, no attribute needed
}

function initTheme() {
    const saved = localStorage.getItem('catura-theme') || 'dark';
    applyTheme(saved);
    // Listen for system preference changes (for auto mode)
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
        if ((localStorage.getItem('catura-theme') || 'dark') === 'auto') applyTheme('auto');
    });
}

// ============================
// 🎨 USER MESSAGE COLOR SYSTEM
// ============================
const USER_MSG_COLORS = {
    red:    { hex: '#e5484d', label: 'Red' },
    yellow: { hex: '#e0b400', label: 'Yellow' },
    green:  { hex: '#10a37f', label: 'Green' },
    brown:  { hex: '#8a5a3b', label: 'Brown' },
    blue:   { hex: '#3b82f6', label: 'Blue' }
};

window.setUserMsgColor = function (color) {
    if (!USER_MSG_COLORS[color]) return;
    localStorage.setItem('catura-usercolor', color);
    applyUserMsgColor(color);
    if (typeof saveSettingToCloud === 'function') saveSettingToCloud('user_msg_color', color);
    syncUserMsgColorUI();
    showToast(`Message color: ${USER_MSG_COLORS[color].label}`, 1500);
};

function applyUserMsgColor(color) {
    const root = document.documentElement;
    if (color && color !== 'green' && USER_MSG_COLORS[color]) {
        root.setAttribute('data-usercolor', color);
    } else {
        root.removeAttribute('data-usercolor');
    }
}

function syncUserMsgColorUI() {
    const color = localStorage.getItem('catura-usercolor') || 'green';
    const meta = USER_MSG_COLORS[color] || USER_MSG_COLORS.green;
    const dot = document.getElementById('scColorDot');
    const label = document.getElementById('scColorLabel');
    if (dot) dot.style.background = meta.hex;
    if (label) label.textContent = meta.label;
    document.querySelectorAll('.sc-color-option').forEach(opt => {
        opt.classList.toggle('active', opt.getAttribute('data-usercolor-option') === color);
    });
}

function initUserMsgColor() {
    const saved = localStorage.getItem('catura-usercolor') || 'green';
    applyUserMsgColor(saved);
}

// ============================
// 🔠 FONT SIZE SYSTEM
// ============================
window.setFontSize = function(size) {
    localStorage.setItem('catura-font', size);
    applyFontSize(size);
    saveSettingToCloud('font_size', size);
    document.querySelectorAll('.font-option').forEach(btn => btn.classList.remove('active'));
    const activeBtn = document.querySelector(`.font-option[onclick="setFontSize('${size}')"]`);
    if (activeBtn) activeBtn.classList.add('active');
    showToast(`Font size: ${size.charAt(0).toUpperCase() + size.slice(1)}`, 1500);
};

function applyFontSize(size) {
    const root = document.documentElement;
    root.removeAttribute('data-fontsize');
    if (size && size !== 'default') root.setAttribute('data-fontsize', size);
}

function initFontSize() {
    const saved = localStorage.getItem('catura-font') || 'default';
    applyFontSize(saved);
}

// ============================
// 🌐 WEB SEARCH & INTENT SYSTEM
// ============================
let webSearchEnabled = false;  // true = force web search on every message

// ── Client-side intent detector ─────────────────────────────────────────────
// Mirrors backend detect_intent() — used ONLY for UI labels (thinking text).
// Actual tool execution always happens on the backend.
function detectClientIntent(message) {
    const lower = message.toLowerCase().trim();

    // Identity → general
    if (/which model|what model|what ai|which ai|are you|who are you|your model|who (made|created|built) you/.test(lower)) return "general";

    // ── GREETINGS → always "general", never search ───────────────────────
    const GREETING_EXACT = new Set([
        "hi","hii","hiii","hiiii","hey","heya","hello","helo","hellow",
        "yo","sup","wassup","whatsup","howdy","hola","ola",
        "bonjour","bonsoir","salut","ciao","hallo","hei",
        "kamon acho","kemon acho","ki obostha","ki holo",
        "kya haal","kya haal hai","kaise ho","kaise hain",
        "theek ho","theek hain","sab theek",
        "vanakkam","namaskaram","sukhamano","ela unnaru",
        "sat sri akal","kem cho","maja ma",
        "aadab","adaab","assalamualaikum","salam",
        "namaste","namaskar","pranam","nomoskar",
        "thanks","thank you","thx","ty","thnx","thankyou",
        "bye","goodbye","see ya","cya","alvida",
        "ok","okay","okk","k","kk","cool","nice","great","awesome",
        "yes","no","yep","nope","yeah","nah","sure","hmm","hm","ohh","oh"
    ]);
    if (GREETING_EXACT.has(lower)) return "general";

    const words = lower.split(/\s+/);
    if (words.length <= 5) {
        const GREETING_STARTS = [
            "hi ","hii","hey ","hello","good morning","good afternoon",
            "good evening","good night","good day","greetings",
            "how are you","how r u","how are u","how ru",
            "what's up","whats up","sup ","yo ","hola ","bonjour",
            "namaste","namaskar","nomoskar","pranam",
            "kamon","kemon","kaise ho","kya haal","kem cho",
            "sat sri","vanakkam",
            "thanks","thank ","thx","bye","goodbye","nice ","cool ","great ","awesome "
            // FIX: removed stray comma inside "awesome," string → "awesome "
        ];
        if (GREETING_STARTS.some(g => lower.startsWith(g))) return "general";
    }

    // ── REAL-TIME OVERRIDE — must run BEFORE wikipedia check ─────────────
    const realtimeSignals = /\bnow\b|\bnew\b|\bcurrent(ly)?\b|\blatest\b|\btoday\b|\brecent(ly)?\b|\bright now\b|\bat (the )?moment\b|\bthis (year|month|week|day)\b|\b2024\b|\b2025\b|\b2026\b|\bjust (happened|announced|elected|appointed|named|won|became)\b/;
    const infoSeeking = /\bwho\b|\bwhat\b|\bwhich\b|\bwhere\b|\bwhen\b|\btell me\b|\bfind\b/;
    if (infoSeeking.test(lower) && realtimeSignals.test(lower)) return "web_search";

    // ── CLOCK ─────────────────────────────────────────────────────────────
    if (/\btime\b|\bclock\b|\bwhat time\b|\bcurrent time\b|\btimezone\b|\btime zone\b|\bist\b|\butc\b|\bgmt\b/.test(lower)) return "clock";
    if (/\bwhat\s+(time|hour|day|date)\s+(is\s+it|now)\b|\bcurrent\s+(time|date|day|hour)\b|\btime\s+(in|at|of)\s+\w+|\btoday'?s?\s+date\b|^\s*(time|date|what time|what date)\??\s*$|\btimezone\b/.test(lower)) return "clock";

    // ── WEATHER ───────────────────────────────────────────────────────────
    if (/weather|temperature|humidity|forecast|sunny|cloudy|will it rain|feels like/.test(lower)) return "weather";
    if (/\bweather\s+(in|at|of|today|tomorrow|now|right now)\b|\btemperature\s+(in|at|of|today|tomorrow|now|right now|outside)\b|\bhumidity\s+(in|today|now)\b|\b(will|is)\s+it\s+(rain|snow|hot|cold|sunny|cloudy)\b|\bforecast\b|\bfeels\s+like\b|\bhow\s+(hot|cold|warm)\s+is\s+it\b|today'?s?\s+weather|^\s*weather\??\s*$/.test(lower)) return "weather";

    // ── FINANCE ───────────────────────────────────────────────────────────
    if (/share price|stock price|stock market|nse|bse|nifty|sensex|crypto|bitcoin|ethereum|exchange rate|rupee/.test(lower)) return "finance";
    if (/(tata|reliance|infosys|wipro|hdfc|icici|bajaj|sbi).*(price|stock|share)|(price|stock|share).*(tata|reliance|infosys|wipro|hdfc|icici|bajaj|sbi)/.test(lower)) return "finance";
    if (/\b(share|stock)\s+price\b|\bprice\s+of\s+\w+\s+(share|stock)\b|\b(nifty|sensex|nasdaq)\s+(today|now|live|current)\b|\b(bitcoin|btc|ethereum|eth|crypto)\s+(price|today|now|live|current|rate)\b|\bprice\s+of\s+(bitcoin|btc|ethereum)\b|\b(dollar|usd|inr|rupee|euro)\s+(rate|today|live|current|exchange)\b|\b(tata|reliance|infosys|wipro|hdfc|icici|bajaj|sbi|adani|maruti|tcs|airtel|paytm|zomato|nykaa|irctc)\s+(share|stock|price)\b/.test(lower)) return "finance";

    // ── SPORTS ────────────────────────────────────────────────────────────
    if (/cricket|ipl|test match|\bodi\b|\bt20\b|football|soccer|fifa|premier league|\bnba\b|\bnfl\b|tennis|live score|match today/.test(lower)) return "sports";
    if (/\b(live|today'?s?|current)\s+(score|match)\b|\bscore\s+(of|today|now|live)\b|\b(ipl|test match|odi|t20|world cup)\s+(today|score|match|live|now|result)\b|\bwho\s+(won|is winning|is playing)\s+(today|now|the match)\b|\bcricket\s+(score|match|live|today)\b|\bfootball\s+(score|match|live|today)\b/.test(lower)) return "sports";

    // ── NEWS ──────────────────────────────────────────────────────────────
    if (/\bnews\b|headlines|breaking|latest news|current events|what happened|recent news/.test(lower)) return "news";
    if (/\b(latest|todays?|recent|breaking|current)\s+news\b|\bnews\s+(today|now|about|on)\b|\bheadlines\b|\bbreaking\s+(news|story)\b|what'?s?\s+happening\s+(today|now)|what\s+happened\s+(today|yesterday|recently)|current\s+events|^\s*news\??\s*$/.test(lower)) return "news";

    // ── WIKIPEDIA (no real-time signals) ──────────────────────────────────
    const hasRealtime = /\b(latest|today|now|current|recent|2024|2025|2026)\b/.test(lower);
    if (!hasRealtime) {
        if (/^tell me about\s+[a-z]|^information (about|on)\s+\w|^facts about\s+\w|^who was\s+[A-Z]|^biography of\s+\w|^history of\s+[A-Z]|\bcapital of\s+\w|\bpopulation of\s+\w|^who is\s+[A-Z][a-z]+\s+[A-Z][a-z]+|\bborn (in|on)\s+\d{4}\b/.test(message)) {
            return "wikipedia";
        }
    }

    // ── WEB SEARCH (real-time / fresh data) ───────────────────────────────
    if (/\blatest\b|\bnewest\b|\bmost recent\b|\btoday\b|\btonight\b|\bthis (week|month|year)\b|\bcurrent(ly)?\b|\bright now\b|\brecently?\b|\bjust (announced|launched|happened|released)\b|\b(202[4-9])\b|\bbreaking\b/.test(lower)) return "web_search";
    if (/who is (the )?(current|new|now|present)\s+(cm|chief minister|pm|prime minister|president|governor|mayor|minister|ceo|chairman)|latest version of|new(est)? (version|update|release|feature)|just (launched|released|announced)|github trending|reddit (saying|trending)|huggingface.{0,30}(latest|new|recent)/.test(lower)) return "web_search";
    if (/\bis\s+\w+\s+(down|up|working|running|having issues|offline)\s+(right now|today|currently)\b|currently rank|now ranking|seo.{0,15}(update|new|latest|this (week|month))/.test(lower)) return "web_search";

    // Default → general
    return "general";
}


// ── performWebSearch (legacy manual toggle) ──────────────────────────────────
async function performWebSearch(query) {
    try {
        const res  = await fetch(`/search?q=${encodeURIComponent(query)}&max_results=5`);
        const data = await res.json();
        return data.results || [];
    } catch (e) {
        console.error("❌ Web search failed:", e);
        return [];
    }
}

// ── streamWordsWithTools — word-queue streaming animator (ChatGPT/Claude style) ──
async function streamWordsWithTools(botMsgInitial, wrapperInitial, reader, decoder, chatbox, onToolUsed, onFirstToken, onToolRunning, onSources) {
    let buffer      = "";
    let fullReply   = "";       // complete received text (source of truth)
    let displayed   = "";       // what has been rendered so far
    let wordQueue   = [];       // words waiting to be painted
    let gotWrapper  = false;
    let streamDone  = false;    // SSE stream finished
    let animRunning = false;

    // ── Live "Thinking" box state (Gemma reasoning models) ──────────────────
    let thinkingText   = "";    // accumulated reasoning text (this stream only)
    let thinkingClosed = false; // whether the </think> tag has been appended to fullReply
    let thinkingEl     = null;  // lazily-created collapsible box, sibling before botMsg
    let thinkingRAF    = false; // throttle flag for re-renders

    // Support both old (direct elements) and new (lazy callback) API
    let botMsg  = botMsgInitial;
    let wrapper = wrapperInitial;

    const getOrCreateWrapper = () => {
        if (!gotWrapper && typeof onFirstToken === "function") {
            const result = onFirstToken();
            if (result) { wrapper = result.wrapper; botMsg = result.botMsg; }
            gotWrapper = true;
        }
        return { wrapper, botMsg };
    };

    // Creates (once) and live-updates the collapsed "Thinking" box while
    // reasoning tokens stream in, ahead of the final answer being ready.
    const renderLiveThinking = () => {
        const { botMsg: bm } = getOrCreateWrapper();
        if (!bm) return;
        if (!thinkingEl) {
            thinkingEl = document.createElement("div");
            thinkingEl.className = "thinking-live-wrap";
            if (bm.parentNode) bm.parentNode.insertBefore(thinkingEl, bm);
            // Build the toggle+content shell once, synchronously, so the
            // dropdown exists and is clickable from the very first token.
            thinkingEl.innerHTML = buildThinkingHTML(thinkingText, false, true);
        }
        if (thinkingRAF) return;
        thinkingRAF = true;
        requestAnimationFrame(() => {
            thinkingRAF = false;
            if (!thinkingEl) return;
            // Patch only the reasoning text in place on subsequent updates.
            // Rebuilding the whole block (old behaviour) replaced the toggle
            // button every frame and reset it to "closed", so a user who
            // opened the dropdown mid-stream had it snap shut instantly —
            // this is what made it feel impossible to open while thinking.
            const inner = thinkingEl.querySelector('.thinking-inner');
            if (inner) {
                inner.innerHTML = formatMessage(thinkingText);
            } else {
                thinkingEl.innerHTML = buildThinkingHTML(thinkingText, false, true);
            }
        });
    };

    const intentLabels = {
        clock:      "🕐 Checking live time…",
        weather:    "🌤️ Checking live weather…",
        finance:    "💹 Fetching market data…",
        sports:     "🏏 Fetching live scores…",
        news:       "📰 Getting latest news…",
        web_search: "🔍 Searching the web…",
        wikipedia:  "📖 Looking up Wikipedia…",
    };

    // ── Word-queue drain loop ────────────────────────────────────────────────
    // Runs on rAF; emits ~2-3 words per frame (~40-50 ms between renders) for
    // a fast but readable pace — similar to ChatGPT / Claude.
    // ── Stick-to-bottom autoscroll ───────────────────────────────────────────
    // Only snaps the chat to the bottom while streaming if the user is
    // already near the bottom (i.e. hasn't manually scrolled up to read
    // earlier messages or the thinking dropdown). This matches ChatGPT/Claude
    // behaviour: streaming never fights the user's scroll position, and the
    // existing "scroll to bottom" button (initScrollToBottom) lets the user
    // jump back down whenever they want.
    const STICK_THRESHOLD = 120; // px tolerance from the true bottom
    let userPinnedToBottom = true;

    (function trackStickToBottom() {
        if (!chatbox) return;
        chatbox.addEventListener('scroll', () => {
            const distFromBottom = chatbox.scrollHeight - chatbox.scrollTop - chatbox.clientHeight;
            userPinnedToBottom = distFromBottom <= STICK_THRESHOLD;
        }, { passive: true });
    })();

    function stickScrollToBottom() {
        if (!chatbox) return;
        if (!userPinnedToBottom) return; // user scrolled up on purpose — don't interrupt them
        chatbox.scrollTop = chatbox.scrollHeight;
    }

    const WORDS_PER_TICK = 2;   // words painted per animation frame
    const RENDER_EVERY   = 2;   // re-parse markdown every N ticks (keeps it smooth)
    let   tickCount      = 0;

    const drainQueue = () => {
        const { botMsg: bm } = getOrCreateWrapper();
        if (!bm) { if (!streamDone || wordQueue.length) requestAnimationFrame(drainQueue); return; }

        if (wordQueue.length === 0) {
            if (!streamDone) {
                // waiting for more tokens — keep loop alive
                requestAnimationFrame(drainQueue);
            } else {
                // stream finished AND queue empty — do final render
                // Strip inline citation numbers [1][2] from the display copy; keep fullReply
                // (the return value, incl. <think> tags) raw for storage.
                // NOTE: Do NOT collapse whitespace (\s{2,} → ' ') here — it destroys newlines
                // needed for markdown lists, paragraphs, and code blocks.
                animRunning = false;
                if (thinkingEl) { thinkingEl.remove(); thinkingEl = null; }
                renderBotContent(wrapper, bm, fullReply.replace(/\[\d+\](\[\d+\])*/g, '').trimEnd());
                bm.classList.remove("streaming");
                stickScrollToBottom();
            }
            return;
        }

        // Paint WORDS_PER_TICK words this frame
        const batch = wordQueue.splice(0, WORDS_PER_TICK);
        displayed += batch.join("");
        tickCount++;

        if (tickCount % RENDER_EVERY === 0 || (streamDone && wordQueue.length === 0)) {
            // Full markdown re-parse (kept infrequent to stay fast)
            bm.innerHTML = formatMessage(repairTruncated(displayed));
            if (!(streamDone && wordQueue.length === 0)) {
                bm.classList.add("streaming");
            } else {
                bm.classList.remove("streaming");
            }
        } else {
            // Lightweight: just append a text node for speed between re-parses
            const cursor = bm.querySelector(".stream-cursor");
            if (cursor) {
                cursor.insertAdjacentText("beforebegin", batch.join(""));
            } else {
                bm.innerHTML = formatMessage(repairTruncated(displayed));
                bm.classList.add("streaming");
            }
        }

        stickScrollToBottom();
        requestAnimationFrame(drainQueue);
    };

    // Push incoming text into word-queue, splitting on whitespace boundaries
    // so each "word + its trailing space" is one queue entry.
    const enqueueText = (text) => {
        // Split keeping the delimiter (space/newline) attached to the left token
        const parts = text.split(/(?<=\s)/);
        for (const p of parts) if (p) wordQueue.push(p);
        if (!animRunning) { animRunning = true; requestAnimationFrame(drainQueue); }
    };

    // ── SSE reader loop ──────────────────────────────────────────────────────
    try {
        outer:
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop();

            for (const line of lines) {
                if (!line.startsWith("data: ")) continue;
                const payload = line.slice(6).trim();
                if (payload === "[DONE]") break outer;
                try {
                    const data = JSON.parse(payload);

                    if (data.tool_used !== undefined) {
                        if (typeof onToolUsed === "function") onToolUsed(data.tool_used);
                        continue;
                    }
                    if (data.sources !== undefined) {
                        if (typeof onSources === "function") onSources(data.sources);
                        continue;
                    }
                    if (data.status === "tool_running") {
                        const label = intentLabels[data.intent] || "🔍 Fetching data…";
                        if (typeof onToolRunning === "function") onToolRunning(label);

                        // Inject search skeleton into chatbox for web_search
                        if (data.intent === "web_search" && !document.getElementById("search-skeleton-placeholder")) {
                            const skeletonWrap = document.createElement("div");
                            skeletonWrap.id = "search-skeleton-placeholder";
                            skeletonWrap.className = "search-skeleton-outer";
                            skeletonWrap.innerHTML = `
                                <div class="search-skeleton-label">🔍 Searching the web…</div>
                                <div class="search-skeleton">
                                    <div class="skeleton-line sk-w90"></div>
                                    <div class="skeleton-line sk-w75"></div>
                                    <div class="skeleton-line sk-w85"></div>
                                    <div class="skeleton-line sk-w60"></div>
                                </div>`;
                            chatbox.appendChild(skeletonWrap);
                            stickScrollToBottom();
                        }
                        continue;
                    }
                    if (data.error) {
                        const sk = document.getElementById("search-skeleton-placeholder");
                        if (sk) sk.remove();
                        if (thinkingEl) { thinkingEl.remove(); thinkingEl = null; }
                        const { botMsg: bm } = getOrCreateWrapper();
                        if (!fullReply.trim() && bm) bm.innerHTML = `<p style="color:#e06c6c">⚠️ ${data.error}</p>`;
                        streamDone = true;
                        return fullReply || "";
                    }
                    if (data.thinking_token) {
                        // Remove search skeleton the moment real content arrives
                        const sk = document.getElementById("search-skeleton-placeholder");
                        if (sk) sk.remove();

                        if (!thinkingText) fullReply += "<think>";
                        thinkingText += data.thinking_token;
                        fullReply += data.thinking_token;
                        renderLiveThinking();
                        continue;
                    }
                    if (data.token) {
                        // Remove search skeleton the moment real content arrives
                        const sk = document.getElementById("search-skeleton-placeholder");
                        if (sk) sk.remove();

                        if (thinkingText && !thinkingClosed) { fullReply += "</think>"; thinkingClosed = true; }
                        fullReply += data.token;
                        enqueueText(data.token);
                    }
                } catch { continue; }
            }
        }
    } catch (e) { console.warn("Stream read error:", e); }

    // Clean up skeleton if somehow still present (no tokens arrived)
    const skFallback = document.getElementById("search-skeleton-placeholder");
    if (skFallback) skFallback.remove();

    // Stream ended mid-thought (no answer tokens followed) — close the tag
    // so storage/history rendering still parses cleanly.
    if (thinkingText && !thinkingClosed) { fullReply += "</think>"; thinkingClosed = true; }

    // Signal animator that no more tokens are coming
    streamDone = true;

    // Wait for the animator to finish draining the queue before returning
    await new Promise(resolve => {
        const poll = () => animRunning ? requestAnimationFrame(poll) : resolve();
        requestAnimationFrame(poll);
    });

    const { wrapper: w, botMsg: bm } = getOrCreateWrapper();

    if (fullReply.trim()) {
        if (bm) {
            // Only re-render if the drain loop didn't already do it (animRunning was set false there)
            if (thinkingEl) { thinkingEl.remove(); thinkingEl = null; }
            renderBotContent(w, bm, fullReply.replace(/\[\d+\](\[\d+\])*/g, '').trimEnd());
        }
        stickScrollToBottom();
        return fullReply;
    }

    if (bm) bm.innerHTML = `<p style="color:#e06c6c">⚠️ No response received. Please try again.</p>`;
    return "";
}

// ============================================================
// 🛑 STOP BUTTON — abort controller for streaming responses
// ============================================================
let activeAbortController = null;
let isStreaming = false;

function setStreamingState(streaming) {
    isStreaming = streaming;
    const btn = document.getElementById('sendBtn');
    if (!btn) return;
    if (streaming) {
        btn.disabled = false;          // stop button must always be clickable
        btn.classList.add('is-stopping');
        btn.title = 'Stop generating';
    } else {
        btn.classList.remove('is-stopping');
        btn.title = 'Send message';
        if (typeof updateSendBtn === 'function') updateSendBtn();
    }
}

window.handleSendOrStop = function () {
    if (isStreaming) {
        // Abort the current stream
        if (activeAbortController) {
            activeAbortController.abort();
            activeAbortController = null;
        }
        setStreamingState(false);
        showToast('Response stopped', 1500);
    } else {
        const btn = document.getElementById('sendBtn');
        if (btn && btn.disabled) return;   // empty input — do nothing
        sendMessage();
    }
};

window.toggleWebSearch = function() {
    webSearchEnabled = !webSearchEnabled;

    // Update the dropdown item's visual state
    const searchItem = document.querySelector('.plus-dropdown-item[data-action="search"]');
    if (searchItem) {
        if (webSearchEnabled) {
            searchItem.classList.add('search-active');
        } else {
            searchItem.classList.remove('search-active');
        }
    }

    if (webSearchEnabled) {
        showToast('🔍 Web search ON — Catura will search the web', 2000);
    } else {
        showToast('🔍 Web search OFF — Catura decides automatically', 2000);
    }
};
function togglePlusMenu(e) {
    e.stopPropagation();
    const dropdown = document.getElementById('plusDropdown');
    dropdown.classList.toggle('open');
}

// Close dropdown when clicking outside
document.addEventListener('click', function(e) {
    const wrap = document.getElementById('plusMenuWrap');
    if (wrap && !wrap.contains(e.target)) {
        const dropdown = document.getElementById('plusDropdown');
        if (dropdown) dropdown.classList.remove('open');
    }
});

function handlePlusAction(action) {
    const dropdown = document.getElementById('plusDropdown');
    if (dropdown) dropdown.classList.remove('open');

    if (action === 'file') {
        const fi = document.getElementById('fileInput');
        if (fi) fi.click();
    } else if (action === 'connect') {
        showToast('Connect apps — coming soon!');
    } else if (action === 'think') {
        showToast('Think mode — coming soon!');
    } else if (action === 'research') {
        showToast('Deep research — coming soon!');
    } else if (action === 'search') {
        toggleWebSearch();
    }
}

// ── Init web-search active state in the plus dropdown on page load ─────────
function initWebSearchUI() {
    const searchItem = document.querySelector('.plus-dropdown-item[data-action="search"]');
    if (!searchItem) return;
    if (webSearchEnabled) {
        searchItem.classList.add('search-active');
    } else {
        searchItem.classList.remove('search-active');
    }
}

// NOTE: handleFileSelect is defined in file-upload.js (the real implementation).
// Do NOT define a stub here — it would override the real one since this file
// loads before file-upload.js.

// ============================
// 🤖 MODEL SELECTOR
// ============================
let selectedModel = 'dagr'; // Default model — options: dagr, apep, sambhav, Gemma4, nivo, Laguna

// ── MODEL ICONS (dropdown list only — the selector button stays plain text) ──
const MODEL_ICONS = {
    dagr:           'openai.png',
    sambhav:        'meta.png',
    gemini35flash:  'gemini.png',
    agnes:          'agnes-.jpg',
    apep:           'openai.png',
    cohere:         'Cohere.png',
    gemma:          'google.png',
    gemma4:         'google.png',
    glm:            'GLM.png',
    glm52:          'GLM.png',
    laguna:         'poolside.ico',
    laguna_core:    'poolside.ico',
    laguna_s:       'poolside.ico',
    ling:           'ling.png',
    mercury2:       'Mercury.png',
    minimax_m3:     'minimax.png',
    mistral_large:  'mistral.png',
    mistral_medium: 'mistral.png',
    mistral_small:  'mistral.png',
    nemotron:       'nvidia.png',
    n_nano:         'nvidia.png',
    nivo:           'groq.png',
    omni:           'nvidia.png',
    stepfun3:       'stepfun.png',
    claude_puter:   'claude.png',
    gpt5_puter:     'openai.png',
    gemini_puter:   'gemini.png',
    grok_puter:     'grok.png'
};

function getModelIconUrl(modelId) {
    const file = MODEL_ICONS[(modelId || '').toLowerCase()];
    return file ? `static/icons/${file}` : null;
}

function modelIconHtml(modelId) {
    const url = getModelIconUrl(modelId);
    if (!url) return '';
    return `<img src="${url}" class="model-option-icon" alt="" onerror="this.remove()">`;
}

// Decorate the static .model-option elements already in the HTML (default
// list, "more models" panel, "puter models" panel) with icons in front of
// the name. Runs once on load — selecting a model never touches this,
// so the model-selector button keeps showing plain text only.
function applyModelOptionIcons(root) {
    (root || document).querySelectorAll('.model-option[data-model]').forEach(opt => {
        if (opt.querySelector('.model-option-icon')) return; // already has one
        const url = getModelIconUrl(opt.getAttribute('data-model'));
        if (!url) return;
        const img = document.createElement('img');
        img.src = url;
        img.className = 'model-option-icon';
        img.alt = '';
        img.onerror = function () { this.remove(); };
        opt.insertBefore(img, opt.firstChild);
    });
}

document.addEventListener('DOMContentLoaded', () => applyModelOptionIcons());

window.toggleModelSelector = function (e) {
    e.stopPropagation();
    const dropdown = document.getElementById('modelDropdown');
    const btn = document.getElementById('modelSelectorBtn');

    const isOpen = dropdown.classList.contains('open');
    closeAllModelMenus();

    if (!isOpen) {
        renderRecentModels();
        const searchInput = document.getElementById('modelSearchInput');
        if (searchInput) {
            searchInput.value = '';
            handleModelSearch({ target: searchInput });
        }

        const isMobile = window.innerWidth <= 768;

        if (isMobile) {
            // Mobile: bottom-sheet, CSS handles positioning
            dropdown.style.top    = '';
            dropdown.style.left   = '';
            dropdown.style.bottom = '';
            dropdown.classList.add('open');
            btn.classList.add('open');

            const moreModels = ['apep', 'gemma', 'gemma4', 'nivo', 'laguna', 'laguna_core', 'laguna_s', 'laguna_lite', 'nemotron','omni','glm','cohere','minimax_m3','glm52','ling','agnes','stepfun3'];
            const puterModelsList = ['claude_puter','gpt5_puter','gemini_puter'];
            if (moreModels.includes(selectedModel)) {
                requestAnimationFrame(() => {
                    requestAnimationFrame(() => { toggleMoreModels(null); });
                });
            } else if (puterModelsList.includes(selectedModel)) {
                requestAnimationFrame(() => {
                    requestAnimationFrame(() => {
                        toggleMoreModels(null);
                        requestAnimationFrame(() => { window.openPuterModels(); });
                    });
                });
            }
        } else {
            // Desktop: floating dropdown with positioning
            const rect = btn.getBoundingClientRect();

            dropdown.style.visibility = 'hidden';
            dropdown.style.opacity = '0';
            dropdown.style.transform = 'none';
            dropdown.style.display = 'block';
            dropdown.style.top = '-9999px';
            dropdown.style.left = '-9999px';
            const dropH = dropdown.scrollHeight || 180;
            const dropW = Math.max(dropdown.offsetWidth || 0, 220);
            dropdown.style.display = '';
            dropdown.style.top = '';
            dropdown.style.left = '';
            dropdown.style.visibility = '';
            dropdown.style.opacity = '';
            dropdown.style.transform = '';

            let top = rect.top - dropH - 8;
            if (top < 8) top = rect.bottom + 8;
            top = Math.max(8, Math.min(top, window.innerHeight - dropH - 8));

            let left = rect.right - dropW;
            if (left < 8) left = 8;
            if (left + dropW > window.innerWidth - 8) left = window.innerWidth - dropW - 8;

            dropdown.style.top    = top + 'px';
            dropdown.style.left   = left + 'px';
            dropdown.style.bottom = 'auto';

            dropdown.classList.add('open');
            btn.classList.add('open');

            const moreModels = ['apep', 'gemma', 'gemma4', 'nivo', 'laguna', 'laguna_core', 'laguna_s', 'laguna_lite','nemotron','omni', 'cohere','glm','minimax_m3','glm52','ling','agnes','stepfun3'];
            const puterModelsList = ['claude_puter','gpt5_puter','gemini_puter'];
            if (moreModels.includes(selectedModel)) {
                requestAnimationFrame(() => {
                    requestAnimationFrame(() => { window.openMoreModels(); });
                });
            } else if (puterModelsList.includes(selectedModel)) {
                requestAnimationFrame(() => {
                    requestAnimationFrame(() => {
                        window.openMoreModels();
                        requestAnimationFrame(() => { window.openPuterModels(); });
                    });
                });
            }
        }
    }
};

// ── RECENTLY USED MODELS ──────────────────────────────────────────
const RECENT_MODELS_KEY = 'catura_recent_models';
const RECENT_MODELS_MAX = 3;

function getRecentModels() {
    try {
        return JSON.parse(localStorage.getItem(RECENT_MODELS_KEY)) || [];
    } catch (e) { return []; }
}

function saveRecentModel(modelId, modelName) {
    let list = getRecentModels().filter(m => m.id !== modelId);
    list.unshift({ id: modelId, name: modelName });
    list = list.slice(0, RECENT_MODELS_MAX);
    try { localStorage.setItem(RECENT_MODELS_KEY, JSON.stringify(list)); } catch (e) {}
}

function renderRecentModels() {
    const section = document.getElementById('recentModelsSection');
    const label = document.getElementById('recentModelsLabel');
    if (!section || !label) return;

    const list = getRecentModels();
    // Don't show a "recent" entry for the model that's already the default/current pick
    section.innerHTML = '';
    if (!list.length) {
        label.style.display = 'none';
        return;
    }
    label.style.display = '';
    list.forEach(m => {
        const original = document.querySelector(`.model-option[data-model="${m.id}"]`);
        const desc = original ? original.querySelector('.model-option-desc')?.textContent || '' : '';
        const div = document.createElement('div');
        div.className = 'model-option' + (m.id === selectedModel ? ' active' : '');
        div.setAttribute('data-model', m.id);
        div.setAttribute('data-name', m.name);
        div.onclick = (e) => selectModel(m.id, m.name, e);
        div.innerHTML = `
            ${modelIconHtml(m.id)}
            <div class="model-option-info">
                <div class="model-option-name">${m.name}</div>
                ${desc ? `<div class="model-option-desc">${desc}</div>` : ''}
            </div>
            <div class="model-option-check">✓</div>`;
        section.appendChild(div);
    });
}

// ── MODEL SEARCH ───────────────────────────────────────────────────
function getAllModelOptionsData() {
    const nodes = document.querySelectorAll('#modelDropdownDefault .model-option, #moreModelsPanel .model-option, #puterModelsPanel .model-option');
    const seen = new Set();
    const data = [];
    nodes.forEach(el => {
        const id = el.getAttribute('data-model');
        if (!id || seen.has(id)) return;
        seen.add(id);
        data.push({
            id,
            name: el.getAttribute('data-name') || el.querySelector('.model-option-name')?.textContent || id,
            desc: el.querySelector('.model-option-desc')?.textContent || ''
        });
    });
    return data;
}

window.handleModelSearch = function (e) {
    const query = e.target.value.trim().toLowerCase();
    const clearBtn = document.getElementById('modelSearchClear');
    const resultsEl = document.getElementById('modelSearchResults');
    const emptyEl = document.getElementById('modelSearchEmpty');
    const defaultEl = document.getElementById('modelDropdownDefault');
    if (clearBtn) clearBtn.style.display = query ? '' : 'none';

    if (!query) {
        if (resultsEl) resultsEl.style.display = 'none';
        if (emptyEl) emptyEl.style.display = 'none';
        if (defaultEl) defaultEl.style.display = '';
        // Also close any open floating sub-panels since we're back to default view
        const morePanel = document.getElementById('moreModelsPanel');
        const puterPanel = document.getElementById('puterModelsPanel');
        if (morePanel) morePanel.classList.remove('open');
        if (puterPanel) puterPanel.classList.remove('open');
        return;
    }

    if (defaultEl) defaultEl.style.display = 'none';
    const morePanel = document.getElementById('moreModelsPanel');
    const puterPanel = document.getElementById('puterModelsPanel');
    if (morePanel) morePanel.classList.remove('open');
    if (puterPanel) puterPanel.classList.remove('open');

    const matches = getAllModelOptionsData().filter(m =>
        m.name.toLowerCase().includes(query) || m.desc.toLowerCase().includes(query)
    );

    if (!resultsEl) return;
    if (!matches.length) {
        resultsEl.style.display = 'none';
        if (emptyEl) emptyEl.style.display = '';
        return;
    }
    if (emptyEl) emptyEl.style.display = 'none';
    resultsEl.style.display = '';
    resultsEl.innerHTML = matches.map(m => `
        <div class="model-option${m.id === selectedModel ? ' active' : ''}" data-model="${m.id}" data-name="${m.name}" onclick="selectModel('${m.id}', '${m.name.replace(/'/g, "\\'")}', event)">
            ${modelIconHtml(m.id)}
            <div class="model-option-info">
                <div class="model-option-name">${m.name}</div>
                ${m.desc ? `<div class="model-option-desc">${m.desc}</div>` : ''}
            </div>
            <div class="model-option-check">✓</div>
        </div>`).join('');
};

window.clearModelSearch = function (e) {
    if (e) e.stopPropagation();
    const input = document.getElementById('modelSearchInput');
    if (input) {
        input.value = '';
        input.focus();
        handleModelSearch({ target: input });
    }
};

window.selectModel = function (modelId, modelName, e) {
    if (e) e.stopPropagation();
    selectedModel = modelId.toLowerCase();
    saveRecentModel(selectedModel, modelName);

    const modelNameEl = document.getElementById('modelName');
    if (modelNameEl) modelNameEl.textContent = modelName;

    document.querySelectorAll('.model-option').forEach(opt => opt.classList.remove('active'));
    const activeOption = document.querySelector(`[data-model="${modelId}"]`);
    if (activeOption) activeOption.classList.add('active');

    const isPuterModel = isPuterRoutedModel(selectedModel);
    const isMobile = window.innerWidth <= 768;
    if (isMobile) {
        setTimeout(() => {
            closeAllModelMenus();
            if (isPuterModel) {
                showPuterInfoModal(selectedModel, modelName);
            } else {
                showToast(`✓ Switched to ${modelName}`, 1500);
            }
        }, 180);
    } else {
        closeAllModelMenus();
        if (isPuterModel) {
            showPuterInfoModal(selectedModel, modelName);
        } else {
            showToast(`✓ Switched to ${modelName}`, 1500);
        }
    }
};

function closeAllModelMenus() {
    const dropdown = document.getElementById('modelDropdown');
    const btn = document.getElementById('modelSelectorBtn');
    if (dropdown) dropdown.classList.remove('open');
    if (btn) btn.classList.remove('open');
    const panel = document.getElementById('moreModelsPanel');
    const row   = document.getElementById('moreModelsRow');
    if (panel) panel.classList.remove('open');
    if (row)   row.classList.remove('open');
    const puterPanel = document.getElementById('puterModelsPanel');
    const puterRow    = document.getElementById('puterModelsRow');
    if (puterPanel) puterPanel.classList.remove('open');
    if (puterRow)   puterRow.classList.remove('open');
}

// ── EFFORT LEVEL row: desktop uses CSS hover + a small close-delay so the
// panel doesn't vanish while the pointer is travelling toward it; mobile
// falls back to tap-to-toggle.
(function initEffortLevelRow() {
    const row   = document.getElementById('effortLevelRow');
    const panel = document.getElementById('effortLevelPanel');
    if (!row || !panel) return;

    let closeTimer = null;
    const OPEN_CLASS = 'open';
    const CLOSE_DELAY = 250; // ms grace period before hiding

    function openPanel() {
        if (closeTimer) { clearTimeout(closeTimer); closeTimer = null; }
        row.classList.add(OPEN_CLASS);
    }

    function scheduleClose() {
        if (closeTimer) clearTimeout(closeTimer);
        closeTimer = setTimeout(() => {
            row.classList.remove(OPEN_CLASS);
            closeTimer = null;
        }, CLOSE_DELAY);
    }

    function isDesktop() { return window.innerWidth > 768; }

    row.addEventListener('mouseenter', () => { if (isDesktop()) openPanel(); });
    row.addEventListener('mouseleave', () => { if (isDesktop()) scheduleClose(); });
    panel.addEventListener('mouseenter', () => { if (isDesktop()) openPanel(); });
    panel.addEventListener('mouseleave', () => { if (isDesktop()) scheduleClose(); });

    row.addEventListener('click', function (e) {
        if (isDesktop()) return; // desktop: hover handles it
        e.stopPropagation();
        e.preventDefault();
        row.classList.toggle(OPEN_CLASS);
    });
    document.addEventListener('click', function (e) {
        if (isDesktop()) return;
        if (!row.contains(e.target)) row.classList.remove(OPEN_CLASS);
    });
})();

window.openMoreModels = function () {
    const panel = document.getElementById('moreModelsPanel');
    const row   = document.getElementById('moreModelsRow');
    if (!panel || !row) return;

    const isOpen = panel.classList.contains('open');
    if (isOpen) return;

    const isMobile = window.innerWidth <= 768;

    if (!isMobile) {
        // Desktop: position panel to the right of the dropdown, always within viewport
        const dropdownEl = document.getElementById('modelDropdown');
        if (!dropdownEl) return;

        const panelW   = 230;
        const gap      = 8;
        const margin   = 8;

        // Measure the dropdown's CURRENT position on screen (already positioned by toggleModelSelector)
        const dropRect = dropdownEl.getBoundingClientRect();

        // Measure panel height by making it visible off-screen with position:fixed so
        // layout is not affected by any parent overflow/transform, and scrollHeight is real.
        const prevPos      = panel.style.position;
        const prevVis      = panel.style.visibility;
        const prevDisplay  = panel.style.display;
        const prevTop      = panel.style.top;
        const prevLeft     = panel.style.left;
        const prevMaxH     = panel.style.maxHeight;
        const prevOverflow = panel.style.overflowY;

        panel.style.position   = 'fixed';
        panel.style.visibility = 'hidden';
        panel.style.display    = 'block';
        panel.style.maxHeight  = '';
        panel.style.overflowY  = '';
        panel.style.top        = '-9999px';
        panel.style.left       = '-9999px';

        // Force a reflow so the browser computes the real height
        void panel.offsetHeight;
        const rawH = panel.scrollHeight || panel.offsetHeight || 260;

        // Restore (we'll set final values below)
        panel.style.position   = prevPos  || '';
        panel.style.visibility = prevVis  || '';
        panel.style.display    = prevDisplay || '';

        // Claude-style fixed cap: panel never grows past this, list scrolls internally.
        const PANEL_MAX_H = 360;
        const panelH = Math.min(rawH, PANEL_MAX_H);

        // Horizontal: prefer right of dropdown, fall back to left
        let left = dropRect.right + gap;
        if (left + panelW > window.innerWidth - margin) {
            left = dropRect.left - panelW - gap;
        }
        if (left < margin) left = margin;

        // Vertical: anchor panel bottom to dropdown bottom (grows upward),
        // but NEVER let the top go above the viewport margin.
        // If there isn't enough room above the dropdown, shrink the panel height
        // so it stays fully within the viewport without flying to the top.
        const anchoredTop = dropRect.bottom - panelH;
        const clampedTop  = Math.max(margin, anchoredTop);

        let finalH;
        if (anchoredTop < margin) {
            // Not enough vertical room — shrink panel so it fits between
            // viewport top margin and the dropdown bottom.
            const availableH = dropRect.bottom - margin;
            finalH = Math.max(Math.min(availableH, PANEL_MAX_H), 80);
        } else {
            finalH = panelH;
        }
        panel.style.maxHeight = finalH + 'px';
        panel.style.overflowY = 'auto';

        panel.style.left = left + 'px';
        panel.style.top  = clampedTop + 'px';
    } else {
        // Mobile: close main dropdown, open sub-panel as new bottom-sheet
        panel.style.left = '';
        panel.style.top  = '';
        const dropdown = document.getElementById('modelDropdown');
        const btn = document.getElementById('modelSelectorBtn');
        if (dropdown) dropdown.classList.remove('open');
        if (btn) btn.classList.remove('open');
    }

    panel.classList.add('open');
    row.classList.add('open');
};

window.closeMoreModels = function () {
    const panel = document.getElementById('moreModelsPanel');
    const row   = document.getElementById('moreModelsRow');
    if (panel) panel.classList.remove('open');
    if (row)   row.classList.remove('open');
    if (window.closePuterModels) window.closePuterModels();
};

// Mobile still uses click/tap to open the "More models" sub-panel.
// Desktop uses hover only (wired up in initMoreModelsHover below).
window.toggleMoreModels = function (e) {
    if (e) { e.stopPropagation(); e.preventDefault(); }
    const isMobile = window.innerWidth <= 768;
    if (!isMobile) return; // desktop: hover handles this, ignore clicks
    const panel = document.getElementById('moreModelsPanel');
    if (panel && panel.classList.contains('open')) {
        window.closeMoreModels();
    } else {
        window.openMoreModels();
    }
};

// ============================================================
// 🟣 PUTER MODELS — nested sub-panel inside "More models".
// Desktop: click-to-open only (NOT hover), positioned beside the
// "More models" panel. Mobile: opens as its own full bottom-sheet level,
// with a back button that returns to the "More models" panel.
// ============================================================
window.openPuterModels = function () {
    const panel = document.getElementById('puterModelsPanel');
    const row   = document.getElementById('puterModelsRow');
    if (!panel || !row) return;

    const isOpen = panel.classList.contains('open');
    if (isOpen) return;

    const isMobile = window.innerWidth <= 768;

    if (!isMobile) {
        // Desktop: position panel to the side of the "More models" panel
        const anchorEl = document.getElementById('moreModelsPanel');
        if (!anchorEl) return;

        const panelW   = 230;
        const gap      = 8;
        const margin   = 8;

        const anchorRect = anchorEl.getBoundingClientRect();

        const prevPos      = panel.style.position;
        const prevVis      = panel.style.visibility;
        const prevDisplay  = panel.style.display;
        const prevTop      = panel.style.top;
        const prevLeft     = panel.style.left;
        const prevMaxH     = panel.style.maxHeight;
        const prevOverflow = panel.style.overflowY;

        panel.style.position   = 'fixed';
        panel.style.visibility = 'hidden';
        panel.style.display    = 'block';
        panel.style.maxHeight  = '';
        panel.style.overflowY  = '';
        panel.style.top        = '-9999px';
        panel.style.left       = '-9999px';

        void panel.offsetHeight;
        const rawH = panel.scrollHeight || panel.offsetHeight || 200;

        panel.style.position   = prevPos  || '';
        panel.style.visibility = prevVis  || '';
        panel.style.display    = prevDisplay || '';

        const PANEL_MAX_H = 300;
        const panelH = Math.min(rawH, PANEL_MAX_H);

        // Horizontal: prefer right of the "More models" panel, fall back to left
        let left = anchorRect.right + gap;
        if (left + panelW > window.innerWidth - margin) {
            left = anchorRect.left - panelW - gap;
        }
        if (left < margin) left = margin;

        // Vertical: anchor panel bottom to anchor panel's bottom (grows upward)
        const anchoredTop = anchorRect.bottom - panelH;
        const clampedTop  = Math.max(margin, anchoredTop);

        let finalH;
        if (anchoredTop < margin) {
            const availableH = anchorRect.bottom - margin;
            finalH = Math.max(Math.min(availableH, PANEL_MAX_H), 80);
        } else {
            finalH = panelH;
        }
        panel.style.maxHeight = finalH + 'px';
        panel.style.overflowY = 'auto';

        panel.style.left = left + 'px';
        panel.style.top  = clampedTop + 'px';
    } else {
        // Mobile: close "More models" bottom-sheet, open this one instead
        panel.style.left = '';
        panel.style.top  = '';
        const moreModelsPanel = document.getElementById('moreModelsPanel');
        const moreModelsRow   = document.getElementById('moreModelsRow');
        if (moreModelsPanel) moreModelsPanel.classList.remove('open');
        if (moreModelsRow)   moreModelsRow.classList.remove('open');
    }

    panel.classList.add('open');
    row.classList.add('open');
};

window.closePuterModels = function () {
    const panel = document.getElementById('puterModelsPanel');
    const row   = document.getElementById('puterModelsRow');
    if (panel) panel.classList.remove('open');
    if (row)   row.classList.remove('open');
};

// Click-to-open on BOTH desktop and mobile (no hover behavior for this row).
window.togglePuterModels = function (e) {
    if (e) { e.stopPropagation(); e.preventDefault(); }
    const panel = document.getElementById('puterModelsPanel');
    if (panel && panel.classList.contains('open')) {
        window.closePuterModels();
    } else {
        window.openPuterModels();
    }
};

// Back button inside the Puter models panel (mobile only) — returns to the
// "More models" panel, NOT the main model dropdown.
window.goBackToMoreModels = function (e) {
    if (e) { e.stopPropagation(); e.preventDefault(); }
    window.closePuterModels();

    const moreModelsPanel = document.getElementById('moreModelsPanel');
    const moreModelsRow   = document.getElementById('moreModelsRow');
    if (moreModelsPanel) {
        moreModelsPanel.style.left = '';
        moreModelsPanel.style.top  = '';
        moreModelsPanel.classList.add('open');
    }
    if (moreModelsRow) moreModelsRow.classList.add('open');
};

// Close the Puter models panel when clicking anywhere outside it (desktop),
// but not when clicking the row that opens it (handled by togglePuterModels).
document.addEventListener('click', function (e) {
    const isDesktop = window.innerWidth > 768;
    if (!isDesktop) return;
    const panel = document.getElementById('puterModelsPanel');
    const row   = document.getElementById('puterModelsRow');
    if (!panel || !panel.classList.contains('open')) return;
    if (panel.contains(e.target) || (row && row.contains(e.target))) return;
    window.closePuterModels();
});

// ── DESKTOP: "More models" opens on hover (Claude-style), like Effort Level.
// Stays open while pointer is over the row OR the panel (small close-delay so
// the pointer can travel between them), and closes instantly on: outside
// click, or the pointer moving to hover any other sibling model option/row.
(function initMoreModelsHover() {
    const row   = document.getElementById('moreModelsRow');
    const panel = document.getElementById('moreModelsPanel');
    const dropdown = document.getElementById('modelDropdown');
    if (!row || !panel || !dropdown) return;

    let closeTimer = null;
    const CLOSE_DELAY = 250;

    function isDesktop() { return window.innerWidth > 768; }

    function open() {
        if (closeTimer) { clearTimeout(closeTimer); closeTimer = null; }
        window.openMoreModels();
    }
    function scheduleClose() {
        if (closeTimer) clearTimeout(closeTimer);
        closeTimer = setTimeout(() => {
            window.closeMoreModels();
            closeTimer = null;
        }, CLOSE_DELAY);
    }
    function closeInstant() {
        if (closeTimer) { clearTimeout(closeTimer); closeTimer = null; }
        window.closeMoreModels();
    }

    row.addEventListener('mouseenter', () => { if (isDesktop()) open(); });
    row.addEventListener('mouseleave', () => { if (isDesktop()) scheduleClose(); });
    panel.addEventListener('mouseenter', () => { if (isDesktop()) open(); });
    panel.addEventListener('mouseleave', () => { if (isDesktop()) scheduleClose(); });

    // The Puter models panel is a nested flyout that lives OUTSIDE
    // moreModelsPanel in the DOM (so its own overflow never clips it).
    // Because of that, moving the mouse from moreModelsPanel into it fires
    // a mouseleave on moreModelsPanel — without this, the hover close-timer
    // would fire mid-hover and yank the whole panel away before a model
    // could be clicked. Treat hovering it as still "hovering more models".
    const puterPanel = document.getElementById('puterModelsPanel');
    if (puterPanel) {
        puterPanel.addEventListener('mouseenter', () => { if (isDesktop()) open(); });
        puterPanel.addEventListener('mouseleave', () => { if (isDesktop()) scheduleClose(); });
    }

    // Instantly close if the pointer hovers any other item in the main
    // dropdown (model options, effort level row, etc.) — Claude-style
    // "only one flyout open at a time".
    dropdown.querySelectorAll('.model-option, #effortLevelRow').forEach(el => {
        if (el === row) return;
        el.addEventListener('mouseenter', () => { if (isDesktop()) closeInstant(); });
    });

    // Instant close on any click anywhere (outside the panel itself), even
    // clicks inside the dropdown — matches "as soon as mouse clicked
    // anywhere it closes instantly".
    document.addEventListener('click', (e) => {
        if (!isDesktop()) return;
        if (panel.contains(e.target)) return; // allow selecting a model inside the panel
        const puterPanel = document.getElementById('puterModelsPanel');
        if (puterPanel && puterPanel.contains(e.target)) return; // allow interacting with nested puter panel
        closeInstant();
    });
})();

// Back button: close more-models panel and reopen main dropdown
window.goBackToMainModels = function (e) {
    if (e) { e.stopPropagation(); e.preventDefault(); }
    const panel    = document.getElementById('moreModelsPanel');
    const row      = document.getElementById('moreModelsRow');
    const dropdown = document.getElementById('modelDropdown');
    const btn      = document.getElementById('modelSelectorBtn');

    if (panel) panel.classList.remove('open');
    if (row)   row.classList.remove('open');

    if (dropdown && btn) {
        // Re-run the same positioning logic as toggleModelSelector so the
        // dropdown ends up in exactly the right place regardless of whether
        // the input box is centred or at the bottom.
        const isMobile = window.innerWidth <= 768;
        if (isMobile) {
            dropdown.style.top    = '';
            dropdown.style.left   = '';
            dropdown.style.bottom = '';
        } else {
            const rect = btn.getBoundingClientRect();

            // Measure dropdown height off-screen with position:fixed
            const prevPos = dropdown.style.position;
            const prevVis = dropdown.style.visibility;
            const prevDisp = dropdown.style.display;
            dropdown.style.position   = 'fixed';
            dropdown.style.visibility = 'hidden';
            dropdown.style.display    = 'block';
            dropdown.style.top        = '-9999px';
            dropdown.style.left       = '-9999px';
            void dropdown.offsetHeight;
            const dropH = dropdown.scrollHeight || dropdown.offsetHeight || 180;
            const dropW = Math.max(dropdown.offsetWidth || 0, 220);
            dropdown.style.position   = prevPos  || '';
            dropdown.style.visibility = prevVis  || '';
            dropdown.style.display    = prevDisp || '';

            let top = rect.top - dropH - 8;
            if (top < 8) top = rect.bottom + 8;
            top = Math.max(8, Math.min(top, window.innerHeight - dropH - 8));

            let left = rect.right - dropW;
            if (left < 8) left = 8;
            if (left + dropW > window.innerWidth - 8) left = window.innerWidth - dropW - 8;

            dropdown.style.top    = top + 'px';
            dropdown.style.left   = left + 'px';
            dropdown.style.bottom = 'auto';
        }
        dropdown.classList.add('open');
        btn.classList.add('open');
    }
};

// Close dropdown when clicking outside
document.addEventListener('click', function (e) {
    const wrap       = document.getElementById('modelSelectorWrap');
    const dropdown   = document.getElementById('modelDropdown');
    const panel      = document.getElementById('moreModelsPanel');
    const puterPanel = document.getElementById('puterModelsPanel');
    const insideWrap       = wrap       && wrap.contains(e.target);
    const insideDropdown   = dropdown   && dropdown.contains(e.target);
    const insidePanel      = panel      && panel.contains(e.target);
    const insidePuterPanel = puterPanel && puterPanel.contains(e.target);
    if (!insideWrap && !insideDropdown && !insidePanel && !insidePuterPanel) {
        closeAllModelMenus();
    }
});

// Get currently selected model
function getSelectedModel() {
    return selectedModel;
}

// ============================================================
// 🟣 CLAUDE SONNET VIA PUTER.JS (frontend-only — bypasses /chat)
// ============================================================
// Puter renames/upgrades its Claude Sonnet model over time, so instead of
// hardcoding a model id, we ask Puter for its live catalog and pick the
// newest "sonnet" entry. If that lookup ever fails, we fall back to the
// latest known id rather than breaking the feature.
// ── Shared tool-badge + sources-chip renderer — used by every model,
// including Claude, so the UI is identical no matter which model ran
// the search/wiki/etc. lookup.
function renderToolBadgeAndSources(wrapper, botMsg, toolUsed, pendingSources) {
    if (!wrapper) return;

    if (toolUsed) {
        const toolBadges = {
            clock:      { icon: "🕐", label: "Live Clock" },
            weather:    { icon: "🌤️", label: "Live Weather" },
            finance:    { icon: "💹", label: "Market Data" },
            sports:     { icon: "🏏", label: "Live Scores" },
            news:       { icon: "📰", label: "Latest News" },
            web_search: { icon: "🔍", label: "Web Search" },
            wikipedia:  { icon: "📖", label: "Wikipedia" },
        };
        const badge = toolBadges[toolUsed] || { icon: "🔧", label: toolUsed };
        const badgeDiv = document.createElement("div");
        badgeDiv.className = "tool-badge";
        badgeDiv.setAttribute("data-tool", toolUsed);
        badgeDiv.innerHTML = `<span class="tool-badge-icon">${badge.icon}</span><span class="tool-badge-label">${badge.label} used</span>`;
        wrapper.insertBefore(badgeDiv, botMsg);
    }

    if (pendingSources && pendingSources.length > 0) {
        const sourcesDiv = document.createElement("div");
        sourcesDiv.className = "sources-section";
        const chips = pendingSources.map(s => {
            if (!s.url) return '';
            const favicon = `https://www.google.com/s2/favicons?domain=${encodeURIComponent(s.domain)}&sz=16`;
            const numBadge = s.num ? `<span class="source-num">[${s.num}]</span>` : '';
            const trustClass = s.trust >= 80 ? 'trust-high' : s.trust >= 60 ? 'trust-med' : 'trust-low';
            return `<a class="source-chip ${trustClass}" href="${s.url}" target="_blank" rel="noopener noreferrer" title="${s.title || s.domain}">
                ${numBadge}
                <img class="source-favicon" src="${favicon}" onerror="this.style.display='none'" alt="">
                <span class="source-domain">${s.domain}</span>
            </a>`;
        }).filter(Boolean).join("");
        const count = pendingSources.filter(s => s.url).length;
        sourcesDiv.innerHTML = `
            <div class="sources-label">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                ${count} Source${count !== 1 ? 's' : ''}
            </div>
            <div class="sources-chips">${chips}</div>`;
        wrapper.appendChild(sourcesDiv);
    }
}

// ── Client-side Wikipedia lookup — mirrors wiki.py's pipeline (search →
// score candidates → fetch summary → format context). Uses only the free,
// keyless Wikimedia REST API, so this runs entirely in the browser with
// zero backend changes.
async function lookupWikipediaForPuter(query) {
    const STOPWORDS = new Set(["the","a","an","of","in","on","for","to","and","or","is","are",
        "was","were","what","who","when","where","how","does","do","did","this","that","with","at","by","be","it","as","from"]);

    function scoreCandidate(candidate, q) {
        const qWords = q.toLowerCase().split(/\s+/).filter(w => w && !STOPWORDS.has(w));
        const text = `${candidate.title} ${candidate.description || ''}`.toLowerCase();
        let score = 0;
        qWords.forEach(w => { if (text.includes(w)) score += 1; });
        return score;
    }

    try {
        const searchRes = await fetch(`https://en.wikipedia.org/w/rest.php/v1/search/page?q=${encodeURIComponent(query)}&limit=5`);
        const searchData = await searchRes.json();
        const candidates = searchData.pages || [];
        if (candidates.length === 0) return null;

        candidates.sort((a, b) => scoreCandidate(b, query) - scoreCandidate(a, query));

        for (const candidate of candidates) {
            const title = candidate.title;
            const summaryRes = await fetch(`https://en.wikipedia.org/api/rest_v1/page/summary/${encodeURIComponent(title)}`);
            if (!summaryRes.ok) continue;
            const data = await summaryRes.json();
            if (data.type === 'disambiguation') continue;

            const extract = (data.extract || '').trim();
            if (extract.length < 80) continue;

            const url = (data.content_urls && data.content_urls.desktop && data.content_urls.desktop.page) || data.canonical_page_url || '';
            return {
                context: `Wikipedia — ${data.title}:\n${extract}${data.timestamp ? `\n(Wikipedia article last updated: ${data.timestamp})` : ''}`,
                source: { url, title: data.title, domain: 'en.wikipedia.org', trust: 90, num: 1 }
            };
        }
        return null;
    } catch (err) {
        console.warn("⚠️ Wikipedia lookup failed:", err);
        return null;
    }
}

const CLAUDE_SONNET_FALLBACK_MODEL = 'claude-sonnet-5';
let _cachedClaudeSonnetModel = null;

// ── PUTER-ROUTED MODEL REGISTRY ─────────────────────────────────────────────
// Every model selectable in the dropdown that runs via Puter.js instead of
// the FastAPI backend gets one entry here: its display name (for the info
// modal + toasts) and a resolver that returns the exact Puter model id to
// call. Adding a new Puter model in future = one entry here + one dropdown
// option in index.html — nothing else needs to change.
const PUTER_MODEL_REGISTRY = {
    claude_puter: {
        displayName: 'Claude Sonnet',
        icon: 'static/icons/claude.png',
        resolve: getLatestClaudeSonnetModelId // defined below
    },
    gpt5_puter: {
        displayName: 'GPT-5.4 Mini',
        icon: 'static/icons/openai.png',
        resolve: async () => 'openai/gpt-5.4-mini'
    },
    gemini_puter: {
        displayName: 'Gemini 3.5 Flash Lite',
        icon: 'static/icons/gemini.png',
        resolve: async () => 'google/gemini-3.5-flash-lite'
    },
    grok_puter: {
        displayName: 'Grok 4',
        icon: 'static/icons/grok.png',
        resolve: async () => 'x-ai/grok-4-1-fast-non-reasoning'
    }
};
function isPuterRoutedModel(modelId) {
    return Object.prototype.hasOwnProperty.call(PUTER_MODEL_REGISTRY, modelId);
}

// Shown when the user selects ANY Puter-routed model (Claude, GPT-5.4 Mini,
// etc.) — tells them a one-time external Puter sign-in popup will appear
// when they send their first message. Every backend model is unaffected.
window.showPuterInfoModal = function (modelId, displayName) {
    const entry = PUTER_MODEL_REGISTRY[modelId];
    const titleEl = document.getElementById('puterInfoModalTitle');
    if (titleEl) titleEl.textContent = `${displayName || (entry && entry.displayName) || 'This model'} — External Model`;
    const iconEl = document.getElementById('puterInfoModalIcon');
    if (iconEl) {
        iconEl.style.display = '';
        iconEl.src = (entry && entry.icon) || 'static/icons/claude.png';
    }
    const modal = document.getElementById('claudePuterInfoModal');
    if (modal) modal.classList.add('active');
};
// Kept as an alias so nothing else that may reference the old name breaks.
window.showClaudePuterInfoModal = function () { window.showPuterInfoModal('claude_puter', 'Claude Sonnet'); };

window.closeClaudePuterInfoModal = function () {
    const modal = document.getElementById('claudePuterInfoModal');
    if (modal) modal.classList.remove('active');
};

async function getLatestClaudeSonnetModelId() {
    if (_cachedClaudeSonnetModel) return _cachedClaudeSonnetModel;
    try {
        const models = await puter.ai.listModels();
        const sonnets = (models || []).filter(m =>
            m.provider === 'claude' && /sonnet/i.test(m.id || m.name || '')
        );
        if (sonnets.length === 0) return (_cachedClaudeSonnetModel = CLAUDE_SONNET_FALLBACK_MODEL);

        // Highest version wins, e.g. claude-sonnet-5 beats claude-sonnet-4-6
        sonnets.sort((a, b) => {
            const ver = id => (id.match(/[\d.]+/g) || ['0']).map(Number);
            const va = ver(a.id), vb = ver(b.id);
            for (let i = 0; i < Math.max(va.length, vb.length); i++) {
                const diff = (vb[i] || 0) - (va[i] || 0);
                if (diff) return diff;
            }
            return 0;
        });
        return (_cachedClaudeSonnetModel = sonnets[0].id);
    } catch (err) {
        console.warn("⚠️ Puter listModels() failed, using fallback Claude model:", err);
        return (_cachedClaudeSonnetModel = CLAUDE_SONNET_FALLBACK_MODEL);
    }
}

async function ensurePuterAuth() {
    if (typeof puter === 'undefined') {
        throw new Error("Puter.js failed to load. Check your connection and try again.");
    }
    const signedIn = await puter.auth.isSignedIn();
    if (!signedIn) {
        await puter.auth.signIn(); // shows Puter's own login popup automatically
    }
}

// Streams a Claude Sonnet reply into the SAME bot bubble UI every other
// model uses, then returns the full text so the caller can save it to
// chat history exactly like a normal bot message.
async function sendViaPuterModel(modelKey, promptText, thinking, chatbox, filesToSend, detectedIntent, originalMessage) {
    await ensurePuterAuth();
    const entry = PUTER_MODEL_REGISTRY[modelKey];
    const model = entry ? await entry.resolve() : modelKey;

    filesToSend = filesToSend || [];
    // Only files that finished uploading successfully have a usable URL
    const readyFiles = filesToSend.filter(f => f.url && !f.uploading && !f.failed);

    // ── Images — Claude/Puter's native vision support (documented) ──────────
    const imageUrls = readyFiles
        .filter(f => (f.type || '').startsWith('image/'))
        .map(f => f.url);

    const isDocLike = f => (f.type || '') === 'application/pdf' ||
                            (f.type || '').includes('word') ||
                            (f.type || '').includes('document');

    // ── Plain text / code files — read their content client-side and inline
    // it as context. Puter's chat API doesn't document reading arbitrary
    // external URLs as "documents", so this is the reliable path for these.
    const textFiles = readyFiles.filter(f => !(f.type || '').startsWith('image/') && !isDocLike(f));

    let augmentedPrompt = promptText || '';
    for (const f of textFiles) {
        try {
            const res  = await fetch(f.url);
            const text = await res.text();
            augmentedPrompt += `\n\n--- Attached file: ${f.name} ---\n${text.slice(0, 20000)}`;
        } catch (err) {
            console.warn("⚠️ Couldn't read attached file for Claude:", f.name, err);
            augmentedPrompt += `\n\n[Attached file "${f.name}" could not be read.]`;
        }
    }

    // ── PDFs / Word docs — Puter doesn't document full-content reading for
    // arbitrary external URLs, so we only pass along the filename + link
    // rather than claim we handed over the real contents.
    const docFiles = readyFiles.filter(isDocLike);
    for (const f of docFiles) {
        augmentedPrompt += `\n\n[User attached a document "${f.name}" (${f.url}). Full document contents may not be readable through this integration — let the user know if you can't access it.]`;
    }

    // ── 🔍 Web search / 📖 Wikipedia — same tools the other models use,
    // wired up for Claude too. Web search reuses the existing /search
    // endpoint (backend keys stay server-side); Wikipedia runs entirely
    // client-side since Wikimedia's REST API needs no key.
    let toolUsed = null;
    let pendingSources = null;

    const WEB_SEARCH_INTENTS = new Set(['web_search', 'news', 'finance', 'sports']);
    if (originalMessage && WEB_SEARCH_INTENTS.has(detectedIntent)) {
        const results = await performWebSearch(originalMessage);
        if (results && results.length > 0) {
            toolUsed = 'web_search';
            pendingSources = results.map((r, i) => ({
                url   : r.url,
                title : r.title,
                domain: r.domain || (r.url ? (() => { try { return new URL(r.url).hostname.replace(/^www\./, ''); } catch (_) { return r.url; } })() : ''),
                trust : r.trust ?? 80,
                num   : r.num ?? (i + 1)
            }));
            const context = results.map((r, i) =>
                `[${i + 1}] ${r.title || r.domain || r.url}\n${r.snippet || r.content || ''}\nSource: ${r.url}`
            ).join('\n\n');
            augmentedPrompt = `Web search results for "${originalMessage}":\n\n${context}\n\n---\nUsing the results above (cite with [1], [2] etc. where relevant), answer: ${augmentedPrompt}`;
        }
    } else if (originalMessage && detectedIntent === 'wikipedia') {
        const wiki = await lookupWikipediaForPuter(originalMessage);
        if (wiki) {
            toolUsed = 'wikipedia';
            pendingSources = [wiki.source];
            augmentedPrompt = `${wiki.context}\n\n---\nUsing the Wikipedia context above, answer: ${augmentedPrompt}`;
        }
    }

    const chatOptions = { model: model, stream: true };
    const response = imageUrls.length
        ? await puter.ai.chat(augmentedPrompt, imageUrls.length === 1 ? imageUrls[0] : imageUrls, chatOptions)
        : await puter.ai.chat(augmentedPrompt, chatOptions);

    let wrapper = null, botMsg = null, fullReply = "";
    for await (const part of response) {
        const chunk = part?.text || "";
        if (!chunk) continue;
        if (!wrapper) {
            thinking.remove();
            const created = createBotWrapper();
            wrapper = created.wrapper;
            botMsg  = created.botMsg;
            chatbox.appendChild(wrapper);
        }
        fullReply += chunk;
        botMsg.innerHTML = formatMessage(repairTruncated(fullReply));
        chatbox.scrollTop = chatbox.scrollHeight;
    }

    // Fallback in case nothing streamed but a reply did come back
    if (!wrapper) {
        thinking.remove();
        const created = createBotWrapper();
        wrapper = created.wrapper;
        botMsg  = created.botMsg;
        chatbox.appendChild(wrapper);
    }

    renderBotContent(wrapper, botMsg, fullReply.trimEnd());
    renderToolBadgeAndSources(wrapper, botMsg, toolUsed, pendingSources);
    return fullReply.trimEnd();
}

// ============================================================
// ✅ PRIVACY POLICY MODAL
// ============================================================
window.showPrivacyModal = function () {
    // Remove any existing
    document.getElementById('privacyModal')?.remove();

    const modal = document.createElement('div');
    modal.id = 'privacyModal';
    modal.className = 'priv-modal-overlay';
    modal.innerHTML = `
        <div class="priv-modal-box" role="dialog" aria-modal="true" aria-label="Data & Privacy">
            <div class="priv-modal-header">
                <div class="priv-modal-title-wrap">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#10a37f" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>
                    </svg>
                    <h2 class="priv-modal-title">Data &amp; Privacy</h2>
                </div>
                <button class="priv-modal-close" onclick="document.getElementById('privacyModal').remove()" aria-label="Close">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line>
                    </svg>
                </button>
            </div>
            <div class="priv-modal-body">
                <p class="priv-intro">We respect your privacy and are committed to protecting your data.</p>

                <div class="priv-item">
                    <div class="priv-num">1</div>
                    <div>
                        <p class="priv-item-title">Data We Collect</p>
                        <p class="priv-item-text">We may collect basic information such as your email address, chat messages, and usage data to improve our AI services.</p>
                    </div>
                </div>
                <div class="priv-item">
                    <div class="priv-num">2</div>
                    <div>
                        <p class="priv-item-title">How We Use Your Data</p>
                        <p class="priv-item-text">Your data is used to provide responses, improve AI performance, and ensure security. We do not sell your personal data to third parties.</p>
                    </div>
                </div>
                <div class="priv-item">
                    <div class="priv-num">3</div>
                    <div>
                        <p class="priv-item-title">AI Conversations</p>
                        <p class="priv-item-text">Your conversations may be stored and analyzed to improve the quality of responses. Avoid sharing sensitive or personal information.</p>
                    </div>
                </div>
                <div class="priv-item">
                    <div class="priv-num">4</div>
                    <div>
                        <p class="priv-item-title">Email &amp; Authentication</p>
                        <p class="priv-item-text">We use your email for account creation, login verification (OTP), and password recovery. We do not send spam.</p>
                    </div>
                </div>
                <div class="priv-item">
                    <div class="priv-num">5</div>
                    <div>
                        <p class="priv-item-title">Data Security</p>
                        <p class="priv-item-text">We implement reasonable security measures to protect your data, but no system is completely secure.</p>
                    </div>
                </div>
                <div class="priv-item">
                    <div class="priv-num">6</div>
                    <div>
                        <p class="priv-item-title">Third-Party Services</p>
                        <p class="priv-item-text">We may use trusted third-party services (such as authentication and email providers) to operate our platform.</p>
                    </div>
                </div>
                <div class="priv-item">
                    <div class="priv-num">7</div>
                    <div>
                        <p class="priv-item-title">Your Control</p>
                        <p class="priv-item-text">You can request deletion of your data at any time by contacting support.</p>
                    </div>
                </div>
                <div class="priv-item">
                    <div class="priv-num">8</div>
                    <div>
                        <p class="priv-item-title">Changes to Policy</p>
                        <p class="priv-item-text">We may update this policy from time to time. Continued use of the service means you accept the changes.</p>
                    </div>
                </div>

                <div class="priv-contact">
                    <p class="priv-contact-title">Contact Us</p>
                    <div class="priv-contact-row">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#10a37f" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"></path>
                            <polyline points="22,6 12,13 2,6"></polyline>
                        </svg>
                        <a href="mailto:support@yourdomain.co" class="priv-link">support@yourdomain.co</a>
                    </div>
                    <div class="priv-contact-row">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#10a37f" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"></path>
                        </svg>
                        <a href="https://github.com/**************" target="_blank" rel="noopener" class="priv-link">github.com/**************</a>
                    </div>
                    <div class="priv-contact-row">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#10a37f" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M16 8a6 6 0 0 1 6 6v7h-4v-7a2 2 0 0 0-2-2 2 2 0 0 0-2 2v7h-4v-7a6 6 0 0 1 6-6z"></path>
                            <rect x="2" y="9" width="4" height="12"></rect>
                            <circle cx="4" cy="4" r="2"></circle>
                        </svg>
                        <a href="https://linkedin.com/in/************************" target="_blank" rel="noopener" class="priv-link">linkedin.com/in/************************</a>
                    </div>
                </div>
            </div>
            <div class="priv-modal-footer">
                <button class="priv-close-btn" onclick="document.getElementById('privacyModal').remove()">Close</button>
            </div>
        </div>
    `;

    // Close on backdrop click
    modal.addEventListener('click', (e) => {
        if (e.target === modal) modal.remove();
    });

    document.body.appendChild(modal);
    // Animate in
    requestAnimationFrame(() => modal.classList.add('priv-modal-open'));
};

// ============================================================
// 🔐 MFA (TOTP) IMPLEMENTATION — Supabase Auth mfa
// ============================================================

// Refresh MFA status label in the account settings row
async function refreshMFAStatus() {
    const sub = document.getElementById('mfaStatusSub');
    if (!sub) return;
    try {
        const { data, error } = await supabaseClient.auth.mfa.listFactors();
        if (error) throw error;
        const verified = (data?.totp || []).filter(f => f.status === 'verified');
        if (verified.length > 0) {
            sub.textContent = 'Enabled — tap to manage';
            sub.style.color = '#10a37f';
        } else {
            sub.textContent = 'Not enabled — tap to set up';
            sub.style.color = '';
        }
    } catch(e) {
        sub.textContent = 'Tap to manage';
        sub.style.color = '';
    }
}

// Patch showSettingsTab to refresh MFA status whenever account tab opens
const _origShowSettingsTab = window.showSettingsTab;
window.showSettingsTab = function(tab, el) {
    _origShowSettingsTab && _origShowSettingsTab(tab, el);
    if (tab === 'account') {
        setTimeout(refreshMFAStatus, 100);
    }
};

window.openMFAModal = async function() {
    document.getElementById('mfaModal')?.remove();

    // Check current MFA status
    let existingFactors = [];
    try {
        const { data } = await supabaseClient.auth.mfa.listFactors();
        existingFactors = (data?.totp || []).filter(f => f.status === 'verified');
    } catch(e) {}

    const isEnabled = existingFactors.length > 0;

    const modal = document.createElement('div');
    modal.id = 'mfaModal';
    modal.className = 'mfa-modal-overlay';
    modal.innerHTML = `
        <div class="mfa-backdrop" id="mfaBackdrop"></div>
        <div class="mfa-dialog" role="dialog" aria-modal="true" aria-label="Multi-factor authentication">
            <button class="mfa-close-btn" id="mfaCloseBtn" title="Close">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>

            <div class="mfa-header">
                <div class="mfa-icon-wrap">
                    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#10a37f" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
                    </svg>
                </div>
                <h2 class="mfa-title">Two-Factor Authentication</h2>
                <p class="mfa-subtitle">Protect your account with an authenticator app (Google Authenticator, Authy, etc.)</p>
            </div>

            <div id="mfaBody">
                ${isEnabled ? `
                    <div class="mfa-status-badge mfa-status-on">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
                        2FA is currently <strong>enabled</strong>
                    </div>
                    <p class="mfa-info-text">Your account is protected by an authenticator app. You can remove 2FA below — you will need to re-verify with a code.</p>
                    <button class="mfa-btn mfa-btn-danger" id="mfaDisableBtn">Remove 2FA</button>
                ` : `
                    <div class="mfa-status-badge mfa-status-off">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
                        2FA is currently <strong>not enabled</strong>
                    </div>
                    <button class="mfa-btn mfa-btn-primary" id="mfaEnrollBtn">Set up 2FA</button>
                `}
            </div>
        </div>
    `;
    document.body.appendChild(modal);
    requestAnimationFrame(() => modal.classList.add('mfa-visible'));

    document.getElementById('mfaCloseBtn').onclick = closeMFAModal;
    document.getElementById('mfaBackdrop').onclick = closeMFAModal;

    if (isEnabled) {
        document.getElementById('mfaDisableBtn').onclick = () => _mfaStartDisable(existingFactors[0].id);
    } else {
        document.getElementById('mfaEnrollBtn').onclick = _mfaStartEnroll;
    }
};

async function _mfaStartEnroll() {
    const body = document.getElementById('mfaBody');
    body.innerHTML = `<div class="mfa-loading"><div class="mfa-spinner"></div><span>Generating QR code…</span></div>`;

    try {
        const { data, error } = await supabaseClient.auth.mfa.enroll({ factorType: 'totp', friendlyName: 'Catura AI' });
        if (error) throw error;

        const { id: factorId, totp } = data;
        const qrSvg = totp.qr_code;   // SVG string from Supabase
        const secret = totp.secret;

        body.innerHTML = `
            <p class="mfa-step-label">Step 1 — Scan this QR code with your authenticator app</p>
            <div class="mfa-qr-wrap" id="mfaQrWrap">${qrSvg}</div>
            <details class="mfa-secret-details">
                <summary>Can't scan? Enter the key manually</summary>
                <code class="mfa-secret-code" id="mfaSecretCode">${secret}</code>
                <button class="mfa-copy-secret" onclick="navigator.clipboard.writeText('${secret}').then(()=>showToast('Key copied!'))">Copy key</button>
            </details>
            <p class="mfa-step-label" style="margin-top:20px;">Step 2 — Enter the 6-digit code from your app to verify</p>
            <div class="mfa-otp-row">
                <input type="text" id="mfaOtpInput" class="mfa-otp-input" maxlength="6" inputmode="numeric" pattern="[0-9]*" placeholder="000000" autocomplete="one-time-code">
                <button class="mfa-btn mfa-btn-primary" id="mfaVerifyBtn">Verify &amp; Enable</button>
            </div>
            <p class="mfa-error" id="mfaError" style="display:none;"></p>
        `;

        const input = document.getElementById('mfaOtpInput');
        const verifyBtn = document.getElementById('mfaVerifyBtn');

        // Only allow digits
        input.addEventListener('input', () => {
            input.value = input.value.replace(/\D/g, '').slice(0, 6);
        });
        input.addEventListener('keydown', e => { if (e.key === 'Enter') verifyBtn.click(); });
        input.focus();

        verifyBtn.onclick = async () => {
            const code = input.value.trim();
            if (code.length !== 6) {
                _mfaShowError('Please enter a 6-digit code.');
                return;
            }
            verifyBtn.disabled = true;
            verifyBtn.textContent = 'Verifying…';

            try {
                // Create a challenge then verify
                const { data: challengeData, error: challengeErr } = await supabaseClient.auth.mfa.challenge({ factorId });
                if (challengeErr) throw challengeErr;

                const { data: verifyData, error: verifyErr } = await supabaseClient.auth.mfa.verify({
                    factorId,
                    challengeId: challengeData.id,
                    code
                });
                if (verifyErr) throw verifyErr;

                // Success
                body.innerHTML = `
                    <div class="mfa-success-wrap">
                        <div class="mfa-success-icon">
                            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#10a37f" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
                        </div>
                        <h3 class="mfa-success-title">2FA Enabled!</h3>
                        <p class="mfa-success-desc">Your account is now protected. You'll be asked for a code from your authenticator app each time you sign in.</p>
                        <button class="mfa-btn mfa-btn-primary" onclick="closeMFAModal()">Done</button>
                    </div>
                `;
                refreshMFAStatus();
                showToast('Two-factor authentication enabled ✓');
            } catch(e) {
                verifyBtn.disabled = false;
                verifyBtn.textContent = 'Verify & Enable';
                _mfaShowError(e.message || 'Invalid code. Try again.');
            }
        };
    } catch(e) {
        body.innerHTML = `<p class="mfa-error" style="display:block;">${e.message || 'Failed to start setup. Try again.'}</p>
            <button class="mfa-btn mfa-btn-primary" onclick="_mfaStartEnroll()" style="margin-top:12px;">Retry</button>`;
    }
}

async function _mfaStartDisable(factorId) {
    const body = document.getElementById('mfaBody');

    // First challenge to get a fresh code
    body.innerHTML = `
        <div class="mfa-status-badge mfa-status-off">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
            Verify to remove 2FA
        </div>
        <p class="mfa-info-text">Enter the current 6-digit code from your authenticator app to confirm removal.</p>
        <div class="mfa-otp-row">
            <input type="text" id="mfaDisableOtp" class="mfa-otp-input" maxlength="6" inputmode="numeric" pattern="[0-9]*" placeholder="000000" autocomplete="one-time-code">
            <button class="mfa-btn mfa-btn-danger" id="mfaConfirmDisableBtn">Remove 2FA</button>
        </div>
        <p class="mfa-error" id="mfaError" style="display:none;"></p>
    `;

    const input = document.getElementById('mfaDisableOtp');
    const btn = document.getElementById('mfaConfirmDisableBtn');
    input.addEventListener('input', () => { input.value = input.value.replace(/\D/g, '').slice(0, 6); });
    input.addEventListener('keydown', e => { if (e.key === 'Enter') btn.click(); });
    input.focus();

    btn.onclick = async () => {
        const code = input.value.trim();
        if (code.length !== 6) { _mfaShowError('Please enter a 6-digit code.'); return; }
        btn.disabled = true;
        btn.textContent = 'Removing…';

        try {
            const { data: ch, error: chErr } = await supabaseClient.auth.mfa.challenge({ factorId });
            if (chErr) throw chErr;

            const { error: vErr } = await supabaseClient.auth.mfa.verify({ factorId, challengeId: ch.id, code });
            if (vErr) throw vErr;

            // Unenroll the factor
            const { error: uErr } = await supabaseClient.auth.mfa.unenroll({ factorId });
            if (uErr) throw uErr;

            body.innerHTML = `
                <div class="mfa-success-wrap">
                    <div class="mfa-success-icon" style="background:rgba(224,108,108,0.12);">
                        <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#e06c6c" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
                    </div>
                    <h3 class="mfa-success-title">2FA Removed</h3>
                    <p class="mfa-success-desc">Two-factor authentication has been disabled for your account.</p>
                    <button class="mfa-btn mfa-btn-primary" onclick="closeMFAModal()">Done</button>
                </div>
            `;
            refreshMFAStatus();
            showToast('Two-factor authentication removed');
        } catch(e) {
            btn.disabled = false;
            btn.textContent = 'Remove 2FA';
            _mfaShowError(e.message || 'Invalid code. Try again.');
        }
    };
}

function _mfaShowError(msg) {
    const el = document.getElementById('mfaError');
    if (!el) return;
    el.textContent = msg;
    el.style.display = 'block';
    setTimeout(() => { if (el) el.style.display = 'none'; }, 4000);
}

window.closeMFAModal = function() {
    const modal = document.getElementById('mfaModal');
    if (!modal) return;
    modal.classList.remove('mfa-visible');
    setTimeout(() => modal.remove(), 300);
};

// ============================================================
// ✅ DELETE ACCOUNT MODAL
// ============================================================
window.showDeleteAccountModal = function () {
    document.getElementById('deleteAccountModal')?.remove();

    const userEmail = currentUser?.email || '';

    const modal = document.createElement('div');
    modal.id = 'deleteAccountModal';
    modal.className = 'priv-modal-overlay';
    modal.innerHTML = `
        <div class="priv-modal-box del-modal-box" role="dialog" aria-modal="true" aria-label="Delete Account">
            <div class="del-modal-icon-wrap">
                <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#e06c6c" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="3 6 5 6 21 6"></polyline>
                    <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path>
                    <path d="M10 11v6M14 11v6"></path>
                    <path d="M9 6V4h6v2"></path>
                </svg>
            </div>
            <h2 class="del-modal-title">Delete Account</h2>
            <p class="del-modal-desc">This action is <strong>permanent and irreversible</strong>. All your conversations, settings, and account data will be deleted forever.</p>
            <p class="del-modal-confirm-label">Type <span class="del-confirm-word">${userEmail}</span> to confirm:</p>
            <input type="email" id="deleteConfirmInput" class="del-confirm-input" placeholder="${userEmail}" autocomplete="off" oninput="checkDeleteConfirm()">
            <div class="del-modal-actions">
                <button class="del-cancel-btn" onclick="document.getElementById('deleteAccountModal').remove()">Cancel</button>
                <button class="del-confirm-btn" id="deleteConfirmBtn" disabled onclick="executeDeleteAccount()">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="3 6 5 6 21 6"></polyline>
                        <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path>
                    </svg>
                    Delete my account
                </button>
            </div>
        </div>
    `;

    modal.addEventListener('click', (e) => {
        if (e.target === modal) modal.remove();
    });

    document.body.appendChild(modal);
    requestAnimationFrame(() => modal.classList.add('priv-modal-open'));

    // Focus input
    setTimeout(() => document.getElementById('deleteConfirmInput')?.focus(), 100);
};

// Enable the delete button only when user types their email exactly
window.checkDeleteConfirm = function () {
    const val = document.getElementById('deleteConfirmInput')?.value || '';
    const btn = document.getElementById('deleteConfirmBtn');
    const userEmail = currentUser?.email || '';
    if (btn) btn.disabled = val.trim().toLowerCase() !== userEmail.toLowerCase();
};

// Execute account deletion — pure Supabase client-side
window.executeDeleteAccount = async function () {
    const btn = document.getElementById('deleteConfirmBtn');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '⏳ Deleting…';
    }

    try {
        if (!currentUser) {
            showModal({ type: 'alert', title: 'Not logged in', subtitle: 'Please refresh and try again.' });
            if (btn) { btn.disabled = false; btn.textContent = 'Delete my account'; }
            return;
        }

        const userId = currentUser.id;

        // 1. Delete all messages for this user
        const { error: msgErr } = await supabaseClient
            .from('messages')
            .delete()
            .eq('user_id', userId);
        if (msgErr) console.warn('messages delete:', msgErr.message);

        // 2. Delete all chat sessions for this user
        const { error: sessErr } = await supabaseClient
            .from('chat_sessions')
            .delete()
            .eq('user_id', userId);
        if (sessErr) console.warn('chat_sessions delete:', sessErr.message);

        // 3. Delete the auth user via Supabase (requires RLS + auth.users delete policy, OR use Admin API)
        const { error: authErr } = await supabaseClient.rpc('delete_user');

        if (authErr) {
            console.error('Auth delete error:', authErr.message);
            await supabaseClient.auth.signOut();
            document.getElementById('deleteAccountModal')?.remove();
            showModal({ type: 'alert', title: 'Data cleared', subtitle: 'Your login account will be fully removed within 24 hours. You have been signed out.', confirmLabel: 'OK', onConfirm: () => { window.location.href = '/auth'; } });
            return;
        }

        // 4. Sign out and redirect
        await supabaseClient.auth.signOut();
        document.getElementById('deleteAccountModal')?.remove();
        window.location.href = '/auth';

    } catch (e) {
        console.error('Delete account error:', e);
        showModal({ type: 'alert', title: 'Something went wrong', subtitle: 'Please try again or contact support.' });
        if (btn) { btn.disabled = false; btn.textContent = 'Delete my account'; }
    }
};
// ── CHANGE PASSWORD MODAL ─────────────────────────────────────────────────────
// ── CHANGE PASSWORD — Supabase reauthenticate() OTP flow ─────────────────────
//
//  STEP 1 (chpw-step-1): User enters new password + confirm
//         → clicks "Send verification code"
//         → we call supabaseClient.auth.reauthenticate()
//         → Supabase emails a 6-digit OTP (nonce) via Reauthentication template
//
//  STEP 2 (chpw-step-2): User enters the 6-digit OTP from email
//         → clicks "Verify & update password"
//         → we call supabaseClient.auth.verifyOtp({ email, token: otp, type: 'reauthentication' })
//           then supabaseClient.auth.updateUser({ password })
//           verifyOtp with type 'reauthentication' is the ONLY correct flow.
//
// ─────────────────────────────────────────────────────────────────────────────

const eyeSVG = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>`;
const eyeOffSVG = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/><path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/><line x1="1" y1="1" x2="23" y2="23"/></svg>`;

window.openChangePasswordModal = function () {
    document.getElementById('changePasswordModal')?.remove();

    const modal = document.createElement('div');
    modal.id = 'changePasswordModal';
    modal.className = 'priv-modal-overlay';
    modal.innerHTML = `
        <div class="priv-modal-box chpw-modal-box" role="dialog" aria-modal="true" aria-label="Change Password">

            <!-- ── STEP 1: New password fields ── -->
            <div id="chpw-step-1">
                <div class="chpw-modal-icon-wrap">
                    <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="#10a37f" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                        <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
                        <path d="M7 11V7a5 5 0 0 1 10 0v4"></path>
                    </svg>
                </div>
                <h2 class="chpw-modal-title">Change Password</h2>
                <p class="chpw-modal-desc">Enter your new password. We'll send a verification code to <strong id="chpwEmailDisplay" style="color:#e8eaf0;"></strong> to confirm it's you.</p>

                <div class="chpw-field-wrap">
                    <label class="chpw-label">New Password</label>
                    <div class="chpw-input-wrap">
                        <input type="password" id="chpwNewInput" class="chpw-input" placeholder="New password (min 8 characters)" autocomplete="new-password" oninput="chpwValidateStep1()">
                        <button class="chpw-eye-btn" type="button" onclick="chpwToggleEye('chpwNewInput',this)" tabindex="-1">${eyeSVG}</button>
                    </div>
                </div>

                <div class="chpw-field-wrap">
                    <label class="chpw-label">Confirm New Password</label>
                    <div class="chpw-input-wrap">
                        <input type="password" id="chpwConfirmInput" class="chpw-input" placeholder="Confirm new password" autocomplete="new-password" oninput="chpwValidateStep1()">
                        <button class="chpw-eye-btn" type="button" onclick="chpwToggleEye('chpwConfirmInput',this)" tabindex="-1">${eyeSVG}</button>
                    </div>
                </div>

                <p id="chpwStep1Error" class="chpw-error" style="display:none;"></p>

                <div class="del-modal-actions" style="margin-top:20px;">
                    <button class="del-cancel-btn" onclick="document.getElementById('changePasswordModal').remove()">Cancel</button>
                    <button class="chpw-save-btn" id="chpwStep1Btn" disabled onclick="chpwSendOtp()">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07A19.5 19.5 0 0 1 4.69 12 19.79 19.79 0 0 1 1.15 3.4 2 2 0 0 1 3.12 1h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L7.09 8.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 21 16z"/>
                        </svg>
                        Send verification code
                    </button>
                </div>
            </div>

            <!-- ── STEP 2: OTP entry ── -->
            <div id="chpw-step-2" style="display:none;">
                <div class="chpw-modal-icon-wrap" style="background:rgba(16,163,127,0.12); border-color:rgba(16,163,127,0.3);">
                    <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="#10a37f" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"></path>
                        <polyline points="22,6 12,13 2,6"></polyline>
                    </svg>
                </div>
                <h2 class="chpw-modal-title">Check your email</h2>
                <p class="chpw-modal-desc">We sent a 6-digit verification code to <strong id="chpwEmailDisplay2" style="color:#e8eaf0;"></strong>. Enter it below to update your password.</p>

                <div class="chpw-field-wrap">
                    <label class="chpw-label">Verification Code</label>
                    <input type="text" id="chpwOtpInput" class="chpw-input chpw-otp-input" placeholder="000000" maxlength="6" autocomplete="one-time-code" oninput="chpwValidateStep2()" style="letter-spacing:8px; font-size:22px; font-weight:700; text-align:center;">
                </div>

                <p id="chpwStep2Error" class="chpw-error" style="display:none;"></p>

                <p id="chpwResendCountdown" class="chpw-error" style="display:none; color:#10a37f;"></p>

                <p class="chpw-resend-row">
                    Didn't receive it? 
                    <button class="chpw-resend-btn" id="chpwResendBtn" onclick="chpwResendOtp()">Resend code</button>
                </p>

                <div class="del-modal-actions" style="margin-top:20px;">
                    <button class="del-cancel-btn" onclick="chpwBackToStep1()">← Back</button>
                    <button class="chpw-save-btn" id="chpwStep2Btn" disabled onclick="chpwVerifyAndUpdate()">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                            <polyline points="20 6 9 17 4 12"/>
                        </svg>
                        Verify &amp; update password
                    </button>
                </div>
            </div>

        </div>
    `;

    modal.addEventListener('click', (e) => { if (e.target === modal) modal.remove(); });
    document.body.appendChild(modal);
    requestAnimationFrame(() => modal.classList.add('priv-modal-open'));

    // Show user's email in the description
    const email = currentUser?.email || '';
    document.getElementById('chpwEmailDisplay').textContent  = email;
    document.getElementById('chpwEmailDisplay2').textContent = email;

    setTimeout(() => document.getElementById('chpwNewInput')?.focus(), 100);
};

window.chpwToggleEye = function (inputId, btn) {
    const input = document.getElementById(inputId);
    if (!input) return;
    const isHidden = input.type === 'password';
    input.type = isHidden ? 'text' : 'password';
    btn.innerHTML = isHidden ? eyeOffSVG : eyeSVG;
};

window.chpwValidateStep1 = function () {
    const pw  = document.getElementById('chpwNewInput')?.value    || '';
    const cpw = document.getElementById('chpwConfirmInput')?.value || '';
    const btn = document.getElementById('chpwStep1Btn');
    const err = document.getElementById('chpwStep1Error');
    if (!btn || !err) return;

    if (pw.length > 0 && pw.length < 8) {
        err.textContent = 'Password must be at least 8 characters.';
        err.style.display = 'block'; btn.disabled = true; return;
    }
    if (cpw.length > 0 && pw !== cpw) {
        err.textContent = 'Passwords do not match.';
        err.style.display = 'block'; btn.disabled = true; return;
    }
    err.style.display = 'none';
    btn.disabled = !(pw.length >= 8 && pw === cpw);
};

window.chpwValidateStep2 = function () {
    const otp = document.getElementById('chpwOtpInput')?.value.replace(/\D/g,'') || '';
    // keep only digits
    if (document.getElementById('chpwOtpInput'))
        document.getElementById('chpwOtpInput').value = otp;
    const btn = document.getElementById('chpwStep2Btn');
    if (btn) btn.disabled = otp.length !== 6;
};

window.chpwSendOtp = async function () {
    const btn = document.getElementById('chpwStep1Btn');
    const err = document.getElementById('chpwStep1Error');
    const origHTML = btn.innerHTML;

    btn.disabled = true;
    btn.innerHTML = '⏳ Sending…';

    try {
        const { error } = await supabaseClient.auth.reauthenticate();
        if (error) throw error;

        // Switch to step 2
        document.getElementById('chpw-step-1').style.display = 'none';
        document.getElementById('chpw-step-2').style.display = 'block';
        setTimeout(() => document.getElementById('chpwOtpInput')?.focus(), 100);

        // Start 60-second cooldown on the Resend button right away
        const resendBtn = document.getElementById('chpwResendBtn');
        const countdownEl = document.getElementById('chpwResendCountdown');
        if (resendBtn) {
            resendBtn.disabled = true;
            let seconds = 60;
            if (countdownEl) {
                countdownEl.style.display = 'block';
                countdownEl.textContent = `For security purposes, you can only request this after ${seconds} seconds.`;
            }
            const interval = setInterval(() => {
                seconds--;
                if (seconds <= 0) {
                    clearInterval(interval);
                    resendBtn.disabled = false;
                    if (countdownEl) countdownEl.style.display = 'none';
                } else {
                    if (countdownEl) {
                        countdownEl.textContent = `For security purposes, you can only request this after ${seconds} seconds.`;
                    }
                }
            }, 1000);
        }

    } catch (e) {
        // Handle Supabase rate-limit: "you can only request this after X seconds"
        const msg = e.message || '';
        const match = msg.match(/(\d+)\s*seconds?/i);
        if (match) {
            err.textContent = `Too many requests. Please wait ${match[1]} seconds before trying again.`;
        } else {
            err.textContent = msg || 'Failed to send code. Please try again.';
        }
        err.style.display = 'block';
        btn.disabled = false;
        btn.innerHTML = origHTML;
    }
};

window.chpwResendOtp = async function () {
    const btn   = document.getElementById('chpwResendBtn');
    const err   = document.getElementById('chpwStep2Error');
    const countdownEl = document.getElementById('chpwResendCountdown');
    btn.disabled = true;
    btn.textContent = 'Sending…';

    try {
        const { error } = await supabaseClient.auth.reauthenticate();
        if (error) throw error;
        btn.textContent = '✓ Sent!';
        if (err) err.style.display = 'none';
        // Clear old OTP — resend issues a NEW nonce that invalidates the previous one
        const otpInput = document.getElementById('chpwOtpInput');
        if (otpInput) { otpInput.value = ''; otpInput.focus(); }
        const step2Btn = document.getElementById('chpwStep2Btn');
        if (step2Btn) step2Btn.disabled = true;

        // Countdown timer: 60 seconds before resend is allowed again
        let seconds = 60;
        if (countdownEl) {
            countdownEl.style.display = 'block';
            countdownEl.textContent = `For security purposes, you can only request this after ${seconds} seconds.`;
        }
        const interval = setInterval(() => {
            seconds--;
            if (seconds <= 0) {
                clearInterval(interval);
                btn.disabled = false;
                btn.textContent = 'Resend code';
                if (countdownEl) countdownEl.style.display = 'none';
            } else {
                if (countdownEl) {
                    countdownEl.textContent = `For security purposes, you can only request this after ${seconds} seconds.`;
                }
            }
        }, 1000);

    } catch (e) {
        // Supabase rate-limit error contains "seconds" — parse and show countdown
        const msg = e.message || '';
        const match = msg.match(/(\d+)\s*seconds?/i);
        if (match) {
            let seconds = parseInt(match[1], 10);
            if (countdownEl) {
                countdownEl.style.display = 'block';
                countdownEl.textContent = `For security purposes, you can only request this after ${seconds} seconds.`;
            }
            const interval = setInterval(() => {
                seconds--;
                if (seconds <= 0) {
                    clearInterval(interval);
                    btn.disabled = false;
                    btn.textContent = 'Resend code';
                    if (countdownEl) countdownEl.style.display = 'none';
                } else {
                    if (countdownEl) {
                        countdownEl.textContent = `For security purposes, you can only request this after ${seconds} seconds.`;
                    }
                }
            }, 1000);
        } else {
            err.textContent = msg || 'Failed to resend. Try again.';
            err.style.display = 'block';
            btn.disabled = false;
            btn.textContent = 'Resend code';
        }
    }
};

window.chpwBackToStep1 = function () {
    document.getElementById('chpw-step-2').style.display = 'none';
    document.getElementById('chpw-step-1').style.display = 'block';
    document.getElementById('chpwStep2Error').style.display = 'none';
    document.getElementById('chpwOtpInput').value = '';
    document.getElementById('chpwStep2Btn').disabled = true;
};

window.chpwVerifyAndUpdate = async function () {
    const otp = (document.getElementById('chpwOtpInput')?.value || '').replace(/\D/g, '');
    const pw  = document.getElementById('chpwNewInput')?.value        || '';
    const btn = document.getElementById('chpwStep2Btn');
    const err = document.getElementById('chpwStep2Error');

    if (otp.length !== 6 || pw.length < 8) return;

    const saveBtnHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg> Verify &amp; update password';

    btn.disabled = true;
    btn.innerHTML = '⏳ Updating…';
    if (err) err.style.display = 'none';

    try {
        // ✅ THE ONLY CORRECT Supabase JS v2 reauthenticate flow:
        //
        // reauthenticate() sends a 6-digit nonce to the user's email.
        // Pass that nonce directly to updateUser() as the `nonce` field.
        //
        // ❌ verifyOtp() with ANY type ('email', 'recovery', 'reauthentication')
        //    ALWAYS returns otp_expired for nonces from reauthenticate() —
        //    this is confirmed Supabase behavior (GitHub Discussion #34956).
        //    verifyOtp() is NOT the right method for this flow.
        //
        // ✅ updateUser({ password, nonce }) is the documented correct approach:
        //    https://supabase.com/docs/reference/javascript/auth-updateuser
        const { error: updateErr } = await supabaseClient.auth.updateUser({
            password: pw,
            nonce: otp
        });

        if (updateErr) {
            const msg = updateErr.message?.toLowerCase() || '';
            const isBadNonce = msg.includes('nonce') || msg.includes('expired') ||
                               msg.includes('invalid') || msg.includes('otp') ||
                               msg.includes('token') || msg.includes('reauth');
            err.textContent = isBadNonce
                ? 'Code is expired or invalid. Click "Resend code" to get a fresh one.'
                : (updateErr.message || 'Failed to update password. Please try again.');
            err.style.display = 'block';
            btn.disabled = false;
            btn.innerHTML = saveBtnHTML;
            return;
        }

        document.getElementById('changePasswordModal')?.remove();
        showToast('✓ Password updated successfully');

    } catch (e) {
        err.textContent = e.message || 'Network error. Please try again.';
        err.style.display = 'block';
        btn.disabled = false;
        btn.innerHTML = saveBtnHTML;
    }
};

// ============================
// 📍 LOCATION METADATA SYSTEM
// Privacy-safe: city/region/timezone only.
// No GPS history. No continuous tracking.
// Raw coordinates deleted immediately after reverse-geocoding.
// ============================

// Keys for localStorage (toggle preference + cached metadata)
const LOC_TOGGLE_KEY  = 'catura_location_enabled';
const LOC_CACHE_KEY   = 'catura_location_meta';
const LOC_CACHE_TTL   = 60 * 60 * 1000; // 1 hour in ms

/**
 * Called when user flips the location toggle.
 * If turning ON: request location once → extract city/region/timezone → cache 1 hr.
 * If turning OFF: clear cache, never call geolocation API again.
 */
window.handleLocationToggle = async function (enabled) {
    localStorage.setItem(LOC_TOGGLE_KEY, enabled ? '1' : '0');
    saveSettingToCloud('location_enabled', enabled);

    if (!enabled) {
        // Wipe any cached location — user opted out
        localStorage.removeItem(LOC_CACHE_KEY);
        debugLog('[Location] Toggle OFF — cache cleared.');
        return;
    }

    // Toggle is ON — request location once
    await requestLocationOnce();
};

/**
 * Request the user's approximate location ONCE.
 * Uses low accuracy (no GPS) → sends to backend for reverse geocoding →
 * stores only { country, region, city, timezone, locale } locally.
 * Raw coordinates are NEVER persisted.
 */
async function requestLocationOnce() {
    if (!('geolocation' in navigator)) {
        // Fallback: derive from IP via backend
        await fetchLocationFromIP();
        return;
    }

    navigator.geolocation.getCurrentPosition(
        async (pos) => {
            // Round to 2 decimal places — city-level precision only
            const coarse_lat = Math.round(pos.coords.latitude  * 100) / 100;
            const coarse_lng = Math.round(pos.coords.longitude * 100) / 100;

            // Send coarse coords to backend → get city/region/timezone back
            // Backend immediately discards raw coordinates
            try {
                const res = await fetch('/api/location-metadata', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ coarse_lat, coarse_lng })
                });
                if (res.ok) {
                    const meta = await res.json();
                    cacheLocationMeta(meta);
                    debugLog('[Location] Metadata stored:', meta.city, meta.region);
                } else {
                    // Backend failed — fallback to browser timezone + IP
                    await fetchLocationFromIP();
                }
            } catch (_) {
                await fetchLocationFromIP();
            }
            // Raw coords are now out of scope — never stored
        },
        async () => {
            // User denied or geolocation unavailable — try IP fallback
            await fetchLocationFromIP();
        },
        {
            enableHighAccuracy: false, // no GPS — approximate network position only
            timeout: 5000,
            maximumAge: 3600000        // reuse a cached browser position up to 1 hr old
        }
    );
}

/**
 * Fallback 1: Ask backend to derive location from IP.
 * Fallback 2: Use browser's timezone string.
 * Fallback 3: Nothing stored — AI works without location context.
 */
async function fetchLocationFromIP() {
    try {
        const res = await fetch('/api/location-metadata/ip');
        if (res.ok) {
            const meta = await res.json();
            cacheLocationMeta(meta);
            debugLog('[Location] IP-derived metadata stored:', meta.city);
            return;
        }
    } catch (_) {}

    // Final fallback: timezone from browser only
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
    if (tz) {
        cacheLocationMeta({ timezone: tz, locale: navigator.language || 'en' });
        debugLog('[Location] Timezone-only fallback stored:', tz);
    }
}

/** Persist metadata with a 1-hour expiry. */
function cacheLocationMeta(meta) {
    const record = { ...meta, cached_at: Date.now() };
    localStorage.setItem(LOC_CACHE_KEY, JSON.stringify(record));
}

/**
 * Returns the current location metadata if the toggle is ON and cache is fresh.
 * Returns null if toggle is OFF or cache has expired.
 * Called by the chat send handler to enrich the request context.
 */
window.getLocationMeta = function () {
    if (localStorage.getItem(LOC_TOGGLE_KEY) !== '1') return null;
    try {
        const raw = localStorage.getItem(LOC_CACHE_KEY);
        if (!raw) return null;
        const record = JSON.parse(raw);
        if (Date.now() - record.cached_at > LOC_CACHE_TTL) {
            // Cache expired — refresh silently in background
            localStorage.removeItem(LOC_CACHE_KEY);
            requestLocationOnce();
            return null;
        }
        // Return only the safe fields — never include raw coords
        const { country, region, city, timezone, locale } = record;
        return { country, region, city, timezone, locale };
    } catch (_) {
        return null;
    }
};

/** On settings open: restore toggle state from localStorage. */
function restoreLocationToggle() {
    const toggle = document.getElementById('locationMetadataToggle');
    if (toggle) {
        toggle.checked = localStorage.getItem(LOC_TOGGLE_KEY) === '1';
    }
    // Restore analytics + training toggles
    const prefs = getPrivacyPrefs();
    const aToggle = document.getElementById('analyticsToggle');
    const tToggle = document.getElementById('trainingToggle');
    if (aToggle) aToggle.checked = prefs.analytics;
    if (tToggle) tToggle.checked = prefs.training;
}

// Patch showSettings to restore toggle state after the panel renders
const _origShowSettings = window.showSettings;
window.showSettings = function (...args) {
    if (_origShowSettings) _origShowSettings(...args);
    // Small delay to let the settings HTML inject into DOM
    setTimeout(restoreLocationToggle, 50);
};

// ── PLANS MODAL ───────────────────────────────────────────────────────────────
window.openPlansModal = function () {
    const existing = document.getElementById('plansModal');
    if (existing) existing.remove();

    const isLight = document.documentElement.getAttribute('data-theme') === 'light';
    const p = isLight ? {
        overlay: 'rgba(0,0,0,0.35)',
        outerBg: '#ffffff',
        outerBorder: '#e4e7ef',
        outerShadow: '0 24px 80px rgba(0,0,0,0.12)',
        closeBtnColor: '#7a8399',
        titleColor: '#0f1117',
        subtitleColor: '#7a8399',
        cardBg: '#f8f9fb',
        cardBorder: '#e4e7ef',
        cardProBorder: 'rgba(16,163,127,0.4)',
        cardMaxBg: 'linear-gradient(145deg,#f8f9fb,#f4f5fb)',
        cardMaxBorder: 'rgba(124,58,237,0.3)',
        tierLabel: '#7a8399',
        priceColor: '#0f1117',
        priceMuted: '#9aa0b5',
        descColor: '#5a6070',
        dividerColor: '#e8eaed',
        featureColor: '#3d4455',
        currentPlanBg: '#f0f2f7',
        currentPlanBorder: '#dde1eb',
        currentPlanColor: '#9aa0b5',
        currentPlanHover: '#e4e7f0',
    } : {
        overlay: 'rgba(0,0,0,0.65)',
        outerBg: '#1a1a1a',
        outerBorder: '#2a2a2a',
        outerShadow: '0 24px 80px rgba(0,0,0,0.6)',
        closeBtnColor: '#888',
        titleColor: '#ffffff',
        subtitleColor: '#888',
        cardBg: '#111111',
        cardBorder: '#2a2a2a',
        cardProBorder: 'rgba(16,163,127,0.35)',
        cardMaxBg: 'linear-gradient(145deg,#1a1a1a,#121212)',
        cardMaxBorder: 'rgba(124,58,237,0.4)',
        tierLabel: '#888',
        priceColor: '#ffffff',
        priceMuted: '#666',
        descColor: '#999',
        dividerColor: '#222',
        featureColor: '#bbbbbb',
        currentPlanBg: '#222222',
        currentPlanBorder: '#333333',
        currentPlanColor: '#666666',
        currentPlanHover: '#2a2a2a',
    };

    const modal = document.createElement('div');
    modal.id = 'plansModal';
    modal.style.cssText = `position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;background:${p.overlay};backdrop-filter:blur(4px);`;

    const freeFeatures = ['Unlimited chats with Dagr & Apep','Access to Sambhav model','Standard response speed','Basic file uploads'];
    const proFeatures = ['Everything in Free','Access to Gemma & Gemma4 models','Priority response speed','Larger file uploads','Extended context window','Early access to new features'];
    const maxFeatures = ['Everything in Pro','Access to Nivo & Laguna models','Fastest response speed','Unlimited file uploads','Maximum context window','Dedicated support'];

    const check = (color) => `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`;

    const makeList = (items, color) => items.map(f => `<li style="display:flex;align-items:flex-start;gap:8px;font-size:13px;color:${p.featureColor};line-height:1.5;">${check(color)}<span>${f}</span></li>`).join('');

    modal.innerHTML = `
    <div style="background:${p.outerBg};border:1px solid ${p.outerBorder};border-radius:16px;width:min(860px,95vw);max-height:90vh;overflow-y:auto;padding:32px 28px;position:relative;box-shadow:${p.outerShadow};">
        <button onclick="document.getElementById('plansModal').remove()" style="position:absolute;top:16px;right:18px;background:none;border:none;color:${p.closeBtnColor};font-size:20px;cursor:pointer;line-height:1;padding:4px 8px;border-radius:6px;transition:color 0.15s;" onmouseover="this.style.color='#10a37f'" onmouseout="this.style.color='${p.closeBtnColor}'">&#x2715;</button>
        <h2 style="margin:0 0 6px;font-size:22px;color:${p.titleColor};font-weight:700;">Choose your plan</h2>
        <p style="margin:0 0 28px;color:${p.subtitleColor};font-size:14px;">Simple, transparent pricing. Upgrade or downgrade anytime.</p>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px;">
            <div style="background:${p.cardBg};border:1px solid ${p.cardBorder};border-radius:12px;padding:24px 20px;display:flex;flex-direction:column;gap:12px;">
                <div style="font-size:12px;color:${p.tierLabel};font-weight:600;letter-spacing:.06em;text-transform:uppercase;">Free</div>
                <div style="font-size:32px;font-weight:800;color:${p.priceColor};">₹0<span style="font-size:14px;font-weight:400;color:${p.priceMuted};">/mo</span></div>
                <p style="font-size:13px;color:${p.descColor};margin:0;">Get started with core AI features, no credit card required.</p>
                <hr style="border:none;border-top:1px solid ${p.dividerColor};margin:4px 0;">
                <ul style="list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:8px;">${makeList(freeFeatures,'#10a37f')}</ul>
                <button disabled style="margin-top:auto;padding:10px;border-radius:8px;border:1px solid ${p.currentPlanBorder};background:${p.currentPlanBg};color:${p.currentPlanColor};font-size:13px;font-weight:600;cursor:default;">Current plan</button>
            </div>
            <div style="background:${p.cardBg};border:1px solid ${p.cardProBorder};border-radius:12px;padding:24px 20px;display:flex;flex-direction:column;gap:12px;position:relative;">
                <div style="position:absolute;top:-1px;right:16px;background:#10a37f;color:#fff;font-size:11px;font-weight:700;padding:3px 10px;border-radius:0 0 8px 8px;letter-spacing:.04em;text-transform:uppercase;">Popular</div>
                <div style="font-size:12px;color:#10a37f;font-weight:600;letter-spacing:.06em;text-transform:uppercase;">Pro</div>
                <div style="font-size:32px;font-weight:800;color:${p.priceColor};">₹199<span style="font-size:14px;font-weight:400;color:${p.priceMuted};">/mo</span></div>
                <p style="font-size:13px;color:${p.descColor};margin:0;">Everything in Free, plus priority access and advanced models.</p>
                <hr style="border:none;border-top:1px solid ${p.dividerColor};margin:4px 0;">
                <ul style="list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:8px;">${makeList(proFeatures,'#10a37f')}</ul>
                <button onclick="showModal({type:'alert',icon:'<rect x=\\'1\\' y=\\'4\\' width=\\'22\\' height=\\'16\\' rx=\\'2\\' ry=\\'2\\'/><line x1=\\'1\\' y1=\\'10\\' x2=\\'23\\' y2=\\'10\\'/>',title:'Pro plan',subtitle:'Coming soon! Stay tuned.',confirmLabel:'Got it'})" style="margin-top:auto;padding:10px;border-radius:8px;border:none;background:#10a37f;color:#fff;font-size:13px;font-weight:600;cursor:pointer;transition:opacity 0.15s;" onmouseover="this.style.opacity='0.85'" onmouseout="this.style.opacity='1'">Upgrade to Pro</button>
            </div>
            <div style="background:${p.cardMaxBg};border:1px solid ${p.cardMaxBorder};border-radius:12px;padding:24px 20px;display:flex;flex-direction:column;gap:12px;">
                <div style="font-size:12px;color:#a78bfa;font-weight:600;letter-spacing:.06em;text-transform:uppercase;">Max</div>
                <div style="font-size:32px;font-weight:800;color:${p.priceColor};">₹349<span style="font-size:14px;font-weight:400;color:${p.priceMuted};">/mo</span></div>
                <p style="font-size:13px;color:${p.descColor};margin:0;">Full access to every model and the highest usage limits.</p>
                <hr style="border:none;border-top:1px solid ${p.dividerColor};margin:4px 0;">
                <ul style="list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:8px;">${makeList(maxFeatures,'#a78bfa')}</ul>
                <button onclick="showModal({type:'alert',icon:'<rect x=\\'1\\' y=\\'4\\' width=\\'22\\' height=\\'16\\' rx=\\'2\\' ry=\\'2\\'/><line x1=\\'1\\' y1=\\'10\\' x2=\\'23\\' y2=\\'10\\'/>',title:'Max plan',subtitle:'Coming soon! Stay tuned.',confirmLabel:'Got it'})" style="margin-top:auto;padding:10px;border-radius:8px;border:none;background:linear-gradient(135deg,#7c3aed,#5b21b6);color:#fff;font-size:13px;font-weight:600;cursor:pointer;transition:opacity 0.15s;" onmouseover="this.style.opacity='0.85'" onmouseout="this.style.opacity='1'">Upgrade to Max</button>
            </div>
        </div>
    </div>`;

    modal.addEventListener('click', (e) => { if (e.target === modal) modal.remove(); });
    document.body.appendChild(modal);
};

// ============================================================
// 🔒 PRIVACY SYSTEM — Analytics · Training · PII Sanitizer
// ============================================================

// ── Preference helpers ────────────────────────────────────────
const PRIVACY_PREFS_KEY = 'catura-privacy-prefs';
const DEFAULT_PREFS = { analytics: true, training: true };

function getPrivacyPrefs() {
    try {
        return { ...DEFAULT_PREFS, ...JSON.parse(localStorage.getItem(PRIVACY_PREFS_KEY) || '{}') };
    } catch (_) { return { ...DEFAULT_PREFS }; }
}

window.savePrivacyPref = function (key, value) {
    const prefs = getPrivacyPrefs();
    prefs[key] = value;
    localStorage.setItem(PRIVACY_PREFS_KEY, JSON.stringify(prefs));
    saveSettingToCloud('privacy_prefs', prefs);

    // Flush/discard pending queue if user just opted out, and start/stop
    // the relevant background flush interval to match the new setting.
    if (key === 'analytics') {
        if (value) startAnalyticsInterval(); else { _analyticsQueue = []; stopAnalyticsInterval(); }
    }
    if (key === 'training') {
        if (value) startTrainingInterval(); else { _trainingQueue = []; stopTrainingInterval(); }
    }
    showToast(value ? `✓ ${key === 'analytics' ? 'Usage analytics' : 'AI training data'} enabled` : `${key === 'analytics' ? 'Usage analytics' : 'AI training data'} disabled`);
};

// ── PII Sanitizer ─────────────────────────────────────────────
// Removes passwords, emails, phones, tokens, API keys, addresses,
// banking info, and other personal identifiers before any storage.
const PII_PATTERNS = [
    // Emails
    { re: /[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}/g,      mask: '[email]' },
    // Phone numbers (international + local variants)
    { re: /(\+?\d[\d\s\-().]{7,}\d)/g,                                 mask: '[phone]' },
    // Passwords typed literally
    { re: /password\s*[=:]\s*\S+/gi,                                   mask: '[credential]' },
    // API keys / tokens (generic long alphanumeric strings 20+ chars)
    { re: /\b([A-Za-z0-9_\-]{20,})\b/g,                               mask: '[token]' },
    // Bearer / Authorization header values
    { re: /bearer\s+[A-Za-z0-9\-_.~+/]+=*/gi,                        mask: '[bearer]' },
    // Credit / debit card numbers (13-19 digits, with optional spaces/dashes)
    { re: /\b(?:\d[ \-]?){13,19}\b/g,                                  mask: '[card]' },
    // SSN / Aadhaar-like patterns
    { re: /\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b/g,                        mask: '[id-number]' },
    // Precise GPS coordinates
    { re: /[-+]?\d{1,3}\.\d{4,},\s*[-+]?\d{1,3}\.\d{4,}/g,           mask: '[coordinates]' },
    // Cookies / session values (key=value;)
    { re: /\b(session[_-]?id|auth[_-]?token|access[_-]?token)\s*=\s*\S+/gi, mask: '[session]' },
];

function sanitizePII(text) {
    if (!text || typeof text !== 'string') return '';
    let out = text;
    for (const { re, mask } of PII_PATTERNS) {
        out = out.replace(re, mask);
    }
    return out.trim();
}

// ── Anonymous user ID ─────────────────────────────────────────
function getAnonId() {
    let id = localStorage.getItem('catura-anon-id');
    if (!id) {
        id = 'anon_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 9);
        localStorage.setItem('catura-anon-id', id);
    }
    return id;
}

// ── Analytics pipeline ────────────────────────────────────────
let _analyticsQueue = [];
const ANALYTICS_BATCH_SIZE = 10;
const ANALYTICS_FLUSH_MS   = 30_000; // flush every 30s

window.trackAnalyticsEvent = function (eventName, metadata = {}) {
    if (!getPrivacyPrefs().analytics) return;

    _analyticsQueue.push({
        anonymous_user_id: getAnonId(),
        event_name: eventName,
        metadata: {
            ...metadata,
            browser: navigator.userAgent.split(' ').pop(),    // just engine token
            viewport: `${window.innerWidth}x${window.innerHeight}`,
            ts: Date.now(),
        }
    });

    if (_analyticsQueue.length >= ANALYTICS_BATCH_SIZE) {
        flushAnalytics();
    }
};

async function flushAnalytics() {
    if (!_analyticsQueue.length) return;
    const batch = _analyticsQueue.splice(0, ANALYTICS_BATCH_SIZE);
    try {
        await fetch('/api/privacy/analytics', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ events: batch }),
            keepalive: true,
        });
    } catch (_) {
        // Silently discard — never retry, never block UX
    }
}

// Periodic flush + flush on page hide (low battery / background).
// The interval only runs while analytics is actually enabled — no point
// ticking a timer forever for visitors who've opted out.
let _analyticsIntervalId = null;
function startAnalyticsInterval() {
    if (_analyticsIntervalId !== null) return; // already running
    _analyticsIntervalId = setInterval(flushAnalytics, ANALYTICS_FLUSH_MS);
}
function stopAnalyticsInterval() {
    if (_analyticsIntervalId === null) return;
    clearInterval(_analyticsIntervalId);
    _analyticsIntervalId = null;
}
if (getPrivacyPrefs().analytics) startAnalyticsInterval();
document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flushAnalytics();
});

// ── Training pipeline ─────────────────────────────────────────
let _trainingQueue = [];
const TRAINING_BATCH_SIZE = 5;
const TRAINING_FLUSH_MS   = 60_000; // flush every 60s

window.submitTrainingConversation = function ({ userMessage, assistantResponse, feedbackScore = null, language = null }) {
    if (!getPrivacyPrefs().training) return;
    if (!userMessage || !assistantResponse) return;

    _trainingQueue.push({
        anonymous_user_id: getAnonId(),
        sanitized_user_message: sanitizePII(userMessage),
        sanitized_assistant_response: sanitizePII(assistantResponse),
        feedback_score: feedbackScore,
        language: language || navigator.language?.slice(0, 5) || 'en',
        coarse_region: null,  // set by backend from IP — never stored raw
    });

    if (_trainingQueue.length >= TRAINING_BATCH_SIZE) {
        flushTraining();
    }
};

async function flushTraining() {
    if (!_trainingQueue.length) return;
    const batch = _trainingQueue.splice(0, TRAINING_BATCH_SIZE);
    try {
        await fetch('/api/privacy/training', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ conversations: batch }),
            keepalive: true,
        });
    } catch (_) { /* discard silently */ }
}

let _trainingIntervalId = null;
function startTrainingInterval() {
    if (_trainingIntervalId !== null) return; // already running
    _trainingIntervalId = setInterval(flushTraining, TRAINING_FLUSH_MS);
}
function stopTrainingInterval() {
    if (_trainingIntervalId === null) return;
    clearInterval(_trainingIntervalId);
    _trainingIntervalId = null;
}
if (getPrivacyPrefs().training) startTrainingInterval();
document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flushTraining();
});

// ── Quality signals ────────────────────────────────────────────
// Call these from existing thumbs up/down and other interaction handlers
window.trackFeedback = function (score, userMessage, assistantResponse) {
    // score: 1 = thumbs up, -1 = thumbs down
    trackAnalyticsEvent('feedback', { score });
    submitTrainingConversation({ userMessage, assistantResponse, feedbackScore: score });
};
window.trackRegenerate  = () => trackAnalyticsEvent('regenerate_clicked');
window.trackCopyResponse = () => trackAnalyticsEvent('response_copied');
window.trackResponseTime = (ms) => trackAnalyticsEvent('response_time_ms', { ms });

// ============================
// 📷 PROFILE PICTURE FEATURE
// ============================

// Profile pic is intentionally NOT loaded from localStorage here.
// It will be loaded after auth resolves in loadSettingsFromCloud(),
// using the user-scoped key, so no previous user's picture leaks in.
window._profilePicDataUrl = null;

function _applyProfilePicToAllAvatars(dataUrl) {
    // Rail avatar (icon rail bottom)
    const railAvatar = document.getElementById('railAvatar');
    if (railAvatar) {
        if (dataUrl) {
            railAvatar.style.backgroundImage = `url(${dataUrl})`;
            railAvatar.style.backgroundSize = 'cover';
            railAvatar.style.backgroundPosition = 'center';
            railAvatar.textContent = '';
        } else {
            railAvatar.style.backgroundImage = '';
            railAvatar.style.backgroundSize = '';
        }
    }
    // Sidebar user avatar
    const userAvatar = document.getElementById('userAvatar');
    if (userAvatar) {
        if (dataUrl) {
            userAvatar.style.backgroundImage = `url(${dataUrl})`;
            userAvatar.style.backgroundSize = 'cover';
            userAvatar.style.backgroundPosition = 'center';
            userAvatar.textContent = '';
        } else {
            userAvatar.style.backgroundImage = '';
        }
    }
    // Settings profile tab avatar
    const scAvatarDisplay = document.getElementById('scAvatarDisplay');
    if (scAvatarDisplay) {
        const wrap = scAvatarDisplay.closest('.sc-avatar-wrap');
        if (wrap) {
            let img = wrap.querySelector('.sc-avatar-img');
            if (dataUrl) {
                scAvatarDisplay.textContent = '';
                if (!img) {
                    img = document.createElement('img');
                    img.className = 'sc-avatar-img';
                    img.alt = 'Profile';
                    wrap.insertBefore(img, scAvatarDisplay.nextSibling);
                }
                img.src = dataUrl;
            } else {
                if (img) img.remove();
            }
        }
    }
}

window.triggerProfilePicUpload = function() {
    let input = document.getElementById('profilePicInput');
    if (!input) {
        input = document.createElement('input');
        input.type = 'file';
        input.id = 'profilePicInput';
        input.accept = 'image/*';
        input.style.display = 'none';
        document.body.appendChild(input);
        input.addEventListener('change', function(e) {
            const file = e.target.files[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = function(ev) {
                openProfileCropModal(ev.target.result);
            };
            reader.readAsDataURL(file);
            input.value = '';
        });
    }
    input.click();
};

function openProfileCropModal(imageSrc) {
    // Remove existing modal if any
    const existing = document.getElementById('profileCropModal');
    if (existing) existing.remove();

    const modal = document.createElement('div');
    modal.id = 'profileCropModal';
    modal.innerHTML = `
        <div class="pcm-backdrop" id="pcmBackdrop"></div>
        <div class="pcm-dialog" role="dialog" aria-modal="true" aria-label="Crop profile picture">
            <div class="pcm-header">
                <h3 class="pcm-title">Adjust Profile Picture</h3>
                <p class="pcm-subtitle">Drag to reposition · Scroll or pinch to zoom</p>
            </div>
            <div class="pcm-canvas-wrap">
                <div class="pcm-viewport" id="pcmViewport">
                    <div class="pcm-img-stage" id="pcmImgStage">
                        <img id="pcmImg" src="${imageSrc}" alt="Profile" draggable="false">
                    </div>
                    <div class="pcm-circle-mask"></div>
                    <div class="pcm-crosshair pcm-ch-h"></div>
                    <div class="pcm-crosshair pcm-ch-v"></div>
                </div>
                <div class="pcm-zoom-bar">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="8" y1="11" x2="14" y2="11"/></svg>
                    <input type="range" id="pcmZoomSlider" min="0.5" max="3" step="0.01" value="1" class="pcm-slider">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="8" y1="11" x2="14" y2="11"/><line x1="11" y1="8" x2="11" y2="14"/></svg>
                </div>
            </div>
            <div class="pcm-actions">
                <button class="pcm-btn pcm-cancel" id="pcmCancelBtn">Cancel</button>
                <button class="pcm-btn pcm-keep" id="pcmKeepBtn">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>
                    Keep
                </button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);

    // Animate in
    requestAnimationFrame(() => modal.classList.add('pcm-visible'));

    const img = document.getElementById('pcmImg');
    const stage = document.getElementById('pcmImgStage');
    const viewport = document.getElementById('pcmViewport');
    const slider = document.getElementById('pcmZoomSlider');
    const VIEWPORT_SIZE = Math.min(window.innerWidth - 40, 340);

    let scale = 1;
    let offsetX = 0, offsetY = 0;
    let isDragging = false;
    let startMouseX, startMouseY, startOffsetX, startOffsetY;

    function applyTransform() {
        img.style.transform = `translate(${offsetX}px, ${offsetY}px) scale(${scale})`;
    }

    function clampOffset() {
        // Allow free movement
    }

    img.onload = function() {
        const naturalW = img.naturalWidth;
        const naturalH = img.naturalHeight;
        const minDim = Math.min(naturalW, naturalH);
        scale = VIEWPORT_SIZE / minDim;
        slider.min = scale * 0.7;
        slider.max = scale * 4;
        slider.step = (scale * 3.3) / 100;
        slider.value = scale;
        offsetX = 0;
        offsetY = 0;
        applyTransform();
    };

    slider.addEventListener('input', function() {
        scale = parseFloat(this.value);
        applyTransform();
    });

    // Drag
    viewport.addEventListener('mousedown', function(e) {
        isDragging = true;
        startMouseX = e.clientX;
        startMouseY = e.clientY;
        startOffsetX = offsetX;
        startOffsetY = offsetY;
        viewport.style.cursor = 'grabbing';
        e.preventDefault();
    });
    document.addEventListener('mousemove', function(e) {
        if (!isDragging) return;
        offsetX = startOffsetX + (e.clientX - startMouseX);
        offsetY = startOffsetY + (e.clientY - startMouseY);
        applyTransform();
    });
    document.addEventListener('mouseup', function() {
        isDragging = false;
        viewport.style.cursor = 'grab';
    });

    // Touch drag
    let lastTouchX, lastTouchY;
    viewport.addEventListener('touchstart', function(e) {
        if (e.touches.length === 1) {
            lastTouchX = e.touches[0].clientX;
            lastTouchY = e.touches[0].clientY;
        }
        e.preventDefault();
    }, { passive: false });
    viewport.addEventListener('touchmove', function(e) {
        if (e.touches.length === 1) {
            offsetX += e.touches[0].clientX - lastTouchX;
            offsetY += e.touches[0].clientY - lastTouchY;
            lastTouchX = e.touches[0].clientX;
            lastTouchY = e.touches[0].clientY;
            applyTransform();
        } else if (e.touches.length === 2) {
            // Pinch zoom
            const dist = Math.hypot(
                e.touches[0].clientX - e.touches[1].clientX,
                e.touches[0].clientY - e.touches[1].clientY
            );
            if (window._lastPinchDist) {
                scale *= dist / window._lastPinchDist;
                scale = Math.max(parseFloat(slider.min), Math.min(parseFloat(slider.max), scale));
                slider.value = scale;
                applyTransform();
            }
            window._lastPinchDist = dist;
        }
        e.preventDefault();
    }, { passive: false });
    viewport.addEventListener('touchend', () => { window._lastPinchDist = null; });

    // Wheel zoom
    viewport.addEventListener('wheel', function(e) {
        scale += e.deltaY * -0.002 * scale;
        scale = Math.max(parseFloat(slider.min), Math.min(parseFloat(slider.max), scale));
        slider.value = scale;
        applyTransform();
        e.preventDefault();
    }, { passive: false });

    // Cancel
    document.getElementById('pcmCancelBtn').onclick = closeProfileCropModal;
    document.getElementById('pcmBackdrop').onclick = closeProfileCropModal;

    // Keep
    document.getElementById('pcmKeepBtn').onclick = async function() {
        const canvas = document.createElement('canvas');
        const OUTPUT = 256;
        canvas.width = OUTPUT;
        canvas.height = OUTPUT;
        const ctx = canvas.getContext('2d');

        // Circular clip
        ctx.beginPath();
        ctx.arc(OUTPUT / 2, OUTPUT / 2, OUTPUT / 2, 0, Math.PI * 2);
        ctx.clip();

        // Compute what portion of image is in the viewport circle
        const vRect = viewport.getBoundingClientRect();
        const iRect = img.getBoundingClientRect();
        const cropX = (vRect.left - iRect.left) / scale;
        const cropY = (vRect.top - iRect.top) / scale;
        const cropSize = VIEWPORT_SIZE / scale;

        ctx.drawImage(img, cropX, cropY, cropSize, cropSize, 0, 0, OUTPUT, OUTPUT);

        const dataUrl = canvas.toDataURL('image/jpeg', 0.92);

        // ── Apply instantly in the UI (use the local dataUrl for speed) ──────────
        window._profilePicDataUrl = dataUrl;
        _applyProfilePicToAllAvatars(dataUrl);
        closeProfileCropModal();

        // Re-render settings profile tab to reflect change
        const activeTab = document.querySelector('.settings-nav-item.active');
        if (activeTab && activeTab.getAttribute('onclick')?.includes('profile')) {
            activeTab.click();
        }

        // ── Upload to Supabase Storage for cross-device sync ──────────────────────
        try {
            if (!currentUser) throw new Error('Not logged in');

            // Convert dataUrl → Blob
            const res  = await fetch(dataUrl);
            const blob = await res.blob();
            const fileName = `avatars/${currentUser.id}/avatar.jpg`;

            // Upsert into the 'avatars' bucket (overwrite if exists)
            const { error: upErr } = await supabaseClient.storage
                .from('avatars')
                .upload(fileName, blob, {
                    contentType: 'image/jpeg',
                    upsert: true
                });

            if (upErr) throw upErr;

            // Get the public URL
            const { data: urlData } = supabaseClient.storage
                .from('avatars')
                .getPublicUrl(fileName);

            const publicUrl = urlData?.publicUrl;
            if (!publicUrl) throw new Error('Could not get public URL');

            // ── Bust CDN cache by appending a version timestamp ──────────────────
            const versionedUrl = publicUrl + '?v=' + Date.now();

            // Persist the URL (not the base64) so all devices load the same image
            setUserItem('catura_profile_pic_url', versionedUrl);
            window._profilePicDataUrl = versionedUrl;
            // Save URL to the user_settings row for cross-device sync
            await saveSettingToCloud('profile_pic_url', versionedUrl);
            // Clear the old base64 column if it ever existed
            await saveSettingToCloud('profile_pic', null);

            showToast('✓ Profile picture saved');
        } catch (err) {
            console.warn('[ProfilePic] Upload failed — keeping local preview only:', err);
            // Fall back: store base64 in cloud so at least the pic is synced somehow
            setUserItem('catura_profile_pic_url', dataUrl);
            await saveSettingToCloud('profile_pic_url', dataUrl);
            showToast('✓ Profile picture saved (local)');
        }
    };
}

function closeProfileCropModal() {
    const modal = document.getElementById('profileCropModal');
    if (!modal) return;
    modal.classList.remove('pcm-visible');
    setTimeout(() => modal.remove(), 350);
}

