/* === 配置页：Policy / Agents / Skills（admin 可写，member 只读）=== */
const Config = (() => {
  const SUBS = [['policy', 'Policy'], ['agents', 'Agents'], ['skills', 'Skills'],
              ['billing', 'Billing']];

  function show(sub) {
    $('cfgNav').innerHTML = '';
    SUBS.forEach(([k, label]) => $('cfgNav').appendChild(
      el('a', {href: `#/config/${k}`, class: k === sub ? 'active' : ''}, label)));
    ({policy: renderPolicy, agents: renderAgents, skills: renderSkills,
      billing: Billing.render}[sub] || renderPolicy)();
  }

  const toArr = s => s.split(',').map(x => x.trim()).filter(Boolean);
  const toStr = a => (a || []).join(', ');

  async function renderPolicy() {
    const c = $('cfgContent');
    c.innerHTML = '';
    try {
      const p = await api('/policy');
      const ro = !isAdmin();
      const f = {
        allowed_tools: el('input', {value: toStr(p.allowed_tools), disabled: ro}),
        approval_required_tools: el('input', {value: toStr(p.approval_required_tools), disabled: ro}),
        blocked_domains: el('input', {value: toStr(p.blocked_domains), disabled: ro}),
        max_concurrent_runs: el('input', {type: 'number', min: 1, value: p.max_concurrent_runs ?? 5, disabled: ro}),
        max_tokens_per_day: el('input', {type: 'number', min: 0, value: p.max_tokens_per_day ?? 0, disabled: ro}),
        max_cost_per_day_usd: el('input', {type: 'number', step: '0.01', min: 0, value: p.max_cost_per_day_usd ?? 0, disabled: ro}),
        max_cost_per_run_usd: el('input', {type: 'number', step: '0.01', min: 0, value: p.max_cost_per_run_usd ?? 0, disabled: ro}),
      };
      c.appendChild(el('div', {class: 'form card'},
        field('允许的工具（逗号分隔，空=全部）', f.allowed_tools),
        field('需审批的工具', f.approval_required_tools),
        field('封禁域名', f.blocked_domains),
        field('最大并发 Run', f.max_concurrent_runs),
        field('每日 Token 上限（0=不限）', f.max_tokens_per_day),
        field('每日成本上限 USD（0=不限）', f.max_cost_per_day_usd),
        field('单 Run 成本上限 USD（0=不限）', f.max_cost_per_run_usd),
        ro ? el('p', {class: 'dim'}, '只读：需要 admin 权限才能修改')
           : el('button', {onclick: async () => {
              try {
                await api('/policy', {method: 'PUT', body: JSON.stringify({
                  allowed_tools: toArr(f.allowed_tools.value),
                  approval_required_tools: toArr(f.approval_required_tools.value),
                  blocked_domains: toArr(f.blocked_domains.value),
                  max_concurrent_runs: +f.max_concurrent_runs.value,
                  max_tokens_per_day: +f.max_tokens_per_day.value,
                  max_cost_per_day_usd: +f.max_cost_per_day_usd.value,
                  max_cost_per_run_usd: +f.max_cost_per_run_usd.value,
                })});
                toast('Policy 已保存', 'success');
              } catch (e) { toast(e.message, 'error'); }
            }}, '保存')));
    } catch (e) { toast(e.message, 'error'); }
  }

  async function renderAgents() {
    const c = $('cfgContent');
    c.innerHTML = '';
    if (isAdmin()) {
      c.appendChild(el('div', {class: 'toolbar'},
        el('button', {onclick: () => agentForm(null)}, '+ 新建 Agent')));
    }
    const holder = el('div', {});
    c.appendChild(holder);
    holder.appendChild(dataTable({columns: [], rows: null}));
    try {
      const agents = await api('/agents');
      const rows = Array.isArray(agents) ? agents : (agents.items || []);
      holder.innerHTML = '';
      holder.appendChild(dataTable({
        empty: '暂无 Agent',
        columns: [
          {key: 'name', label: '名称', render: a => el('span', {}, a.name, a.is_default ? ' ⭐' : '')},
          {key: 'model', label: '模型'},
          {key: 'tools', label: '工具', sortable: false, render: a => toStr(a.tools) || '—'},
          {key: 'handoffs', label: 'Handoffs', sortable: false, render: a => toStr(a.handoffs) || '—'},
          {key: 'as_tool', label: 'As-Tool', render: a => a.as_tool ? '✅' : '—'},
          {key: 'enabled', label: '状态', render: a =>
            el('span', {class: a.enabled ? 'on' : 'off'}, a.enabled ? '启用' : '停用')},
          {key: '_', label: '操作', sortable: false, render: a =>
            isAdmin()
              ? el('button', {class: 'mini ghost', onclick: () => agentForm(a)}, '编辑')
              : el('span', {class: 'dim'}, '只读')},
        ],
        rows,
      }));
    } catch (e) { holder.innerHTML = ''; toast(e.message, 'error'); }
  }

  function agentForm(a) {
    const f = {
      name: el('input', {value: a?.name || ''}),
      description: el('input', {value: a?.description || ''}),
      instructions: el('textarea', {rows: 5}, a?.instructions || ''),
      model: el('input', {value: a?.model || 'gpt-4o', placeholder: 'gpt-4o'}),
      tools: el('input', {value: toStr(a?.tools)}),
      handoffs: el('input', {value: toStr(a?.handoffs)}),
      as_tool: el('input', {type: 'checkbox', checked: a?.as_tool ?? false}),
      is_default: el('input', {type: 'checkbox', checked: a?.is_default ?? false}),
      enabled: el('input', {type: 'checkbox', checked: a?.enabled ?? true}),
    };
    const errBox = el('div', {class: 'err-text'});
    openModal(a ? `编辑 Agent：${a.name}` : '新建 Agent', el('div', {class: 'form'},
      field('名称', f.name),
      field('描述', f.description),
      field('Instructions（system prompt）', f.instructions),
      field('模型', f.model),
      field('工具（逗号分隔）', f.tools),
      field('Handoff 目标（逗号分隔的 Agent 名）', f.handoffs),
      el('label', {class: 'field row'}, f.as_tool, ' 可作为子代理工具（Agents-as-Tools）'),
      el('label', {class: 'field row'}, f.is_default, ' 默认 Agent'),
      el('label', {class: 'field row'}, f.enabled, ' 启用'),
      errBox), [
      el('button', {class: 'ghost', onclick: closeModal}, '取消'),
      el('button', {onclick: async () => {
        const body = JSON.stringify({
          name: f.name.value.trim(),
          description: f.description.value.trim(),
          instructions: f.instructions.value,
          model: f.model.value.trim() || 'gpt-4o',
          tools: toArr(f.tools.value),
          handoffs: toArr(f.handoffs.value),
          as_tool: f.as_tool.checked,
          is_default: f.is_default.checked,
          enabled: f.enabled.checked,
        });
        try {
          if (a) await api(`/agents/${a.id}`, {method: 'PUT', body});
          else await api('/agents', {method: 'POST', body});
          closeModal();
          toast('已保存', 'success');
          renderAgents();
        } catch (e) { errBox.textContent = e.message; }
      }}, '保存'),
    ]);
  }

  async function renderSkills() {
    const c = $('cfgContent');
    c.innerHTML = '';
    if (isAdmin()) {
      c.appendChild(el('div', {class: 'toolbar'},
        el('button', {onclick: () => skillForm(null)}, '+ 新建 Skill')));
    }
    const holder = el('div', {});
    c.appendChild(holder);
    holder.appendChild(dataTable({columns: [], rows: null}));
    try {
      const skills = await api('/skills');
      const rows = Array.isArray(skills) ? skills : (skills.items || []);
      holder.innerHTML = '';
      holder.appendChild(dataTable({
        empty: '暂无 Skill',
        columns: [
          {key: 'name', label: '名称'},
          {key: 'description', label: '描述', render: s => s.description || '—'},
          {key: 'enabled', label: '状态', render: s =>
            el('span', {class: s.enabled ? 'on' : 'off'}, s.enabled ? '启用' : '停用')},
          {key: '_', label: '操作', sortable: false, render: s => !isAdmin()
            ? el('span', {class: 'dim'}, '只读')
            : el('div', {class: 'row'},
              el('button', {class: 'mini ghost', onclick: () => skillForm(s)}, '编辑'),
              el('button', {class: 'mini danger', onclick: async () => {
                if (!await confirmDlg(`删除 Skill「${s.name}」？`)) return;
                try {
                  await api(`/skills/${s.id}`, {method: 'DELETE'});
                  toast('已删除', 'success');
                  renderSkills();
                } catch (e) { toast(e.message, 'error'); }
              }}, '删除'))},
        ],
        rows,
      }));
    } catch (e) { holder.innerHTML = ''; toast(e.message, 'error'); }
  }

  function skillForm(s) {
    const f = {
      name: el('input', {value: s?.name || ''}),
      description: el('input', {value: s?.description || ''}),
      instructions: el('textarea', {rows: 6}, s?.instructions || ''),
      enabled: el('input', {type: 'checkbox', checked: s?.enabled ?? true}),
    };
    const errBox = el('div', {class: 'err-text'});
    openModal(s ? `编辑 Skill：${s.name}` : '新建 Skill', el('div', {class: 'form'},
      field('名称', f.name),
      field('描述', f.description),
      field('Instructions', f.instructions),
      el('label', {class: 'field row'}, f.enabled, ' 启用'),
      errBox), [
      el('button', {class: 'ghost', onclick: closeModal}, '取消'),
      el('button', {onclick: async () => {
        const body = JSON.stringify({
          name: f.name.value.trim(),
          description: f.description.value.trim(),
          instructions: f.instructions.value,
          enabled: f.enabled.checked,
        });
        try {
          if (s) await api(`/skills/${s.id}`, {method: 'PUT', body});
          else await api('/skills', {method: 'POST', body});
          closeModal();
          toast('已保存', 'success');
          renderSkills();
        } catch (e) { errBox.textContent = e.message; }
      }}, '保存'),
    ]);
  }

  return {show};
})();
