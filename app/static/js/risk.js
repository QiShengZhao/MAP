/* === 风控页：规则 CRUD / dry-run / incidents / 暂停管理 === */
const Risk = (() => {
  const METRICS = ['tool_call_rate', 'error_rate', 'cost_per_min', 'distinct_tools',
                   'sandbox_exec_rate', 'approval_denied', 'token_rate'];
  const SUBS = [['rules', '规则'], ['incidents', '事件'], ['pause', '暂停状态']];

  function show(sub) {
    $('riskNav').innerHTML = '';
    SUBS.forEach(([k, label]) => $('riskNav').appendChild(
      el('a', {href: `#/risk/${k}`, class: k === sub ? 'active' : ''}, label)));
    ({rules: renderRules, incidents: renderIncidents, pause: renderPause}[sub] || renderRules)();
  }

  async function renderRules() {
    const c = $('riskContent');
    c.innerHTML = '';
    c.appendChild(el('div', {class: 'toolbar'},
      el('button', {onclick: () => ruleForm(null)}, '+ 新建规则'),
      el('button', {class: 'ghost', onclick: () => dryRunModal(null)}, '🧪 Dry-run')));
    const holder = el('div', {});
    c.appendChild(holder);
    holder.appendChild(dataTable({columns: [], rows: null}));
    try {
      const rules = await api('/risk/rules?include_platform=true');
      holder.innerHTML = '';
      holder.appendChild(dataTable({
        empty: '暂无规则，点击「新建规则」创建',
        columns: [
          {key: 'name', label: '名称'},
          {key: 'expression', label: '表达式', render: r =>
            el('code', {class: 'expr', title: r.expression},
              r.expression.length > 40 ? r.expression.slice(0, 40) + '…' : r.expression)},
          {key: 'action', label: 'Action'},
          {key: 'severity', label: '级别', render: r =>
            el('span', {class: `sev ${r.severity}`}, r.severity)},
          {key: 'enabled', label: '状态', render: r =>
            el('span', {class: r.enabled ? 'on' : 'off'}, r.enabled ? '启用' : '停用')},
          {key: 'platform_scope', label: '作用域', render: r => r.platform_scope ? '平台' : '租户'},
          {key: '_', label: '操作', sortable: false, render: r => el('div', {class: 'row'},
            el('button', {class: 'mini ghost', disabled: r.platform_scope,
              onclick: () => ruleForm(r)}, '编辑'),
            el('button', {class: 'mini ghost', disabled: r.platform_scope,
              onclick: () => toggleRule(r)}, r.enabled ? '停用' : '启用'),
            el('button', {class: 'mini ghost', onclick: () => dryRunModal(r)}, 'Dry-run'),
            el('button', {class: 'mini danger', disabled: r.platform_scope,
              onclick: () => deleteRule(r)}, '删除'))},
        ],
        rows: rules,
      }));
    } catch (e) { holder.innerHTML = ''; toast(e.message, 'error'); }
  }

  async function toggleRule(r) {
    try {
      await api(`/risk/rules/${r.id}/toggle?enabled=${!r.enabled}`, {method: 'PATCH'});
      toast(`已${r.enabled ? '停用' : '启用'}：${r.name}`, 'success');
      renderRules();
    } catch (e) { toast(e.message, 'error'); }
  }

  async function deleteRule(r) {
    if (!await confirmDlg(`删除规则「${r.name}」？该操作不可撤销，已产生的事件记录会保留。`)) return;
    try {
      await api(`/risk/rules/${r.id}`, {method: 'DELETE'});
      toast('已删除', 'success');
      renderRules();
    } catch (e) { toast(e.message, 'error'); }
  }

  function ruleForm(rule) {
    const f = {
      name: el('input', {value: rule?.name || '', maxlength: 64}),
      description: el('input', {value: rule?.description || ''}),
      expression: el('textarea', {rows: 2}, rule?.expression || ''),
      action: el('select', {}, ...['throttle', 'flag', 'pause', 'notify'].map(a =>
        el('option', {value: a, selected: rule?.action === a}, a))),
      cooldown: el('input', {type: 'number', min: 10, max: 86400, value: rule?.cooldown_seconds ?? 300}),
      severity: el('select', {}, ...['info', 'warning', 'critical'].map(s =>
        el('option', {value: s, selected: (rule?.severity || 'warning') === s}, s))),
      enabled: el('input', {type: 'checkbox', checked: rule?.enabled ?? true}),
    };
    const paramsBox = el('div', {});
    const p0 = rule?.action_params || {};
    f.p_mcr = null;
    f.p_dur = null;
    f.p_url = null;

    function renderParams() {
      paramsBox.innerHTML = '';
      const a = f.action.value;
      if (a === 'throttle') {
        f.p_mcr = el('input', {type: 'number', min: 0, max: 100, value: p0.max_concurrent_runs ?? 1});
        f.p_dur = el('input', {type: 'number', min: 60, max: 86400, value: p0.duration_seconds ?? 600});
        paramsBox.append(
          field('最大并发 Run（0-100）', f.p_mcr),
          field('持续秒数（60-86400）', f.p_dur));
      } else if (a === 'pause') {
        f.p_dur = el('input', {type: 'number', min: 0, max: 86400, value: p0.duration_seconds ?? 1800});
        paramsBox.append(
          field('持续秒数（0=手动解除，或 60-86400）', f.p_dur, '注意：cooldown 必须 ≤ 持续秒数'));
      } else if (a === 'notify') {
        f.p_url = el('input', {type: 'url', value: p0.webhook_url || '', placeholder: 'https://...'});
        paramsBox.append(field('Webhook URL（必须 https://）', f.p_url));
      }
    }
    f.action.addEventListener('change', renderParams);
    renderParams();

    const errBox = el('div', {class: 'err-text'});
    const body = el('div', {class: 'form'},
      field('名称（2-64）', f.name),
      field('描述', f.description),
      field('表达式（≤2000）', f.expression, '可用指标：' + METRICS.join(', ') + '　示例：error_rate > 0.3'),
      field('Action', f.action),
      paramsBox,
      field('冷却秒数（10-86400）', f.cooldown),
      field('级别', f.severity),
      el('label', {class: 'field row'}, f.enabled, ' 启用'),
      errBox);

    async function submit() {
      errBox.textContent = '';
      const a = f.action.value;
      const action_params = a === 'throttle'
        ? {max_concurrent_runs: +f.p_mcr.value, duration_seconds: +f.p_dur.value}
        : a === 'pause' ? {duration_seconds: +f.p_dur.value}
        : a === 'notify' ? (f.p_url.value ? {webhook_url: f.p_url.value} : {})
        : {};
      if (a === 'pause' && +f.p_dur.value > 0 && +f.cooldown.value > +f.p_dur.value) {
        return errBox.textContent = 'cooldown_seconds 必须 ≤ pause 持续秒数';
      }
      if (a === 'notify' && action_params.webhook_url && !action_params.webhook_url.startsWith('https://')) {
        return errBox.textContent = 'webhook_url 必须以 https:// 开头';
      }
      const payload = {
        name: f.name.value.trim(),
        description: f.description.value.trim(),
        expression: f.expression.value.trim(),
        action: a,
        action_params,
        cooldown_seconds: +f.cooldown.value,
        severity: f.severity.value,
        enabled: f.enabled.checked,
        platform_scope: false,
      };
      try {
        if (rule) await api(`/risk/rules/${rule.id}`, {method: 'PUT', body: JSON.stringify(payload)});
        else await api('/risk/rules', {method: 'POST', body: JSON.stringify(payload)});
        closeModal();
        toast(rule ? '已更新' : '已创建', 'success');
        renderRules();
      } catch (e) { errBox.textContent = e.message; }
    }

    openModal(rule ? `编辑规则：${rule.name}` : '新建规则', body, [
      el('button', {class: 'ghost', onclick: closeModal}, '取消'),
      el('button', {onclick: submit}, rule ? '保存' : '创建'),
    ]);
  }

  function dryRunModal(rule) {
    const exprIn = el('textarea', {rows: 2, disabled: !!rule}, rule?.expression || 'error_rate > 0.3');
    const ctxIn = el('textarea', {rows: 5},
      JSON.stringify([{error_rate: 0.5, cost_per_min: 0.1}, {error_rate: 0.1}], null, 2));
    const winIn = el('input', {type: 'number', min: 0, max: 60, value: 0});
    const result = el('div', {});
    const errBox = el('div', {class: 'err-text'});

    async function run() {
      errBox.textContent = '';
      result.innerHTML = '';
      let contexts;
      try {
        contexts = JSON.parse(ctxIn.value);
        if (!Array.isArray(contexts)) throw 0;
      } catch (_) { return errBox.textContent = 'contexts 必须是 JSON 数组'; }
      const body = {contexts, use_recent_windows: +winIn.value || 0};
      if (rule) body.rule_id = rule.id;
      else body.expression = exprIn.value.trim();
      try {
        const r = await api('/risk/rules/dry-run', {method: 'POST', body: JSON.stringify(body)});
        result.append(
          el('p', {class: 'dim'}, `total: ${r.total}　hits: ${r.hits}　hit_rate: ${(r.hit_rate * 100).toFixed(1)}%`),
          dataTable({
            columns: [
              {key: 'hit', label: '命中', sortable: false, render: x =>
                x.error ? el('span', {class: 'sev critical'}, 'ERR') : (x.hit ? '✅' : '—')},
              {key: 'context', label: 'Context', sortable: false, render: x =>
                el('code', {class: 'expr'}, JSON.stringify(x.context))},
              {key: 'error', label: '错误', sortable: false, render: x => x.error || ''},
            ],
            rows: r.results,
          }));
      } catch (e) { errBox.textContent = e.message; }
    }

    openModal(`Dry-run${rule ? `：${rule.name}` : ''}`, el('div', {class: 'form'},
      field('表达式', exprIn, rule ? '使用已存规则的表达式' : '可用指标：' + METRICS.join(', ')),
      field('Contexts（JSON 数组）', ctxIn),
      field('use_recent_windows（0=不用真实窗口，1-60）', winIn),
      errBox, result), [
      el('button', {class: 'ghost', onclick: closeModal}, '关闭'),
      el('button', {onclick: run}, '执行 Dry-run'),
    ]);
  }

  async function renderIncidents(offset = 0, severity = '') {
    const c = $('riskContent');
    c.innerHTML = '';
    const sevSel = el('select', {onchange: () => renderIncidents(0, sevSel.value)},
      ...['', 'info', 'warning', 'critical'].map(s =>
        el('option', {value: s, selected: s === severity}, s || '全部级别')));
    c.appendChild(el('div', {class: 'toolbar'}, sevSel));
    const holder = el('div', {});
    c.appendChild(holder);
    holder.appendChild(dataTable({columns: [], rows: null}));
    try {
      const q = `limit=50&offset=${offset}` + (severity ? `&severity=${severity}` : '');
      const r = await api(`/risk/incidents?${q}`);
      holder.innerHTML = '';
      holder.appendChild(dataTable({
        empty: '暂无风控事件',
        columns: [
          {key: 'created_at', label: '时间', render: x => fmtTime(x.created_at)},
          {key: 'rule_name', label: '规则'},
          {key: 'severity', label: '级别', render: x => el('span', {class: `sev ${x.severity}`}, x.severity)},
          {key: 'action', label: 'Action'},
          {key: 'action_executed', label: '已执行', render: x => x.action_executed ? '✅' : '（抑制）'},
          {key: 'context', label: 'Context', sortable: false, render: x =>
            el('details', {}, el('summary', {}, '查看'),
              el('pre', {class: 'ctx'}, JSON.stringify(x.context, null, 2)))},
        ],
        rows: r.items,
      }));
      holder.appendChild(el('div', {class: 'row toolbar'},
        el('span', {class: 'dim'}, `共 ${r.total} 条`),
        el('button', {class: 'mini ghost', disabled: offset <= 0,
          onclick: () => renderIncidents(Math.max(0, offset - 50), severity)}, '上一页'),
        el('button', {class: 'mini ghost', disabled: offset + 50 >= r.total,
          onclick: () => renderIncidents(offset + 50, severity)}, '下一页')));
    } catch (e) { holder.innerHTML = ''; toast(e.message, 'error'); }
  }

  async function renderPause() {
    const c = $('riskContent');
    c.innerHTML = '';
    try {
      const st = await api('/risk/pause/status');
      let ttl = st.pause_ttl_seconds;
      const ttlEl = el('b', {}, ttl ? `${Math.floor(ttl / 60)}:${String(ttl % 60).padStart(2, '0')}` : '需手动解除');
      if (ttl) {
        const t = setInterval(() => {
          if (--ttl <= 0) { clearInterval(t); renderPause(); return; }
          ttlEl.textContent = `${Math.floor(ttl / 60)}:${String(ttl % 60).padStart(2, '0')}`;
        }, 1000);
        Cleanup.push(() => clearInterval(t));
      }
      const card = (title, ...kids) => el('div', {class: 'card'}, el('h3', {}, title), ...kids);
      c.append(
        card('租户暂停状态',
          el('div', {class: 'kv'}, '状态', el('b', {class: st.tenant_paused ? 'sev critical' : 'on'},
            st.tenant_paused ? '已暂停' : '正常')),
          st.tenant_paused ? el('div', {class: 'kv'}, '原因',
            el('b', {}, st.pause_info?.reason || st.pause_info?.rule || '—')) : null,
          st.tenant_paused ? el('div', {class: 'kv'}, '自动恢复', ttlEl) : null,
          st.tenant_paused ? el('button', {class: 'danger', onclick: unpauseTenant}, '解除租户暂停') : null),
        card('限流（throttle）',
          st.throttle
            ? el('div', {class: 'kv'}, '最大并发 Run', el('b', {}, st.throttle.max_concurrent_runs))
            : el('p', {class: 'dim'}, '无生效限流')),
        card('暂停中的 Run',
          el('div', {class: 'kv'}, '数量', el('b', {}, st.paused_run_count)),
          el('a', {href: '#/ops/runs', class: 'banner-link'}, '前往运维页查看')));
    } catch (e) { toast(e.message, 'error'); }
  }

  async function unpauseTenant() {
    if (!await confirmDlg('解除租户风控暂停？暂停中的 Run 将自动重新入队。')) return;
    try {
      const r = await api('/risk/pause', {method: 'DELETE'});
      toast(`已解除，${r.runs_requeued} 个 Run 重新入队`, 'success');
      Auth.pollPauseStatus();
      if (S.currentView === 'risk') renderPause();
    } catch (e) {
      if (e.status === 404) toast('租户当前未被暂停', 'info');
      else toast(e.message, 'error');
    }
  }

  return {show, unpauseTenant};
})();
