// QC Dashboard
var _qcDashboardData = { shots: [], scenes: [], stats: {}, pipeline: {}, rubric: {} };
var _qcFilter = 'all';

function toggleQCStats() {
    var panel = document.getElementById('qc-stats-panel');
    if (panel) {
        panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
    }
}

function loadQCDashboard() {
    fetch('/api/orchestrator/qc-dashboard')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (!data.success) return;
            _qcDashboardData = data;
            renderQCDashboard();
        })
        .catch(function(e) { console.error('QC load failed:', e); });
}

function renderQCDashboard() {
    var s = _qcDashboardData.stats || {};
    var p = _qcDashboardData.pipeline || {};
    var r = _qcDashboardData.rubric || {};

    // Stats
    document.getElementById('qc-stat-total').textContent = s.total || 0;
    document.getElementById('qc-stat-approved').textContent = s.approved || 0;
    document.getElementById('qc-stat-passed').textContent = s.qc_passed || 0;
    document.getElementById('qc-stat-failed').textContent = s.qc_failed || 0;
    document.getElementById('qc-stat-pending').textContent = s.pending || 0;
    var pct = s.completion_pct || 0;
    document.getElementById('qc-pct-badge').textContent = pct + '%';
    document.getElementById('qc-progress-bar').style.width = pct + '%';

    var summary = (s.total || 0) + ' shots';
    if (s.approved) summary += ' \u00b7 ' + s.approved + ' approved';
    if (s.qc_passed) summary += ' \u00b7 ' + s.qc_passed + ' passed';
    if (s.qc_failed) summary += ' \u00b7 ' + s.qc_failed + ' failed';
    document.getElementById('qc-summary-line').textContent = summary;

    // Scene filters
    var fc = document.getElementById('qc-scene-filters');
    fc.innerHTML = '';
    (_qcDashboardData.scenes || []).forEach(function(sc) {
        var b = document.createElement('button');
        b.className = 'btn btn-secondary qc-filter' + (_qcFilter === sc.scene_id ? ' active' : '');
        b.style.cssText = 'padding:2px 8px;font-size:0.65rem;';
        b.textContent = sc.scene_title || sc.scene_id;
        b.onclick = (function(sid) { return function() { filterQCShots(sid, b); }; })(sc.scene_id);
        fc.appendChild(b);
    });

    renderPipeline(p);
    renderRubric(r);
    renderQCShots();
}

// --- Pipeline Progress ---
function renderPipeline(p) {
    var container = document.getElementById('qc-pipeline-steps');
    if (!container) return;

    var steps = [
        { key: 'ideas', label: 'Ideas', icon: '💡', done: !!p.ideas },
        { key: 'screenplay', label: 'Screenplay', icon: '📜', done: !!p.screenplay },
        { key: 'characters', label: 'Characters', icon: '👤', count: p.characters || 0, done: (p.characters || 0) > 0 },
        { key: 'locations', label: 'Locations', icon: '🏠', count: p.locations || 0, done: (p.locations || 0) > 0 },
        { key: 'scenes', label: 'Scenes', icon: '🎬', count: p.scenes || 0, done: (p.scenes || 0) > 0 },
        { key: 'storyboards', label: 'Storyboards', icon: '🎨', count: p.shots_generated || 0, total: p.shots_total || 0, done: (p.shots_generated || 0) > 0 },
        { key: 'qc', label: 'QC Review', icon: '🎯', count: p.shots_approved || 0, total: p.shots_total || 0, done: (p.shots_approved || 0) === (p.shots_total || 0) && (p.shots_total || 0) > 0 }
    ];

    var h = '';
    steps.forEach(function(step, i) {
        var color = step.done ? '#4caf50' : (step.count ? '#ff9800' : '#555');
        var bg = step.done ? 'rgba(76,175,80,0.15)' : (step.count ? 'rgba(255,152,0,0.1)' : 'rgba(255,255,255,0.03)');
        var countText = '';
        if (step.total !== undefined) {
            countText = '<div style="font-size:0.6rem;color:' + color + ';margin-top:2px;">' + (step.count || 0) + '/' + step.total + '</div>';
        } else if (step.count !== undefined && step.count > 0) {
            countText = '<div style="font-size:0.6rem;color:' + color + ';margin-top:2px;">' + step.count + '</div>';
        }
        h += '<div style="display:flex;flex-direction:column;align-items:center;flex:1;min-width:80px;position:relative;">';
        if (i > 0) {
            h += '<div style="position:absolute;top:14px;right:50%;width:100%;height:2px;background:' + (step.done ? '#4caf50' : '#333') + ';z-index:0;"></div>';
        }
        h += '<div style="width:28px;height:28px;border-radius:50%;background:' + bg + ';border:2px solid ' + color + ';display:flex;align-items:center;justify-content:center;font-size:0.8rem;z-index:1;">' + step.icon + '</div>';
        h += '<div style="font-size:0.65rem;color:' + color + ';margin-top:4px;font-weight:600;z-index:1;">' + step.label + '</div>';
        h += countText;
        h += '</div>';
    });
    container.innerHTML = h;
}

// --- Scoring Rubric ---
function renderRubric(r) {
    var container = document.getElementById('qc-rubric-criteria');
    if (!container || !r.criteria) return;

    document.getElementById('qc-rubric-threshold').textContent = r.pass_threshold || 7;
    var autoRetry = document.getElementById('qc-auto-retry');
    if (autoRetry) autoRetry.checked = r.auto_retry !== false;
    var maxRetries = document.getElementById('qc-max-retries');
    if (maxRetries) maxRetries.value = r.max_retries || 3;
    var passThreshold = document.getElementById('qc-pass-threshold');
    if (passThreshold) passThreshold.value = r.pass_threshold || 7;

    var h = '';
    r.criteria.forEach(function(c) {
        h += '<div style="display:flex;align-items:center;gap:8px;padding:4px 8px;background:var(--bg-primary);border-radius:6px;">';
        h += '<input type="checkbox" ' + (c.enabled ? 'checked' : '') + ' onchange="toggleQCCriterion(\'' + c.id + '\', this.checked)" style="cursor:pointer;">';
        h += '<span style="flex:1;font-size:0.75rem;color:var(--text-primary);font-weight:500;">' + c.label + '</span>';
        h += '<input type="range" min="0" max="50" value="' + c.weight + '" style="width:80px;accent-color:var(--accent-primary);" onchange="updateQCWeight(\'' + c.id + '\', this.value)" title="Weight: ' + c.weight + '%">';
        h += '<span style="font-size:0.7rem;color:var(--accent-primary);font-weight:600;min-width:30px;text-align:right;">' + c.weight + '%</span>';
        h += '<span style="font-size:0.6rem;color:var(--text-secondary);cursor:help;" title="' + (c.desc || '') + '">ℹ️</span>';
        h += '</div>';
    });
    container.innerHTML = h;
}

function toggleQCCriterion(id, enabled) {
    var r = _qcDashboardData.rubric;
    if (!r || !r.criteria) return;
    r.criteria.forEach(function(c) { if (c.id === id) c.enabled = enabled; });
    saveQCRubric();
}

function updateQCWeight(id, value) {
    var r = _qcDashboardData.rubric;
    if (!r || !r.criteria) return;
    r.criteria.forEach(function(c) { if (c.id === id) { c.weight = parseInt(value); } });
    // Update the label
    renderRubric(r);
    saveQCRubric();
}

function saveQCRubric() {
    var r = _qcDashboardData.rubric;
    if (!r) return;
    var autoRetry = document.getElementById('qc-auto-retry');
    var maxRetries = document.getElementById('qc-max-retries');
    var passThreshold = document.getElementById('qc-pass-threshold');
    r.auto_retry = autoRetry ? autoRetry.checked : true;
    r.max_retries = maxRetries ? parseInt(maxRetries.value) || 3 : 3;
    r.pass_threshold = passThreshold ? parseInt(passThreshold.value) || 7 : 7;

    fetch('/api/orchestrator/qc-rubric', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rubric: r })
    }).then(function(res) { return res.json(); })
      .then(function(d) { if (d.success) { document.getElementById('qc-rubric-threshold').textContent = r.pass_threshold; } })
      .catch(function(e) { console.error('Failed to save rubric:', e); });
}

// --- Shot Grid ---
function filterQCShots(f, btn) {
    _qcFilter = f;
    document.querySelectorAll('.qc-filter').forEach(function(b) { b.classList.remove('active'); });
    if (btn) btn.classList.add('active');
    renderQCShots();
}

function renderQCShots() {
    var grid = document.getElementById('qc-shot-grid');
    var shots = _qcDashboardData.shots || [];

    // Empty state
    if (!shots.length) {
        var emptyEl = document.getElementById('qc-empty-state');
        if (!emptyEl) {
            grid.innerHTML = '<div id="qc-empty-state" style="text-align:center;padding:30px 20px;color:var(--text-secondary);grid-column:1/-1;">'
                + '<div style="font-size:1.5rem;margin-bottom:8px;">🎬</div>'
                + '<div style="font-size:0.85rem;font-weight:600;color:var(--text-primary);margin-bottom:4px;">No shots to evaluate yet</div>'
                + '<div style="font-size:0.75rem;max-width:400px;margin:0 auto;">Generate storyboard images from the <b>Storyboard & Video</b> page. Each generated shot will be automatically evaluated against your scoring rubric above.</div>'
                + '<div style="margin-top:12px;display:flex;gap:8px;justify-content:center;">'
                + '<button onclick="navigateToPage(\'storyboard\')" class="btn btn-primary" style="padding:6px 14px;font-size:0.75rem;">📋 Go to Storyboard</button>'
                + '<button onclick="document.getElementById(\'qc-rubric-section\').scrollIntoView({behavior:\'smooth\'})" class="btn btn-secondary" style="padding:6px 14px;font-size:0.75rem;">📐 Edit Rubric</button>'
                + '</div></div>';
        }
        return;
    }

    if (_qcFilter !== 'all') shots = shots.filter(function(s) { return s.scene_id === _qcFilter; });
    if (!shots.length) { grid.innerHTML = '<div style="text-align:center;padding:8px 0;color:var(--text-secondary);font-size:0.75rem;grid-column:1/-1;">No shots found.</div>'; return; }

    var h = '';
    shots.forEach(function(shot) {
        var c = shot.approved ? '#4caf50' : shot.qc_passed ? '#2196f3' : shot.qc_score > 0 ? '#f44336' : '#ff9800';
        var t = shot.approved ? 'Approved' : shot.qc_passed ? 'QC Passed' : shot.qc_score > 0 ? 'QC Failed' : 'Pending';
        var sb = shot.qc_score > 0 ? '<div style="display:flex;align-items:center;gap:6px;margin-top:4px;"><div style="flex:1;background:var(--bg-primary);border-radius:3px;height:4px;"><div style="height:100%;width:' + (shot.qc_score*10) + '%;background:' + c + ';border-radius:3px;"></div></div><span style="font-size:0.7rem;color:' + c + ';font-weight:600;">' + shot.qc_score + '/10</span></div>' : '';
        var fb = shot.qc_feedback ? '<div style="font-size:0.7rem;color:var(--text-secondary);margin-top:4px;font-style:italic;">' + shot.qc_feedback.substring(0,80) + (shot.qc_feedback.length>80?'...':'') + '</div>' : '';
        var img = shot.generated_image ? '<img src="' + getAssetImageSrc(shot.generated_image, true) + '" style="width:100%;height:100%;object-fit:cover;"/>' : '<div style="color:var(--text-secondary);">No Image</div>';
        h += '<div class="qc-shot-card" style="background:var(--bg-secondary);border-radius:8px;overflow:hidden;border:1px solid var(--border-color);cursor:pointer;" onclick="openQCDetail(\'' + shot.shot_id + '\')">'
            + '<div style="background:#1a1a2e;height:140px;display:flex;align-items:center;justify-content:center;position:relative;">' + img
            + '<div style="position:absolute;top:6px;right:6px;background:rgba(0,0,0,0.7);padding:2px 8px;border-radius:10px;font-size:0.65rem;color:' + c + ';font-weight:600;">' + t + '</div>'
            + '<div style="position:absolute;top:6px;left:6px;background:rgba(0,0,0,0.7);padding:2px 8px;border-radius:10px;font-size:0.65rem;color:var(--text-primary);">' + shot.shot_id + '</div></div>'
            + '<div style="padding:10px;"><div style="font-size:0.75rem;color:var(--accent-primary);font-weight:600;margin-bottom:2px;">' + (shot.scene_title || shot.scene_id) + '</div>'
            + '<div style="font-size:0.7rem;color:var(--text-secondary);margin-bottom:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + (shot.shot_type || shot.description || '') + '</div>'
            + sb + fb + '</div></div>';
    });
    grid.innerHTML = h;
}

function openQCDetail(shotId) {
    var shot = (_qcDashboardData.shots||[]).find(function(s) { return s.shot_id === shotId; });
    if (!shot) return;
    document.getElementById('qc-detail-title').textContent = shot.shot_id + ' - ' + (shot.scene_title||shot.scene_id);
    var c = shot.approved?'#4caf50':shot.qc_passed?'#2196f3':shot.qc_score>0?'#f44336':'#ff9800';
    var t = shot.approved?'Approved':shot.qc_passed?'QC Passed':shot.qc_score>0?'QC Failed':'Pending';
    var h = '<div style="display:flex;gap:16px;margin-bottom:16px;flex-wrap:wrap;">'
        + '<div style="flex:1;min-width:200px;background:#1a1a2e;border-radius:8px;height:250px;display:flex;align-items:center;justify-content:center;overflow:hidden;">'
        + (shot.generated_image ? '<img src="' + getAssetImageSrc(shot.generated_image) + '" style="width:100%;height:100%;object-fit:contain;" />' : '<div style="color:var(--text-secondary);">No Image</div>')
        + '</div><div style="flex:1;min-width:200px;">'
        + '<div style="margin-bottom:10px;"><b>Status:</b> <span style="color:' + c + ';">' + t + '</span></div>'
        + '<div style="margin-bottom:10px;"><b>Shot Type:</b> ' + (shot.shot_type||'N/A') + '</div>'
        + '<div style="margin-bottom:10px;"><b>Description:</b> ' + (shot.description||'N/A') + '</div>'
        + '<div style="margin-bottom:10px;"><b>Feedback:</b> <i>' + (shot.qc_feedback||'None') + '</i></div>'
        + '</div></div>';
    // Sub-scores breakdown
    var subScores = shot.qc_sub_scores || {};
    if (Object.keys(subScores).length > 0) {
        var rubric = (_qcDashboardData.rubric || {}).criteria || [];
        h += '<div style="margin-bottom:12px;padding:10px;background:var(--bg-secondary);border-radius:8px;">';
        h += '<div style="font-size:0.75rem;font-weight:600;color:var(--accent-primary);margin-bottom:6px;">📐 Score Breakdown</div>';
        rubric.forEach(function(cr) {
            var sv = subScores[cr.id] || 0;
            var sc = sv >= 7 ? '#4caf50' : sv >= 5 ? '#ff9800' : '#f44336';
            h += '<div style="display:flex;align-items:center;gap:8px;margin-bottom:3px;">';
            h += '<span style="font-size:0.7rem;color:var(--text-secondary);min-width:140px;">' + cr.label + '</span>';
            h += '<div style="flex:1;background:var(--bg-primary);border-radius:3px;height:4px;"><div style="height:100%;width:' + (sv*10) + '%;background:' + sc + ';border-radius:3px;"></div></div>';
            h += '<span style="font-size:0.7rem;color:' + sc + ';font-weight:600;min-width:25px;text-align:right;">' + sv + '/10</span>';
            h += '</div>';
        });
        h += '</div>';
    }
    h += '<div style="display:flex;gap:8px;flex-wrap:wrap;">'
        + '<button class="btn btn-primary" onclick="qcAction(\'' + shot.shot_id + '\',\'approve\')" style="padding:8px 16px;">✅ Approve</button>'
        + '<button class="btn btn-secondary" onclick="qcAction(\'' + shot.shot_id + '\',\'reject\',prompt(\'Reason:\'))" style="padding:8px 16px;border:1px solid #f44336;color:#f44336;">❌ Reject</button>'
        + '<button class="btn btn-secondary" onclick="qcAction(\'' + shot.shot_id + '\',\'regenerate\',prompt(\'Feedback:\'))" style="padding:8px 16px;border:1px solid #ff9800;color:#ff9800;">🔄 Regen</button>'
        + '</div>';
    document.getElementById('qc-detail-content').innerHTML = h;
    document.getElementById('qc-detail-modal').style.display = 'flex';
}

function closeQCDetail() { document.getElementById('qc-detail-modal').style.display = 'none'; }

function qcAction(shotId, action, fb) {
    fetch('/api/orchestrator/shot-qc-update', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({shot_id: shotId, action: action, feedback: fb || ''})
    })
    .then(function(r) { return r.json(); })
    .then(function(d) {
        if (d.success) { closeQCDetail(); loadQCDashboard(); }
        else { alert('Error: ' + (d.error || 'Unknown')); }
    })
    .catch(function(e) { alert('Failed: ' + e.message); });
}
