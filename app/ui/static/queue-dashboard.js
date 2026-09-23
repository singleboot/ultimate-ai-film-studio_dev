// Queue dashboard + badge — shared by / and /timeline (single source of truth).
// Extracted from the duplicated inline blocks; uses global notify/showConfirm.
// ================= ComfyUI queue dashboard =================
let comfyQueueTimer = null;

async function openQueueDashboard() {
    const modal = document.getElementById('comfy-queue-modal');
    if (!modal) return;
    modal.classList.add('active');
    await refreshQueueDashboard();
    if (comfyQueueTimer) clearInterval(comfyQueueTimer);
    comfyQueueTimer = setInterval(refreshQueueDashboard, 2000);   // live refresh while open
}

function closeQueueDashboard() {
    const modal = document.getElementById('comfy-queue-modal');
    if (modal) modal.classList.remove('active');
    if (comfyQueueTimer) { clearInterval(comfyQueueTimer); comfyQueueTimer = null; }
}

async function refreshQueueDashboard() {
    const listEl = document.getElementById('comfy-queue-list');
    const countEl = document.getElementById('comfy-queue-count');
    let data;
    try {
        const r = await fetch('/api/comfyui/queue');
        data = await r.json();
    } catch (e) {
        if (listEl) listEl.innerHTML = '<div style="color:#e74c3c; padding:8px;">ComfyUI unreachable: ' + e.message + '</div>';
        return;
    }
    const n = (data.running || []).length + (data.pending || []).length;
    if (countEl) {
        countEl.textContent = n;
        countEl.style.display = n > 0 ? 'inline-block' : 'none';
    }
    if (!listEl) return;
    if (!data.connected) {
        listEl.innerHTML = '<div style="color:#e74c3c; padding:8px;">⚠️ ComfyUI is not reachable.</div>';
        return;
    }
    if (n === 0) {
        listEl.innerHTML = '<div style="color:var(--text-secondary); padding:8px;">Queue is empty — nothing running or pending.</div>';
        return;
    }
    const row = (job, badge, badgeColor) => `
        <div style="display:flex; align-items:center; gap:8px; padding:8px 10px; border:1px solid var(--border-color); border-radius:6px; margin-bottom:6px; background:rgba(255,255,255,0.03);">
            <span style="font-size:0.65rem; font-weight:700; padding:2px 8px; border-radius:10px; background:${badgeColor}; color:#fff;">${badge}</span>
            <div style="flex:1; min-width:0;">
                <div style="white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${job.label || 'ComfyUI job'}</div>
                <div style="font-size:0.68rem; color:var(--text-secondary); font-family:monospace;">${(job.prompt_id || '').slice(0, 18)}…</div>
            </div>
            <button class="btn btn-secondary" onclick="cancelComfyJob('${job.prompt_id}', this)" style="padding:3px 10px; font-size:0.72rem; background:#c0392b; color:#fff; border:none;">✕ Cancel</button>
        </div>`;
    listEl.innerHTML =
        (data.running || []).map(j => row(j, 'RUNNING', '#2980b9')).join('') +
        (data.pending || []).map(j => row(j, 'PENDING', '#8e44ad')).join('');
}

async function cancelComfyJob(promptId, btn) {
    const ok = await showConfirm('Cancel this job?', 'The job will be removed from ComfyUI\'s queue (the running one is interrupted immediately).');
    if (!ok) return;
    try {
        const r = await fetch('/api/comfyui/queue/cancel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt_id: promptId })
        });
        const d = await r.json();
        if (d.success) notify('Job cancelled (' + d.action + ')', '', '✅');
        else notify('Could not cancel', d.error || 'Job already finished or removed', '❌');
    } catch (e) {
        notify('Cancel failed', e.message, '❌');
    }
    refreshQueueDashboard();
}

async function pollQueueBadge() {
    try {
        const r = await fetch('/api/comfyui/queue');
        const d = await r.json();
        const countEl = document.getElementById('comfy-queue-count');
        if (!countEl) return;
        const n = ((d.running || []).length + (d.pending || []).length);
        countEl.textContent = n;
        countEl.style.display = n > 0 ? 'inline-block' : 'none';
    } catch (e) { /* ComfyUI down — leave badge as-is */ }
}
setInterval(pollQueueBadge, 5000);
