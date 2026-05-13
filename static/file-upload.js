// ============================
// 📎 CATURA AI — FILE UPLOAD SYSTEM v3
// static/file-upload.js
// ============================

'use strict';

// ── State ─────────────────────────────────────────────────────────────────────
let attachedFiles = []; // { id, name, url, type, size, localUrl? }

// Active XHR/fetch AbortControllers keyed by temp file id (for cancellation)
const _uploadControllers = new Map();

// ── Constants ─────────────────────────────────────────────────────────────────
const MAX_FILES     = 5;
const MAX_FILE_SIZE = 25 * 1024 * 1024; // 25 MB

// Allowed MIME types (client-side hint — server must enforce independently)
const ALLOWED_MIME = new Set([
    'image/jpeg', 'image/jpg', 'image/png', 'image/gif', 'image/webp',
    'application/pdf',
    'text/plain', 'text/csv',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    // Code / text files — browsers often report these as text/plain too,
    // but we also accept the explicit subtypes some OS/browsers emit.
    'text/javascript', 'application/javascript', 'application/x-javascript',
    'text/x-python', 'application/x-python', 'application/x-python-code',
    'text/html', 'application/xhtml+xml',
    'text/css',
    'application/json', 'text/json',
    'text/x-java-source', 'text/x-java',
    'text/x-c', 'text/x-c++', 'text/x-csrc', 'text/x-cppsrc',
    'text/x-csharp',
    'text/x-typescript',
    'text/markdown', 'text/x-markdown',
    'text/x-sh', 'application/x-sh', 'text/x-shellscript',
    'application/xml', 'text/xml',
    'text/x-go', 'text/x-rust', 'text/x-kotlin', 'text/x-swift',
    'text/x-php', 'application/x-httpd-php',
    'text/x-ruby', 'application/x-ruby',
    'text/x-sql',
    'application/x-yaml', 'text/yaml', 'text/x-yaml',
    'application/toml',
]);

// Extension allowlist cross-checked against MIME
const ALLOWED_EXT = new Set([
    'jpg', 'jpeg', 'png', 'gif', 'webp',
    'pdf',
    'txt', 'csv',
    'doc', 'docx',
    // code files
    'py', 'js', 'ts', 'jsx', 'tsx',
    'html', 'htm', 'css', 'json',
    'java', 'c', 'cpp', 'cs', 'go', 'rs', 'rb', 'php', 'swift', 'kt',
    'sh', 'bash', 'zsh',
    'xml', 'yaml', 'yml', 'toml', 'md', 'markdown',
    'sql', 'r', 'lua', 'dart', 'scala', 'hs',
]);

// Magic-byte signatures for server-enforceable types we can partially check
// client-side (first N bytes as hex).  Checked for images and PDFs only.
const MAGIC_SIGNATURES = {
    'image/jpeg' : [['ffd8ff']],
    'image/png'  : [['89504e47']],
    'image/gif'  : [['47494638']],
    'image/webp' : [['52494646', 8, '57454250']], // RIFF....WEBP
    'application/pdf': [['25504446']]              // %PDF
};

const BUCKET       = 'chat-files';
const FILE_TTL_MS  = 25 * 24 * 60 * 60 * 1000; // 25 days

// ── Entry point ───────────────────────────────────────────────────────────────
// Called by <input type="file" onchange="handleFileSelect(event)">
async function handleFileSelect(event) {
    const files = Array.from(event.target.files || []);
    event.target.value = ''; // reset so same file can be re-selected
    if (!files.length) return;

    // Count slots available AFTER current attached files
    const slotsAvailable = MAX_FILES - attachedFiles.length;
    if (slotsAvailable <= 0) {
        showToast('❌ Max ' + MAX_FILES + ' files at a time');
        return;
    }

    // Validate each file (MIME, extension, size, magic bytes)
    // Run validation concurrently; sequential would be fine too since it's local
    const validationResults = await Promise.all(files.map(validateFile));
    const valid = files.filter(function(_, i) {
        if (!validationResults[i].ok) {
            showToast('❌ ' + validationResults[i].reason + ': ' + files[i].name);
            return false;
        }
        return true;
    });

    if (!valid.length) return;

    // Check for duplicates already in the list (same name + size)
    const deduplicated = valid.filter(function(f) {
        const isDupe = attachedFiles.some(function(a) {
            return a.name === f.name && a.size === f.size;
        });
        if (isDupe) showToast('⚠️ Already attached: ' + f.name);
        return !isDupe;
    });

    if (!deduplicated.length) return;

    // Respect slot limit — take only as many as fit
    const toUpload = deduplicated.slice(0, slotsAvailable);
    if (toUpload.length < deduplicated.length) {
        showToast('⚠️ Only ' + toUpload.length + ' file(s) added — max ' + MAX_FILES + ' reached');
    }

    // Create placeholder entries with local preview URLs so the UI updates
    // immediately without waiting for the Supabase upload to finish.
    const placeholders = toUpload.map(function(f) {
        return {
            _tempId  : _tempId(),
            name     : f.name,
            url      : null,               // filled after upload
            localUrl : f.type.startsWith('image/') ? URL.createObjectURL(f) : null,
            type     : f.type,
            size     : f.size,
            uploading: true,
            failed   : false
        };
    });

    attachedFiles = attachedFiles.concat(placeholders);
    renderAttachedPreview();

    // Upload all files concurrently (up to MAX_FILES)
    await Promise.all(toUpload.map(function(f, i) {
        return uploadOneFile(f, placeholders[i]);
    }));
}

// ── Validation ────────────────────────────────────────────────────────────────
// Code/text extensions — browser often reports these as text/plain
var TEXT_CODE_EXTS = new Set([
    'py','js','ts','jsx','tsx','html','htm','css','json','java','c','cpp',
    'cs','go','rs','rb','php','swift','kt','sh','bash','zsh','xml','yaml',
    'yml','toml','md','markdown','sql','r','lua','dart','scala','hs','txt','csv'
]);

async function validateFile(file) {
    // Size check
    if (file.size > MAX_FILE_SIZE) {
        return { ok: false, reason: 'Exceeds 25 MB' };
    }
    if (file.size === 0) {
        return { ok: false, reason: 'Empty file' };
    }

    // Extension check first — code files often come in as text/plain from browser
    var ext = (file.name.split('.').pop() || '').toLowerCase();
    if (!ALLOWED_EXT.has(ext)) {
        return { ok: false, reason: 'Extension not allowed' };
    }

    // MIME check — allow text/plain for all text-based extensions (code files)
    var mimeOk = ALLOWED_MIME.has(file.type) ||
                 (TEXT_CODE_EXTS.has(ext) && (file.type === 'text/plain' || file.type === ''));
    if (!mimeOk) {
        return { ok: false, reason: 'Type not supported' };
    }

    // Magic-byte check for types we have signatures for (images + PDF only)
    var sigs = MAGIC_SIGNATURES[file.type];
    if (sigs) {
        var valid = await checkMagicBytes(file, sigs);
        if (!valid) {
            return { ok: false, reason: 'File content does not match its type' };
        }
    }

    return { ok: true };
}

// Reads the first 12 bytes of a file and checks against known magic signatures
async function checkMagicBytes(file, signatures) {
    try {
        const headerBytes = await readBytesAsHex(file, 12);
        return signatures.some(function(sig) {
            // Simple case: sig is [hexPrefix] — header must start with it
            if (sig.length === 1) {
                return headerBytes.startsWith(sig[0]);
            }
            // Compound case: sig is [prefix, offset, suffix] e.g. RIFF/WEBP
            if (sig.length === 3) {
                const prefix = sig[0];
                const offset = sig[1];
                const suffix = sig[2];
                return headerBytes.startsWith(prefix) &&
                       headerBytes.slice(offset * 2).startsWith(suffix);
            }
            return false;
        });
    } catch (_) {
        // If we can't read, fail open (server will re-validate)
        return true;
    }
}

function readBytesAsHex(file, numBytes) {
    return new Promise(function(resolve, reject) {
        var reader = new FileReader();
        reader.onload = function(e) {
            var arr = new Uint8Array(e.target.result);
            var hex = Array.from(arr).map(function(b) {
                return b.toString(16).padStart(2, '0');
            }).join('');
            resolve(hex);
        };
        reader.onerror = reject;
        reader.readAsArrayBuffer(file.slice(0, numBytes));
    });
}

// ── Single-file upload with retry ─────────────────────────────────────────────
async function uploadOneFile(file, placeholder, retryCount) {
    retryCount = retryCount || 0;
    const MAX_RETRIES = 2;

    var controller = new AbortController();
    _uploadControllers.set(placeholder._tempId, controller);

    try {
        var ts   = Date.now();
        var rand = Math.random().toString(36).substring(2, 8);
        var safe = file.name.replace(/[^a-zA-Z0-9._-]/g, '_');
        var path = currentUser.id + '/' + currentSessionId + '/' + ts + '_' + rand + '_' + safe;

        // Upload to Supabase Storage
        var upResult = await supabaseClient.storage.from(BUCKET).upload(path, file, {
            contentType: file.type,
            // signal: controller.signal  // uncomment if supabase-js supports AbortSignal
        });

        if (upResult.error) {
            throw new Error('Storage: ' + upResult.error.message);
        }

        var urlResult = supabaseClient.storage.from(BUCKET).getPublicUrl(path);
        var publicUrl = (urlResult.data && urlResult.data.publicUrl) ? urlResult.data.publicUrl : '';
        if (!publicUrl) {
            throw new Error('Could not retrieve public URL for uploaded file');
        }

        // Insert metadata into DB only after storage succeeds
        var dbResult = await supabaseClient.from('files').insert([{
            user_id   : currentUser.id,
            session_id: currentSessionId,
            file_name : file.name,
            file_url  : publicUrl,
            file_type : file.type,
            file_size : file.size,
            expires_at: new Date(Date.now() + FILE_TTL_MS).toISOString()
        }]).select().single();

        if (dbResult.error) {
            // Non-fatal: the file is uploaded; DB record is advisory only.
            // Log and continue rather than showing an error to the user.
            console.warn('[file-upload] DB insert warning:', dbResult.error.message);
        }

        // Revoke the temporary object URL to free memory
        if (placeholder.localUrl) {
            URL.revokeObjectURL(placeholder.localUrl);
        }

        // Update the placeholder in-place
        _patchPlaceholder(placeholder._tempId, {
            id       : dbResult.data ? dbResult.data.id : null,
            url      : publicUrl,
            localUrl : null,
            uploading: false,
            failed   : false
        });

        showToast('✅ ' + _truncName(file.name) + ' ready');
    } catch (err) {
        if (err && err.name === 'AbortError') {
            // Upload was cancelled by the user — placeholder already removed
            return;
        }

        console.error('[file-upload] Upload error:', err);

        if (retryCount < MAX_RETRIES) {
            console.info('[file-upload] Retrying', file.name, '(attempt', retryCount + 2, ')');
            await _sleep(1000 * Math.pow(2, retryCount)); // exponential back-off
            return uploadOneFile(file, placeholder, retryCount + 1);
        }

        // All retries exhausted — mark as failed
        _patchPlaceholder(placeholder._tempId, { uploading: false, failed: true });
        showToast('❌ Failed to upload: ' + _truncName(file.name));
    } finally {
        _uploadControllers.delete(placeholder._tempId);
        renderAttachedPreview();
    }
}

// ── Preview bar ───────────────────────────────────────────────────────────────
function renderAttachedPreview() {
    var preview = document.getElementById('attachedFilesPreview');
    var list    = document.getElementById('attachedFilesList');
    if (!preview || !list) return;

    if (!attachedFiles.length) {
        preview.style.display = 'none';
        list.innerHTML = '';
        return;
    }

    preview.style.display = '';

    list.innerHTML = attachedFiles.map(function(f, idx) {
        var displayUrl = f.localUrl || f.url;
        var isImage = f.type && f.type.startsWith('image/');
        var ext = (f.name.split('.').pop() || '').toUpperCase().slice(0, 5);
        var failed  = f.failed;
        var loading = f.uploading;

        var cardClass = 'af-card' +
            (isImage ? ' af-card--image' : ' af-card--file') +
            (failed  ? ' af-card--failed' : '') +
            (loading ? ' af-card--loading' : '');

        var inner = '';

        if (isImage) {
            // ── Image card: big thumbnail ──
            var imgSrc = displayUrl || '';
            inner =
                '<div class="af-card-img-wrap">' +
                    (imgSrc
                        ? '<img src="' + escAttr(imgSrc) + '" class="af-card-img" alt="' + escAttr(f.name) + '" loading="lazy" onerror="this.style.display=\'none\'">'
                        : '<div class="af-card-img-placeholder">' + fileIconSVG(f.type) + '</div>') +
                    (loading ? '<div class="af-card-spinner-overlay"><span class="af-spinner"></span></div>' : '') +
                    (failed  ? '<div class="af-card-spinner-overlay af-card-fail-overlay"><span class="af-fail-icon">!</span></div>' : '') +
                '</div>' +
                '<div class="af-card-label" title="' + escAttr(f.name) + '">' + escHtml(_truncName(f.name, 20)) + '</div>';
        } else {
            // ── File card: icon + ext badge + name/size ──
            var badge = '<span class="af-ext-badge">' + escHtml(ext) + '</span>';
            var statusLabel = loading ? 'Uploading…' : failed ? 'Failed' : fmtSize(f.size);
            inner =
                '<div class="af-card-file-icon">' +
                    fileIconSVG(f.type) +
                    badge +
                    (loading ? '<span class="af-spinner af-spinner--sm"></span>' : '') +
                    (failed  ? '<span class="af-fail-icon af-fail-icon--sm">!</span>' : '') +
                '</div>' +
                '<div class="af-card-file-info">' +
                    '<span class="af-card-name" title="' + escAttr(f.name) + '">' + escHtml(_truncName(f.name, 22)) + '</span>' +
                    '<span class="af-card-size' + (failed ? ' af-card-size--fail' : '') + '">' + escHtml(statusLabel) + '</span>' +
                '</div>';
        }

        return '<div class="' + cardClass + '" role="listitem">' +
               inner +
               '<button type="button" class="af-card-remove" ' +
                   'onclick="removeAttachedFile(' + idx + ')" ' +
                   'aria-label="Remove ' + escAttr(f.name) + '" title="Remove">✕</button>' +
               '</div>';
    }).join('');
}

// Called from inline onclick — must remain on window
window.removeAttachedFile = function(idx) {
    var entry = attachedFiles[idx];
    if (!entry) return;

    // Abort in-progress upload for this file
    if (entry.uploading && _uploadControllers.has(entry._tempId)) {
        _uploadControllers.get(entry._tempId).abort();
        _uploadControllers.delete(entry._tempId);
    }

    // Revoke local object URL
    if (entry.localUrl) {
        URL.revokeObjectURL(entry.localUrl);
    }

    attachedFiles.splice(idx, 1);
    renderAttachedPreview();
};

// ── File chips in chat bubble ─────────────────────────────────────────────────
function buildFileAttachHTML(files) {
    if (!files || !files.length) return '';

    var chips = files.map(function(f) {
        if (!f.url) return ''; // skip if upload failed or still pending

        if (f.type && f.type.startsWith('image/')) {
            // Use data-src so a lightweight lightbox or lazy-loader can bind later
            return '<div class="msg-file msg-file--img">' +
                       '<img src="' + escAttr(f.url) + '" alt="' + escAttr(f.name) + '" ' +
                           'class="msg-file-img" loading="lazy" ' +
                           'onerror="this.parentElement.style.display=\'none\'" ' +
                           'onclick="window.open(' + JSON.stringify(f.url) + ',\'_blank\',\'noopener,noreferrer\')">' +
                       '<span class="msg-file-name">' + escHtml(_truncName(f.name)) + '</span>' +
                   '</div>';
        }

        return '<div class="msg-file msg-file--doc">' +
                   fileIconSVG(f.type) +
                   '<div class="msg-file-meta">' +
                       '<span class="msg-file-name">' + escHtml(_truncName(f.name)) + '</span>' +
                       '<span class="msg-file-size">' + fmtSize(f.size) + '</span>' +
                   '</div>' +
                   '<a href="' + escAttr(f.url) + '" target="_blank" rel="noopener noreferrer" ' +
                       'class="msg-file-dl" title="Open ' + escAttr(f.name) + '">↓</a>' +
               '</div>';
    }).join('');

    return chips ? '<div class="msg-files-row">' + chips + '</div>' : '';
}

// ── Clear all attached files (e.g. after message send) ───────────────────────
function clearAttachedFiles() {
    // Abort any in-progress uploads
    _uploadControllers.forEach(function(ctrl) { ctrl.abort(); });
    _uploadControllers.clear();

    // Revoke object URLs
    attachedFiles.forEach(function(f) {
        if (f.localUrl) URL.revokeObjectURL(f.localUrl);
    });

    attachedFiles = [];
    renderAttachedPreview();
}

// ── Internal helpers ──────────────────────────────────────────────────────────

// Escape for HTML text content
function escHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// Escape for HTML attribute values (stricter — also encodes single-quotes)
function escAttr(str) {
    return escHtml(str);
}

function _truncName(name, max) {
    max = max || 28;
    if (!name || name.length <= max) return name || '';
    var dot  = name.lastIndexOf('.');
    var ext  = dot > 0 ? name.slice(dot) : '';
    var base = name.slice(0, max - ext.length - 3);
    return base + '…' + ext;
}

function fmtSize(bytes) {
    if (!bytes || bytes === 0) return '0 B';
    var units = ['B', 'KB', 'MB', 'GB'];
    var i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    return (bytes / Math.pow(1024, i)).toFixed(1) + ' ' + units[i];
}

function _tempId() {
    return 'tmp_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
}

function _sleep(ms) {
    return new Promise(function(r) { setTimeout(r, ms); });
}

// Update a placeholder entry by tempId, preserving other fields
function _patchPlaceholder(tempId, patch) {
    var idx = attachedFiles.findIndex(function(f) { return f._tempId === tempId; });
    if (idx === -1) return; // was removed while uploading
    attachedFiles[idx] = Object.assign({}, attachedFiles[idx], patch);
}

function fileIconSVG(type) {
    var a = 'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"';
    var ext_color = '#10a37f';

    // Image
    if (type && type.startsWith('image/')) {
        return '<svg class="file-icon" width="22" height="22" viewBox="0 0 24 24" fill="none" ' + a + ' aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>';
    }
    // PDF
    if (type === 'application/pdf') {
        return '<svg class="file-icon file-icon--pdf" width="22" height="22" viewBox="0 0 24 24" fill="none" ' + a + ' aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="9" y1="13" x2="15" y2="13"/><line x1="9" y1="17" x2="15" y2="17"/></svg>';
    }
    // Word doc
    if (type && (type.includes('word') || type.includes('document'))) {
        return '<svg class="file-icon file-icon--doc" width="22" height="22" viewBox="0 0 24 24" fill="none" ' + a + ' aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="16" y2="17"/><line x1="8" y1="9" x2="10" y2="9"/></svg>';
    }
    // Generic text/code — use a code-bracket icon
    return '<svg class="file-icon file-icon--code" width="22" height="22" viewBox="0 0 24 24" fill="none" ' + a + ' aria-hidden="true"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/><polyline points="9 14 7 16 9 18"/><polyline points="15 14 17 16 15 18"/></svg>';
}

// ════════════════════════════════════════════════════════════════════════════
// 🖱️  DRAG & DROP — Full-page drop zone with Claude-style overlay
// ════════════════════════════════════════════════════════════════════════════

(function initDragDrop() {
    // Inject the drop overlay once DOM is ready
    function _createOverlay() {
        if (document.getElementById('catura-drop-overlay')) return;
        var el = document.createElement('div');
        el.id = 'catura-drop-overlay';
        el.setAttribute('aria-hidden', 'true');
        el.innerHTML =
            '<div class="drop-overlay-inner">' +
                '<div class="drop-overlay-icon">' +
                    '<svg width="52" height="52" viewBox="0 0 24 24" fill="none" ' +
                        'stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">' +
                        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>' +
                        '<polyline points="17 8 12 3 7 8"/>' +
                        '<line x1="12" y1="3" x2="12" y2="15"/>' +
                    '</svg>' +
                '</div>' +
                '<div class="drop-overlay-title">Drop files to add them</div>' +
                '<div class="drop-overlay-sub">Images, PDFs, code files &amp; documents</div>' +
            '</div>';
        document.body.appendChild(el);
    }

    var _dragCounter = 0; // track nested dragenter/dragleave pairs

    function _show() {
        var el = document.getElementById('catura-drop-overlay');
        if (el) el.classList.add('drop-overlay--visible');
        document.body.classList.add('drag-active');
    }

    function _hide() {
        var el = document.getElementById('catura-drop-overlay');
        if (el) el.classList.remove('drop-overlay--visible');
        document.body.classList.remove('drag-active');
    }

    function _hasFiles(dt) {
        if (!dt) return false;
        if (dt.types) {
            for (var i = 0; i < dt.types.length; i++) {
                if (dt.types[i] === 'Files') return true;
            }
        }
        return false;
    }

    document.addEventListener('dragenter', function(e) {
        if (!_hasFiles(e.dataTransfer)) return;
        e.preventDefault();
        _dragCounter++;
        if (_dragCounter === 1) _show();
    }, false);

    document.addEventListener('dragleave', function(e) {
        if (!_hasFiles(e.dataTransfer)) return;
        _dragCounter--;
        if (_dragCounter <= 0) {
            _dragCounter = 0;
            _hide();
        }
    }, false);

    document.addEventListener('dragover', function(e) {
        if (!_hasFiles(e.dataTransfer)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'copy';
    }, false);

    document.addEventListener('drop', function(e) {
        e.preventDefault();
        _dragCounter = 0;
        _hide();

        var files = e.dataTransfer && e.dataTransfer.files
            ? Array.from(e.dataTransfer.files)
            : [];
        if (!files.length) return;

        // Re-use the existing file-select pipeline
        var fakeEvent = { target: { files: files, value: '' } };
        handleFileSelect(fakeEvent);
    }, false);

    // Create overlay when DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _createOverlay);
    } else {
        _createOverlay();
    }
})();
