/* ============================================================
   Job Autopilot — Vanilla JS frontend
   ============================================================
   Pages:
     /                        Companies overview
     /#/company/{company_id}  Job listing for one company
   ============================================================ */

const API = '/api';

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

// ----------------------------------------------------------------------
// Helpers
// ----------------------------------------------------------------------

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

function showToast(message, type = 'info', durationMs = 3000) {
  const toast = $('#toast');
  toast.textContent = message;
  toast.className = `toast ${type}`;
  toast.hidden = false;
  setTimeout(() => { toast.hidden = true; }, durationMs);
}

async function apiGet(path) {
  const r = await fetch(API + path);
  if (!r.ok) {
    throw new Error(`${r.status} ${r.statusText}`);
  }
  return r.json();
}

async function apiPost(path) {
  const r = await fetch(API + path, { method: 'POST' });
  if (!r.ok) {
    throw new Error(`${r.status} ${r.statusText}`);
  }
  return r.json();
}

// ----------------------------------------------------------------------
// Page renderers
// ----------------------------------------------------------------------

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

  // Hero stats
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
      <div class="hero-value">${fmtRelative(data.last_refreshed)}</div>
      <div class="hero-label">Last refreshed</div>
    </div>
  `;

  // Empty state
  if (data.companies.length === 0) {
    view.innerHTML = `
      <div class="empty">
        <div class="empty-title">No jobs yet</div>
        <p>Click the Refresh button above to fetch jobs from your configured sources in <code>config/sources.json</code>.</p>
      </div>
    `;
    return;
  }

  // Companies table
  const rows = data.companies.map((c) => `
    <tr class="clickable" data-company-id="${escapeHtml(c.id)}">
      <td class="cell-title">${escapeHtml(c.name)}</td>
      <td class="num">${c.job_count}</td>
      <td class="cell-muted">${fmtRelative(c.last_updated)}</td>
      <td class="arrow">→</td>
    </tr>
  `).join('');

  view.innerHTML = `
    <div class="view-header">
      <h2 class="view-title">Companies</h2>
      <span class="view-subtitle">Click a company to see its jobs</span>
    </div>
    <table class="table">
      <thead>
        <tr>
          <th>Company</th>
          <th class="num">Open roles</th>
          <th>Last updated</th>
          <th></th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;

  // Wire up row clicks
  $$('#view tr.clickable').forEach((row) => {
    row.addEventListener('click', () => {
      const id = row.dataset.companyId;
      window.location.hash = `#/company/${id}`;
    });
  });
}

async function renderCompanyJobs(companyId) {
  const view = $('#view');
  const hero = $('#hero');

  hero.innerHTML = '';
  view.innerHTML = '<div class="loading">Loading jobs…</div>';

  let data;
  try {
    data = await apiGet(`/companies/${encodeURIComponent(companyId)}/jobs`);
  } catch (err) {
    view.innerHTML = `
      <div class="error">
        <div><a href="#/" class="back-link">← Back to companies</a></div>
        Failed to load: ${escapeHtml(err.message)}
      </div>`;
    return;
  }

  const { company, jobs } = data;

  const rows = jobs.map((j) => `
    <tr class="clickable" data-url="${escapeHtml(j.url)}">
      <td class="cell-title">${escapeHtml(j.title)}</td>
      <td class="cell-muted">${escapeHtml(j.location || '—')}</td>
      <td class="cell-muted">${escapeHtml(j.posted_on || '—')}</td>
      <td class="cell-muted">${escapeHtml(j.employment_type || '—')}</td>
      <td class="arrow">↗</td>
    </tr>
  `).join('');

  view.innerHTML = `
    <div class="view-header">
      <div>
        <a href="#/" class="back-link">← Back to companies</a>
        <h2 class="view-title">${escapeHtml(company.name)}</h2>
        <span class="view-subtitle">
          ${company.job_count} open role${company.job_count === 1 ? '' : 's'} ·
          updated ${fmtRelative(company.last_updated)}
        </span>
      </div>
    </div>
    <table class="table">
      <thead>
        <tr>
          <th>Title</th>
          <th>Location</th>
          <th>Posted</th>
          <th>Type</th>
          <th></th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;

  // Wire up row clicks: open job URL in new tab
  $$('#view tr.clickable').forEach((row) => {
    row.addEventListener('click', () => {
      window.open(row.dataset.url, '_blank', 'noopener,noreferrer');
    });
  });
}

// ----------------------------------------------------------------------
// Router
// ----------------------------------------------------------------------

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

// ----------------------------------------------------------------------
// Refresh button
// ----------------------------------------------------------------------

const refreshBtn = $('#refresh-btn');
const btnLabel = refreshBtn.querySelector('.btn-label');
const btnSpinner = refreshBtn.querySelector('.btn-spinner');

refreshBtn.addEventListener('click', async () => {
  refreshBtn.disabled = true;
  btnLabel.hidden = true;
  btnSpinner.hidden = false;

  try {
    const result = await apiPost('/discover');
    if (result.ok) {
      const keptInfo = result.kept !== undefined && result.kept !== result.discovered
        ? ` (kept ${result.kept} after filtering)`
        : '';
      showToast(
        `Discovered ${result.discovered} jobs${keptInfo} · +${result.added} new, ~${result.updated} updated · ${result.duration_sec}s`,
        'success',
      );
      route(); // re-render current view
    } else {
      showToast(`Discovery failed: ${result.error || 'unknown error'}`, 'error', 6000);
    }
  } catch (err) {
    showToast(`Discovery failed: ${err.message}`, 'error', 6000);
  } finally {
    refreshBtn.disabled = false;
    btnLabel.hidden = false;
    btnSpinner.hidden = true;
  }
});

// ----------------------------------------------------------------------
// Boot
// ----------------------------------------------------------------------

route();