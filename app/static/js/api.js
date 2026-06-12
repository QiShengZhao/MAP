/* === 全局状态 + API 封装 === */
const S = {
  token: localStorage.getItem('token') || null,
  tenantId: localStorage.getItem('tenantId') || null,
  userRole: localStorage.getItem('userRole') || 'member',
  workspaceId: localStorage.getItem('workspaceId') || null,
  currentView: 'chat',
  curSession: null, curRun: null, curRunStatus: 'idle', streamEs: null,
};

const api = async (path, opt = {}) => {
  const r = await fetch('/v1' + path, {
    ...opt,
    headers: {
      'Content-Type': 'application/json',
      'Authorization': 'Bearer ' + S.token,
      ...(opt.headers || {}),
    },
  });
  if (r.status === 401) {
    Auth.logout();
    throw Object.assign(new Error('登录已过期'), {status: 401});
  }
  if (r.status === 429) {
    const ra = r.headers.get('Retry-After');
    let d = '请求过于频繁';
    try { d = (await r.json()).detail || d; } catch (_) {}
    throw Object.assign(new Error(d + (ra ? `（${ra}s 后可重试）` : '')), {status: 429});
  }
  if (!r.ok) {
    let detail = r.statusText;
    try {
      const j = await r.json();
      detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail);
    } catch (_) {}
    throw Object.assign(new Error(detail), {status: r.status});
  }
  return r.status === 204 ? null : r.json();
};

/* === 工具函数 === */
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmtTime = t => t ? new Date(t).toLocaleString('zh-CN', {hour12: false}) : '—';
const shortId = id => (id || '').slice(0, 8);
const fmtSize = n => n > 1048576 ? (n / 1048576).toFixed(1) + 'MB' : n > 1024 ? (n / 1024).toFixed(1) + 'KB' : n + 'B';
const isAdmin = () => ['owner', 'admin'].includes(S.userRole);

const Cleanup = [];
function runCleanup() {
  while (Cleanup.length) {
    try { Cleanup.pop()(); } catch (_) {}
  }
}

function showAppShell(on) {
  document.querySelector('header')?.classList.toggle('hidden', !on);
  document.querySelector('main')?.classList.toggle('hidden', !on);
}
