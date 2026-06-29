let downloadedVersions = new Set();
let usedPorts = new Set();
let detailSessionId = null;
let detailPollTimer = null;
let detailLineCount = 0;
let versionMap = {};        // MC version string → data version int
let reverseVersionMap = {}; // data version int → MC version string
let lastServersJson = null;
let sessionsLoaded = false;

const MINECRAFT_PORTS = [25565, 25566, 25567, 25568, 25569];

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

function joinAddress(port) {
    const host = window.location.hostname;
    return port === 25565 ? host : `${host}:${port}`;
}

// ── Tabs ───────────────────────────────────────────────────────────

function switchTab(name) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    document.querySelector(`.tab[data-tab="${name}"]`).classList.add('active');
    document.getElementById(`tab-${name}`).classList.add('active');
    if (name === 'sessions' && !sessionsLoaded) {
        sessionsLoaded = true;
        loadSessions();
    }
    if (name === 'customize') {
        loadCustomizeWorlds();
    }
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
    if (document.getElementById('new-world-loader').value !== 'vanilla') {
        await loadLoaderVersions();
    }
}

function onMcVersionChange() {
    if (document.getElementById('new-world-loader').value !== 'vanilla') {
        loadLoaderVersions();
    }
}

async function onLoaderChange() {
    const loader = document.getElementById('new-world-loader').value;
    const versionRow = document.getElementById('loader-version-row');
    if (loader === 'vanilla') {
        versionRow.style.display = 'none';
        updateCreateBtn();
        return;
    }
    versionRow.style.display = '';
    await loadLoaderVersions();
}

async function loadLoaderVersions() {
    const loader = document.getElementById('new-world-loader').value;
    if (loader === 'vanilla') return;
    const mcVersion = document.getElementById('new-world-version').value;
    const sel = document.getElementById('new-world-loader-version');
    if (!mcVersion || document.getElementById('new-world-version').selectedIndex < 0) {
        sel.innerHTML = '<option disabled selected>Select MC version first</option>';
        updateCreateBtn();
        return;
    }
    sel.innerHTML = '<option disabled selected>Loading…</option>';
    updateCreateBtn();
    const r = await fetch(`/api/modloader/versions?loader=${encodeURIComponent(loader)}&mc_version=${encodeURIComponent(mcVersion)}`);
    if (!r.ok) {
        sel.innerHTML = '<option disabled selected>Error loading versions</option>';
        updateCreateBtn();
        return;
    }
    const versions = await r.json();
    if (!versions.length) {
        sel.innerHTML = '<option disabled selected>Not available for this MC version</option>';
    } else {
        sel.innerHTML = versions.map(v => `<option value="${v}">${v}</option>`).join('');
    }
    updateCreateBtn();
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
    usedPorts = new Set(worlds.map(w => w.port).filter(p => p > 0));
    const sel = document.getElementById('world');
    const startBtn = document.getElementById('start-btn');
    if (worlds.length) {
        sel.innerHTML = worlds.map(w => {
            const loaderStr = w.mod_loader && w.mod_loader !== 'vanilla'
                ? ` · ${escapeHtml(w.mod_loader)} ${escapeHtml(w.mod_loader_version)}`
                : '';
            return `<option value="${w.name}">${w.name} (${escapeHtml(reverseVersionMap[w.version] || String(w.version))}${loaderStr} · port ${w.port})</option>`;
        }).join('');
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

function populatePortSelect() {
    const available = MINECRAFT_PORTS.filter(p => !usedPorts.has(p));
    const sel = document.getElementById('new-world-port');
    if (!available.length) {
        sel.innerHTML = '<option disabled selected value="">No ports available</option>';
    } else {
        sel.innerHTML = available.map(p => `<option value="${p}">${p}</option>`).join('');
    }
    updateCreateBtn();
}

function updateCreateBtn() {
    const portSel = document.getElementById('new-world-port');
    const loader = document.getElementById('new-world-loader').value;
    const loaderVerSel = document.getElementById('new-world-loader-version');
    const loaderVerOk = loader === 'vanilla' ||
        (loaderVerSel.value && !loaderVerSel.options[loaderVerSel.selectedIndex]?.disabled);
    document.getElementById('create-btn').disabled =
        !document.getElementById('eula-accept').checked || !portSel.value || !loaderVerOk;
}

function toggleNewWorld() {
    const form = document.getElementById('new-world-form');
    const open = form.classList.toggle('open');
    if (open) {
        populatePortSelect();
        document.getElementById('new-world-name').focus();
    } else {
        document.getElementById('new-world-name').value = '';
        document.getElementById('eula-accept').checked = false;
        document.getElementById('new-world-loader').value = 'vanilla';
        document.getElementById('loader-version-row').style.display = 'none';
        updateCreateBtn();
    }
}

async function createWorld() {
    const name = document.getElementById('new-world-name').value.trim();
    const version = document.getElementById('new-world-version').value;
    const port = parseInt(document.getElementById('new-world-port').value);
    const loader = document.getElementById('new-world-loader').value;
    const loaderVersion = loader !== 'vanilla' ? document.getElementById('new-world-loader-version').value : '';
    if (!name || !version || !port || !document.getElementById('eula-accept').checked) return;
    if (loader !== 'vanilla' && !loaderVersion) return;
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
    if (loader !== 'vanilla') {
        const loaderLabel = loader === 'neoforge' ? 'NeoForge' : loader.charAt(0).toUpperCase() + loader.slice(1);
        downloadParts.push(`${loaderLabel} ${loaderVersion}`);
    }

    const indicator = document.getElementById('download-indicator');
    const statusText = document.getElementById('download-status-text');
    const createBtn = document.getElementById('create-btn');
    const cancelBtn = document.getElementById('cancel-btn');

    indicator.classList.add('visible');
    statusText.textContent = downloadParts.length
        ? `Downloading ${downloadParts.join(', ')}… (this may take a few minutes)`
        : 'Creating world…';
    createBtn.disabled = true;
    cancelBtn.disabled = true;

    const r = await fetch('/api/worlds', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name, version: versionInt, port, mod_loader: loader, mod_loader_version: loaderVersion}),
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

function _fallbackCopy(text) {
    const el = document.createElement('textarea');
    el.value = text;
    el.style.cssText = 'position:fixed;top:0;left:0;opacity:0;pointer-events:none';
    document.body.appendChild(el);
    el.select();
    try { document.execCommand('copy'); } catch {}
    document.body.removeChild(el);
}

function copyAddress(btn, port) {
    const text = joinAddress(port);
    const done = () => {
        btn.textContent = 'Copied!';
        setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
    };
    if (navigator.clipboard) {
        navigator.clipboard.writeText(text).then(done).catch(() => { _fallbackCopy(text); done(); });
    } else {
        _fallbackCopy(text);
        done();
    }
}

function renderServerCard(s) {
    const versionStr = reverseVersionMap[s.version] || String(s.version);
    if (s.status === 'running') {
        return `
        <div class="server-card running">
            <div class="server-meta">
                <div class="server-title">${escapeHtml(versionStr)} &middot; ${escapeHtml(s.world)}</div>
                <div class="server-detail">${s.memory_mb} MB &nbsp;&middot;&nbsp; port ${s.port} &nbsp;&middot;&nbsp; Session #${s.session_id}</div>
                <div style="display:flex;align-items:center;gap:6px">
                    <span class="server-join">join at: ${escapeHtml(joinAddress(s.port))}</span>
                    <button onclick="copyAddress(this, ${s.port})" style="padding:2px 7px;font-size:0.75rem">Copy</button>
                </div>
            </div>
            <div style="display:flex;align-items:center;gap:8px">
                <span class="badge running">Running</span>
                <button onclick="openDetail(${s.session_id}, ${jsAttr(versionStr)}, ${jsAttr(s.world)}, ${s.port})">View</button>
                <button class="btn-stop" onclick="stopServer(${s.session_id})">Stop</button>
            </div>
        </div>`;
    }
    if (s.status === 'crashed') {
        return `
        <div class="server-card crashed">
            <div class="server-meta">
                <div class="server-title">${escapeHtml(versionStr)} &middot; ${escapeHtml(s.world)}</div>
                <div class="server-detail">${s.memory_mb} MB &nbsp;&middot;&nbsp; port ${s.port} &nbsp;&middot;&nbsp; Session #${s.session_id}</div>
            </div>
            <div style="display:flex;align-items:center;gap:8px">
                <span class="badge crashed">Crashed</span>
                <button onclick="openDetail(${s.session_id}, ${jsAttr(versionStr)}, ${jsAttr(s.world)}, ${s.port}, 'crashed')">View</button>
                <button onclick="dismissServer(${s.session_id})">Dismiss</button>
            </div>
        </div>`;
    }
    const labels = {stopping: 'Stopping…', saving: 'Saving session…', saved: 'Session saved'};
    const label = labels[s.status] || s.status;
    return `
    <div class="server-card">
        <div class="server-meta">
            <div class="server-title">${escapeHtml(versionStr)} &middot; ${escapeHtml(s.world)}</div>
            <div class="server-detail">${s.memory_mb} MB &nbsp;&middot;&nbsp; port ${s.port} &nbsp;&middot;&nbsp; Session #${s.session_id}</div>
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
    switchTab('servers');
}

async function dismissServer(sessionId) {
    clearError();
    const r = await fetch(`/api/server/dismiss?session_id=${sessionId}`, {method: 'POST'});
    if (!r.ok) showError(await apiErrorDetail(r));
    // SSE delivers the removal.
}

async function stopServer(sessionId) {
    clearError();
    const r = await fetch(`/api/server/stop?session_id=${sessionId}`, {method: 'POST'});
    if (!r.ok) showError(await apiErrorDetail(r));
    // SSE delivers status transitions automatically.
}

async function refreshServers() {
    const servers = await fetch('/api/servers').then(r => r.json());
    const json = JSON.stringify(servers);
    if (json === lastServersJson) return;
    lastServersJson = json;
    const list = document.getElementById('server-list');
    if (!servers.length) {
        list.innerHTML = '<div class="empty">No servers running.</div>';
        return;
    }
    list.innerHTML = servers.map(renderServerCard).join('');
}

// ── Live detail view ───────────────────────────────────────────────

function _setDetailCrashed() {
    clearInterval(detailPollTimer);
    detailPollTimer = null;
    const badge = document.getElementById('detail-badge');
    badge.textContent = 'Crashed';
    badge.className = 'badge crashed';
    document.getElementById('detail-stop-btn').style.display = 'none';
    document.getElementById('cmd-input').disabled = true;
    document.getElementById('cmd-send-btn').disabled = true;
    pollDetailLogs(); // one final fetch to pick up the crash message
}

function openDetail(sessionId, version, world, port, status = 'running') {
    detailSessionId = sessionId;
    detailLineCount = 0;
    document.getElementById('detail-version').textContent = version;
    document.getElementById('detail-world').textContent = world;
    document.getElementById('detail-port').textContent = `${port}`;
    document.getElementById('terminal').textContent = '';

    document.getElementById('main-view').style.display = 'none';
    document.getElementById('detail-view').style.display = '';

    if (status === 'crashed') {
        _setDetailCrashed();
    } else {
        const badge = document.getElementById('detail-badge');
        badge.textContent = 'Running';
        badge.className = 'badge running';
        document.getElementById('detail-stop-btn').style.display = '';
        document.getElementById('cmd-input').disabled = false;
        document.getElementById('cmd-send-btn').disabled = false;
        pollDetailOnce();
        detailPollTimer = setInterval(pollDetailOnce, 1000);
    }
}

function closeDetail() {
    clearError();
    detailSessionId = null;
    clearInterval(detailPollTimer);
    detailPollTimer = null;
    document.getElementById('detail-view').style.display = 'none';
    document.getElementById('main-view').style.display = '';
    refreshServers();
    if (document.getElementById('tab-sessions').classList.contains('active')) loadSessions();
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

    if (lines.length < detailLineCount) {
        // Server-side deque wrapped — clear and re-render from scratch.
        terminal.textContent = lines.join('\n');
        detailLineCount = lines.length;
        terminal.scrollTop = terminal.scrollHeight;
        return;
    }
    if (lines.length === detailLineCount) return;

    const atBottom = terminal.scrollTop + terminal.clientHeight >= terminal.scrollHeight - 4;
    const newText = (detailLineCount > 0 ? '\n' : '') + lines.slice(detailLineCount).join('\n');
    terminal.appendChild(document.createTextNode(newText));
    detailLineCount = lines.length;
    if (atBottom) terminal.scrollTop = terminal.scrollHeight;
}

async function pollDetailStats() {
    if (detailSessionId === null) return;
    const r = await fetch(`/api/server/stats?session_id=${detailSessionId}`);
    if (r.status === 404) {
        closeDetail();
        return;
    }
    if (!r.ok || r.status === 204) return; // server still present but no psutil data yet
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

// ── Import world form ──────────────────────────────────────────────

function toggleImportWorld() {
    const form = document.getElementById('import-world-form');
    const open = form.classList.toggle('open');
    if (open) {
        document.getElementById('new-world-form').classList.remove('open');
        loadImportVersions();
        document.getElementById('import-world-name').focus();
    } else {
        document.getElementById('import-world-name').value = '';
        document.getElementById('import-world-file').value = '';
        updateImportBtn();
    }
}

async function loadImportVersions() {
    const snapshots = document.getElementById('import-world-snapshots').checked;
    const r = await fetch(`/api/versions?snapshots=${snapshots}`);
    const sel = document.getElementById('import-world-version');
    if (!r.ok) {
        sel.innerHTML = '<option disabled selected>Unavailable</option>';
        updateImportBtn();
        return;
    }
    const versions = await r.json();
    sel.innerHTML = versions.map(v => `<option value="${v}">${v}</option>`).join('');
    updateImportBtn();
}

function updateImportBtn() {
    const name = document.getElementById('import-world-name').value.trim();
    const file = document.getElementById('import-world-file').files[0];
    const versionSel = document.getElementById('import-world-version');
    const versionOk = versionSel.value && !versionSel.options[versionSel.selectedIndex]?.disabled;
    document.getElementById('import-btn').disabled = !name || !file || !versionOk;
}

async function importWorld() {
    const name = document.getElementById('import-world-name').value.trim();
    const version = document.getElementById('import-world-version').value;
    const file = document.getElementById('import-world-file').files[0];
    if (!name || !version || !file) return;
    const versionInt = versionMap[version];
    if (versionInt === undefined) { showError(`Version "${version}" is not in the supported version map.`); return; }
    clearError();

    const formData = new FormData();
    formData.append('file', file);
    formData.append('name', name);
    formData.append('version', String(versionInt));
    formData.append('port', '0');

    const indicator = document.getElementById('import-download-indicator');
    const statusText = document.getElementById('import-status-text');
    const importBtn = document.getElementById('import-btn');
    indicator.classList.add('visible');
    statusText.textContent = `Importing ${file.name}…`;
    importBtn.disabled = true;

    const r = await fetch('/api/worlds/import', { method: 'POST', body: formData });
    indicator.classList.remove('visible');
    importBtn.disabled = false;

    if (!r.ok) { showError(await apiErrorDetail(r)); return; }
    toggleImportWorld();
    await Promise.all([loadWorlds(), loadDownloadedVersions()]);
    document.getElementById('world').value = name;
}

// ── World Customization tab ────────────────────────────────────────

let currentCustomizeWorld = null;

async function loadCustomizeWorlds() {
    const r = await fetch('/api/worlds');
    if (!r.ok) return;
    const worlds = await r.json();
    const sel = document.getElementById('customize-world');
    const prev = sel.value;
    if (!worlds.length) {
        sel.innerHTML = '<option disabled selected>No worlds yet</option>';
        document.getElementById('file-browser-link').style.display = 'none';
        document.getElementById('customize-body').style.display = 'none';
        return;
    }
    sel.innerHTML = worlds.map(w => {
        const v = reverseVersionMap[w.version] || String(w.version);
        const loaderStr = w.mod_loader && w.mod_loader !== 'vanilla'
            ? ` · ${w.mod_loader} ${w.mod_loader_version}` : '';
        return `<option value="${escapeHtml(w.name)}">${escapeHtml(w.name)} (${escapeHtml(v + loaderStr)})</option>`;
    }).join('');
    if (prev && worlds.find(w => w.name === prev)) {
        sel.value = prev;
    } else {
        sel.selectedIndex = 0;
    }
    await onCustomizeWorldChange(worlds);
}

async function onCustomizeWorldChange(cachedWorlds) {
    const sel = document.getElementById('customize-world');
    const name = sel.value;
    if (!name || sel.options[sel.selectedIndex]?.disabled) {
        document.getElementById('customize-body').style.display = 'none';
        document.getElementById('file-browser-link').style.display = 'none';
        return;
    }
    currentCustomizeWorld = name;
    const parts = window.location.hostname.split('.');
    const filestashUrl = `https://filestash.${parts.slice(1).join('.')}/files/app_data/${parts[0]}/worlds/${encodeURIComponent(name)}/`;
    const link = document.getElementById('file-browser-link');
    link.href = filestashUrl;
    link.style.display = '';
    document.getElementById('customize-body').style.display = '';

    const worlds = cachedWorlds || await fetch('/api/worlds').then(r => r.json());
    const world = worlds.find(w => w.name === name);
    if (world) {
        const v = reverseVersionMap[world.version] || String(world.version);
        const loaderStr = world.mod_loader && world.mod_loader !== 'vanilla'
            ? ` (${world.mod_loader} ${world.mod_loader_version})` : '';
        document.getElementById('current-jar-label').textContent = `${v}${loaderStr}`;
    }
    await loadServerSettings(name);
}


function toggleJarForm() {
    const form = document.getElementById('change-jar-form');
    const open = form.classList.toggle('open');
    if (open) loadJarVersions();
}

async function loadJarVersions() {
    const snapshots = document.getElementById('jar-snapshots').checked;
    const r = await fetch(`/api/versions?snapshots=${snapshots}`);
    const sel = document.getElementById('jar-version');
    if (!r.ok) {
        sel.innerHTML = '<option disabled selected>Unavailable</option>';
        updateJarApplyBtn();
        return;
    }
    const versions = await r.json();
    sel.innerHTML = versions.map(v => `<option value="${v}">${v}</option>`).join('');
    updateJarApplyBtn();
    if (document.getElementById('jar-loader').value !== 'vanilla') await loadJarLoaderVersions();
}

function onJarMcVersionChange() {
    if (document.getElementById('jar-loader').value !== 'vanilla') loadJarLoaderVersions();
    updateJarApplyBtn();
}

async function onJarLoaderChange() {
    const loader = document.getElementById('jar-loader').value;
    const row = document.getElementById('jar-loader-version-row');
    if (loader === 'vanilla') {
        row.style.display = 'none';
        updateJarApplyBtn();
        return;
    }
    row.style.display = '';
    await loadJarLoaderVersions();
}

async function loadJarLoaderVersions() {
    const loader = document.getElementById('jar-loader').value;
    if (loader === 'vanilla') return;
    const mcVersion = document.getElementById('jar-version').value;
    const sel = document.getElementById('jar-loader-version');
    if (!mcVersion || document.getElementById('jar-version').selectedIndex < 0) {
        sel.innerHTML = '<option disabled selected>Select MC version first</option>';
        updateJarApplyBtn();
        return;
    }
    sel.innerHTML = '<option disabled selected>Loading…</option>';
    updateJarApplyBtn();
    const r = await fetch(`/api/modloader/versions?loader=${encodeURIComponent(loader)}&mc_version=${encodeURIComponent(mcVersion)}`);
    if (!r.ok) {
        sel.innerHTML = '<option disabled selected>Error loading versions</option>';
        updateJarApplyBtn();
        return;
    }
    const versions = await r.json();
    if (!versions.length) {
        sel.innerHTML = '<option disabled selected>Not available for this MC version</option>';
    } else {
        sel.innerHTML = versions.map(v => `<option value="${v}">${v}</option>`).join('');
    }
    updateJarApplyBtn();
}

function updateJarApplyBtn() {
    const versionSel = document.getElementById('jar-version');
    const loader = document.getElementById('jar-loader').value;
    const loaderVerSel = document.getElementById('jar-loader-version');
    const loaderOk = loader === 'vanilla' ||
        (loaderVerSel.value && !loaderVerSel.options[loaderVerSel.selectedIndex]?.disabled);
    document.getElementById('jar-apply-btn').disabled = !versionSel.value || !loaderOk;
}

async function applyJarChange() {
    if (!currentCustomizeWorld) return;
    const mcVersion = document.getElementById('jar-version').value;
    const loader = document.getElementById('jar-loader').value;
    const loaderVersion = loader !== 'vanilla' ? document.getElementById('jar-loader-version').value : '';
    if (!mcVersion || (loader !== 'vanilla' && !loaderVersion)) return;
    const versionInt = versionMap[mcVersion];
    if (versionInt === undefined) { showError(`Version "${mcVersion}" not in version map.`); return; }

    const indicator = document.getElementById('jar-download-indicator');
    const statusText = document.getElementById('jar-download-status-text');
    const applyBtn = document.getElementById('jar-apply-btn');
    const loaderLabel = loader === 'vanilla' ? '' :
        ` + ${loader === 'neoforge' ? 'NeoForge' : loader.charAt(0).toUpperCase() + loader.slice(1)} ${loaderVersion}`;
    indicator.classList.add('visible');
    statusText.textContent = `Updating to ${mcVersion}${loaderLabel}… (this may take a few minutes)`;
    applyBtn.disabled = true;
    clearError();

    const r = await fetch(`/api/worlds/${encodeURIComponent(currentCustomizeWorld)}/jar`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({version: versionInt, mod_loader: loader, mod_loader_version: loaderVersion}),
    });
    indicator.classList.remove('visible');
    applyBtn.disabled = false;
    if (!r.ok) { showError(await apiErrorDetail(r)); return; }

    await Promise.all([loadWorlds(), loadDownloadedVersions()]);
    document.getElementById('change-jar-form').classList.remove('open');
    await loadCustomizeWorlds();
}

async function loadServerSettings(name) {
    const r = await fetch(`/api/worlds/${encodeURIComponent(name)}/config`);
    if (!r.ok) return;
    const cfg = await r.json();
    document.getElementById('cfg-motd').value = cfg['motd'] ?? '';
    document.getElementById('cfg-max-players').value = cfg['max-players'] ?? '20';
    document.getElementById('cfg-difficulty').value = cfg['difficulty'] ?? 'easy';
    document.getElementById('cfg-gamemode').value = cfg['gamemode'] ?? 'survival';
    document.getElementById('cfg-view-distance').value = cfg['view-distance'] ?? '10';
    document.getElementById('cfg-sim-distance').value = cfg['simulation-distance'] ?? '10';
    document.getElementById('cfg-spawn-protection').value = cfg['spawn-protection'] ?? '16';
    document.getElementById('cfg-pvp').checked = cfg['pvp'] !== 'false';
    document.getElementById('cfg-allow-flight').checked = cfg['allow-flight'] === 'true';
    document.getElementById('cfg-allow-nether').checked = cfg['allow-nether'] !== 'false';
}

async function saveServerSettings() {
    if (!currentCustomizeWorld) return;
    clearError();
    const cfg = {
        'motd': document.getElementById('cfg-motd').value,
        'max-players': document.getElementById('cfg-max-players').value,
        'difficulty': document.getElementById('cfg-difficulty').value,
        'gamemode': document.getElementById('cfg-gamemode').value,
        'view-distance': document.getElementById('cfg-view-distance').value,
        'simulation-distance': document.getElementById('cfg-sim-distance').value,
        'spawn-protection': document.getElementById('cfg-spawn-protection').value,
        'pvp': document.getElementById('cfg-pvp').checked ? 'true' : 'false',
        'allow-flight': document.getElementById('cfg-allow-flight').checked ? 'true' : 'false',
        'allow-nether': document.getElementById('cfg-allow-nether').checked ? 'true' : 'false',
    };
    const saveBtn = document.getElementById('cfg-save-btn');
    saveBtn.disabled = true;
    saveBtn.textContent = 'Saving…';
    const r = await fetch(`/api/worlds/${encodeURIComponent(currentCustomizeWorld)}/config`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(cfg),
    });
    saveBtn.disabled = false;
    saveBtn.textContent = 'Save settings';
    if (!r.ok) { showError(await apiErrorDetail(r)); return; }
    saveBtn.textContent = 'Saved!';
    setTimeout(() => { saveBtn.textContent = 'Save settings'; }, 2000);
}

// ── Init ───────────────────────────────────────────────────────────

loadVersionMap();
loadNewWorldVersions();
loadAvailableVersions();
loadDownloadedVersions();
loadDownloadedJavaVersions();
loadWorlds();
refreshServers();

const _serverEvents = new EventSource('/api/servers/events');
_serverEvents.onmessage = (e) => {
    const servers = JSON.parse(e.data);
    console.log('[SSE] servers update', servers);

    if (detailSessionId !== null) {
        const current = servers.find(s => s.session_id === detailSessionId);
        if (current && current.status === 'crashed' && detailPollTimer !== null) {
            _setDetailCrashed();
        }
    }

    const json = JSON.stringify(servers);
    if (json === lastServersJson) return;
    lastServersJson = json;
    const list = document.getElementById('server-list');
    if (!servers.length) {
        list.innerHTML = '<div class="empty">No servers running.</div>';
        return;
    }
    list.innerHTML = servers.map(renderServerCard).join('');
};
_serverEvents.onerror = (e) => console.warn('[SSE] connection error', e);
