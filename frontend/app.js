/* Job Autopilot dashboard. */

const API = '/api';

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

let filtersConfig = { locations: [] };
let currentJobs = [];
let currentLocationId = 'all';
let currentSearch = '';
let currentRecFilter = 'apply_consider';
let currentSort = 'score';

// ---------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------

function fmtRelative(iso) {
  if (!iso) return '—';
  const date = new Date(iso);
  const diffMs = Date.now() - date.getTime();
  const diffSec = Math.round(diffMs / 1000);
  if (diffSec < 60) return 'just now';
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin} min ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr} hr ago`;
  const diffDay = Math.round(diffHr / 24);
  if (diffDay < 30) return `${diffDay} day${diffDay > 1 ? 's' : ''} ago`;
  return date.toLocaleDateString();
}

function escapeHtml(str) {
  if (str == null) return '';
  return String(str)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function formatLocations(loc) {
  if (!loc) return '—';
  const parts = loc.split(/;\s*/).map(s => s.trim()).filter(Boolean);
  if (parts.length === 1) return escapeHtml(parts[0]);
  return `
    <div>${escapeHtml(parts[0])}</div>
    <div class="loc-extra">+ ${parts.length - 1} more: ${escapeHtml(parts.slice(1).join(' · '))}</div>
  `;
}

function showToast(message, type = 'info', durationMs = 3000) {
  const toast = $('#toast');
  toast.textContent = message;
  toast.className = `toast ${type}`;
  toast.hidden = false;
  setTimeout(() => { toast.hidden = true; }, durationMs);
}

async function apiGet(path) {
  const r = await fetch(API + path);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

async function apiPost(path) {
  const r = await fetch(API + path, { method: 'POST' });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

function scoreBadge(score, recommendation) {
  if (score == null) {
    return '<span class="score-badge score-pending">— pending</span>';
  }
  const rec = (recommendation || '').toLowerCase();
  return `<span class="score-badge score-${rec}">${score} <span class="score-rec">${rec.toUpperCase()}</span></span>`;
}

function topScoreBadge(score, recommendation) {
  if (score == null) return '<span class="muted">— pending</span>';
  const rec = (recommendation || '').toLowerCase();
  return `<span class="score-badge score-${rec}">${score} <span class="score-rec">${rec.toUpperCase()}</span></span>`;
}

// ---------------------------------------------------------------------
// Status (application) helpers
// ---------------------------------------------------------------------

const STATUS_META = {
  interested: { label: 'Interested', icon: '★', cls: 'status-interested' },
  applied:    { label: 'Applied',    icon: '✓', cls: 'status-applied' },
  interview:  { label: 'Interview',  icon: '📞', cls: 'status-interview' },
  offer:      { label: 'Offer',      icon: '🎉', cls: 'status-offer' },
  rejected:   { label: 'Rejected',   icon: '✗', cls: 'status-rejected' },
};

function statusPill(application, jobId) {
  if (!application) {
    return `
      <button class="status-pill status-untracked" data-job-id="${escapeHtml(jobId)}" title="Set status">
        <span class="status-text">Mark</span>
        <span class="status-caret">▾</span>
      </button>
    `;
  }
  const meta = STATUS_META[application.status] || STATUS_META.applied;
  const dateBit = application.applied_at
    ? ` <span class="status-date">(${shortDate(application.applied_at)})</span>`
    : '';
  return `
    <button class="status-pill ${meta.cls}" data-job-id="${escapeHtml(jobId)}" title="Edit status">
      <span class="status-icon">${meta.icon}</span>
      <span class="status-text">${meta.label}${dateBit}</span>
      <span class="status-caret">▾</span>
    </button>
  `;
}

function shortDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

async function setStatus(jobId, status, note = '') {
  if (status === '__clear__') {
    const r = await fetch(API + `/applications/${encodeURIComponent(jobId)}`, {
      method: 'DELETE',
    });
    if (!r.ok && r.status !== 404) {
      throw new Error(`Delete failed: ${r.status}`);
    }
    return null;
  }
  const body = { status, note };
  const r = await fetch(API + `/applications/${encodeURIComponent(jobId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const txt = await r.text();
    throw new Error(`Update failed: ${r.status} ${txt}`);
  }
  return r.json();
}

// Popup menu — created lazily, only one at a time
let activeMenu = null;

function closeStatusMenu() {
  if (activeMenu) {
    activeMenu.remove();
    activeMenu = null;
  }
}

function openStatusMenu(pillBtn) {
  closeStatusMenu();
  const jobId = pillBtn.dataset.jobId;
  const rect = pillBtn.getBoundingClientRect();

  const menu = document.createElement('div');
  menu.className = 'status-menu';
  menu.innerHTML = `
    <button data-status="interested"><span>★</span> Interested</button>
    <button data-status="applied"><span>✓</span> Applied</button>
    <button data-status="interview"><span>📞</span> Interview</button>
    <button data-status="offer"><span>🎉</span> Offer</button>
    <button data-status="rejected"><span>✗</span> Rejected</button>
    <div class="status-menu-sep"></div>
    <button data-status="__clear__" class="status-menu-clear">Clear status</button>
  `;
  menu.style.position = 'fixed';
  menu.style.top = `${rect.bottom + 4}px`;
  menu.style.left = `${rect.left}px`;
  document.body.appendChild(menu);
  activeMenu = menu;

  menu.querySelectorAll('button').forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const status = btn.dataset.status;
      closeStatusMenu();
      try {
        await setStatus(jobId, status);
        // Update the local job in memory so the row re-renders correctly
        const job = currentJobs.find((j) => j.id === jobId);
        if (job) {
          if (status === '__clear__') {
            job.application = null;
          } else {
            const now = new Date().toISOString();
            const existing = job.application || {};
            job.application = {
              ...existing,
              job_id: jobId,
              status,
              applied_at: existing.applied_at ||
                (status === 'applied' ? now : existing.applied_at),
              updated_at: now,
            };
          }
        }
        rerenderJobsTable();
        showToast(`Status: ${status === '__clear__' ? 'cleared' : status}`, 'success', 1500);
      } catch (err) {
        showToast(`Failed: ${err.message}`, 'error', 4000);
      }
    });
  });
}

// Click outside closes menu
document.addEventListener('click', (e) => {
  if (
    activeMenu &&
    !activeMenu.contains(e.target) &&
    !(e.target.closest && e.target.closest('.status-pill'))
  ) {
    closeStatusMenu();
  }
});

// Escape closes menu
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeStatusMenu();
});

// ---------------------------------------------------------------------
// Filtering / sorting
// ---------------------------------------------------------------------

function jobMatchesLocationFilter(job, locationId) {
  if (locationId === 'all') return true;
  const filter = filtersConfig.locations.find((f) => f.id === locationId);
  if (!filter) return true;
  const loc = (job.location || '').toLowerCase();
  return filter.matchAny.some((needle) => loc.includes(needle.toLowerCase()));
}

function jobMatchesSearch(job, query) {
  if (!query) return true;
  const q = query.toLowerCase();
  return (
    (job.title || '').toLowerCase().includes(q) ||
    (job.location || '').toLowerCase().includes(q)
  );
}

function jobMatchesRecFilter(job, mode) {
  if (mode === 'all') return true;
  if (job.score == null) return mode === 'all';
  if (mode === 'apply') return job.recommendation === 'apply';
  if (mode === 'apply_consider') {
    return job.recommendation === 'apply' || job.recommendation === 'consider';
  }
  return true;
}

function applyFilters() {
  let filtered = currentJobs.filter(
    (j) =>
      jobMatchesLocationFilter(j, currentLocationId) &&
      jobMatchesSearch(j, currentSearch) &&
      jobMatchesRecFilter(j, currentRecFilter),
  );
  if (currentSort === 'score') {
    filtered.sort((a, b) => {
      const aHas = a.score != null ? 0 : 1;
      const bHas = b.score != null ? 0 : 1;
      if (aHas !== bHas) return aHas - bHas;
      return (b.score || 0) - (a.score || 0);
    });
  } else {
    filtered.sort((a, b) =>
      (b.discovered_at || '').localeCompare(a.discovered_at || ''),
    );
  }
  return filtered;
}

function rerenderJobsTable() {
  const filtered = applyFilters();
  $('#jobs-count').textContent =
    filtered.length === currentJobs.length
      ? `${currentJobs.length} jobs`
      : `Showing ${filtered.length} of ${currentJobs.length} jobs`;

  const tbody = $('#jobs-tbody');
  if (!tbody) return;

  if (filtered.length === 0) {
    tbody.innerHTML = `
      <tr><td colspan="7" class="empty" style="padding: 32px;">
        No jobs match your filters.
      </td></tr>`;
    return;
  }

  tbody.innerHTML = filtered.map((j, i) => {
    const safeUrl = escapeHtml(j.url);
    const hasDetail = !!(j.description_text || j.score_summary);
    const detailId = `det-${i}`;
    const expandBtn = hasDetail
      ? `<button class="expand-btn" data-target="${detailId}" title="Show details">+</button>`
      : '';

    let detailRow = '';
    if (hasDetail) {
      const dims = j.dimensions || {};
      const dimsLine = j.score != null
        ? `<div class="match-dims">Skills ${dims.skills_match || 0} · Experience ${dims.experience_level || 0} · Domain ${dims.domain_match || 0} · Role ${dims.role_fit || 0}</div>`
        : '';
      const summary = j.score_summary
        ? `<div class="match-summary">${escapeHtml(j.score_summary)}</div>`
        : '';
      const strengths = (j.strengths && j.strengths.length)
        ? `<div class="match-list-title">Strengths</div>
           <ul class="match-list">
             ${j.strengths.map(s => `<li>${escapeHtml(s)}</li>`).join('')}
           </ul>`
        : '';
      const gaps = (j.gaps && j.gaps.length)
        ? `<div class="match-list-title">Gaps</div>
           <ul class="match-list match-list-gaps">
             ${j.gaps.map(g => `<li>${escapeHtml(g)}</li>`).join('')}
           </ul>`
        : '';
      const matchSection = j.score != null
        ? `<div class="match-section">
             <div class="match-header">Match analysis (scored ${fmtRelative(j.scored_at)})</div>
             ${dimsLine}
             ${summary}
             ${strengths}
             ${gaps}
           </div>`
        : '';
      const jd = j.description_text
        ? `<div class="jd-section">
             <div class="jd-header">Job Description</div>
             <pre class="jd-text">${escapeHtml(j.description_text)}</pre>
           </div>`
        : '';
      detailRow = `
        <tr class="detail-row" id="${detailId}" hidden>
          <td colspan="7" class="detail-cell">
            ${matchSection}
            ${jd}
          </td>
        </tr>`;
    }

    return `
      <tr class="clickable" data-url="${safeUrl}">
        <td class="cell-expand">${expandBtn}</td>
        <td>${scoreBadge(j.score, j.recommendation)}</td>
        <td class="cell-title">${escapeHtml(j.title)}</td>
        <td class="cell-muted location-cell">${formatLocations(j.location)}</td>
        <td class="cell-muted">${escapeHtml(j.posted_on || '—')}</td>
        <td class="cell-status">${statusPill(j.application, j.id)}</td>
        <td class="arrow">↗</td>
      </tr>
      ${detailRow}`;
  }).join('');

  // Expanders
  // Wire up expanders
  $$('#view .expand-btn').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const target = document.getElementById(btn.dataset.target);
      if (target) {
        target.hidden = !target.hidden;
        btn.textContent = target.hidden ? '+' : '−';
      }
    });
  });

  // Status pill clicks (must come before row click handler, and stopPropagation)
  $$('#view .status-pill').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      openStatusMenu(btn);
    });
  });

  // Row clicks - skip if the click was on a button/menu
  $$('#view tr.clickable').forEach((row) => {
    row.addEventListener('click', (e) => {
      if (e.target.closest('.status-pill') || e.target.closest('.expand-btn')) {
        return;
      }
      window.open(row.dataset.url, '_blank', 'noopener,noreferrer');
    });
  });
}

// ---------------------------------------------------------------------
// Page renderers
// ---------------------------------------------------------------------

async function renderCompanies() {
  const view = $('#view');
  const hero = $('#hero');

  view.innerHTML = '<div class="loading">Loading companies…</div>';

  let data;
  try {
    data = await apiGet('/companies');
  } catch (err) {
    view.innerHTML = `<div class="error">Failed to load: ${escapeHtml(err.message)}</div>`;
    return;
  }

  let totalApply = 0;
  let totalConsider = 0;
  let totalSkip = 0;
  for (const c of data.companies) {
    totalApply += c.apply_count || 0;
    totalConsider += c.consider_count || 0;
    totalSkip += c.skip_count || 0;
  }

  hero.innerHTML = `
    <div class="hero-stat">
      <div class="hero-value">${data.total_jobs}</div>
      <div class="hero-label">Total jobs</div>
    </div>
    <div class="hero-divider"></div>
    <div class="hero-stat">
      <div class="hero-value">${data.company_count}</div>
      <div class="hero-label">Companies</div>
    </div>
    <div class="hero-divider"></div>
    <div class="hero-stat">
      <div class="hero-value">
        <span class="rec-apply">${totalApply}</span> ·
        <span class="rec-consider">${totalConsider}</span> ·
        <span class="rec-skip">${totalSkip}</span>
      </div>
      <div class="hero-label">apply / consider / skip</div>
    </div>
    <div class="hero-divider"></div>
    <div class="hero-stat">
      <div class="hero-value">${fmtRelative(data.last_refreshed)}</div>
      <div class="hero-label">Last refreshed</div>
    </div>
  `;

  if (data.companies.length === 0) {
    view.innerHTML = `
      <div class="empty">
        <div class="empty-title">No jobs yet</div>
        <p>Click <strong>Refresh jobs</strong> above to fetch from your configured sources.</p>
      </div>
    `;
    return;
  }

  const rows = data.companies.map((c) => `
    <tr class="clickable" data-company-id="${escapeHtml(c.id)}">
      <td class="cell-title">${escapeHtml(c.name)}</td>
      <td class="num">${c.job_count}</td>
      <td>${topScoreBadge(c.top_score, c.top_recommendation)}</td>
      <td class="cell-muted">${c.apply_count} apply · ${c.consider_count} consider</td>
      <td class="cell-muted">${fmtRelative(c.last_updated)}</td>
      <td class="arrow">→</td>
    </tr>
  `).join('');

  view.innerHTML = `
    <div class="view-header">
      <h2 class="view-title">Companies</h2>
      <span class="view-subtitle">Sorted by top match</span>
    </div>
    <table class="table">
      <thead>
        <tr>
          <th>Company</th>
          <th class="num">Open roles</th>
          <th>Top match</th>
          <th>Recommendations</th>
          <th>Last updated</th>
          <th></th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;

  $$('#view tr.clickable').forEach((row) => {
    row.addEventListener('click', () => {
      window.location.hash = `#/company/${row.dataset.companyId}`;
    });
  });
}

async function renderCompanyJobs(companyId) {
  const view = $('#view');
  const hero = $('#hero');

  hero.innerHTML = '';
  view.innerHTML = '<div class="loading">Loading jobs…</div>';

  currentLocationId = 'all';
  currentSearch = '';

  let data;
  try {
    data = await apiGet(`/companies/${encodeURIComponent(companyId)}/jobs`);
  } catch (err) {
    const backLink = '<a href="#/" class="back-link">' + '← Back to companies' + '</a>';
    view.innerHTML = `
      <div class="error">
        <div>${backLink}</div>
        Failed to load: ${escapeHtml(err.message)}
      </div>`;
    return;
  }

  const { company, jobs } = data;
  currentJobs = jobs;

  const locationOptions = [
    `<option value="all">All locations</option>`,
    ...filtersConfig.locations.map(
      (f) => `<option value="${escapeHtml(f.id)}">${escapeHtml(f.label)}</option>`,
    ),
  ].join('');

  const avgScoreText = data.avg_score != null
    ? `Avg score: <strong>${data.avg_score}</strong> · `
    : '';
  const breakdown = `${data.apply_count} apply · ${data.consider_count} consider · ${data.skip_count} skip${data.unscored_count ? ` · ${data.unscored_count} pending` : ''}`;

  const backLink = '<a href="#/" class="back-link">' + '← Back to companies' + '</a>';
  view.innerHTML = `
    <div class="view-header">
      <div>
        ${backLink}
        <h2 class="view-title">${escapeHtml(company.name)}</h2>
        <span class="view-subtitle">
          ${company.job_count} open role${company.job_count === 1 ? '' : 's'} ·
          updated ${fmtRelative(company.last_updated)}
        </span>
        <div class="view-stats">${avgScoreText}${breakdown}</div>
      </div>
    </div>

    <div class="filters">
      <div class="filter-group">
        <label class="filter-label" for="location-filter">Location</label>
        <select id="location-filter" class="select">${locationOptions}</select>
      </div>
      <div class="filter-group">
        <label class="filter-label" for="rec-filter">Show</label>
        <select id="rec-filter" class="select">
          <option value="apply_consider">Apply + Consider</option>
          <option value="apply">Apply only</option>
          <option value="all">All (including skip)</option>
        </select>
      </div>
      <div class="filter-group">
        <label class="filter-label" for="sort-select">Sort</label>
        <select id="sort-select" class="select">
          <option value="score">Score (highest)</option>
          <option value="posted">Posted date</option>
        </select>
      </div>
      <div class="filter-group filter-grow">
        <label class="filter-label" for="search-input">Search</label>
        <input id="search-input" class="input" type="search" placeholder="Filter by title or location…" />
      </div>
      <div class="filter-count" id="jobs-count">${jobs.length} jobs</div>
    </div>

    <table class="table">
      <thead>
        <tr>
          <th class="cell-expand"></th>
          <th>Score</th>
          <th>Title</th>
          <th>Location</th>
          <th>Posted</th>
          <th>Status</th>
          <th></th>
        </tr>
      </thead>
      <tbody id="jobs-tbody"></tbody>
    </table>
  `;

  $('#location-filter').addEventListener('change', (e) => {
    currentLocationId = e.target.value;
    rerenderJobsTable();
  });
  $('#rec-filter').value = currentRecFilter;
  $('#rec-filter').addEventListener('change', (e) => {
    currentRecFilter = e.target.value;
    rerenderJobsTable();
  });
  $('#sort-select').value = currentSort;
  $('#sort-select').addEventListener('change', (e) => {
    currentSort = e.target.value;
    rerenderJobsTable();
  });
  $('#search-input').addEventListener('input', (e) => {
    currentSearch = e.target.value.trim();
    rerenderJobsTable();
  });

  rerenderJobsTable();
}

// ---------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------

function route() {
  const hash = window.location.hash || '#/';
  const match = hash.match(/^#\/company\/([^/]+)$/);
  if (match) {
    renderCompanyJobs(decodeURIComponent(match[1]));
  } else {
    renderCompanies();
  }
}

window.addEventListener('hashchange', route);

// ---------------------------------------------------------------------
// Buttons
// ---------------------------------------------------------------------

function wireButton(btnId, apiPath, doneLabel) {
  const btn = $('#' + btnId);
  const label = btn.querySelector('.btn-label');
  const spinner = btn.querySelector('.btn-spinner');

  btn.addEventListener('click', async () => {
    btn.disabled = true;
    label.hidden = true;
    spinner.hidden = false;
    try {
      const result = await apiPost(apiPath);
      if (result.ok) {
        if (apiPath === '/discover') {
          const kept = result.kept !== undefined && result.kept !== result.discovered
            ? ` (kept ${result.kept} after filtering)` : '';
          showToast(
            `Discovered ${result.discovered} jobs${kept} · +${result.added} new, ~${result.updated} updated · ${result.duration_sec}s`,
            'success',
          );
        } else if (apiPath === '/score') {
          showToast(
            `Scored ${result.scored} new, ${result.cached} cached · $${result.est_cost_usd} · ${result.duration_sec}s`,
            'success',
          );
        }
        route();
      } else {
        showToast(`${doneLabel} failed: ${result.error || 'unknown'}`, 'error', 6000);
      }
    } catch (err) {
      showToast(`${doneLabel} failed: ${err.message}`, 'error', 6000);
    } finally {
      btn.disabled = false;
      label.hidden = false;
      spinner.hidden = true;
    }
  });
}

wireButton('refresh-btn', '/discover', 'Refresh');
wireButton('score-btn', '/score', 'Score');

// ---------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------

(async () => {
  try {
    filtersConfig = await apiGet('/filters');
  } catch {
    filtersConfig = { locations: [] };
  }
  route();
})();