/* === 登录 / boot / 路由 / 角色 gating / 暂停状态轮询 === */
const Auth = (() => {

  function pickTenant(tenants) {
    return new Promise(resolve => {
      openModal('选择租户', el('div', {class: 'form'},
        ...tenants.map(t => el('button', {class: 'ghost', onclick: () => {
          closeModal();
          resolve(t);
        }}, `${t.name || shortId(t.id)}（${t.role}）`))), []);
    });
  }

  async function login() {
    try {
      const creds = {email: $('loginEmail').value, password: $('loginPwd').value};
      let r = await api('/auth/login', {method: 'POST', body: JSON.stringify(creds)});

      let tenants = Array.isArray(r.tenants) ? r.tenants : [];
      if (!tenants.length) {
        try {
          const claims = JSON.parse(atob(r.access_token.split('.')[1]
            .replace(/-/g, '+').replace(/_/g, '/')));
          tenants = [{id: claims.tid, name: '', role: claims.role || 'member'}];
        } catch (_) {
          tenants = [{id: '', name: '', role: 'member'}];
        }
      }
      localStorage.setItem('tenants', JSON.stringify(tenants));

      const picked = tenants.length > 1 ? await pickTenant(tenants) : tenants[0];
      if (tenants.length > 1 && picked?.id) {
        r = await api('/auth/login', {method: 'POST', body: JSON.stringify({
          ...creds, tenant_id: picked.id})});
        tenants = Array.isArray(r.tenants) ? r.tenants : tenants;
        localStorage.setItem('tenants', JSON.stringify(tenants));
      }

      S.token = r.access_token || r.token;
      const t = picked || tenants[0] || {};
      S.tenantId = t.id || '';
      S.userRole = t.role || 'member';
      localStorage.setItem('token', S.token);
      localStorage.setItem('tenantId', S.tenantId);
      localStorage.setItem('userRole', S.userRole);
      localStorage.setItem('tenantName', t.name || '');
      if (r.refresh_token) localStorage.setItem('refresh', r.refresh_token);
      $('loginErr').textContent = '';
      boot();
    } catch (e) { $('loginErr').textContent = e.message; }
  }

  async function register() {
    try {
      const email = $('loginEmail').value;
      const name = (email.split('@')[0] || 'tenant').slice(0, 32);
      await api('/auth/register', {method: 'POST', body: JSON.stringify({
        email, password: $('loginPwd').value,
        tenant_name: name.length >= 2 ? name : 'mytenant'})});
      await login();
    } catch (e) { $('loginErr').textContent = e.message; }
  }

  function logout() {
    try { api('/auth/logout', {method: 'POST'}).catch(() => {}); } catch (_) {}
    localStorage.clear();
    location.hash = '';
    location.reload();
  }

  const VIEWS = {chat: null, ops: 'Ops', risk: 'Risk', config: 'Config'};

  function route() {
    runCleanup();
    const parts = location.hash.split('/');
    let view = parts[1] || 'chat', sub = parts[2] || '';
    if ((view === 'ops' || view === 'risk') && !isAdmin()) {
      toast('无权限访问该页面', 'error');
      view = 'chat';
      location.hash = '#/chat';
    }
    if (!(view in VIEWS)) view = 'chat';
    S.currentView = view;
    document.querySelectorAll('.view').forEach(v => v.classList.add('hidden'));
    $('view-' + view).classList.remove('hidden');
    document.querySelectorAll('#mainNav a').forEach(a =>
      a.classList.toggle('active', a.dataset.view === view));
    if (view === 'ops') Ops.show(sub || 'runs');
    if (view === 'risk') Risk.show(sub || 'rules');
    if (view === 'config') Config.show(sub || 'policy');
    if (view === 'chat') Chat.pollSide();
  }

  let pausePoll = null;

  async function pollPauseStatus() {
    if (!isAdmin()) return;
    try {
      const st = await api('/risk/pause/status');
      const b = $('tenantBanner');
      if (st.tenant_paused) {
        b.className = 'banner critical show';
        b.innerHTML = '';
        b.append(
          `⛔ 租户已被风控暂停：${st.pause_info?.reason || st.pause_info?.rule || ''}` +
          (st.pause_ttl_seconds ? `（${Math.ceil(st.pause_ttl_seconds / 60)} 分钟后自动恢复）` : '（需手动解除）') +
          `　暂停中 Run：${st.paused_run_count}　`);
        b.appendChild(el('button', {class: 'mini', onclick: Risk.unpauseTenant}, '解除暂停'));
        b.appendChild(el('a', {href: '#/risk/pause', class: 'banner-link'}, '查看详情'));
        $('sendBtn').disabled = true;
      } else {
        b.className = 'banner';
        if (st.throttle) {
          b.className = 'banner warning show';
          b.textContent = `⚠️ 租户被风控限流：最大并发 ${st.throttle.max_concurrent_runs}`;
        }
        if (!S.curRun) $('sendBtn').disabled = false;
      }
    } catch (_) {}
  }

  async function boot() {
    if (!S.token) {
      $('login').classList.remove('hidden');
      showAppShell(false);
      return;
    }
    document.body.classList.toggle('is-admin', isAdmin());
    try {
      const ws = await api('/workspaces');
      $('login').classList.add('hidden');
      showAppShell(true);
      $('wsSel').innerHTML = ws.length
        ? ws.map(w => `<option value="${esc(w.id)}" ${w.id === S.workspaceId ? 'selected' : ''}>${esc(w.name)}</option>`).join('')
        : '<option value="">— 无工作区 —</option>';
      S.workspaceId = S.workspaceId || ws[0]?.id || null;
      if (S.workspaceId) localStorage.setItem('workspaceId', S.workspaceId);

      $('who').textContent = '';
      $('who').append(`${localStorage.getItem('tenantName') || S.tenantId || ''}（${S.userRole}）`);
      const tenants = JSON.parse(localStorage.getItem('tenants') || '[]');
      if (tenants.length > 1) {
        $('who').appendChild(el('a', {href: '#', class: 'banner-link',
          onclick: async ev => {
            ev.preventDefault();
            if (await confirmDlg('切换租户需要重新登录，继续？')) logout();
          }}, ' 切换租户'));
      }

      window.addEventListener('hashchange', route);
      if (!location.hash) location.hash = '#/chat';
      route();
      Chat.init();
      pollPauseStatus();
      if (pausePoll) clearInterval(pausePoll);
      pausePoll = setInterval(pollPauseStatus, 30000);
    } catch (e) {
      if (e.status !== 401) toast(e.message, 'error');
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    ['loginEmail', 'loginPwd'].forEach(id =>
      $(id).addEventListener('keydown', e => { if (e.key === 'Enter') login(); }));
    boot();
  });

  return {login, register, logout, boot, route, pollPauseStatus};
})();
