/* === 对话页：会话 / SSE / Run 控制 / 审批 / 侧栏 === */
const Chat = (() => {
  let asstEl = null, curApproval = null, sidePoll = null;

  function init() {
    $('input').addEventListener('keydown', e => { if (e.ctrlKey && e.key === 'Enter') send(); });
    loadSessions();
    pollSide();
    if (sidePoll) clearInterval(sidePoll);
    sidePoll = setInterval(pollSide, 10000);
  }

  function onWorkspaceChange() {
    S.workspaceId = $('wsSel').value || null;
    if (S.workspaceId) localStorage.setItem('workspaceId', S.workspaceId);
    S.curSession = null;
    S.curRun = null;
    $('msgs').innerHTML = '';
    setRunStatus('idle');
    loadSessions();
  }

  function msgText(content) {
    if (content == null) return '';
    if (typeof content === 'string') return content;
    return content.text ?? JSON.stringify(content);
  }

  async function loadSessions() {
    if (!S.workspaceId) S.workspaceId = $('wsSel').value || null;
    let sessions = [];
    try {
      sessions = await api(`/workspaces/${S.workspaceId}/sessions`);
    } catch (_) {
      sessions = JSON.parse(localStorage.getItem('sessHistory') || '[]');
    }
    $('sessList').innerHTML = '';
    sessions.forEach(s => $('sessList').appendChild(
      el('div', {
        class: `item ${s.id === S.curSession ? 'active' : ''}`,
        onclick: () => openSession(s.id),
      }, s.title || `会话 ${shortId(s.id)}`)));
  }

  function rememberSession(id, title) {
    const h = JSON.parse(localStorage.getItem('sessHistory') || '[]');
    if (!h.find(x => x.id === id)) {
      h.unshift({id, title: title || `会话 ${shortId(id)}`});
      localStorage.setItem('sessHistory', JSON.stringify(h.slice(0, 50)));
    }
  }

  async function newSession() {
    S.curSession = null;
    S.curRun = null;
    $('msgs').innerHTML = '';
    setRunStatus('idle');
    await loadSessions();
  }

  async function openSession(id) {
    S.curSession = id;
    $('msgs').innerHTML = '';
    try {
      const msgs = await api(`/sessions/${id}/messages`);
      msgs.forEach(m => addMsg(m.role, msgText(m.content)));

      const lastRun = msgs.findLast?.(m => m.run_id) ?? [...msgs].reverse().find(m => m.run_id);
      if (lastRun?.run_id) {
        let status = lastRun.run_status;
        try {
          status = (await api(`/runs/${lastRun.run_id}`)).status || status;
        } catch (_) {}
        status = status?.value || status;
        if (['running', 'queued', 'awaiting_approval'].includes(status)) {
          S.curRun = lastRun.run_id;
          setRunStatus(status);
          setRunning(true);
          subscribe(lastRun.run_id);
        } else if (status === 'paused') {
          S.curRun = lastRun.run_id;
          setRunStatus('paused');
          addEvent('⏸ 该会话有一个暂停中的 Run，可点击「恢复」继续');
        }
      } else {
        S.curRun = null;
        setRunStatus('idle');
      }
    } catch (e) {
      if (e.status !== 404) toast(e.message, 'error');
    }
    await loadSessions();
    scroll_();
  }

  async function send() {
    const text = $('input').value.trim();
    if (!text || S.curRun || !S.workspaceId) {
      if (!S.workspaceId) toast('请先选择工作区', 'error');
      return;
    }
    try {
      $('input').value = '';
      addMsg('user', text);
      const r = await api('/messages', {method: 'POST', body: JSON.stringify({
        workspace_id: S.workspaceId,
        session_id: S.curSession,
        content: text,
      })});
      if (!S.curSession && r.session_id) {
        S.curSession = r.session_id;
        rememberSession(r.session_id, text.slice(0, 40));
        loadSessions();
      }
      S.curRun = r.run_id;
      setRunning(true);
      setRunStatus('queued');
      subscribe(r.run_id);
    } catch (e) {
      if (e.status === 403 && /paused/i.test(e.message)) {
        const b = $('tenantBanner');
        b.className = 'banner critical show';
        b.innerHTML = '';
        b.append(`⛔ ${e.message}　`);
        if (isAdmin()) {
          b.appendChild(el('a', {href: '#/risk/pause', class: 'banner-link'}, '前往风控页处理'));
        }
      } else {
        toast(e.message, 'error');
      }
    }
  }

  function subscribe(runId) {
    S.streamEs?.close();
    const es = new EventSource(`/v1/runs/${runId}/stream?token=${encodeURIComponent(S.token)}`);
    S.streamEs = es;

    const evData = e => {
      try { return JSON.parse(e.data) || {}; } catch (_) { return {}; }
    };

    const H = {
      'model.delta': p => {
        if (!asstEl) { asstEl = addMsg('assistant', ''); asstEl.classList.add('cursor'); }
        asstEl.textContent += p.text || '';
        scroll_();
      },
      'tool.call': p => addTool(p),
      'tool.result': p => finishTool(p),
      'tool.blocked': p => addEvent(`⛔ 工具被拦截: ${p.name}`),
      'agent.handoff': p => addEvent(`🔀 交接 → ${p.to || '?'}`),
      'approval.required': p => {
        setRunStatus('awaiting_approval');
        curApproval = p.approval_id;
        $('apvTool').textContent = `工具：${p.tool}`;
        $('apvArgs').textContent = JSON.stringify(p.args, null, 2);
        $('apvDlg').showModal();
      },
      'run.started': () => setRunStatus('running'),
      'run.paused': p => {
        setRunStatus('paused');
        addEvent(`⏸ 已暂停：${p.reason || ''}（${p.paused_from || ''}，iteration ${p.iteration ?? '?'})`);
        setRunning(false);
        S.streamEs?.close();
        S.streamEs = null;
      },
      'run.resumed': p => {
        setRunStatus('running');
        addEvent(`▶ 已从检查点恢复（iteration ${p.iteration ?? '?'}）`);
      },
      'run.completed': () => endRun('completed'),
      'run.failed': p => { endRun('failed'); addEvent(`❌ ${p.reason || ''}`); },
      'run.cancelled': () => endRun('cancelled'),
      'run.budget_exceeded': () => addEvent('⛔ 预算熔断'),
    };
    for (const [type, fn] of Object.entries(H)) es.addEventListener(type, e => fn(evData(e)));
    es.onerror = () => { if (es.readyState === EventSource.CLOSED) S.streamEs = null; };
  }

  function endRun(status) {
    setRunStatus(status);
    setRunning(false);
    S.curRun = null;
    S.streamEs?.close();
    S.streamEs = null;
    asstEl?.classList.remove('cursor');
    asstEl = null;
    pollSide();
  }

  async function pauseRun() {
    if (!S.curRun) return;
    try {
      const r = await api(`/risk/runs/${S.curRun}/pause?reason=manual`, {method: 'POST'});
      toast(r.note || '暂停请求已提交，将在下一安全点生效', 'info');
    } catch (e) { toast(e.message, 'error'); }
  }

  async function resumeRun() {
    if (!S.curRun) return;
    try {
      await api(`/risk/runs/${S.curRun}/resume`, {method: 'POST'});
      toast('已重新入队', 'success');
      setRunStatus('queued');
      setRunning(true);
      subscribe(S.curRun);
    } catch (e) {
      if (e.status === 409 && /tenant/i.test(e.message)) {
        toast('租户处于风控暂停中，请先解除租户暂停', 'error');
      } else toast(e.message, 'error');
    }
  }

  async function cancelRun() {
    if (!S.curRun) return;
    try { await api(`/runs/${S.curRun}/cancel`, {method: 'POST'}); }
    catch (e) { toast(e.message, 'error'); }
  }

  async function decide(approve) {
    $('apvDlg').close();
    try {
      await api(`/approvals/${curApproval}/decide`, {
        method: 'POST',
        body: JSON.stringify({approved: approve}),
      });
      setRunStatus('running');
      curApproval = null;
    } catch (e) { toast(e.message, 'error'); }
  }

  function setRunStatus(s) {
    S.curRunStatus = s;
    setBadge(s);
    const can = {
      pause: ['running', 'awaiting_approval'],
      resume: ['paused'],
      cancel: ['running', 'awaiting_approval', 'queued'],
    };
    $('btnPause').classList.toggle('hidden', !(isAdmin() && can.pause.includes(s)));
    $('btnResume').classList.toggle('hidden', !(isAdmin() && can.resume.includes(s)));
    $('btnCancel').classList.toggle('hidden', !can.cancel.includes(s));
  }

  function setBadge(s) {
    const b = $('runBadge');
    b.className = 'badge ' + s;
    b.textContent = String(s).replace('_', ' ');
  }

  function setRunning(on) {
    $('sendBtn').disabled = on;
    if (!on && S.curRunStatus !== 'paused') setRunStatus('idle');
  }

  function addMsg(role, text) {
    const d = el('div', {class: 'msg ' + role}, text);
    $('msgs').appendChild(d);
    scroll_();
    return d;
  }

  function addEvent(t) {
    $('msgs').appendChild(el('div', {class: 'msg event'}, t));
    scroll_();
  }

  function addTool(p) {
    const d = el('div', {class: 'tool', 'data-cid': p.id},
      el('div', {class: 'hd', onclick: e => e.currentTarget.parentNode.classList.toggle('open')},
        `🔧 ${p.name} `, el('span', {class: 'st'}, '⏳')),
      el('pre', {}, JSON.stringify(p.args, null, 2)));
    $('msgs').appendChild(d);
    scroll_();
  }

  function finishTool(p) {
    const d = document.querySelector(`.tool[data-cid="${CSS.escape(p.id || '')}"]`);
    if (!d) return;
    const ok = !String(p.output || '').includes('"error"');
    d.querySelector('.st').textContent = ok ? '✅' : '❌';
    if (!ok) d.classList.add('fail');
    d.querySelector('pre').textContent += `\n── result ──\n${(p.output || '').slice(0, 2000)}`;
  }

  function scroll_() { $('msgs').scrollTop = $('msgs').scrollHeight; }

  async function pollSide() {
    if (S.currentView !== 'chat') return;
    try {
      const summary = await api('/usage/summary?days=1');
      const budget = await api('/usage/budget');
      const pct = budget.limit_usd ? budget.used_usd / budget.limit_usd : 0;
      $('usage').innerHTML = [
        ['今日用量', Object.entries(summary).map(([k, v]) => `${k}:${v}`).join(' ') || '—'],
        ['已用/限额', `$${(+budget.used_usd).toFixed(4)} / $${(+budget.limit_usd).toFixed(2)}`],
        ['剩余', `$${(+budget.remaining_usd).toFixed(4)}`],
      ].map(([k, v]) => `<div class="kv">${k}<b>${esc(v)}</b></div>`).join('');
      const bb = $('budgetBanner');
      bb.className = 'banner';
      if (pct >= 0.9) { bb.className = 'banner emergency show'; bb.textContent = '⛔ 预算告急'; }
      else if (pct >= 0.7) { bb.className = 'banner critical show'; bb.textContent = '🚨 预算即将耗尽'; }
      else if (pct >= 0.5) { bb.className = 'banner warning show'; bb.textContent = '⚠️ 预算消耗较快'; }
    } catch (_) {}

    try {
      if (S.curSession) {
        const arts = await api(`/artifacts?session_id=${S.curSession}`);
        $('artifacts').innerHTML = '';
        if (!arts.length) $('artifacts').innerHTML = '<div class="kv">暂无</div>';
        arts.forEach(a => $('artifacts').appendChild(el('div', {class: 'artifact'},
          el('span', {}, a.name),
          el('a', {href: '#', onclick: async ev => {
            ev.preventDefault();
            const r = await api(`/artifacts/${a.id}/download`);
            window.open(r.url);
          }}, `↓ ${fmtSize(a.size)}`))));
      }
      const apvs = await api('/approvals');
      $('approvals').innerHTML = '';
      if (!apvs.length) $('approvals').innerHTML = '<div class="kv">无</div>';
      apvs.forEach(a => $('approvals').appendChild(el('div', {class: 'artifact'},
        el('span', {}, a.tool),
        el('a', {href: '#', onclick: ev => {
          ev.preventDefault();
          curApproval = a.id;
          $('apvTool').textContent = `工具：${a.tool}`;
          $('apvArgs').textContent = JSON.stringify(a.args || {}, null, 2);
          $('apvDlg').showModal();
        }}, '审批'))));
    } catch (_) {}
  }

  return {
    init, loadSessions, newSession, openSession, send, onWorkspaceChange,
    pauseRun, resumeRun, cancelRun, decide, pollSide,
  };
})();
