let downloadedVersions = new Set();
let detailSessionId = null;
let detailPollTimer = null;
let versionMap = {};        // MC version string → data version int
let reverseVersionMap = {}; // data version int → MC version string

// ── Utilities ──────────────────────────────────────────────────────

function showError(msg) {
    document.getElementById('error-message').textContent = msg;
    document.getElementById('error-banner').classList.add('visible');
}

function clearError() {
    document.getElementById('error-banner').classList.remove('visible');
}

async function apiErrorDetail(r) {
    try { return (await r.json()).detail ?? `Server error (${r.status})`; }
    catch { return `Server error (${r.status})`; }
}

function escapeHtml(s) {
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// Wrap a JS value for safe embedding inside a double-quoted HTML attribute.
function jsAttr(v) { return escapeHtml(JSON.stringify(v)); }

function fmtUptime(s) {
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${sec}s`;
    return `${sec}s`;
}

function formatSessionDate(s) {
    const m = s.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})$/);
    if (!m) return s;
    const d = new Date(+m[1], +m[2]-1, +m[3], +m[4], +m[5], +m[6]);
    return d.toLocaleString(undefined, {month:'short', day:'numeric', year:'numeric', hour:'2-digit', minute:'2-digit'});
}

// ── Version / world loaders ────────────────────────────────────────

async function loadVersionMap() {
    const r = await fetch('/api/versions/map');
    if (!r.ok) return;
    versionMap = await r.json();
    for (const [str, int] of Object.entries(versionMap)) {
        reverseVersionMap[int] = str;
    }
}

async function loadNewWorldVersions() {
    const snapshots = document.getElementById('new-world-snapshots').checked;
    const r = await fetch(`/api/versions?snapshots=${snapshots}`);
    if (!r.ok) {
        showError(await apiErrorDetail(r));
        document.getElementById('new-world-version').innerHTML = '<option disabled selected>Unavailable</option>';
        return;
    }
    const versions = await r.json();
    document.getElementById('new-world-version').innerHTML =
        versions.map(v => `<option value="${v}">${v}</option>`).join('');
}

async function loadAvailableVersions() {
    const snapshots = document.getElementById('available-snapshots').checked;
    const r = await fetch(`/api/versions?snapshots=${snapshots}`);
    const container = document.getElementById('available-tags');
    if (!r.ok) {
        showError(await apiErrorDetail(r));
        container.innerHTML = '<span class="tag-empty">Unavailable</span>';
        return;
    }
    const versions = await r.json();
    container.innerHTML = versions.length
        ? versions.map(v => `<span class="tag">${v}</span>`).join('')
        : '<span class="tag-empty">None available</span>';
}

async function loadDownloadedVersions() {
    const r = await fetch('/api/versions/downloaded');
    if (!r.ok) return;
    const versions = await r.json();
    downloadedVersions = new Set(versions);
    const container = document.getElementById('downloaded-tags');
    container.innerHTML = versions.length
        ? versions.map(v => `<span class="tag">${v}</span>`).join('')
        : '<span class="tag-empty">None downloaded yet</span>';
}

async function loadDownloadedJavaVersions() {
    const r = await fetch('/api/java/downloaded');
    if (!r.ok) return;
    const versions = await r.json();
    const container = document.getElementById('downloaded-java-tags');
    container.innerHTML = versions.length
        ? versions.map(v => `<span class="tag">Java ${v}</span>`).join('')
        : '<span class="tag-empty">None downloaded yet</span>';
}

async function loadWorlds() {
    const worlds = await fetch('/api/worlds').then(r => r.json());
    const sel = document.getElementById('world');
    const startBtn = document.getElementById('start-btn');
    if (worlds.length) {
        sel.innerHTML = worlds.map(w => `<option value="${w.name}">${w.name} (${escapeHtml(reverseVersionMap[w.version] || String(w.version))})</option>`).join('');
        sel.classList.remove('empty');
        startBtn.disabled = false;
    } else {
        sel.innerHTML = '<option disabled selected>No worlds yet</option>';
        sel.classList.add('empty');
        startBtn.disabled = true;
    }
}

// ── Past sessions ──────────────────────────────────────────────────

async function loadSessions() {
    const r = await fetch('/api/sessions');
    if (!r.ok) return;
    const worlds = await r.json();
    const container = document.getElementById('sessions-list');
    if (!worlds.length) {
        container.innerHTML = '<div class="empty">No past sessions.</div>';
        return;
    }
    container.innerHTML = worlds.map(ws => `
        <div class="sessions-world">
            <div class="sessions-world-name">${escapeHtml(ws.world)}</div>
            ${ws.sessions.map(s => `
                <div class="session-row">
                    <span class="session-label">
                        #${s.session_id} &nbsp;·&nbsp; ${escapeHtml(formatSessionDate(s.started_at))}
                    </span>
                    <button onclick="openSessionLog(${jsAttr(ws.world)}, ${s.session_id}, ${jsAttr(s.started_at)})">View</button>
                </div>
            `).join('')}
        </div>
    `).join('');
}

async function openSessionLog(world, sessionId, startedAt) {
    document.getElementById('session-world-name').textContent = world;
    document.getElementById('session-world-display').textContent = world;
    document.getElementById('session-num').textContent = sessionId;
    document.getElementById('session-date').textContent = formatSessionDate(startedAt);
    document.getElementById('session-terminal').textContent = 'Loading…';
    document.getElementById('main-view').style.display = 'none';
    document.getElementById('session-view').style.display = '';

    const r = await fetch(`/api/sessions/log?world=${encodeURIComponent(world)}&session_id=${sessionId}`);
    const t = document.getElementById('session-terminal');
    if (!r.ok) {
        t.textContent = await apiErrorDetail(r);
        return;
    }
    const lines = await r.json();
    t.textContent = lines.join('\n');
    t.scrollTop = t.scrollHeight;
}

function closeSession() {
    document.getElementById('session-view').style.display = 'none';
    document.getElementById('main-view').style.display = '';
}

// ── New world form ─────────────────────────────────────────────────

function updateCreateBtn() {
    document.getElementById('create-btn').disabled =
        !document.getElementById('eula-accept').checked;
}

function toggleNewWorld() {
    const form = document.getElementById('new-world-form');
    const open = form.classList.toggle('open');
    if (open) {
        document.getElementById('new-world-name').focus();
    } else {
        document.getElementById('new-world-name').value = '';
        document.getElementById('eula-accept').checked = false;
        updateCreateBtn();
    }
}

async function createWorld() {
    const name = document.getElementById('new-world-name').value.trim();
    const version = document.getElementById('new-world-version').value;
    if (!name || !version || !document.getElementById('eula-accept').checked) return;
    clearError();

    const versionInt = versionMap[version];
    if (versionInt === undefined) { showError(`Version "${version}" is not in the supported version map.`); return; }

    const downloadParts = [];
    if (!downloadedVersions.has(version)) downloadParts.push(version);
    const javaR = await fetch(`/api/java/required?mc_version=${encodeURIComponent(version)}`);
    if (javaR.ok) {
        const j = await javaR.json();
        if (!j.downloaded) downloadParts.push(`Java ${j.java_version}`);
    }

    const indicator = document.getElementById('download-indicator');
    const statusText = document.getElementById('download-status-text');
    const createBtn = document.getElementById('create-btn');
    const cancelBtn = document.getElementById('cancel-btn');

    indicator.classList.add('visible');
    statusText.textContent = downloadParts.length
        ? `Downloading ${downloadParts.join(' and ')}… (this may take a minute)`
        : 'Creating world…';
    createBtn.disabled = true;
    cancelBtn.disabled = true;

    const r = await fetch('/api/worlds', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name, version: versionInt}),
    });

    indicator.classList.remove('visible');
    createBtn.disabled = false;
    cancelBtn.disabled = false;

    if (!r.ok) { showError(await apiErrorDetail(r)); return; }
    toggleNewWorld();
    await Promise.all([loadWorlds(), loadDownloadedVersions(), loadDownloadedJavaVersions()]);
    document.getElementById('world').value = name;
}

// ── Server list ────────────────────────────────────────────────────

function renderServerCard(s) {
    const versionStr = reverseVersionMap[s.version] || String(s.version);
    if (s.status === 'running') {
        return `
        <div class="server-card running">
            <div class="server-meta">
                <div class="server-title">${escapeHtml(versionStr)} &middot; ${escapeHtml(s.world)}</div>
                <div class="server-detail">${s.memory_mb} MB &nbsp;&middot;&nbsp; Session #${s.session_id}</div>
            </div>
            <div style="display:flex;align-items:center;gap:8px">
                <span class="badge running">Running</span>
                <button onclick="openDetail(${s.session_id}, ${jsAttr(versionStr)}, ${jsAttr(s.world)})">View</button>
                <button class="btn-stop" onclick="stopServer(${s.session_id})">Stop</button>
            </div>
        </div>`;
    }
    const labels = {stopping: 'Stopping…', saving: 'Saving session…', saved: 'Session saved'};
    const label = labels[s.status] || s.status;
    return `
    <div class="server-card">
        <div class="server-meta">
            <div class="server-title">${escapeHtml(versionStr)} &middot; ${escapeHtml(s.world)}</div>
            <div class="server-detail">${s.memory_mb} MB &nbsp;&middot;&nbsp; Session #${s.session_id}</div>
        </div>
        <div style="display:flex;align-items:center;gap:8px">
            <span class="badge">${label}</span>
            ${s.status !== 'saved' ? '<span class="spinner"></span>' : ''}
        </div>
    </div>`;
}

async function startServer() {
    clearError();
    const r = await fetch('/api/server/start', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            world: document.getElementById('world').value,
            memory_mb: parseInt(document.getElementById('memory').value),
        }),
    });
    if (!r.ok) { showError(await apiErrorDetail(r)); return; }
    await refreshServers();
}

async function stopServer(sessionId) {
    clearError();
    const r = await fetch(`/api/server/stop?session_id=${sessionId}`, {method: 'POST'});
    if (!r.ok) showError(await apiErrorDetail(r));
    // Status transitions are driven by polling — no forced refresh needed.
}

async function refreshServers() {
    const servers = await fetch('/api/servers').then(r => r.json());
    const list = document.getElementById('server-list');
    if (!servers.length) {
        list.innerHTML = '<div class="empty">No servers running.</div>';
        return;
    }
    list.innerHTML = servers.map(renderServerCard).join('');
}

// ── Live detail view ───────────────────────────────────────────────

function openDetail(sessionId, version, world) {
    detailSessionId = sessionId;
    document.getElementById('detail-version').textContent = version;
    document.getElementById('detail-world').textContent = world;
    document.getElementById('terminal').textContent = '';
    document.getElementById('detail-stop-btn').style.display = '';
    document.getElementById('cmd-input').disabled = false;
    document.getElementById('cmd-send-btn').disabled = false;

    document.getElementById('main-view').style.display = 'none';
    document.getElementById('detail-view').style.display = '';

    pollDetailOnce();
    detailPollTimer = setInterval(pollDetailOnce, 1000);
}

function closeDetail() {
    clearError();
    detailSessionId = null;
    clearInterval(detailPollTimer);
    detailPollTimer = null;
    document.getElementById('detail-view').style.display = 'none';
    document.getElementById('main-view').style.display = '';
    refreshServers();
    if (document.getElementById('past-sessions-details').open) loadSessions();
}

async function pollDetailOnce() {
    if (detailSessionId === null) return;
    await Promise.all([pollDetailLogs(), pollDetailStats()]);
}

async function pollDetailLogs() {
    if (detailSessionId === null) return;
    const r = await fetch(`/api/server/logs?session_id=${detailSessionId}`);
    if (detailSessionId === null || !r.ok) return;
    const lines = await r.json();
    const terminal = document.getElementById('terminal');
    const atBottom = terminal.scrollTop + terminal.clientHeight >= terminal.scrollHeight - 4;
    terminal.textContent = lines.join('\n');
    if (atBottom) terminal.scrollTop = terminal.scrollHeight;
}

async function pollDetailStats() {
    if (detailSessionId === null) return;
    const r = await fetch(`/api/server/stats?session_id=${detailSessionId}`);
    if (!r.ok || r.status === 204) {
        // Process has exited (204) or session gone (4xx) — return to main screen.
        closeDetail();
        return;
    }
    const s = await r.json();
    document.getElementById('stat-cpu').textContent = `${s.cpu_percent.toFixed(1)}%`;
    document.getElementById('stat-ram').textContent = `${s.memory_mb.toFixed(0)} MB`;
    document.getElementById('stat-uptime').textContent = fmtUptime(s.uptime_seconds);
    document.getElementById('stat-pid').textContent = s.pid;
}

async function sendCommand() {
    const input = document.getElementById('cmd-input');
    const cmd = input.value.trim();
    if (!cmd || detailSessionId === null) return;
    input.value = '';
    const r = await fetch('/api/server/command', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({session_id: detailSessionId, command: cmd}),
    });
    if (!r.ok) showError(await apiErrorDetail(r));
    else await pollDetailLogs();
}

async function stopDetailServer() {
    const sessionId = detailSessionId;
    if (sessionId === null) return;
    document.getElementById('detail-stop-btn').style.display = 'none';
    document.getElementById('cmd-input').disabled = true;
    document.getElementById('cmd-send-btn').disabled = true;
    const r = await fetch(`/api/server/stop?session_id=${sessionId}`, {method: 'POST'});
    // 404 means the server was already removed — treat as success.
    if (!r.ok && r.status !== 404) showError(await apiErrorDetail(r));
}

// ── Init ───────────────────────────────────────────────────────────

loadVersionMap();
loadNewWorldVersions();
loadAvailableVersions();
loadDownloadedVersions();
loadDownloadedJavaVersions();
loadWorlds();
refreshServers();
setInterval(refreshServers, 2000);
