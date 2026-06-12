/* === 运维页：Run 列表 / 审计时间线 / 成员 / 沙箱 === */
const Ops = (() => {
  const SUBS = [['runs', 'Run 列表'], ['members', '成员'], ['sandboxes', '沙箱'],
                ['routing', '模型路由']];
  let currentSub = 'runs';

  function show(sub) {
    currentSub = sub || 'runs';
    $('opsNav').innerHTML = '';
    SUBS.forEach(([k, label]) => $('opsNav').appendChild(
      el('a', {href: `#/ops/${k}`, class: k === currentSub ? 'active' : ''}, label)));
    ({runs: renderRuns, members: renderMembers, sandboxes: renderSandboxes,
      routing: renderRouting}[currentSub] || renderRuns)();
  }

  let runsFilter = '';

  async function renderRuns() {
    const c = $('opsContent');
    c.innerHTML = '';
    const sel = el('select', {onchange: () => { runsFilter = sel.value; refresh(); }},
      ...['', 'queued', 'running', 'paused', 'awaiting_approval', 'completed', 'failed', 'cancelled']
        .map(s => el('option', {value: s, selected: s === runsFilter}, s || '全部状态')));
    c.appendChild(el('div', {class: 'toolbar'}, sel,
      el('button', {class: 'mini ghost', onclick: () => refresh()}, '↻ 刷新'),
      el('span', {class: 'dim', id: 'runsTs'})));
    const holder = el('div', {});
    c.appendChild(holder);
    holder.appendChild(dataTable({columns: [], rows: null}));

    async function refresh() {
      try {
        const q = runsFilter ? `?status=${runsFilter}&limit=50` : '?limit=50';
        const runs = await api('/admin/runs' + q);
        const rows = Array.isArray(runs) ? runs : (runs.items || []);
        holder.innerHTML = '';
        holder.appendChild(dataTable({
          empty: '暂无 Run',
          columns: [
            {key: 'id', label: 'Run', render: r => el('code', {title: r.id}, shortId(r.id))},
            {key: 'status', label: '状态', render: r => statusBadge(r.status?.value || r.status)},
            {key: 'created_at', label: '创建时间', render: r => fmtTime(r.created_at)},
            {key: 'usage', label: '用量', sortable: false, render: r =>
              r.usage ? `${r.usage.total_tokens ?? '—'} tok / $${(+r.usage.cost_usd || 0).toFixed(4)}` : '—'},
            {key: 'error', label: '原因/错误', render: r =>
              el('span', {class: 'dim', title: r.error || ''}, (r.error || '—').slice(0, 40))},
            {key: '_', label: '操作', sortable: false, render: r => {
              const st = r.status?.value || r.status;
              const btns = [];
              if (['running', 'awaiting_approval'].includes(st)) {
                btns.push(el('button', {class: 'mini ghost', onclick: () => pauseRun(r)}, '暂停'));
                btns.push(el('button', {class: 'mini danger', onclick: () => cancelRun(r)}, '终止'));
              }
              if (st === 'paused') btns.push(el('button', {class: 'mini', onclick: () => resumeRun(r)}, '恢复'));
              btns.push(el('button', {class: 'mini ghost', onclick: () => auditModal(r)}, '审计'));
              return el('div', {class: 'row'}, ...btns);
            }},
          ],
          rows,
        }));
        const ts = $('runsTs');
        if (ts) ts.textContent = `更新于 ${new Date().toLocaleTimeString('zh-CN')}`;
      } catch (e) { toast(e.message, 'error'); }
    }
    await refresh();
    const t = setInterval(() => { if (S.currentView === 'ops') refresh(); }, 10000);
    Cleanup.push(() => clearInterval(t));
  }

  async function pauseRun(r) {
    try {
      const res = await api(`/risk/runs/${r.id}/pause?reason=manual`, {method: 'POST'});
      toast(res.note || '暂停请求已提交，将在下一安全点生效', 'info');
    } catch (e) { toast(e.message, 'error'); }
  }

  async function resumeRun(r) {
    try {
      await api(`/risk/runs/${r.id}/resume`, {method: 'POST'});
      toast('已重新入队', 'success');
    } catch (e) {
      if (e.status === 409 && /tenant/i.test(e.message)) {
        toast('租户处于风控暂停中，请先在风控页解除租户暂停', 'error');
      } else toast(e.message, 'error');
    }
  }

  async function cancelRun(r) {
    if (!await confirmDlg(`终止 Run ${shortId(r.id)}？`)) return;
    try {
      await api(`/runs/${r.id}/cancel`, {method: 'POST'});
      toast('已终止', 'success');
    } catch (e) { toast(e.message, 'error'); }
  }

  async function auditModal(run) {
    let events = [];
    try {
      const r = await api(`/admin/runs/${run.id}/audit`);
      events = r.events || [];
    } catch (e) { return toast(e.message, 'error'); }

    const types = [...new Set(events.map(e => e.type))].sort();
    const filterSel = el('select', {onchange: render},
      el('option', {value: ''}, '全部类型'),
      ...types.map(t => el('option', {value: t}, t)));
    const list = el('div', {class: 'timeline'});

    function render() {
      list.innerHTML = '';
      const fil = filterSel.value;
      let deltaGroup = null;
      for (const ev of events) {
        const type = ev.type;
        if (fil && type !== fil) continue;
        const payload = ev.payload;
        if (type === 'model.delta') {
          if (!deltaGroup) {
            deltaGroup = {count: 0, text: '', first: ev};
            const d = el('details', {class: 'tl-item delta'},
              el('summary', {}, ''), el('pre', {class: 'ctx'}, ''));
            deltaGroup.el = d;
            list.appendChild(d);
          }
          deltaGroup.count++;
          deltaGroup.text += payload?.text || '';
          deltaGroup.el.querySelector('summary').textContent =
            `#${deltaGroup.first.seq} model.delta ×${deltaGroup.count}（折叠）`;
          deltaGroup.el.querySelector('pre').textContent = deltaGroup.text;
          continue;
        }
        deltaGroup = null;
        list.appendChild(el('div', {class: 'tl-item'},
          el('span', {class: 'tl-seq'}, `#${ev.seq}`),
          el('span', {class: 'tl-type'}, type),
          el('span', {class: 'tl-ts dim'}, fmtTime(ev.ts || ev.created_at)),
          el('details', {}, el('summary', {}, 'payload'),
            el('pre', {class: 'ctx'}, JSON.stringify(payload, null, 2)))));
      }
      if (!list.children.length) list.appendChild(el('div', {class: 'empty'}, '无匹配事件'));
    }
    render();
    openModal(`审计：Run ${shortId(run.id)}（${events.length} 条事件）`,
      el('div', {}, el('div', {class: 'toolbar'}, filterSel), list),
      [el('button', {class: 'ghost', onclick: closeModal}, '关闭')]);
  }

  async function renderMembers() {
    const c = $('opsContent');
    c.innerHTML = '';
    const holder = el('div', {});
    c.appendChild(holder);
    holder.appendChild(dataTable({columns: [], rows: null}));
    try {
      const members = await api('/admin/members');
      const rows = Array.isArray(members) ? members : (members.items || []);
      holder.innerHTML = '';
      holder.appendChild(dataTable({
        empty: '暂无成员',
        columns: [
          {key: 'email', label: 'Email'},
          {key: 'role', label: '角色'},
          {key: '_', label: '修改角色', sortable: false, render: m => {
            if (m.role === 'owner') return el('span', {class: 'dim'}, '不可修改');
            const sel = el('select', {onchange: async () => {
              try {
                await api(`/admin/members/${m.user_id}/role`, {
                  method: 'PUT',
                  body: JSON.stringify({role: sel.value}),
                });
                toast(`已将 ${m.email} 改为 ${sel.value}`, 'success');
              } catch (e) { toast(e.message, 'error'); sel.value = m.role; }
            }}, ...['admin', 'member'].map(r =>
              el('option', {value: r, selected: m.role === r}, r)));
            return sel;
          }},
        ],
        rows,
      }));
    } catch (e) { holder.innerHTML = ''; toast(e.message, 'error'); }
  }

  async function renderSandboxes() {
    const c = $('opsContent');
    c.innerHTML = '';
    const holder = el('div', {});
    c.appendChild(holder);
    holder.appendChild(dataTable({columns: [], rows: null}));
    try {
      const sbs = await api('/sandboxes');
      const rows = Array.isArray(sbs) ? sbs : (sbs.items || []);
      holder.innerHTML = '';
      holder.appendChild(dataTable({
        empty: '无活跃沙箱',
        columns: [
          {key: 'session_id', label: 'Session', render: s => el('code', {}, shortId(s.session_id))},
          {key: 'status', label: '状态'},
          {key: 'pod', label: 'Pod'},
          {key: 'created_at', label: '创建时间', render: s => fmtTime(s.created_at)},
          {key: '_', label: '操作', sortable: false, render: s =>
            el('button', {class: 'mini danger', onclick: async () => {
              if (!await confirmDlg(`终止沙箱 ${shortId(s.session_id)}？`)) return;
              try {
                await api(`/sandboxes/${s.session_id}`, {method: 'DELETE'});
                toast('已终止', 'success');
                renderSandboxes();
              } catch (e) { toast(e.message, 'error'); }
            }}, '终止')},
        ],
        rows,
      }));
    } catch (e) { holder.innerHTML = ''; toast(e.message, 'error'); }
  }

  async function renderRouting(silent = false) {
    const c = $('opsContent');
    if (!silent) {
      c.innerHTML = '';
      c.appendChild(el('div', {class: 'toolbar'},
        el('button', {class: 'mini ghost', onclick: () => renderRouting()}, '↻ 刷新'),
        el('span', {class: 'dim'},
          '有效成本 = 期望成本 / (1 - 失败率) + 延迟惩罚；ε-greedy 探索')));
    }
    const holder = silent ? c.querySelector('.routing-table') : el('div', {class: 'routing-table'});
    if (!silent) c.appendChild(holder);
    if (!silent) holder.appendChild(dataTable({columns: [], rows: null}));
    try {
      const r = await api('/admin/model-routing');
      const rows = (Array.isArray(r) ? r : (r.providers || r.items || [])).map(p => ({
        ...p,
        _score: p.effective_cost ?? p.score,
        breaker_open: p.breaker_open ?? (p.breaker === 'open'),
      }));
      const best = rows.length ? Math.min(...rows.map(p => +p._score || Infinity)) : Infinity;
      const tbl = dataTable({
        empty: '暂无路由统计',
        columns: [
          {key: 'provider', label: 'Provider', render: p =>
            el('span', {}, p.provider || p.name,
              +p._score === best && rows.length ? ' 🏆' : '',
              p.breaker_open ? el('span', {class: 'sev critical'}, ' [熔断]') : '')},
          {key: 'cost_per_1k', label: '名义成本/1k', render: p =>
            p.cost_per_1k != null ? `$${(+p.cost_per_1k).toFixed(4)}` : '—'},
          {key: 'fail_rate', label: '失败率(EWMA)', render: p => {
            const v = +p.fail_rate || 0;
            return el('span', {class: v > 0.2 ? 'sev critical' : v > 0.05 ? 'sev warning' : ''},
              (v * 100).toFixed(1) + '%');
          }},
          {key: 'latency_ms', label: '延迟(EWMA)', render: p =>
            p.p50_latency_ms != null ? `${Math.round(p.p50_latency_ms)}ms`
              : p.latency_ms != null ? `${Math.round(p.latency_ms)}ms` : '—'},
          {key: '_score', label: '有效成本(评分)', render: p =>
            p._score != null ? (+p._score).toFixed(5) : '—'},
          {key: 'calls', label: '请求数', render: p => p.calls ?? p.request_count ?? '—'},
          {key: 'breaker_open', label: '熔断器', render: p =>
            el('span', {class: p.breaker_open ? 'sev critical' : 'on'},
              p.breaker_open ? 'OPEN' : 'CLOSED')},
        ],
        rows,
      });
      if (silent) {
        holder.innerHTML = '';
        holder.appendChild(tbl);
      } else {
        holder.innerHTML = '';
        holder.appendChild(tbl);
        if (r.strategy || r.epsilon != null) {
          holder.appendChild(el('p', {class: 'dim', style: 'margin-top:8px'},
            `策略：${r.strategy || 'cost'}　ε=${r.epsilon ?? '—'}　统计来源：Redis 共享 EWMA（跨 Worker）`));
        }
      }
    } catch (e) {
      if (!silent) {
        holder.innerHTML = '';
        holder.appendChild(el('div', {class: 'empty'},
          e.status === 404 ? '路由观测接口未启用' : e.message));
      }
    }
    if (!silent) {
      const t = setInterval(() => {
        if (S.currentView === 'ops' && currentSub === 'routing') renderRouting(true);
      }, 15000);
      Cleanup.push(() => clearInterval(t));
    }
  }

  return {show};
})();
