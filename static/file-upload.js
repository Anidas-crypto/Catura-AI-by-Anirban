// ============================
// 📎 CATURA AI — FILE UPLOAD SYSTEM v2
// Complete replacement for static/file-upload.js
// ============================

let attachedFiles = []; // { id, name, url, type, size } after upload

const MAX_FILES     = 5;
const MAX_FILE_SIZE = 25 * 1024 * 1024;
const ALLOWED_TYPES = [
    'image/jpeg','image/jpg','image/png','image/gif','image/webp',
    'application/pdf','text/plain','text/csv',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
];
const BUCKET = 'chat-files';

// ── Called by <input type="file" onchange="handleFileSelect(event)"> ─────────
async function handleFileSelect(event) {
    const files = Array.from(event.target.files || []);
    event.target.value = '';
    if (!files.length) return;

    if (attachedFiles.length + files.length > MAX_FILES) {
        showToast('❌ Max ' + MAX_FILES + ' files at a time');
        return;
    }

    const valid = files.filter(function(f) {
        if (f.size > MAX_FILE_SIZE) { showToast('❌ "' + f.name + '" exceeds 25 MB'); return false; }
        if (!ALLOWED_TYPES.includes(f.type)) { showToast('❌ Type not supported: ' + f.name); return false; }
        return true;
    });
    if (!valid.length) return;

    showToast('📤 Uploading ' + valid.length + ' file(s)...');
    const uploaded = await uploadFilesToSupabase(valid);
    if (uploaded.length) {
        attachedFiles = attachedFiles.concat(uploaded);
        renderAttachedPreview();
    }
}

// ── Upload to Supabase Storage + insert into `files` table ───────────────────
async function uploadFilesToSupabase(files) {
    var results = [];
    for (var i = 0; i < files.length; i++) {
        var file = files[i];
        try {
            var ts       = Date.now();
            var rand     = Math.random().toString(36).substring(2, 8);
            var safe     = file.name.replace(/[^a-zA-Z0-9._-]/g, '_');
            var path     = currentUser.id + '/' + currentSessionId + '/' + ts + '_' + rand + '_' + safe;

            var upResult = await supabaseClient.storage.from(BUCKET).upload(path, file, { contentType: file.type });
            if (upResult.error) {
                console.error('Storage upload failed:', upResult.error.message);
                showToast('❌ Failed: ' + file.name);
                continue;
            }

            var urlResult = supabaseClient.storage.from(BUCKET).getPublicUrl(path);
            var publicUrl = (urlResult.data && urlResult.data.publicUrl) ? urlResult.data.publicUrl : '';

            var dbResult = await supabaseClient.from('files').insert([{
                user_id   : currentUser.id,
                session_id: currentSessionId,
                file_name : file.name,
                file_url  : publicUrl,
                file_type : file.type,
                file_size : file.size,
                expires_at: new Date(Date.now() + 25 * 24 * 60 * 60 * 1000).toISOString()
            }]).select().single();

            if (dbResult.error) console.warn('DB insert warning:', dbResult.error.message);

            results.push({
                id  : dbResult.data ? dbResult.data.id : null,
                name: file.name,
                url : publicUrl,
                type: file.type,
                size: file.size
            });
            showToast('✅ ' + file.name + ' ready');
        } catch (err) {
            console.error('Upload error:', err);
            showToast('❌ Error: ' + file.name);
        }
    }
    return results;
}

// ── Preview bar above input ───────────────────────────────────────────────────
function renderAttachedPreview() {
    var preview = document.getElementById('attachedFilesPreview');
    var list    = document.getElementById('attachedFilesList');
    if (!preview || !list) return;

    if (!attachedFiles.length) {
        preview.style.display = 'none';
        list.innerHTML = '';
        return;
    }

    preview.style.display = 'block';
    list.innerHTML = attachedFiles.map(function(f, idx) {
        var thumb = f.type.startsWith('image/')
            ? '<img src="' + f.url + '" class="af-thumb" alt="' + escHtml(f.name) + '" onclick="window.open(\'' + f.url + '\',\'_blank\')">'
            : fileIconSVG(f.type);
        return '<div class="attached-file-item">' +
                   thumb +
                   '<div class="attached-file-details">' +
                       '<span class="attached-file-name">' + escHtml(truncName(f.name)) + '</span>' +
                       '<span class="attached-file-size">' + fmtSize(f.size) + '</span>' +
                   '</div>' +
                   '<button type="button" class="remove-file-btn" onclick="removeAttachedFile(' + idx + ')">✕</button>' +
               '</div>';
    }).join('');
}

// Public — called from inline onclick
window.removeAttachedFile = function(idx) {
    attachedFiles.splice(idx, 1);
    renderAttachedPreview();
};

// ── Build file attachment HTML for inside the user chat bubble ────────────────
function buildFileAttachHTML(files) {
    if (!files || !files.length) return '';
    var chips = files.map(function(f) {
        if (f.type.startsWith('image/')) {
            return '<div class="msg-file msg-file--img">' +
                       '<img src="' + f.url + '" alt="' + escHtml(f.name) + '" class="msg-file-img" onclick="window.open(\'' + f.url + '\',\'_blank\')" loading="lazy">' +
                       '<span class="msg-file-name">' + escHtml(truncName(f.name)) + '</span>' +
                   '</div>';
        }
        return '<div class="msg-file msg-file--doc">' +
                   fileIconSVG(f.type) +
                   '<div class="msg-file-meta">' +
                       '<span class="msg-file-name">' + escHtml(truncName(f.name)) + '</span>' +
                       '<span class="msg-file-size">' + fmtSize(f.size) + '</span>' +
                   '</div>' +
                   '<a href="' + f.url + '" target="_blank" class="msg-file-dl" title="Open file">↓</a>' +
               '</div>';
    }).join('');
    return '<div class="msg-files-row">' + chips + '</div>';
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function escHtml(str) {
    return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function truncName(name, max) {
    max = max || 22;
    if (name.length <= max) return name;
    var ext  = name.lastIndexOf('.') > 0 ? name.slice(name.lastIndexOf('.')) : '';
    var base = name.slice(0, max - ext.length - 3);
    return base + '...' + ext;
}
function fmtSize(b) {
    if (!b) return '0 B';
    var k = 1024, s = ['B','KB','MB'];
    var i = Math.floor(Math.log(b) / Math.log(k));
    return (b / Math.pow(k, i)).toFixed(1) + ' ' + s[i];
}
function fileIconSVG(type) {
    var a = 'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"';
    if (type === 'application/pdf')
        return '<svg class="file-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" ' + a + '><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="9" y1="13" x2="15" y2="13"/><line x1="9" y1="17" x2="15" y2="17"/></svg>';
    if (type && (type.includes('word') || type.includes('document')))
        return '<svg class="file-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" ' + a + '><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="16" y2="17"/><line x1="8" y1="9" x2="10" y2="9"/></svg>';
    if (type === 'text/csv' || type === 'text/plain')
        return '<svg class="file-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" ' + a + '><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="16" y2="17"/></svg>';
    return '<svg class="file-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" ' + a + '><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>';
}
